"""The Muse gadget link, and speech-to-speech calls placed over it.

A call takes the Echo's microphone and speaker away from Home Assistant for
its duration. The controller already owns both, so nothing of the Assist
pipeline is involved once a call starts: `em_musecall` decides a transcript
asks for Muse, the Home Assistant run is aborted, and the audio runs here.

Two connections to Muse, deliberately:

* the **service**, which holds the registered link, keeps the gadget present
  in the Muse app and refreshes the device token. It runs for as long as the
  controller does, and advertises no commands at all (`DenyAll`).
* a **call**, opened beside it for each conversation. The SDK's voice
  transport never registers, so it cannot reuse the service's link
  (`linux/README.md`, "Realtime voice"); it is a second connection built from
  the same pairing.

The pairing is single-holder. Refreshing the device token rotates the refresh
token, so this process must be the only thing using the state directory.

`musegadget` is imported inside the functions that need it. The controller
reads `em_musecall` on every turn; it must not pay for the SDK at startup.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Iterable

import em_musecall

log = logging.getLogger("echomuse.muselink")

# What a Muse call carries: 24 kHz mono S16_LE in 40 ms frames.
MUSE_FRAME_SAMPLES = 960
MUSE_FRAME_BYTES = MUSE_FRAME_SAMPLES * 2

# How long to wait for the voice transport before giving up on a call. The
# SDK's own client uses 35 s.
READY_TIMEOUT_S = 35.0

# The connection identity a voice call runs under. LinkSession leaves this
# unset by default, which leaves app_id out of the noise URL and gets the call
# refused with HTTP 400; the SDK's own voice-call command passes this value
# (cli.py:429), so a call made any other way needs it too.
VOICE_APP_ID = "hatch-web"

# Queued speaker audio, in frames. Muse can answer faster than the Echo plays,
# and a bounded queue is what stops a long answer growing without limit.
_PLAYBACK_QUEUE_FRAMES = 256


class EchoCallAudio:
    """The Echo's microphone and speaker, as a Muse call expects to see them.

    The SDK asks for four things: an async iterator of input frames, a
    synchronous ``write`` for playback, a ``flush`` that drops what has not
    been played, and ``close``. The Echo's side arrives as 80 ms frames of
    16 kHz mono and leaves as 48 kHz, so this is also where the rate
    conversion happens.

    80 ms of microphone is exactly two 40 ms Muse frames, so no input frame is
    ever split across a boundary.

    Playback leaves through :meth:`playback`, an async source for
    ``Device.stream_speaker_chunks``, rather than a callback: ``write`` is
    called from inside the SDK's receive loop, and a slow speaker must not
    stall the call. Pacing costs a live call nothing — ``em_pacing.lead_delay``
    returns 0 until a producer runs more than ``VOICE_LEAD_S`` ahead of
    realtime, which a conversation never does.
    """

    def __init__(
        self,
        mic_queue: asyncio.Queue,
        flush: Callable[[], None] | None = None,
    ) -> None:
        self._mic = mic_queue
        self._flush = flush
        self._up = em_musecall.mic_to_muse()
        self._down = em_musecall.muse_to_wire()
        self._playback: asyncio.Queue[bytes | None] = asyncio.Queue(
            maxsize=_PLAYBACK_QUEUE_FRAMES)
        self._pending = bytearray()
        self._closed = False
        # A call begins where a voice turn left off, and that turn ended
        # because the device ended its microphone stream at end of speech.
        # So the microphone starts CLOSED: the first thing a call needs is a
        # new stream, and until it arrives the frames going up are silence.
        # Starting "open" left frames() waiting on a queue nothing would ever
        # fill, so Muse heard nothing and said nothing (first run on real
        # hardware, 2026-11-17).
        self._mic_open = False
        self._needs_mic = asyncio.Event()
        self._needs_mic.set()
        self._silence = bytes(MUSE_FRAME_BYTES)
        self.dropped_frames = 0
        self.silent_frames = 0

    # ── Microphone ───────────────────────────────────────────────────────

    def mic_closed(self) -> None:
        """The device ended its turn stream; the listener has stopped speaking."""
        self._mic_open = False
        self._needs_mic.set()

    def mic_opened(self) -> None:
        """A fresh turn stream is running; real audio is coming again."""
        self._mic_open = True
        self._needs_mic.clear()

    async def wait_mic_needed(self) -> None:
        """Block until the device's turn stream has ended and wants arming."""
        await self._needs_mic.wait()

    async def frames(self) -> Iterable[bytes]:
        """Yield 40 ms frames of 24 kHz audio until the call ends.

        The Echo's microphone is not open for the whole call: the device ends
        a turn stream at end of speech, and the controller arms a new one for
        the next thing the listener says. Muse's endpointing needs the frames
        to keep coming across that gap, so a closed microphone yields silence
        at the same cadence — which is also what tells Muse the listener has
        finished and it may answer.

        Silence is sent only while the microphone is known closed, never on a
        timeout: a guess would splice silence into the middle of a word
        whenever a frame arrived late.

        Anything on the queue that is not PCM is a marker belonging to the
        voice turn. The sentinel that ends a turn stream is what closes the
        microphone here.
        """
        while not self._closed:
            while len(self._pending) >= MUSE_FRAME_BYTES:
                frame = bytes(self._pending[:MUSE_FRAME_BYTES])
                del self._pending[:MUSE_FRAME_BYTES]
                yield frame
                if self._closed:
                    return

            if self._mic_open:
                chunk = await self._mic.get()
            else:
                # Hold the 40 ms cadence while nobody is speaking. A real
                # frame arriving ends the wait early.
                try:
                    chunk = await asyncio.wait_for(
                        self._mic.get(), MUSE_FRAME_SAMPLES / em_musecall.MUSE_RATE)
                except asyncio.TimeoutError:
                    self.silent_frames += 1
                    yield self._silence
                    continue

            if isinstance(chunk, (bytes, bytearray)):
                self._pending += self._up.process(bytes(chunk))
            elif chunk is None:
                # End of speech. Everything already captured still goes up.
                self.mic_closed()

    # ── Speaker ──────────────────────────────────────────────────────────

    def write(self, pcm: bytes) -> None:
        """Queue Muse's audio for the Echo. Called by the SDK, never awaited."""
        if self._closed or not pcm:
            return
        try:
            self._playback.put_nowait(self._down.process(pcm))
        except asyncio.QueueFull:
            # The speaker is further behind than the queue allows. Dropping
            # the newest frame keeps playback continuous and bounded; the
            # alternative is unbounded delay between Muse speaking and the
            # Echo playing it.
            self.dropped_frames += 1

    def flush(self) -> None:
        """Drop queued playback. The SDK calls this when the listener barges in."""
        while True:
            try:
                self._playback.get_nowait()
            except asyncio.QueueEmpty:
                break
        if self._flush is not None:
            self._flush()

    def close(self) -> None:
        self._closed = True
        try:
            self._playback.put_nowait(None)
        except asyncio.QueueFull:
            pass

    async def playback(self):
        """Muse's audio at the wire rate, until the call closes.

        Shaped for ``Device.stream_speaker_chunks``, which owns the periods,
        the output chain and the closing EOS.
        """
        while True:
            chunk = await self._playback.get()
            if chunk is None:
                return
            yield chunk


