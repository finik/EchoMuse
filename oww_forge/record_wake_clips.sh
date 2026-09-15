#!/bin/bash
# Quick loop for recording oww_forge training clips: press Enter to start
# a take, say the phrase, press Enter again to stop and save it, repeat.
# Ctrl+C when done. Writes 16kHz mono wav straight into positive_train/ —
# no need to go through the web UI's upload dialog at all.
#
# Usage: ./record_wake_clips.sh [name-prefix] [wakeword-dir-name]
#   ./record_wake_clips.sh dad            # -> dad_001.wav, dad_002.wav, ...
#   ./record_wake_clips.sh mom hey_jarvis # explicit wake-word name

set -euo pipefail

PREFIX="${1:-family}"
WAKEWORD="${2:-hey_jarvis}"
OUTDIR="$(cd "$(dirname "$0")" && pwd)/data/wakewords/$WAKEWORD/positive_train"
mkdir -p "$OUTDIR"

# First avfoundation audio device — see the printout above for the index.
# Override with: AUDIO_DEVICE=:1 ./record_wake_clips.sh ...
DEVICE="${AUDIO_DEVICE:-:0}"

FFPID=""
cleanup() {
    if [ -n "$FFPID" ] && kill -0 "$FFPID" 2>/dev/null; then
        kill -INT "$FFPID" 2>/dev/null || true
        wait "$FFPID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

next_path() {
    printf '%s/%s_%03d.wav' "$OUTDIR" "$PREFIX" "$i"
}

start_recording() {
    ffmpeg -hide_banner -loglevel error -f avfoundation -i "$DEVICE" \
        -ar 16000 -ac 1 -sample_fmt s16 -y "$(next_path)" &
    FFPID=$!
    # 1.2s, not 0.3s: a USB/webcam mic (no built-in mic on this machine) took
    # noticeably longer than 300ms to actually start delivering samples,
    # which was clipping the start of every take. A silent lead-in is
    # harmless for training; a truncated word is not.
    sleep 1.2
}

echo "Writing clips to: $OUTDIR"
echo "Device: $DEVICE (Ctrl+C to stop entirely)"
echo "Available input devices (override a bad pick with AUDIO_DEVICE=:N):"
ffmpeg -f avfoundation -list_devices true -i "" 2>&1 | sed -n '/audio devices/,/^\[in#0/p' | grep '^\[AVFoundation' | grep -v 'audio devices'
echo

i=1
while true; do
    echo "[$i] press Enter to START recording"
    read -r _
    start_recording
    echo "    recording — say \"hey jarvis\", then press Enter to STOP and save"
    read -r _
    kill -INT "$FFPID" 2>/dev/null || true
    wait "$FFPID" 2>/dev/null || true
    echo "    saved $(basename "$(next_path)")"
    i=$((i + 1))
done
