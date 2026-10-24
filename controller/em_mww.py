"""
em_mww.py — microWakeWord scored alongside openWakeWord, as a measurement.

WHY THIS EXISTS. Every microWakeWord number we had before this module was
OFFLINE rescoring of clips openWakeWord had already chosen to save. That can
answer one question and not the other:

  - "does mww reject oww's false positives?"  — measurable, and the answer was
    99.3% over a 1079-clip corpus (2026-10-18).
  - "does mww have false positives of ITS OWN?" — NOT measurable that way, and
    not because the number was small. Clips are written only when oww crosses
    wakeClipMinScore, so audio mww would fire on and oww ignored was never
    recorded at all. The number did not exist.

So this scorer exists to make the second question answerable, and the capture
floor in em_wake_samples.consider() is deliberately OR'd across both engines
for the same reason. Adding mww here while leaving capture gated on oww alone
would reproduce the blind spot exactly.

NOT A DETECTOR. Nothing here can start a turn. decide_wake_source() never sees
these scores. This is instrumentation, and it stays instrumentation until the
two-sided comparison says otherwise.

HOW IT DIFFERS FROM OWW, which is the whole reason it is worth running:
openWakeWord is melspectrogram -> a frozen Google speech embedding -> a small
classifier head, scored on 80ms chunks. microWakeWord is one end-to-end
streaming network over the micro_speech 40-channel frontend at a 10ms step.
Different features, different architecture, independently trained — measured
correlation between their scores on 1041 shared false positives was +0.128,
i.e. they are fooled by nearly disjoint things. Two scorers that fail together
would not be worth the CPU.

STATE IS PER DEVICE AND MUST STAY THAT WAY. Both the frontend and the network
are streaming: the tflite model carries variable tensors across invocations
and the frontend carries a sample remainder. Two devices sharing one Scorer
would interleave their audio into each other's state and score noise. One
Scorer per Device, built on demand, dropped with the connection.

DETECTION RULE is ESPHome's, not ours — streaming_model.cpp determine_detected():
a moving AVERAGE of the last `sliding_window_size` window probabilities
compared against `probability_cutoff` (`sum > cutoff * N`). It is an average,
NOT N consecutive windows above the bar; the distinction matters because the
average tolerates a dip that a consecutive rule rejects.
"""

import logging
import os
import json
from pathlib import Path

import numpy as np

log = logging.getLogger("echomuse.mww")

# Model + config live beside the controller, not in the DB directory: they are
# code-adjacent constants shipped with the release, not per-install state.
MODEL_DIR = Path(os.environ.get(
    "EM_MWW_DIR", Path(__file__).resolve().parent / "models" / "mww"))
MODEL_FILE = "alexa.tflite"
CONFIG_FILE = "alexa.json"

# Each invoke consumes this many 10ms frontend frames (the model's input is
# [1, 3, 40]). Read from the model rather than hardcoded, but named here
# because the frame remainder logic below is only correct for a fixed stride.
_FEATURE_BINS = 40

_unavailable_reason: str | None = None
_shared = None  # lazily-built (interpreter-free) shared config


def _load_config() -> dict:
    with open(MODEL_DIR / CONFIG_FILE) as fh:
        return json.load(fh)["micro"]


def available() -> tuple[bool, str]:
    """
    Whether a Scorer can be built, and why not when it cannot.

    Checked once at startup so a missing runtime is one clear log line rather
    than an exception per device per connection. The controller runs fine
    without it — mww is a measurement, and losing it must never cost a wake.
    """
    global _unavailable_reason
    try:
        import ai_edge_litert.interpreter  # noqa: F401
        import pymicro_features  # noqa: F401
    except ImportError as exc:
        _unavailable_reason = (
            f"{exc.name} not installed — microWakeWord scoring disabled "
            f"(pip install ai-edge-litert pymicro-features)")
        return False, _unavailable_reason
    for f in (MODEL_FILE, CONFIG_FILE):
        if not (MODEL_DIR / f).is_file():
            _unavailable_reason = f"{MODEL_DIR / f} missing — microWakeWord scoring disabled"
            return False, _unavailable_reason
    try:
        _load_config()
    except (OSError, ValueError, KeyError) as exc:
        _unavailable_reason = f"{MODEL_DIR / CONFIG_FILE} unreadable ({exc}) — microWakeWord scoring disabled"
        return False, _unavailable_reason
    return True, ""


