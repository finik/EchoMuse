"""Handover phrases and the call's rate conversion."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import resample_poly

import em_musecall


class TestPhrases:
    @pytest.mark.parametrize("said", [
        "call muse", "Call Muse", "Call Muse.", "  call   muse  ",
        "call jarvis", "Call Jarvis!", "talk to muse", "Talk to Jarvis",
    ])
    def test_handover_phrases(self, said):
        assert em_musecall.is_handover(said)

    @pytest.mark.parametrize("said", [
        # A substring match would steal the turn on every one of these.
        "ask gemini what a call muse is",
        "call muse and then turn on the lights",
        "what did muse say",
        "call mum",
        "turn off the kitchen light",
        "", "   ", None,
    ])
    def test_leaves_ordinary_turns_alone(self, said):
        assert not em_musecall.is_handover(said)



def _tone(samples: int, hz: float, rate: int) -> np.ndarray:
    t = np.arange(samples) / rate
    return (np.sin(2 * np.pi * hz * t) * 12000).astype("<i2")


class TestResampler:
    @pytest.mark.parametrize("factory, rate_in, rate_out", [
        (em_musecall.mic_to_muse, em_musecall.MIC_RATE, em_musecall.MUSE_RATE),
        (em_musecall.muse_to_wire, em_musecall.MUSE_RATE, em_musecall.WIRE_RATE),
    ])
    def test_length_is_exact_for_a_whole_block(self, factory, rate_in, rate_out):
        r = factory()
        # 80 ms of mic, or 40 ms of Muse: the real frame sizes.
        block = _tone(rate_in // 25 * 2, 440, rate_in)
        out = r.process(block.tobytes())
        expected = block.size * r.up // r.down
        assert len(out) // 2 == expected

    @pytest.mark.parametrize("factory, rate_in", [
        (em_musecall.mic_to_muse, em_musecall.MIC_RATE),
        (em_musecall.muse_to_wire, em_musecall.MUSE_RATE),
    ])
    def test_blocks_give_exactly_what_one_call_gives(self, factory, rate_in):
        """The property that means no click at a frame boundary: converting a
        stream block by block is sample-for-sample converting it whole."""
        signal = _tone(rate_in, 440, rate_in)
        block = rate_in // 25

        one_shot = factory().process(signal.tobytes())
        streaming = factory()
        streamed = b"".join(
            streaming.process(signal[i:i + block].tobytes())
            for i in range(0, signal.size, block)
        )
        assert streamed == one_shot

    def test_a_forgetful_resampler_would_fail_that(self):
        """The continuity test is only worth having if restarting the filter
        every block would break it. A fresh Resampler per block does that."""
        rate_in = em_musecall.MIC_RATE
        signal = _tone(rate_in // 2, 440, rate_in)
        block = rate_in // 25

        one_shot = em_musecall.mic_to_muse().process(signal.tobytes())
        forgetful = b"".join(
            em_musecall.mic_to_muse().process(signal[i:i + block].tobytes())
            for i in range(0, signal.size, block)
        )
        a = np.frombuffer(one_shot, dtype="<i2").astype(np.float64)
        b = np.frombuffer(forgetful, dtype="<i2").astype(np.float64)
        n = min(a.size, b.size)
        assert np.abs(a[:n] - b[:n]).max() > 100

    @pytest.mark.parametrize("factory, rate_in, rate_out", [
        (em_musecall.mic_to_muse, em_musecall.MIC_RATE, em_musecall.MUSE_RATE),
        (em_musecall.muse_to_wire, em_musecall.MUSE_RATE, em_musecall.WIRE_RATE),
    ])
    def test_it_is_a_real_resample(self, factory, rate_in, rate_out):
        """Against scipy's own resampler, once the filter's known delay is
        taken off. Continuity alone would also pass for something that merely
        smoothed the signal; this says the samples are right."""
        r = factory()
        signal = _tone(rate_in, 440, rate_in)
        got = np.frombuffer(r.process(signal.tobytes()), dtype="<i2").astype(np.float64)
        reference = resample_poly(signal.astype(np.float64), r.up, r.down)

        d = r.delay_samples
        lo, hi = rate_out // 4, rate_out // 2          # a settled middle window
        error = np.abs(got[lo:hi] - reference[lo - d:hi - d]).max()
        assert error < 0.01 * 12000, f"worst sample differs by {error}"

    @pytest.mark.parametrize("factory, rate_in, rate_out", [
        (em_musecall.mic_to_muse, em_musecall.MIC_RATE, em_musecall.MUSE_RATE),
        (em_musecall.muse_to_wire, em_musecall.MUSE_RATE, em_musecall.WIRE_RATE),
    ])
    def test_a_tone_keeps_its_pitch(self, factory, rate_in, rate_out):
        got = np.frombuffer(
            factory().process(_tone(rate_in, 440, rate_in).tobytes()),
            dtype="<i2").astype(np.float64)
        spectrum = np.abs(np.fft.rfft(got * np.hanning(got.size)))
        peak = np.fft.rfftfreq(got.size, 1 / rate_out)[spectrum.argmax()]
        assert abs(peak - 440) < 1.0

    def test_silence_stays_silent(self):
        r = em_musecall.mic_to_muse()
        out = r.process(np.zeros(1280, dtype="<i2").tobytes())
        assert set(np.frombuffer(out, dtype="<i2")) == {0}

    def test_empty_block(self):
        assert em_musecall.mic_to_muse().process(b"") == b""

    def test_reset_clears_history(self):
        r = em_musecall.mic_to_muse()
        loud = _tone(1280, 440, em_musecall.MIC_RATE)
        r.process(loud.tobytes())
        r.reset()
        after = np.frombuffer(
            r.process(np.zeros(1280, dtype="<i2").tobytes()), dtype="<i2")
        assert set(after) == {0}, "history from the last call bled into this one"

    def test_rejects_a_bad_ratio(self):
        with pytest.raises(ValueError):
            em_musecall.Resampler(up=0, down=1)


def test_phrase_matching_does_not_drag_in_scipy():
    """em_esphome consults this module on every turn, so importing it must
    stay cheap. scipy belongs to the call, not to the phrase check
    (tests/test_lazy_heavy_imports.py covers the controller as a whole)."""
    import subprocess
    import sys
    from pathlib import Path

    controller = Path(__file__).resolve().parents[1]
    probe = (
        f"import sys; sys.path.insert(0, {str(controller)!r});"
        "import em_musecall;"
        "assert em_musecall.is_handover('call muse');"
        "print(','.join(sorted({m.split('.')[0] for m in sys.modules}"
        " & {'scipy', 'sklearn', 'openwakeword'})))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, cwd=controller, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "", f"imported at module level: {out.stdout.strip()}"


class TestTruncatedAndPrefixed:
    """Speech-to-text does not hand over a clean phrase. "call Jarvis" came
    back as "Call." on 2026-11-17 — the handover missed, and the fragment
    reached the conversation agent, which said "Unable to get response" out
    loud and woke the Echo next to it."""

    @pytest.mark.parametrize("said", [
        "Call.", "call", "Alexa call", "Alexa, call Jarvis.",
        "alexa call muse", "Alexa talk to Muse",
    ])
    def test_reaches_muse(self, said):
        assert em_musecall.is_handover(said, "alexa")

    @pytest.mark.parametrize("said", [
        "call mum", "call the office", "recall", "called",
        "Alexa turn on the lights", "",
    ])
    def test_is_left_to_home_assistant(self, said):
        assert not em_musecall.is_handover(said, "alexa")

    def test_the_wake_word_goes_only_once(self):
        """"Alexa, call Alexa" keeps the second one, so it is not a handover."""
        assert not em_musecall.is_handover("Alexa call Alexa", "alexa")
