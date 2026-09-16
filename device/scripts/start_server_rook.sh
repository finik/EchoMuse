#!/system/bin/sh
# EchoMuse start script — Echo Spot 1st gen (rook).
#
# Installed as a Magisk service.d script, like biscuit's start_server.sh:
# Magisk's daemon runs these, which sidesteps Android init's refusal to execve an
# untrusted /data binary. Install to BOTH /data/adb/service.d and
# /sbin/.core/img/.core/service.d — Magisk 17.3 runs the copy inside magisk.img,
# and a script present only in /data/adb/service.d is silently ignored.
#
# Every mixer write resolves its control BY NAME. biscuit addresses controls by
# position; those offsets are not portable, because the mixer list concatenates
# machine-level and codec-level controls and rook's machine configuration is not
# biscuit's.
#
# Logs go to /data/local/tmp — this device has no /tmp, so biscuit's log path
# would leave the daemon started under a redirect that cannot be opened.
#
# biscuit's script also writes controls 56, 64 and 88 during speaker init, whose
# names are recorded nowhere. They are omitted rather than guessed; the full
# mixer listing is dumped to the log on each start so they can be named from real
# data. The firmware closes the codec's DAPM routes itself either way.

# ── Single instance only ──────────────────────────────────────────────────────
# Two copies of the daemon fight over the speaker PCM without reporting a
# conflict: both seed volume from the controller and whichever writes last wins,
# so `PCM Playback Volume` can end at 0 while the log reads "Volume set to
# 110/127" and playback reports no underruns into silence. Magisk runs service.d
# once per boot, so this guard is for hand restarts.
LOCK=/data/local/tmp/start_server_rook.lock
if [ -f "$LOCK" ]; then
    OLD=$(cat "$LOCK" 2>/dev/null)
    if [ -n "$OLD" ] && [ -d "/proc/$OLD" ]; then
        echo "[start_server_rook] already running as pid $OLD — exiting" >> /data/local/tmp/server.log
        exit 0
    fi
fi
echo $$ > "$LOCK"
# Drop the lock on any exit path, so a crash does not wedge the next start.
trap 'rm -f "$LOCK"' EXIT INT TERM

LOG=/data/local/tmp/server.log

log() { echo "[start_server_rook] $*" >> "$LOG"; }

# ── Wait for Amazon's audio service to bring the codec up ─────────────────────
# Same reasoning as biscuit: on Fire OS the HAL configures the codec before we
# take it. /dev/__properties__ is Android's property service, so this tests
# whether Android is running at all rather than which OS this is.
if [ -e /dev/__properties__ ]; then
    i=0
    while [ $i -lt 30 ]; do
        # `ps -A`, not bare `ps`: Android's toybox ps lists only processes on the
        # caller's controlling terminal, and a service.d script has none, so bare
        # `ps` matches nothing and this loop burns its full timeout every boot.
        pid=$(ps -A 2>/dev/null | grep echoaudio | grep -v grep)
        if [ -n "$pid" ]; then
            sleep 5
            break
        fi
        sleep 2
        i=$((i + 2))
    done
fi

# ── Mixer control resolution by name ─────────────────────────────────────────
# The listing is fetched ONCE into a file: process spawns are expensive on this
# hardware (a heavy shell command has been observed inducing mic capture
# stalls), so this must not run tinymix once per lookup.
MIXER_LIST=/data/local/tmp/rook_mixer_list.txt
tinymix -D 0 > "$MIXER_LIST" 2>/dev/null

# Dump the listing into the log on every start: it is the record of this board's
# real control offsets, and cheaper to keep than to fetch over a shell session.
log "mixer listing follows ($(wc -l < "$MIXER_LIST") lines)"
cat "$MIXER_LIST" >> "$LOG"

# mixer_ctl <exact name> -> prints the control index, or nothing.
#
# Matches the name as a whole, anchored between the value-count column and the
# control's current value, so a name that is a prefix of a longer control does
# not match it — "ADC_A Left Mute" must not resolve to "ADC_A Left Mute Switch".
# BUSYBOX awk, not awk: Android's toybox ships no awk at all, so every lookup
# returns empty and every gain silently reports MISSING. Magisk bundles busybox,
# which is guaranteed present on a device that booted this script.
AWK=/data/adb/magisk/busybox\ awk
[ -x /data/adb/magisk/busybox ] || AWK=awk   # fall back, and fail loudly below

mixer_ctl() {
    $AWK -v want="$1" '
        {
            # Strip the leading index/type/count columns, keep the remainder.
            idx = $1
            line = $0
            sub(/^[[:space:]]*[0-9]+[[:space:]]+[A-Z0-9]+[[:space:]]+[0-9]+[[:space:]]+/, "", line)
            # The name is the remainder up to the value; test for the name
            # appearing at the start of it followed by end-of-name boundary.
            if (index(line, want) == 1) {
                rest = substr(line, length(want) + 1)
                if (rest == "" || rest ~ /^[[:space:]]/) { print idx; exit }
            }
        }
    ' "$MIXER_LIST"
}

# mixer_set <exact name> <value...>
mixer_set() {
    name="$1"
    shift
    ctl=$(mixer_ctl "$name")
    if [ -z "$ctl" ]; then
        log "MISSING mixer control: '$name' — not set"
        return 1
    fi
    if tinymix -D 0 "$ctl" "$@" >/dev/null 2>&1; then
        log "set '$name' (ctl $ctl) = $*"
    else
        log "FAILED to set '$name' (ctl $ctl) = $*"
        return 1
    fi
}