class Scorer:
    """
    One device's microWakeWord stream. Not thread-safe; feed from one task.

    feed() takes the SAME int16 chunk the openWakeWord path scores, so the two
    engines see identical audio including the `device.speaking` skip. A gap in
    the stream is a gap in both, which keeps the comparison honest — and is
    why this is called from beside model.predict() and nowhere else.
    """

    def __init__(self):
        from ai_edge_litert.interpreter import Interpreter
        from pymicro_features import MicroFrontend

        cfg = _load_config()
        self.cutoff = float(cfg["probability_cutoff"])      # 0.9 for alexa v2
        self.window = int(cfg["sliding_window_size"])       # 5

        self._frontend_cls = MicroFrontend
        self._frontend = MicroFrontend()
        self._it = Interpreter(model_path=str(MODEL_DIR / MODEL_FILE))
        self._it.allocate_tensors()
        self._in = self._it.get_input_details()[0]
        self._out = self._it.get_output_details()[0]
        self._slices = int(self._in["shape"][1])            # 3 frames/invoke
        self._scale, self._zero = self._in["quantization"]

        # Frontend frames not yet consumed by an invoke. The frontend emits one
        # frame per 10ms and the model eats 3 at a time, so a chunk whose frame
        # count is not a multiple of 3 leaves a remainder that belongs to the
        # NEXT invoke — dropping it would shift every subsequent window and
        # quietly lower every score.
        self._pending: list = []
        # Window probabilities for the moving average, oldest first.
        self._recent: list[float] = []
        self._last = 0.0

    def reset(self) -> None:
        """
        Drop all streaming state.

        Called wherever the openWakeWord model is reset(), for the same reason
        em_oww_warmup exists: state carried across a turn boundary is scored
        against audio it does not belong to. mww needs no warm-up gate — unlike
        openwakeword it seeds its state with ZEROS, not with embeddings of
        random noise — but it must still not carry the previous turn's tail.
        """
        self._frontend = self._frontend_cls()
        self._it.reset_all_variables()
        self._pending.clear()
        self._recent.clear()
        self._last = 0.0

    def feed(self, samples: np.ndarray) -> float:
        """
        Score one chunk of int16 mono 16kHz audio; return the current window
        average (the value ESPHome compares against probability_cutoff).

        Returns the running average, not this chunk's peak: the average IS the
        detection statistic, and a caller that took a max over raw window
        probabilities would be applying a rule microWakeWord was never tuned
        for.
        """
        pcm = np.asarray(samples, dtype=np.int16).tobytes()
        i = 0
        while i < len(pcm):
            r = self._frontend.process_samples(pcm[i:])
            if r.samples_read == 0:
                break          # frontend wants more audio than this chunk has
            i += r.samples_read * 2
            if r.features:
                self._pending.append(r.features)

        while len(self._pending) >= self._slices:
            chunk = np.asarray(self._pending[:self._slices], dtype=np.float32)
            del self._pending[:self._slices]
            q = np.clip(chunk / self._scale + self._zero, -128, 127).astype(np.int8)
            self._it.set_tensor(self._in["index"], q.reshape(self._in["shape"]))
            self._it.invoke()
            p = float(self._it.get_tensor(self._out["index"])[0][0]) / 255.0
            self._recent.append(p)
            if len(self._recent) > self.window:
                self._recent.pop(0)
            # Only a FULL window is a detection statistic. A partial average
            # over 1-2 windows after a reset is high-variance and would read as
            # a confident score of almost nothing — the same shape of bug that
            # em_oww_warmup exists to prevent on the other engine.
            if len(self._recent) == self.window:
                self._last = sum(self._recent) / self.window
        return self._last

    @property
    def score(self) -> float:
        return self._last

    @property
    def detected(self) -> bool:
        """Would ESPHome have fired? Recorded, never acted on."""
        return self._last > self.cutoff


def build() -> "Scorer | None":
    """A Scorer, or None with the reason already logged once at startup."""
    try:
        return Scorer()
    except Exception as exc:                       # noqa: BLE001 — never fatal
        log.warning(f"microWakeWord scorer unavailable: {exc}")
        return None
