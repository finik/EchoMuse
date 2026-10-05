#!/bin/bash
# Put everything a working Echo Spot needs onto one unit, over adb, idempotently.
#
# WHY THIS EXISTS. A Spot needs seven things in four different places, and the
# bring-up doc describes them as prose. Doing it by hand on a second unit meant
# remembering all of them, and the two that are easiest to forget are the two
# that fail SILENTLY: the ONNX runtime (absent -> the Echo cannot score its own
# wake word, so it streams instead, and the dashboard still says it is fine),
# and the magisk.img copy of the service.d scripts (see below).
#
# THE TRAP THIS SCRIPT EXISTS TO AVOID. On Magisk 17.3 here,
# /data/adb/service.d/ and /sbin/.core/img/.core/service.d/ are DIFFERENT FILES
# and Magisk runs the one inside magisk.img. Installing to /data/adb/service.d
# alone changes nothing: the old script keeps running at every boot while the
# new one passes every test you run by hand. That cost a day on 2026-10-03/04,
# where a lock fix "worked" in isolation and failed on every single boot.
# Every script here is therefore written to BOTH and verified in BOTH.
#
# Verification is by md5 at both ends for every file, because a truncated push
# is the right size and fails later at dlopen or at exec with an error naming
# nothing. The device has no md5sum on PATH but Magisk's busybox does, and
# where that lives moved between boots on these units, so it is resolved rather
# than assumed.
#
#   ./deploy_rook.sh <adb-serial> [--no-apk]
#
# Safe to re-run: every step checks before it writes, and says "ok" when the
# device already matches.
set -uo pipefail

S="${1:-}"
[ -z "$S" ] && { echo "usage: $0 <adb-serial> [--no-apk]" >&2; exit 2; }
SKIP_APK=0
[ "${2:-}" = "--no-apk" ] && SKIP_APK=1

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
RUNTIME_SRC="${EM_OWW_RUNTIME_DIR:-$HOME/work/musecontroller/controller/models/oww_runtime}"

ok=0; fail=0
say()  { printf '  %s\n' "$*"; }
good() { printf '  \033[32mok\033[0m    %s\n' "$*"; ok=$((ok+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fail=$((fail+1)); }

# Run a command as root on the device.
#
# Via a pushed FILE, never an inline string. `adb shell "su -c \"...\""` is
# parsed by the device's own shell before su ever sees it, so $VAR inside the
# command expands there — to nothing — and the command silently does something
# else. That is how the busybox probe below first reported "no busybox" on a
# unit that has it. Never interpolate into a shell command (root CLAUDE.md).
sh_() {
    printf '%s\n' "$1" > /tmp/.em_rook_cmd.$$
    adb -s "$S" push /tmp/.em_rook_cmd.$$ /data/local/tmp/.em_cmd >/dev/null 2>&1
    rm -f /tmp/.em_rook_cmd.$$
    adb -s "$S" shell 'su -c "sh /data/local/tmp/.em_cmd"' 2>/dev/null | tr -d '\r'
}

echo "== deploy to $S =="
adb -s "$S" shell true 2>/dev/null || { echo "device not reachable" >&2; exit 1; }

# Magisk's busybox has moved between boots on these units (/data/adb/magisk,
# /sbin/.core/busybox), so find it rather than hardcode it. No busybox means no
# md5 on the device, and this script refuses rather than ship unverified bytes.
BB=$(sh_ 'for d in /sbin/.core/busybox /data/adb/magisk /sbin/.magisk/busybox; do [ -x $d/busybox ] && echo $d/busybox && break; [ -x $d/md5sum ] && echo $d/md5sum && break; done' | head -1)
[ -z "$BB" ] && { echo "no busybox found — cannot verify transfers, refusing" >&2; exit 1; }
case "$BB" in *md5sum) MD5="$BB" ;; *) MD5="$BB md5sum" ;; esac
say "md5 via $MD5"