class DenyAll:
    """An executor that answers nothing.

    The SDK's default executor dispatches ``system.run``, ``file.read`` and
    ``file.write`` (``executor.py``), which would let Muse run shell commands
    on the controller. A gadget whose only job is to talk needs none of them,
    so this refuses every command and advertises none: ``specs()`` returning
    empty means nothing is offered at registration either.

    It subclasses the SDK's ``Executor`` because the service reads
    ``executor.account`` when it chowns its local socket.
    """

    def __new__(cls, account=None):
        from musegadget.executor import Account, Executor

        class _DenyAll(Executor):
            def specs(self, **unused):
                return {}

            def run(self, command, params, timeout_ms=None):
                log.warning("refused a command from Muse: %s", command)
                return {"ok": False, "error": "this gadget runs no commands"}

        return _DenyAll(account or Account.current())


async def place_call(
    audio: EchoCallAudio,
    *,
    thread_id: str | None = None,
    idle_timeout: float = 45.0,
    overall_timeout: float = 300.0,
) -> str:
    """Hold one speech-to-speech call with Muse, and return why it ended.

    Opens its own connection: the SDK's voice transport never registers, so
    it cannot share the service's link (``linux/README.md``). Both are built
    from the same pairing.

    Muse allows one call at a time, and a new call replaces whatever was
    running — including one on the phone.
    """
    from musegadget import __version__, config, muse_api
    from musegadget.identity import load_or_create
    from musegadget.link_client import DeviceDescription, LinkSession
    from musegadget.voice_call import VoiceCall

    # websockets logs the Authorization header at DEBUG. A call is
    # credentialed, so that must never be on, whatever the controller's level.
    logging.getLogger("websockets").setLevel(logging.WARNING)

    pairing = config.load_json(config.PAIRING_FILE)
    if not pairing:
        raise RuntimeError("no Muse pairing; run the pairing script first")
    identity = load_or_create()

    vms, status = await asyncio.to_thread(
        muse_api.fetch_vms_with_status, pairing["access_token"],
        muse_api.api_root(pairing.get("api_url_v2", "")))
    if status != 200 or not vms:
        raise ConnectionError(f"Muse VM lookup failed (HTTP {status})")
    vm = next((v for v in vms if v["is_default"]), vms[0])

    session = LinkSession(
        noise_host=pairing.get("noise_host") or "",
        vm_id=vm["vm_id"], vm_auth_token=vm["vm_auth_token"],
        app_id=VOICE_APP_ID, register_device=False,
        device=DeviceDescription(identity.node_id, "EchoMuse voice",
                                 __version__, {}),
        run_command=lambda *unused: {"ok": False, "error": "unsupported command"},
    )
    stop = asyncio.Event()
    connection = asyncio.ensure_future(session.run(stop))
    try:
        await session.wait_transport_ready(READY_TIMEOUT_S)
        call = VoiceCall(
            session, device_id=identity.node_id, thread_id=thread_id,
            half_duplex=True, idle_timeout=idle_timeout,
            overall_timeout=overall_timeout)
        await call.run(audio.frames(), audio)
        return call.reason or "ended"
    finally:
        # Closing ends audio.playback(), which ends the caller's speaker
        # stream and sends the device its EOS.
        audio.close()
        stop.set()
        if not connection.done():
            connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)


async def rearm_microphone(audio: EchoCallAudio,
                           arm: Callable[[], Awaitable[None]]) -> None:
    """Open a new turn stream each time the device ends one.

    The device ends its stream at end of speech, and after a few seconds of
    nobody speaking at all. Neither means the call is over, so each one is
    answered with a fresh stream; the call's own idle timer, or the listener
    saying goodbye, is what ends it.

    Run as a task for the length of a call and cancel it afterwards.
    """
    while True:
        await audio.wait_mic_needed()
        await arm()
        audio.mic_opened()


def available() -> str | None:
    """Why Muse cannot be called, or None if it can.

    The SDK is not a controller dependency and the pairing is made by hand on
    the machine that holds it, so most installations have neither. A turn is
    only ever offered to Muse when both are present; without them "call Muse"
    stays an ordinary Home Assistant turn rather than failing in front of the
    listener.
    """
    try:
        from musegadget import config
    except ImportError:
        return "the Muse Gadget SDK is not installed"
    try:
        if not config.load_json(config.PAIRING_FILE):
            return f"no Muse pairing in {config.state_dir()}"
    except Exception as exc:                      # unreadable state directory
        return f"the Muse pairing could not be read: {exc}"
    return None