# ── Hardware init ────────────────────────────────────────────────────────────
ip link set p2p0 down 2>/dev/null

# Prevent WiFi suspension
echo "EchoMuse" > /sys/power/wake_lock 2>/dev/null

# Speaker playback volume. rook names the DAC digital volume "PCM Playback
# Volume"; biscuit's label is "DAC Playback Volume". Range here is 0-175.
mixer_set "PCM Playback Volume" 100 100

# Mic gain. rook's capture part is a TLV320AIC3101 with two ADCs (A/B) taking
# four mics differentially, so there is no C or D to equalise. MICPGA sits at 0
# from reset — no analog mic gain at all.
for adc in A B; do
    mixer_set "ADC_${adc} Digital Volume Control" 88 88
    # 40, NOT the hardware maximum of 80. MICPGA is an analog stage ahead of the
    # ADC, so overdriving it distorts without ever incrementing the digital clip
    # counter — the audio gets LOUDER and WORSE, and nothing in the logs says so.
    #
    # Measured on hardware, same room, 15 minutes apart (2026-09-14):
    #
    #	MICPGA 40   wake scores 0.961 / 0.469 / 0.325   rms ~0.0004
    #	MICPGA 80   wake scores 0.296 / 0.326 / 0.271   rms ~0.003
    #
    # 40 -> 80 is a real +20.6dB of level and costs roughly half the score. Tune
    # this against wake-word SCORES, never against peak level or the clip count.
    #
    # Only the pre-connect floor: the controller's adcMicpga push overwrites it
    # seconds after the daemon connects, so the operating value lives there too
    # (per-device config, not the fleet default).
    mixer_set "ADC_${adc} MICPGA Volume Ctrl" 40 40
done

# ── Unmute the capture path ───────────────────────────────────────────────────
# Required: on rook these come out of reset MUTED with the mic PGA off.
#
#   Mic PGA Switch           Off Off
#   ADCFGA Left/Right Mute Switch  On
#   ADC_A Left/Right Mute          On
#   ADC_B Left/Right Mute          On
#
# A muted ADC on this part does NOT read as silence. It reads as a healthy
# capture stream at a flat -70dBFS with no modulation — the I2S noise floor,
# indistinguishable from a quiet room in every level meter the firmware prints.
# Clearing these four mutes and enabling the PGA yields 21.5dB of speech
# modulation. No amount of gain tuning substitutes for it.
for c in "ADC_A Left Mute" "ADC_A Right Mute" "ADC_B Left Mute" "ADC_B Right Mute" \
         "ADCFGA Left Mute Switch" "ADCFGA Right Mute Switch"; do
    mixer_set "$c" 0
done
mixer_set "Mic PGA Switch" 1 1

# rook has no LED ring (the round LCD replaces it), so biscuit's ledcontroller
# kill does not apply.

# ── Log size cap ─────────────────────────────────────────────────────────────
# Everything below only appends. Past MAX_LOG, keep the newest KEEP_LOG bytes in
# server.log.1 and truncate in place; the server's O_APPEND fd continues at the
# new EOF, so no restart is needed.
MAX_LOG=5242880    # 5MB
KEEP_LOG=524288    # 512KB carried into server.log.1
(
    while true; do
        sleep 300
        SIZE=$(wc -c < "$LOG" 2>/dev/null)
        if [ -n "$SIZE" ] && [ "$SIZE" -gt $MAX_LOG ]; then
            tail -c $KEEP_LOG "$LOG" > "${LOG}.1" 2>/dev/null
            : > "$LOG"
        fi
    done
) &

# ── Supervise the server ─────────────────────────────────────────────────────
# Same retry policy as biscuit: an exit under MIN_RUNTIME counts as a failed
# start, and MAX_ATTEMPTS consecutive fast exits gives up rather than spinning.
# No A/B slot restore here yet — rook has no fielded OTA story to roll back to,
# deliberately less than biscuit's script rather than aspirationally equal to it.
MAX_ATTEMPTS=3
MIN_RUNTIME=15
SERVER=/data/local/bin/server

# ── Keep Android from killing us ─────────────────────────────────────────────
# oom_score_adj -1000 is the "never kill this" end of the scale, the protection
# Android gives its own persistent services. A 2GB device running full Fire OS
# plus a foreground panel app will otherwise let lowmemorykiller take the daemon,
# which looks like a silent disappearance: no panic, the log just ends. Applied
# to the supervising shell too, so killing the child cannot orphan the loop.
echo -1000 > /proc/$$/oom_score_adj 2>/dev/null

attempts=0
while [ $attempts -lt $MAX_ATTEMPTS ]; do
    start=$(date +%s)
    log "starting $SERVER"
    "$SERVER" >> "$LOG" 2>&1 &
    SRV_PID=$!
    # Protect the daemon itself, and the panel that draws its ring: if either is
    # killed the device looks dead while reporting itself healthy.
    echo -1000 > /proc/$SRV_PID/oom_score_adj 2>/dev/null
    for pid in $(/data/adb/magisk/busybox pgrep -f rookpanel 2>/dev/null); do
        echo -1000 > /proc/$pid/oom_score_adj 2>/dev/null
    done
    wait $SRV_PID
    end=$(date +%s)
    ran=$((end - start))
    if [ $ran -ge $MIN_RUNTIME ]; then
        log "server ran ${ran}s then exited — operational failure, restarting"
        attempts=0
    else
        attempts=$((attempts + 1))
        log "server exited after ${ran}s (fast exit $attempts/$MAX_ATTEMPTS)"
    fi
    sleep 2
done

log "giving up after $MAX_ATTEMPTS consecutive fast exits"
