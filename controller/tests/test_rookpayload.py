"""
The Spot's own payloads reach it over the shell plane, not over a cable.

Until em_rookpayload the Magisk service.d scripts and the panel APK had no
update path at all: OTA carried the server binary, start_server.sh and the
debloat, and everything else meant adb. A Spot screwed to a wall or wired to
a light switch is a poor thing to need a USB cable for, and the Bedroom unit
spent four days on a backlight fix it could not be given.

Driven through the real sync functions against a fake shell plane.
"""

import asyncio
import hashlib

import pytest

import em_rookpayload
import em_shell
import em_state
from _sources import def_source


class FakeShell:
    """
    A device that answers md5 queries from a dict of path -> bytes and
    records every file pushed to it.
    """

    def __init__(self, files: dict[str, bytes] | None = None,
                 img: str = "/sbin/.core/img/.core/service.d",
                 answers: bool = True):
        self.files = dict(files or {})
        self.img = img
        self.answers = answers
        self.pushed: dict[str, bytes] = {}
        self.commands: list[str] = []

    async def run(self, live, cmd: str, timeout: float = 30.0) -> str:
        self.commands.append(cmd)
        if "service.d; do" in cmd or "/img/" in cmd and "md5sum" not in cmd:
            return self.img
        if not self.answers:
            return ""
        out = []
        for path, data in self.files.items():
            if f"md5sum {path} " in cmd or cmd.endswith(f"md5sum {path}"):
                out.append(f"{hashlib.md5(data).hexdigest()}  {path}")
        if em_shell.OK_MARK in cmd:
            out.append(em_shell.OK_MARK)
        return "\n".join(out)

    async def stream_file(self, live, data: bytes, dest: str,
                          mode: str = "755", require_verify: bool = False):
        self.pushed[dest] = data
        self.files[dest] = data
        return em_shell.TransferResult(True) if hasattr(em_shell, "TransferResult") else True


@pytest.fixture
def shell(monkeypatch):
    fake = FakeShell()
    monkeypatch.setattr(em_shell, "run", fake.run)
    monkeypatch.setattr(em_shell, "stream_file", fake.stream_file)
    monkeypatch.setattr(em_rookpayload.em_events, "push_log",
                        lambda *a, **k: asyncio.sleep(0))
    return fake


def test_only_a_rook_gets_them(monkeypatch, shell):
    # A Dot has no Magisk service.d of this shape and no panel at all.
    em_state.boards["DOT"] = "biscuit"
    asyncio.run(em_rookpayload.sync(object(), "DOT"))
    assert not shell.pushed
    assert not shell.commands, "a Dot spent a shell round trip for nothing"


def test_a_missing_script_is_installed_on_both_copies(shell):
    # /data/adb/service.d and the copy inside magisk.img are DIFFERENT files
    # and Magisk runs the one in the image — writing only the first changes
    # nothing at boot. deploy_rook.sh has always written both.
    em_state.boards["SPOT"] = "rook"
    asyncio.run(em_rookpayload.sync_service_scripts(object(), "SPOT"))
    for name in em_rookpayload.SERVICE_SCRIPTS.values():
        assert f"/data/adb/service.d/{name}" in shell.pushed
        assert f"{shell.img}/{name}" in shell.pushed


def test_a_script_already_in_step_is_not_pushed(shell):
    want = {}
    for src, name in em_rookpayload.SERVICE_SCRIPTS.items():
        data = em_rookpayload.payload(src)
        want[f"/data/adb/service.d/{name}"] = data
        want[f"{shell.img}/{name}"] = data
    shell.files.update(want)
    asyncio.run(em_rookpayload.sync_service_scripts(object(), "SPOT"))
    assert not shell.pushed, "an up-to-date Spot was rewritten anyway"


def test_a_device_that_does_not_answer_is_left_alone(shell):
    # em_shell.run returns "" both for "the file is absent" and for "no shell
    # came up". Reading the second as the first pushes a payload down a link
    # that is not there — the trap em_payloads.sync_start_script documents.
    shell.answers = False
    asyncio.run(em_rookpayload.sync_service_scripts(object(), "SPOT"))
    assert not shell.pushed


