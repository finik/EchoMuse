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
    # Milliseconds to seconds WITHOUT arithmetic: this board's shell is
    # 32-bit, and $((1794818467948 / 1000)) evaluates to -477861 — a negative
    # number, so `now -ge hold_s` was always true and the hold never applied.
    # The screen dimmed through every tap and every button press, which is
    # exactly the "brightness does nothing" this was meant to provide. Seconds
    # still fit in 32 bits (until 2038), so only the millisecond value has to
    # avoid the arithmetic. Stripping the last three digits as TEXT does that.
    hold_s=${hold%???}
    case "$hold_s" in "" | *[!0-9]*) hold_s=0 ;; esac
    night=0
    case "$hour" in
        00|01|02|03|04|05|06) night=1 ;;
    esac
    #
    # Inside the night window this script OWNS the backlight, and writes the
    # level it wants every pass rather than only on a change. Two reasons,
    # both found the hard way:
    #
    #   * Android's display service reverts a one-off sysfs write, so the
    #     level only holds by being rewritten. A write "on change" loses to
    #     it within a second.
    #   * `dimmed` was the script's memory of having dimmed, and it starts at
    #     0 on every start. Restarting during the night with a hold in force
    #     left the screen on the PREVIOUS run's dim level, unable to dim
    #     (held) and unwilling to brighten (it had not dimmed), so the Spot
    #     stayed dark until 7am whatever was pressed. Seen 2026-11-16.
    #
    # Outside the window the backlight is still left alone, except to restore
    # it once if this run dimmed it.
    if [ "$night" -eq 1 ]; then
        if [ "$now" -ge "$hold_s" ]; then
            echo "$NIGHT" > "$BL" 2>/dev/null
        else
            echo "$DAY" > "$BL" 2>/dev/null
        fi
        dimmed=1
    elif [ "$dimmed" -eq 1 ]; then
        echo "$DAY" > "$BL" 2>/dev/null
        dimmed=0
    fi
    sleep 2
done
