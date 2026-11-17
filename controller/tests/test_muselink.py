"""The Echo side of a Muse call: microphone in, speaker out."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

import em_muselink
from em_musecall import MIC_RATE, MUSE_RATE, WIRE_RATE


def mic_frame(ms: int = 80, hz: float = 440.0) -> bytes:
    n = MIC_RATE * ms // 1000
    t = np.arange(n) / MIC_RATE
    return (np.sin(2 * np.pi * hz * t) * 9000).astype("<i2").tobytes()


def muse_frame(frames: int = 1) -> bytes:
    n = em_muselink.MUSE_FRAME_SAMPLES * frames
    t = np.arange(n) / MUSE_RATE
    return (np.sin(2 * np.pi * 330 * t) * 9000).astype("<i2").tobytes()


class Speaker:
    """Stands in for Device.stream_speaker_chunks."""

    def __init__(self):
        self.written = bytearray()
        self.flushes = 0

    async def drain(self, chunks):
        async for chunk in chunks:
            self.written += chunk

    def flush(self) -> None:
        self.flushes += 1


def make(queue=None):
    speaker = Speaker()
    audio = em_muselink.EchoCallAudio(
        queue if queue is not None else asyncio.Queue(), speaker.flush)
    return audio, speaker


async def take(audio, count):
    """The first `count` frames, then end the call."""
    got = []
    async for frame in audio.frames():
        got.append(frame)
        if len(got) == count:
            audio.close()
            break
    return got


class TestMicrophone:
    def test_eighty_ms_of_mic_is_exactly_two_muse_frames(self):
        """40 ms at 24 kHz divides 80 ms at 16 kHz exactly, so no input frame
        is ever split across a frame boundary."""
        async def scenario():
            q = asyncio.Queue()
            audio, _ = make(q)
            q.put_nowait(mic_frame())
            return await take(audio, 2)

        got = asyncio.run(scenario())
        assert [len(f) for f in got] == [em_muselink.MUSE_FRAME_BYTES] * 2

    def test_markers_that_are_not_end_of_speech_are_skipped(self):
        """voice_queue carries other markers too. Only the end-of-speech
        sentinels mean the listener stopped."""
        async def scenario():
            q = asyncio.Queue()
            audio, _ = make(q)
            audio.mic_opened()
            q.put_nowait(None)
            q.put_nowait("listen_mode")
            q.put_nowait(mic_frame())
            got = await take(audio, 2)
            return got, audio._mic_open

        got, still_open = asyncio.run(scenario())
        assert len(got) == 2
        assert still_open, "an unrelated marker closed the microphone"

    @pytest.mark.parametrize("sentinel", ["vad_end", "vad_no_speech_timeout"])
    def test_end_of_speech_closes_the_microphone(self, sentinel):
        """Matching None instead of these is what made Muse wait ~25s for a
        server-side timeout before answering: end of speech never registered,
        so no silence went up and its endpointing had nothing to finish on."""
        async def scenario():
            q = asyncio.Queue()
            audio, _ = make(q)
            audio.mic_opened()
            q.put_nowait(mic_frame())
            q.put_nowait(sentinel)
            got = []
            async for frame in audio.frames():
                got.append(frame)
                if len(got) == 4:
                    audio.close()
                    break
            return got, audio.silent_frames

        got, silent = asyncio.run(scenario())
        assert silent >= 2, "no silence went up, so Muse cannot know to answer"
        assert got[2:] == [bytes(em_muselink.MUSE_FRAME_BYTES)] * 2

    def test_the_sentinels_are_the_ones_the_turn_actually_sends(self):
        """Repeated rather than imported, because em_esphome imports this
        module. Pinned here so a rename cannot silently stop ending speech."""
        import _oww_stub
        _oww_stub.install()
        try:
            import em_esphome
        finally:
            _oww_stub.uninstall()
        assert em_muselink.END_OF_SPEECH == {
            em_esphome.VAD_SENTINEL_END, em_esphome.VAD_SENTINEL_TIMEOUT}

    def test_the_mic_is_resampled_to_the_call_rate(self):
        """One continuous tone, cut into 80 ms frames the way the device sends
        them, arrives at Muse as the same tone at 24 kHz."""
        seconds = 2
        t = np.arange(MIC_RATE * seconds) / MIC_RATE
        tone = (np.sin(2 * np.pi * 440 * t) * 9000).astype("<i2")
        step = MIC_RATE * 80 // 1000

        async def scenario():
            q = asyncio.Queue()
            audio, _ = make(q)
            for i in range(0, tone.size, step):
                q.put_nowait(tone[i:i + step].tobytes())
            return b"".join(await take(audio, seconds * 25 - 2))

        pcm = np.frombuffer(asyncio.run(scenario()), dtype="<i2").astype(float)
        spectrum = np.abs(np.fft.rfft(pcm * np.hanning(pcm.size)))
        freqs = np.fft.rfftfreq(pcm.size, 1 / MUSE_RATE)
        peak = freqs[spectrum.argmax()]
        assert abs(peak - 440) < 2 * (freqs[1] - freqs[0]), f"peak at {peak} Hz"


class TestSpeaker:
    def test_muse_audio_reaches_the_speaker_at_the_wire_rate(self):
        async def scenario():
            audio, speaker = make()
            drain = asyncio.ensure_future(speaker.drain(audio.playback()))
            audio.write(muse_frame(frames=25))   # one second at 24 kHz
            await asyncio.sleep(0)
            audio.close()
            await asyncio.wait_for(drain, 2)
            return speaker.written

        out = np.frombuffer(bytes(asyncio.run(scenario())), dtype="<i2")
        assert out.size == pytest.approx(WIRE_RATE, rel=0.01), (
            "24 kHz in should come out as twice as many samples at 48 kHz")

    def test_flush_drops_what_has_not_played(self):
        """A barge-in must not be followed by the rest of the old answer."""
        async def scenario():
            audio, speaker = make()
            audio.write(muse_frame(frames=10))
            audio.flush()
            drain = asyncio.ensure_future(speaker.drain(audio.playback()))
            await asyncio.sleep(0)
            audio.close()
            await asyncio.wait_for(drain, 2)
            return speaker

        speaker = asyncio.run(scenario())
        assert speaker.written == b""
        assert speaker.flushes == 1

    def test_a_slow_speaker_bounds_the_backlog(self):
        """Muse can answer faster than the Echo plays. The queue is capped, so
        a long answer drops frames rather than growing without limit."""
        async def scenario():
            audio, _ = make()
            for _ in range(em_muselink._PLAYBACK_QUEUE_FRAMES + 40):
                audio.write(muse_frame())
            return audio.dropped_frames

        assert asyncio.run(scenario()) == 40

    def test_write_after_close_is_ignored(self):
        async def scenario():
            audio, speaker = make()
            audio.close()
            audio.write(muse_frame())
            await asyncio.wait_for(
                asyncio.ensure_future(speaker.drain(audio.playback())), 2)
            return speaker.written

        assert asyncio.run(scenario()) == b""

    def test_empty_write(self):
        async def scenario():
            audio, _ = make()
            audio.write(b"")
            return audio._playback.qsize()

        assert asyncio.run(scenario()) == 0


class TestMicGaps:
    """Between utterances the device closes its turn stream. Muse still needs
    frames at the call's cadence, and must not get silence mid-word."""

    def test_silence_only_once_the_mic_is_known_closed(self):
        async def scenario():
            q = asyncio.Queue()
            audio, _ = make(q)
            q.put_nowait(mic_frame())       # 80 ms of speech = 2 frames
            q.put_nowait(None)              # end of speech
            got = []
            async for frame in audio.frames():
                got.append(frame)
                if len(got) == 5:
                    audio.close()
                    break
            return got, audio.silent_frames

        got, silent = asyncio.run(scenario())
        assert len(got) == 5
        # The two speech frames come first and are untouched.
        assert any(f != bytes(em_muselink.MUSE_FRAME_BYTES) for f in got[:2])
        # Only after the sentinel does silence start.
        assert got[2:] == [bytes(em_muselink.MUSE_FRAME_BYTES)] * 3
        assert silent == 3

    def test_a_late_frame_is_not_spliced_with_silence(self):
        """While the mic is open, frames() waits however long the device
        takes. Filling on a timeout would corrupt speech."""
        async def scenario():
            q = asyncio.Queue()
            audio, _ = make(q)
            audio.mic_opened()              # a stream is running

            async def late():
                await asyncio.sleep(0.25)   # six frame periods late
                q.put_nowait(mic_frame())

            asyncio.ensure_future(late())
            got = []
            async for frame in audio.frames():
                got.append(frame)
                if len(got) == 2:
                    audio.close()
                    break
            return got, audio.silent_frames

        got, silent = asyncio.run(scenario())
        assert silent == 0, "silence was spliced into an open microphone"
        assert all(f != bytes(em_muselink.MUSE_FRAME_BYTES) for f in got)

    def test_the_mic_reopening_stops_the_silence(self):
        async def scenario():
            q = asyncio.Queue()
            audio, _ = make(q)
            q.put_nowait(None)              # closed from the start
            got = []
            async for frame in audio.frames():
                got.append(frame)
                if len(got) == 2:           # two silent frames
                    audio.mic_opened()
                    q.put_nowait(mic_frame())
                if len(got) == 4:
                    audio.close()
                    break
            return got

        got = asyncio.run(scenario())
        assert got[:2] == [bytes(em_muselink.MUSE_FRAME_BYTES)] * 2
        assert all(f != bytes(em_muselink.MUSE_FRAME_BYTES) for f in got[2:])


