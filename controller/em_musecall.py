"""Handing a turn over from Home Assistant to a direct Muse voice call.

A normal turn runs Alexa → Home Assistant → Gemini and the controller never
looks at what was said. When the transcript is one of the handover phrases
("call Muse"), the controller cancels the Home Assistant run and opens a
speech-to-speech call to Muse instead, holding the Echo's microphone and
speaker for the length of the call.

This module holds the parts that need neither Muse nor a device: deciding
whether a transcript is a handover or a hang-up, and the rate conversion
between the three sample rates involved.

    Echo mic     16 kHz   mono S16_LE   (0x01 frames, 1280 samples each)
    Muse call    24 kHz   mono S16_LE   (960 samples a frame, 40 ms)
    Echo speaker 48 kHz   mono S16_LE   (0x02 frames)

Both conversions are whole ratios (3/2 up, then 2/1 up), so a block whose
length divides the ratio produces an exact output length and the call never
drifts against the clock.
"""

from __future__ import annotations

import re

import numpy as np

MIC_RATE = 16_000
MUSE_RATE = 24_000
WIRE_RATE = 48_000

# What a listener says to reach Muse. Both names are the same Muse and the
# same conversation; "Jarvis" is a second name for it, nothing more.
HANDOVER_PHRASES = frozenset({
    "call muse", "call jarvis",
    "talk to muse", "talk to jarvis",
})

# Speech-to-text returns an ordinary sentence: capitals, a trailing full stop,
# sometimes a comma. Strip to bare words before matching so "Call Muse." and
# "call muse" are one phrase.
_NOT_WORDS = re.compile(r"[^a-z0-9]+")


def normalise(text: str | None) -> str:
    """A transcript reduced to lower-case words separated by single spaces."""
    if not text:
        return ""
    return _NOT_WORDS.sub(" ", text.lower()).strip()


def is_handover(text: str | None) -> bool:
    """Whether this transcript asks for Muse.

    The whole utterance must be the phrase. Matching a substring would hand
    the turn over on "ask Gemini what a call muse is", and a wrongly stolen
    turn is worse than a handover that needs saying again.
    """
    return normalise(text) in HANDOVER_PHRASES


# Nothing here ends a call. The voice SDK reports only the words MUSE speaks
# (voice_call.TranscriptDelta: "More of the words Muse speaks"), never the
# listener's, so the controller cannot match a spoken "goodbye" even in
# principle. Muse ends the call itself when asked -- verified against the live
# service on 2026-11-16: "Goodbye Muse. Please end this call now." returned
# reason "remote_stop" rather than running out the idle timer. The other ways
# out are the call's own idle timeout and the Echo's mute button, which ends
# the microphone stream on the device.


class Resampler:
    """Streaming rate conversion by a whole ratio, continuous across blocks.

    Resampling each block on its own leaves a click at every boundary, because
    the filter restarts with zeros 25 times a second. This keeps the tail of
    the previous block as the filter's history (overlap-save) and discards the
    outputs that belong to it, so a stream converted block by block is
    sample-for-sample what converting the whole stream would have given.

    ``block_multiple`` is the input length that yields a whole number of
    output samples; feed blocks of that size (or a multiple) and the output
    length is exact.
    """

    # Half-length of the anti-imaging filter, in output-rate samples. 32 gives
    # about 80 dB of stopband rejection, which is past what a 48 kHz mono Echo
    # speaker can show, at a few hundred microseconds of work per frame.
    _HALF = 32

    def __init__(self, up: int, down: int) -> None:
        # scipy is imported here, not at module level: the controller reads
        # this module's phrase matching on every turn, and scipy costs ~90 MB
        # and a couple of seconds at startup for something only a call uses
        # (tests/test_lazy_heavy_imports.py).
        from scipy.signal import firwin, upfirdn

        self._upfirdn = upfirdn
        if up < 1 or down < 1:
            raise ValueError("up and down must be positive")
        common = np.gcd(up, down)
        self.up, self.down = up // common, down // common
        self.block_multiple = self.down

        # Odd length, so the filter is linear phase and (length - 1) is even;
        # the history that length implies then converts to a whole number of
        # output samples for any ratio whose denominator is 1 or 2.
        taps = 2 * self._HALF * max(self.up, self.down) + 1
        # Cut off below both Nyquist limits, and scale by `up` because
        # inserting zeros between samples divides amplitude by that much.
        cutoff = 1.0 / max(self.up, self.down)
        self._h = firwin(taps, cutoff).astype(np.float64) * self.up
        self._history = np.zeros(taps - 1, dtype=np.float64)
        self._lead = (taps - 1) * self.up // self.down
        # A linear-phase filter delays what it passes by half its length. The
        # output is that much later than the input, constantly, which is
        # latency rather than distortion: about 2 ms for either conversion.
        # Correcting it would need samples that have not arrived yet.
        self.delay_samples = (taps - 1) // 2 // self.down

    def reset(self) -> None:
        """Forget the previous stream. Call between calls, never within one."""
        self._history[:] = 0.0

    def process(self, pcm: bytes) -> bytes:
        """One block of mono S16_LE in, the converted block out."""
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        if samples.size == 0:
            return b""
        if self.up == self.down == 1:
            return pcm

        padded = np.concatenate((self._history, samples))
        full = self._upfirdn(self._h, padded, self.up, self.down)
        # Keep only the outputs that belong to this block's input. Whatever is
        # left of the input stays as the next block's history.
        wanted = -(-samples.size * self.up // self.down)
        out = full[self._lead:self._lead + wanted]
        self._history = padded[-self._history.size:]

        clipped = np.clip(np.rint(out), -32768, 32767)
        return clipped.astype("<i2").tobytes()


def mic_to_muse() -> Resampler:
    """16 kHz Echo microphone to the 24 kHz a Muse call expects."""
    return Resampler(up=3, down=2)


def muse_to_wire() -> Resampler:
    """24 kHz from Muse to the 48 kHz the speaker plane runs at."""
    return Resampler(up=2, down=1)
