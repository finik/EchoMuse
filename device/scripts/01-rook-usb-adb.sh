#!/system/bin/sh
# Magisk service.d script — bring USB up as a device and start adbd on rook.
#
# Patching default.prop in the boot ramdisk sets the right properties but is not
# sufficient on this board: adb still does not appear. Two causes, both handled
# here, because neither can be diagnosed without the access being restored:
#
#  1. The MUSB controller sits in charging-only mode (cmode 2). It must be put in
#     device mode by writing 1 to /sys/devices/platform/mt_usb/cmode; above
#     cmode 2 the gadget reports DISCONNECTED forever however it is configured.
#     Charging-only is a plausible factory default for a service port.
#  2. init never applies persist.sys.usb.config to sys.usb.config, so the gadget
#     is never configured even with the controller in device mode.
#
# Idempotent and logged, so the log separates "set it and USB still did not come
# up" from "could not set it" — those want different next moves.
LOG=/data/local/tmp/rook_usb_adb.log
exec >> "$LOG" 2>&1
echo "==== $(date) usb/adb bring-up ===="

echo "before: cmode=$(cat /sys/devices/platform/mt_usb/cmode 2>/dev/null) sys.usb.config=$(getprop sys.usb.config) persist=$(getprop persist.sys.usb.config)"

# 1) Put the USB controller into device (peripheral) mode.
if [ -w /sys/devices/platform/mt_usb/cmode ]; then
    echo 1 > /sys/devices/platform/mt_usb/cmode && echo "wrote cmode=1"
else
    echo "cmode not writable or absent: $(ls -l /sys/devices/platform/mt_usb/cmode 2>&1)"
fi

# 2) Ask init to configure the gadget for adb. resetprop is Magisk's own tool
#    and can set properties the property service would refuse; prefer it when
#    present, fall back to setprop.
RESETPROP=/data/adb/magisk/resetprop
[ -x "$RESETPROP" ] || RESETPROP=""

if [ -n "$RESETPROP" ]; then
    "$RESETPROP" persist.sys.usb.config adb && echo "resetprop persist.sys.usb.config=adb"
    "$RESETPROP" sys.usb.config adb && echo "resetprop sys.usb.config=adb"
else
    setprop persist.sys.usb.config adb && echo "setprop persist.sys.usb.config=adb"
    setprop sys.usb.config adb && echo "setprop sys.usb.config=adb"
fi

# 3) Start adbd directly in case init's property trigger did not fire.
start adbd 2>/dev/null && echo "start adbd issued"
sleep 3
echo "adbd running: $(ps | grep -c '[a]dbd')"

echo "after:  cmode=$(cat /sys/devices/platform/mt_usb/cmode 2>/dev/null) sys.usb.config=$(getprop sys.usb.config) persist=$(getprop persist.sys.usb.config)"
echo "usb state: $(getprop sys.usb.state)"
echo "==== done ===="