def test_the_apk_goes_in_under_a_remount(shell):
    asyncio.run(em_rookpayload.sync_panel_apk(object(), "SPOT"))
    assert "/data/local/tmp/.rookpanel.apk" in shell.pushed, \
        "the APK must be staged outside /system"
    cmd = next(c for c in shell.commands if "remount" in c)
    assert cmd.index("mount -o rw,remount /system") < cmd.index("cp ")
    assert cmd.index("cp ") < cmd.index("mount -o ro,remount /system"), \
        "/system must go back to read-only in the same command"


def test_the_apk_already_installed_is_left_alone(shell):
    shell.files[em_rookpayload.APK_PATH] = em_rookpayload.payload(
        em_rookpayload.APK_PAYLOAD)
    asyncio.run(em_rookpayload.sync_panel_apk(object(), "SPOT"))
    assert not shell.pushed


def test_an_absent_payload_file_is_survivable(monkeypatch, shell):
    monkeypatch.setattr(em_rookpayload, "PAYLOADS_DIR",
                        em_rookpayload.PAYLOADS_DIR / "nope")
    asyncio.run(em_rookpayload.sync(object(), "SPOT"))  # must not raise
    assert not shell.pushed


def test_every_payload_this_module_names_exists_on_disk():
    # The one that cannot be caught at runtime: a renamed script would turn
    # into a log line on every connect and nothing else.
    for src in em_rookpayload.SERVICE_SCRIPTS:
        assert (em_rookpayload.PAYLOADS_DIR / src).is_file(), src


def test_the_transfers_are_md5_verified():
    body = def_source("em_rookpayload.sync_service_scripts")
    body += def_source("em_rookpayload.sync_panel_apk")
    assert body.count("require_verify=True") == 2, \
        "a payload that lands corrupt must never replace a working one"


def test_both_call_sites_gate_on_android():
    # The rule the debloat learned the expensive way: a Magisk payload sent
    # to emOS holds the shell lock for its whole timeout.
    for fn in ("em_reconcile._reconcile_payloads", "em_ota._run_update_locked"):
        body = def_source(fn)
        assert "em_rookpayload.sync" in body
        before = body.split("em_rookpayload.sync")[0]
        assert "live.android_userspace" in before


def test_a_spot_without_a_magisk_image_still_gets_the_on_disk_copy(shell):
    # Where magisk.img is mounted has moved between boots on these units, so
    # the directory is probed rather than assumed — and a probe that finds
    # nothing must not cost the device the scripts it CAN have.
    shell.img = ""
    asyncio.run(em_rookpayload.sync_service_scripts(object(), "SPOT"))
    assert shell.pushed
    assert all(p.startswith(em_rookpayload.SERVICE_D) for p in shell.pushed)


def test_an_apk_that_fails_to_transfer_never_touches_system(shell, monkeypatch):
    async def refuse(live, data, dest, mode="755", require_verify=False):
        return em_shell.TransferResult(False, stage="transfer")
    monkeypatch.setattr(em_shell, "stream_file", refuse)

    asyncio.run(em_rookpayload.sync_panel_apk(object(), "SPOT"))
    assert not any("remount" in c for c in shell.commands), \
        "/system was remounted read-write for a file that never arrived"


def test_an_apk_that_lands_wrong_is_reported(shell, monkeypatch):
    said = []
    monkeypatch.setattr(em_rookpayload.em_events, "push_log",
                        lambda d, lvl, src, msg: said.append((lvl, msg))
                        or asyncio.sleep(0))

    async def copy_goes_wrong(live, cmd, timeout=30.0):
        shell.commands.append(cmd)
        if "remount" in cmd:
            return "00000000000000000000000000000000  " + em_rookpayload.APK_PATH
        return await FakeShell.run(shell, live, cmd, timeout)
    monkeypatch.setattr(em_shell, "run", copy_goes_wrong)

    asyncio.run(em_rookpayload.sync_panel_apk(object(), "SPOT"))
    assert any(lvl == "warn" and "sync failed" in msg for lvl, msg in said)
