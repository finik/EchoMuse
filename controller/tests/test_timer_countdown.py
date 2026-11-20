"""
The running-timer arc: it paints, it survives a dropped frame, and it says so.

`countdown_leds` had tests; the task that calls it had none, and that task is
where a timer can silently stop showing. A frame that fails to send used to
end the arc for the rest of the timer, with no ring and nothing in the log —
indistinguishable from HA never having sent the timer event at all
(controller/CLAUDE.md: "No on_timer_event log line = HA never sent the
event").

Driven through the real `sync_timer_countdown` / `_paint_timer_countdown`
against a registry holding a real timer.
"""

import asyncio

import _oww_stub

_oww_stub.install()
try:
    import em_controller
finally:
    _oww_stub.uninstall()

import em_timers  # noqa: E402


class FakeServer:
    def __init__(self) -> None:
        self.registry = em_timers.TimerRegistry()

    @property
    def timer_ringing(self) -> bool:
        return self.registry.ringing

    def timer_countdown(self, now=None):
        return self.registry.soonest(now)


class FakeDevice:
    def __init__(self, fail_frames: int = 0) -> None:
        self.device_id = "G070RQ00000000AA"
        self.voice_lock = asyncio.Lock()
        self.oww_paused = asyncio.Event()
        self.speaker_busy = 0
        self.timer_alarm_task = None
        self.timer_countdown_task = None
        self.frames: list = []
        self._fail = fail_frames
        self.led_anim_capable = False
        self.anims: list = []

    async def send_led_anim(self, anim):
        self.anims.append(anim)

    async def set_leds(self, leds, listening=None):
        if self._fail > 0:
            self._fail -= 1
            raise ConnectionResetError("control plane went away")
        self.frames.append(leds)


def arm(monkeypatched_server: FakeServer) -> None:
    em_controller.esphome.get_server = lambda _id: monkeypatched_server


async def quiesce() -> None:
    """Let the painter's first pass run without waiting out its 1s tick."""
    for _ in range(20):
        await asyncio.sleep(0)


def test_a_started_timer_paints_the_arc():
    async def scenario():
        server = FakeServer()
        arm(server)
        server.registry.apply(em_timers.TIMER_STARTED, "t1",
                              seconds_left=900, total_seconds=900)
        device = FakeDevice()
        await em_controller.sync_timer_countdown(device)
        await quiesce()
        assert device.frames, "a running timer painted nothing"
        lit = [p for p in device.frames[0] if p["r"] or p["g"] or p["b"]]
        assert len(lit) == 12, "a timer that just started is a whole ring"
        device.timer_countdown_task.cancel()

    asyncio.run(scenario())


def test_a_frame_that_cannot_be_sent_does_not_end_the_arc():
    async def scenario():
        server = FakeServer()
        arm(server)
        server.registry.apply(em_timers.TIMER_STARTED, "t1",
                              seconds_left=60, total_seconds=60)
        device = FakeDevice(fail_frames=1)
        await em_controller.sync_timer_countdown(device)
        await quiesce()
        task = device.timer_countdown_task
        assert task is not None and not task.done(), (
            "one failed write ended the countdown for the rest of the timer"
        )
        task.cancel()

    asyncio.run(scenario())


def test_the_arc_stops_when_the_timer_finishes():
    async def scenario():
        server = FakeServer()
        arm(server)
        server.registry.apply(em_timers.TIMER_STARTED, "t1",
                              seconds_left=60, total_seconds=60)
        device = FakeDevice()
        await em_controller.sync_timer_countdown(device)
        await quiesce()
        server.registry.apply(em_timers.TIMER_FINISHED, "t1")
        await em_controller.sync_timer_countdown(device)
        assert device.timer_countdown_task is None

    asyncio.run(scenario())


def test_an_arc_that_dies_is_reported():
    # Without the done callback the exception sits unretrieved on the task
    # and the only symptom is a ring that never lights.
    from _sources import def_source

    body = def_source("sync_timer_countdown")
    assert "add_done_callback" in body
    assert "Timer countdown arc failed" in body
