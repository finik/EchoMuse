#!/system/bin/sh
# Magisk service.d script — stop Fire OS from OTA-updating itself on rook.
#
# The boot partition carries a patched cmdline (androidboot.selinux=permissive)
# and Magisk's magiskinit. A Fire OS OTA reflashes boot, so a successful update
# silently removes root and permissive SELinux, and on a device whose bootloader
# was rolled back by amonet a partially-applied OTA is worse still. biscuit uses
# the f1r30s.zip OTA-block patch; rook has no such zip, so this does that job
# with the root already present.
#
# Deliberately does NOT touch com.amazon.device.echoaudioservice: it triggers the
# audio HAL to initialise the codec and I2S clock at boot, and the raw-ALSA path
# depends on the state it leaves behind. Disabling it yields a device that looks
# healthy and plays silence.
LOG=/data/local/tmp/rook_ota_block.log
exec >> "$LOG" 2>&1
echo "==== $(date) ota block ===="

# Wait for the package manager to be up; `pm` before then fails with a
# connection error and the disable silently does not happen.
i=0
while [ $i -lt 90 ]; do
    if pm list packages >/dev/null 2>&1; then break; fi
    sleep 2
    i=$((i + 2))
done
echo "pm available after ${i}s"

for p in \
    com.amazon.device.software.ota \
    com.amazon.device.software.ota.override \
    com.amazon.kindle.otter.oobe.forced.ota \
    com.amazon.dcp \
    com.amazon.otaverifier ; do
    if pm list packages 2>/dev/null | grep -q "^package:$p$"; then
        pm disable "$p" >/dev/null 2>&1 && echo "disabled $p" || echo "FAILED to disable $p"
    else
        echo "absent: $p"
    fi
done

# Belt and braces: the updater also reads these.
setprop persist.sys.ota.silent false 2>/dev/null
echo "ota.silent now: $(getprop persist.sys.ota.silent)"

echo "==== done ===="