class TestRearm:
    def test_each_ended_stream_is_answered_with_a_new_one(self):
        """End of speech is not the end of a call. Every stream the device
        closes gets a fresh one until the call itself ends."""
        async def scenario():
            q = asyncio.Queue()
            audio, _ = make(q)
            armed = []

            async def arm():
                armed.append(len(armed) + 1)
                q.put_nowait(mic_frame())

            task = asyncio.ensure_future(em_muselink.rearm_microphone(audio, arm, settle=0))
            for _ in range(3):
                audio.mic_closed()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            task.cancel()
            return armed

        assert len(asyncio.run(scenario())) == 3

    def test_a_call_arms_the_microphone_before_anything_is_said(self):
        """A call begins where a turn ended, and that turn ended because the
        device closed its microphone at end of speech. If the adapter assumed
        the microphone was still open it would wait on a queue nothing fills,
        Muse would hear nothing, and the call would hang with the ring lit —
        which is what the first run on real hardware did."""
        async def scenario():
            audio, _ = make()               # empty queue, as a real call starts
            armed = []

            async def arm():
                armed.append(1)

            task = asyncio.ensure_future(em_muselink.rearm_microphone(audio, arm, settle=0))
            await asyncio.sleep(0.05)
            task.cancel()
            return armed

        assert asyncio.run(scenario()) == [1], "the call would never hear anything"

    def test_an_open_mic_arms_nothing_more(self):
        async def scenario():
            audio, _ = make()
            armed = []

            async def arm():
                armed.append(1)

            task = asyncio.ensure_future(em_muselink.rearm_microphone(audio, arm, settle=0))
            await asyncio.sleep(0.02)
            audio.mic_opened()
            await asyncio.sleep(0.05)
            task.cancel()
            return armed

        assert asyncio.run(scenario()) == [1], "armed again while the mic was open"

    def test_silence_goes_up_until_the_first_stream_arrives(self):
        """Muse needs frames from the moment the call opens, not from whenever
        the device gets round to sending audio."""
        async def scenario():
            audio, _ = make()
            got = []
            async for frame in audio.frames():
                got.append(frame)
                if len(got) == 3:
                    audio.close()
                    break
            return got

        got = asyncio.run(scenario())
        assert got == [bytes(em_muselink.MUSE_FRAME_BYTES)] * 3