# push_verify <local> <device-path> [mode]
push_verify() {
    local src="$1" dst="$2" mode="${3:-644}"
    [ -f "$src" ] || { bad "missing locally: $src"; return 1; }
    local want; want=$(md5 -q "$src" 2>/dev/null || md5sum "$src" | cut -d' ' -f1)
    local have; have=$(sh_ "$MD5 '$dst' 2>/dev/null" | cut -d' ' -f1)
    if [ "$have" = "$want" ]; then good "$dst (unchanged)"; return 0; fi
    adb -s "$S" push "$src" /data/local/tmp/.stage >/dev/null 2>&1 || { bad "push failed: $dst"; return 1; }
    local staged; staged=$(sh_ "$MD5 /data/local/tmp/.stage" | cut -d' ' -f1)
    [ "$staged" != "$want" ] && { bad "$dst: staged md5 $staged != $want"; return 1; }
    sh_ "mkdir -p '$(dirname "$dst")'; cp /data/local/tmp/.stage '$dst'; chmod $mode '$dst'; chown root:root '$dst'; rm -f /data/local/tmp/.stage" >/dev/null
    local final; final=$(sh_ "$MD5 '$dst'" | cut -d' ' -f1)
    [ "$final" = "$want" ] && good "$dst" || bad "$dst: md5 $final != $want after move"
}

echo
echo "-- service.d scripts (BOTH copies; magisk.img is the one that runs) --"
IMG=$(sh_ 'for d in /sbin/.core/img/.core/service.d /sbin/.magisk/img/.core/service.d; do [ -d $d ] && echo $d && break; done' | head -1)
[ -z "$IMG" ] && say "no magisk.img service.d found — only /data/adb/service.d written"
for f in 00-rook-discovery.sh 01-rook-usb-adb.sh 02-rook-block-ota.sh 03-rook-backlight.sh; do
    push_verify "$HERE/$f" "/data/adb/service.d/$f" 755
    [ -n "$IMG" ] && push_verify "$HERE/$f" "$IMG/$f" 755
done
push_verify "$HERE/start_server_rook.sh" "/data/adb/service.d/50-start_server_rook.sh" 755
[ -n "$IMG" ] && push_verify "$HERE/start_server_rook.sh" "$IMG/50-start_server_rook.sh" 755

echo
echo "-- firmware --"
push_verify "$REPO/device/build/server" "/data/local/bin/server" 755

echo
echo "-- on-device wake word (absent = the Echo streams instead, silently) --"
push_verify "$RUNTIME_SRC/libonnxruntime.so" "/data/local/share/echomuse/oww/libonnxruntime.so" 644
push_verify "$RUNTIME_SRC/silero_vad.onnx"   "/data/local/share/echomuse/oww/silero_vad.onnx"   644
missing=$(sh_ 'for m in melspectrogram.onnx embedding_model.onnx alexa_v0.1.onnx; do [ -f /data/local/share/echomuse/oww/$m ] || echo $m; done')
[ -z "$missing" ] && good "wake models present" || bad "missing models: $missing (install from the dashboard Updates tab)"

if [ "$SKIP_APK" = 0 ]; then
    echo
    echo "-- panel app (system app; needs a reboot to take) --"
    APK="$REPO/rook_panel/rook_panel.apk"
    if [ -f "$APK" ]; then
        want=$(md5 -q "$APK")
        have=$(sh_ "$MD5 /system/app/RookPanel/RookPanel.apk 2>/dev/null" | cut -d' ' -f1)
        if [ "$have" = "$want" ]; then good "RookPanel.apk (unchanged)"
        else
            adb -s "$S" push "$APK" /data/local/tmp/.apk >/dev/null 2>&1
            sh_ "mount -o rw,remount /system; mkdir -p /system/app/RookPanel; cp /data/local/tmp/.apk /system/app/RookPanel/RookPanel.apk; chmod 644 /system/app/RookPanel/RookPanel.apk; mount -o ro,remount /system; rm -f /data/local/tmp/.apk" >/dev/null
            final=$(sh_ "$MD5 /system/app/RookPanel/RookPanel.apk" | cut -d' ' -f1)
            [ "$final" = "$want" ] && good "RookPanel.apk" || bad "RookPanel.apk md5 $final != $want"
        fi
    else
        bad "no rook_panel.apk — build it with rook_panel/build.sh"
    fi
fi

echo
echo "== $ok ok, $fail failed =="
[ "$fail" -gt 0 ] && exit 1
cat <<'EOF'

Next, and neither is done by this script:
  1. Reboot the unit. The panel APK is a system app and the service.d scripts
     only run at boot. The daemon starts itself; nothing needs a hand restart.
  2. In the controller, set this device's owwOnDevice to "on" so it detects its
     own wake word and stops streaming. It will report listen_state "local",
     which is the only claim about privacy worth trusting — it comes from the
     Echo, not from the setting.
  3. In Home Assistant, give the device's assist_satellite an AREA, or
     area-less commands ("turn on the lights") have nothing to resolve against.
EOF
