#!/system/bin/sh
# Night dim for the Spot clock, from root.
#
# The panel used to set this as a window attribute on its UI thread. That call
# blocked inside the window manager at midnight and froze the clock on the
# last frame, 00:00. The panel now only records a tap. This script owns the
# backlight. Midnight until 7am is dim; a tap holds day brightness for as
# long as the file says. Outside that window the backlight is left alone,
# except to restore it at 7am if this script was the one that dimmed it.
BL=/sys/class/leds/lcd-backlight/brightness
HOLD=/data/data/com.echomuse.rookpanel/files/bright_until
DAY=249
NIGHT=15
dimmed=0

while true; do
    hour=$(date +%H 2>/dev/null)
    now=$(date +%s 2>/dev/null)
    hold=0
    if [ -r "$HOLD" ]; then
        hold=$(cat "$HOLD" 2>/dev/null)
    fi
    case "$hold" in
        ""|*[!0-9]*) hold=0 ;;
    esac
    case "$now" in
        ""|*[!0-9]*) now=0 ;;
    esac
    hold_s=$((hold / 1000))
    night=0
    case "$hour" in
        00|01|02|03|04|05|06) night=1 ;;
    esac
    if [ "$night" -eq 1 ] && [ "$now" -ge "$hold_s" ]; then
        echo "$NIGHT" > "$BL" 2>/dev/null
        dimmed=1
    elif [ "$dimmed" -eq 1 ]; then
        echo "$DAY" > "$BL" 2>/dev/null
        dimmed=0
    fi
    sleep 2
done