class TestGadget:
    """The SDK is not a controller dependency — an installation without it
    must still run every ordinary turn — so these skip when it is absent."""

    def test_muse_is_offered_only_with_the_sdk_and_a_pairing(self, monkeypatch, tmp_path):
        pytest.importorskip("musegadget")
        monkeypatch.setenv("MUSEGADGET_STATE_DIR", str(tmp_path))
        assert "no Muse pairing" in em_muselink.available()

    def test_a_broken_state_directory_is_a_reason_not_a_crash(self, monkeypatch, tmp_path):
        pytest.importorskip("musegadget")
        (tmp_path / "pairing.json").write_text("{not json")
        monkeypatch.setenv("MUSEGADGET_STATE_DIR", str(tmp_path))
        reason = em_muselink.available()
        assert reason and "pairing" in reason

    def test_muse_is_offered_nothing_to_run(self):
        """The SDK's stock executor runs shell commands and reads files for
        Muse. This gadget only talks, so it answers nothing and advertises
        nothing — an empty specs() means none is offered at registration."""
        pytest.importorskip("musegadget")
        deny = em_muselink.DenyAll()
        assert deny.specs() == {}
        for command in ("system.run", "file.read", "file.write", "device.health"):
            answer = deny.run(command, {})
            assert answer["ok"] is False, f"{command} was not refused"


class TestHalfDuplex:
    """Muse answers only when it hears silence, and cancels an answer it is
    already giving if the room keeps arriving. Both were seen on hardware:
    re-arming after 0 and 1 silent frames, each answer 'cancelled'."""

    def test_silence_runs_before_the_microphone_reopens(self):
        async def scenario():
            q = asyncio.Queue()
            audio, _ = make(q)
            audio.mic_opened()
            armed = []

            async def arm():
                armed.append(audio.silent_frames)

            task = asyncio.ensure_future(
                em_muselink.rearm_microphone(audio, arm, settle=0.3))
            pump = asyncio.ensure_future(take(audio, 100))
            audio.mic_closed()
            await asyncio.sleep(0.45)
            task.cancel(); pump.cancel()
            await asyncio.gather(task, pump, return_exceptions=True)
            return armed

        armed = asyncio.run(scenario())
        assert armed, "the microphone never came back"
        assert armed[0] >= 5, (
            f"only {armed[0]} frames of silence before listening again; "
            "Muse cannot tell the listener has finished")

    def test_the_microphone_stays_shut_while_muse_talks(self):
        async def scenario():
            audio, _ = make()
            armed = []

            async def arm():
                armed.append(1)

            audio.muse_started()
            task = asyncio.ensure_future(
                em_muselink.rearm_microphone(audio, arm, settle=0))
            await asyncio.sleep(0.1)
            during = len(armed)
            audio.muse_finished()
            await asyncio.sleep(0.15)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return during, len(armed)

        during, after = asyncio.run(scenario())
        assert during == 0, "listened while Muse was talking — it cancels its own answer"
        assert after == 1, "the microphone never came back after Muse finished"
