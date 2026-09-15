#!/usr/bin/env python3
"""
Replay saved wake-clip recordings through the same openWakeWord model the
live controller uses, to compare offline scores against what was recorded
at detection time (turns.wake_score).

Matches em_controller.py's live scoring path exactly:
  - OWWModel(wakeword_models=[owwModel]) from openwakeword.model.Model
  - CHUNK_BYTES = 2560 (1280 int16 samples, 80ms @ 16kHz mono)
  - prediction dict keyed by em_oww_models.prediction_key(owwModel)

Caveat: each file is scored from a freshly reset model (model.reset()),
so it starts "cold" — no audio history from before the clip. Live scoring
had continuous context leading into the same window. openWakeWord's own
window is short enough that this is usually a close match, not an exact
one — treat a small delta as expected, not a bug.

Usage:
    python3 tools/rescore_recordings.py [recordings_dir] [--model NAME_OR_PATH]

    # Stock model (default):
    python3 tools/rescore_recordings.py

    # A retrained model, to check it now scores these clips low:
    python3 tools/rescore_recordings.py --model /path/to/hey_jarvis_v2.onnx
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np
from openwakeword.model import Model as OWWModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import em_oww_models  # noqa: E402

CHUNK_BYTES = 1280 * 2
DEFAULT_OWW_MODEL = "hey_jarvis_v0.1"


def score_file(model: OWWModel, model_key: str, path: Path) -> float:
    model.reset()
    peak = 0.0
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000, f"{path}: unexpected sample rate {w.getframerate()}"
        assert w.getsampwidth() == 2, f"{path}: unexpected sample width {w.getsampwidth()}"
        pcm = w.readframes(w.getnframes())
    buf = bytearray(pcm)
    while len(buf) >= CHUNK_BYTES:
        chunk = bytes(buf[:CHUNK_BYTES])
        del buf[:CHUNK_BYTES]
        samples = np.frombuffer(chunk, dtype=np.int16)
        prediction = model.predict(samples)
        peak = max(peak, prediction.get(model_key, 0.0))
    return peak


def main() -> None:
    default_dir = Path(__file__).resolve().parent.parent / "data" / "recordings"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings_dir", nargs="?", type=Path, default=default_dir)
    parser.add_argument(
        "--model", default=DEFAULT_OWW_MODEL,
        help="Stock name (e.g. hey_jarvis_v0.1) or path to a custom .onnx — "
             "same values owwModel accepts in the dashboard.",
    )
    args = parser.parse_args()

    files = sorted(args.recordings_dir.glob("*.wav"))
    if not files:
        print(f"No .wav files in {args.recordings_dir}")
        return

    model_key = em_oww_models.prediction_key(args.model)
    model = OWWModel(wakeword_models=[args.model])

    print(f"model: {args.model}")
    print(f"{'file':<32} {'offline peak':>12}")
    for f in files:
        peak = score_file(model, model_key, f)
        print(f"{f.name:<32} {peak:>12.4f}")


if __name__ == "__main__":
    main()
