"""
The rook payloads OTA never carried: Magisk service.d scripts and the panel
APK.

Everything else a Spot runs is already reconciled over the shell plane — the
server binary and start_server.sh by em_ota, the debloat by em_payloads, the
wake word models and the ONNX runtime by em_oww_assets. These two were the
exception, and only because deploy_rook.sh was written first and nothing
forced the change: both are ordinary file pushes to a device we already hold
a root shell on. Until this module they meant a USB cable, and a Spot on a
light switch (or screwed to a wall) is a poor thing to need a cable for.

What that cost, measured: the Bedroom Spot sat four days on a backlight fix
it could not be given, because the fix is a service.d script plus the panel
APK and the only path for either was adb.

  /data/adb/service.d/      Magisk's on-disk service.d
  $IMG/                     the SAME scripts inside magisk.img, which is the
                            copy Magisk actually runs — deploy_rook.sh writes
                            both and so does this, for the same reason: the
                            two are different files and writing only the
                            first changes nothing at boot
  /system/app/RookPanel/    the panel APK, a system app, so /system has to be
                            remounted rw and back

Every push is md5-verified by em_shell.stream_file before it replaces
anything, and every one of these takes effect at the next reboot — a running
service.d script's shell keeps reading the old inode, and Android loads a
system APK once at boot.

Best-effort throughout, like the payload syncs next door: a failure logs and
returns, and never blocks the firmware update it rides along with.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import em_events
import em_shell
import em_state

log = logging.getLogger("echomuse.api")

PAYLOADS_DIR = Path(__file__).parent / "device_payloads" / "rook"

# payload file -> name under service.d. start_server_rook.sh is numbered 50 on
# the device so it runs after the four bring-up scripts; the payload keeps its
# own name, as deploy_rook.sh has always had it.
SERVICE_SCRIPTS = {
    "00-rook-discovery.sh":  "00-rook-discovery.sh",
    "01-rook-usb-adb.sh":    "01-rook-usb-adb.sh",
    "02-rook-block-ota.sh":  "02-rook-block-ota.sh",
    "03-rook-backlight.sh":  "03-rook-backlight.sh",
    "start_server_rook.sh":  "50-start_server_rook.sh",
}

SERVICE_D = "/data/adb/service.d"
APK_PAYLOAD = "rook_panel.apk"
APK_PATH = "/system/app/RookPanel/RookPanel.apk"

# Where magisk.img is mounted differs between boots on these units, so the
# directory is resolved on the device rather than assumed — the same rule
# deploy_rook.sh follows for busybox.
_IMG_PROBE = (
    'for d in /sbin/.core/img/.core/service.d '
    '/sbin/.magisk/img/.core/service.d; do '
    '[ -d "$d" ] && echo "$d" && break; done'
)


def is_rook(device_id: str) -> bool:
    """Whether this device reported the rook board (em_boards)."""
    return em_state.boards.get(device_id) == "rook"


def payload(name: str) -> bytes | None:
    """A payload's bytes, or None with a log line if it is not there."""
    try:
        return (PAYLOADS_DIR / name).read_bytes()
    except OSError as e:
        log.error(f"[api] rook payload {name} unreadable — skipping: {e}")
        return None


async def device_md5(live, path: str) -> str | None:
    """
    The device's md5 of `path`, "" when the file is absent, None when the
    device did not answer.

    The three are different decisions and em_shell.run cannot tell the last
    two apart on its own — it returns "" for both — so the marker is what
    separates "install it" from "leave this device alone" (the same trap
    em_payloads.sync_start_script documents).
    """
    out = await em_shell.run(
        live, f"busybox md5sum {path} 2>/dev/null; echo {em_shell.OK_MARK}")
    if em_shell.OK_MARK not in out:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 32:
            return parts[0]
    return ""


async def sync_service_scripts(live, device_id: str) -> None:
    """Push any service.d script whose md5 differs, to both copies."""
    dirs = [SERVICE_D]
    img = (await em_shell.run(live, _IMG_PROBE)).strip().splitlines()
    if img and img[-1].startswith("/"):
        dirs.append(img[-1].strip())
    else:
        log.info(f"[api] [{device_id}] no magisk.img service.d found — "
                 f"writing {SERVICE_D} only")

    for src, name in SERVICE_SCRIPTS.items():
        data = payload(src)
        if data is None:
            continue
        want = hashlib.md5(data).hexdigest()
        for d in dirs:
            dest = f"{d}/{name}"
            have = await device_md5(live, dest)
            if have is None:
                log.info(f"[api] [{device_id}] {name}: no answer from the "
                         f"device — leaving it alone")
                return
            if have == want:
                continue
            await em_events.push_log(device_id, "info", "controller",
                                     f"{name} out of date — syncing")
            res = await em_shell.stream_file(live, data, dest, mode="755",
                                             require_verify=True)
            await em_events.push_log(
                device_id, "info" if res else "warn", "controller",
                f"{name} synced — takes effect on next device reboot"
                if res else f"{name} sync failed: {res}")


async def sync_panel_apk(live, device_id: str) -> None:
    """
    Replace the panel APK when it differs, remounting /system for it.

    Staged in /data/local/tmp and copied into place under the remount, so
    /system spends the transfer read-only and only the copy happens while it
    is writable. The remount back to ro runs whether or not the copy worked.
    """
    data = payload(APK_PAYLOAD)
    if data is None:
        return
    want = hashlib.md5(data).hexdigest()

    have = await device_md5(live, APK_PATH)
    if have is None:
        log.info(f"[api] [{device_id}] RookPanel.apk: no answer from the "
                 f"device — leaving it alone")
        return
    if have == want:
        return

    staged = "/data/local/tmp/.rookpanel.apk"
    res = await em_shell.stream_file(live, data, staged, mode="644",
                                     require_verify=True)
    if not res:
        await em_events.push_log(device_id, "warn", "controller",
                                 f"RookPanel.apk transfer failed: {res}")
        return

    out = await em_shell.run(live,
        f"mount -o rw,remount /system; "
        f"mkdir -p {APK_PATH.rsplit('/', 1)[0]}; "
        f"cp {staged} {APK_PATH} && chmod 644 {APK_PATH}; "
        f"mount -o ro,remount /system; rm -f {staged}; "
        f"busybox md5sum {APK_PATH} 2>/dev/null")
    ok = want in out
    await em_events.push_log(
        device_id, "info" if ok else "warn", "controller",
        "RookPanel.apk synced — takes effect on next device reboot"
        if ok else f"RookPanel.apk sync failed ({out.strip() or 'no output'})")


async def sync(live, device_id: str) -> None:
    """Every rook-only payload, on a device that is a rook."""
    if not is_rook(device_id):
        return
    await sync_service_scripts(live, device_id)
    await sync_panel_apk(live, device_id)
