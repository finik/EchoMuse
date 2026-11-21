#!/system/bin/sh
# Magisk post-fs-data/service.d script — rook bring-up discovery dump.
#
# Runs as root at every boot and writes the hardware inventory to /data, which
# survives a reboot. The persistence is the point: the firmware fatals if the mic
# or speaker cannot be opened, so on a board whose ALSA numbering differs from
# biscuit's the daemon never reaches the controller and there is no remote shell
# to ask these questions through.
#
# Read-only: every command below only inspects.
OUT=/data/local/tmp/rook_discovery.txt
exec >> "$OUT" 2>&1

echo "################ boot at $(date) ################"

echo "##### PLATFORM #####"
for p in ro.product.device ro.product.model ro.product.name ro.build.version.sdk \
         ro.build.version.release ro.build.version.name ro.build.version.number \
         ro.product.cpu.abi ro.product.cpu.abilist ro.boot.selinux ro.hardware \
         ro.board.platform ro.revision ro.build.fingerprint ro.zygote \
         sys.usb.config persist.sys.usb.config ro.debuggable ro.secure; do
  echo "$p = $(getprop $p)"
done
echo "uname: $(uname -a)"
echo "selinux: $(getenforce 2>/dev/null)"
echo "id: $(id)"

echo "##### ALSA #####"
echo "--- /proc/asound/cards ---"; cat /proc/asound/cards 2>/dev/null
echo "--- /proc/asound/pcm ---";   cat /proc/asound/pcm 2>/dev/null
echo "--- /dev/snd ---";           ls -l /dev/snd/ 2>/dev/null

echo "##### MIXER (tinymix full listing) #####"
if [ -x /system/bin/tinymix ]; then
  /system/bin/tinymix 2>&1
else
  echo "tinymix ABSENT at /system/bin/tinymix"
fi
for t in tinycap tinyplay tinypcminfo; do
  [ -x /system/bin/$t ] && echo "$t: present" || echo "$t: absent"
done

echo "##### I2C NAMES (codec / ALS) #####"
for dev in /sys/bus/i2c/devices/*/; do
  n=$(cat "$dev/name" 2>/dev/null)
  [ -n "$n" ] && echo "$(basename $dev) -> $n"
done

echo "##### INPUT DEVICES #####"
cat /proc/bus/input/devices 2>/dev/null

echo "##### LEDS / GPIO / USB MODE #####"
ls -l /sys/class/leds/ 2>/dev/null
echo "mt_usb cmode: $(cat /sys/devices/platform/mt_usb/cmode 2>/dev/null)"

echo "##### SENSORS #####"
ls -l /sys/bus/iio/devices/ 2>/dev/null
ls -l /sys/class/sensors/ 2>/dev/null

echo "##### BLUETOOTH #####"
ls -l /dev/stpbt 2>/dev/null || echo "no /dev/stpbt"

echo "##### WIFI STATE (does it have stored credentials?) #####"
ls -l /data/misc/wifi/wpa_supplicant.conf 2>/dev/null
grep -c "network=" /data/misc/wifi/wpa_supplicant.conf 2>/dev/null
ip addr show wlan0 2>/dev/null | grep -E "inet |state"

echo "##### CODEC DMESG #####"
dmesg 2>/dev/null | grep -iE "tlv320|aic32|codec|mt_soc|amzn|als|tsl2|i2s|magisk" | tail -40

echo "################ end ################"
