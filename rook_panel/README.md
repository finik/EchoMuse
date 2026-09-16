# rook_panel

The on-screen panel for the Echo Spot (`rook`). rook has no LED ring — it has a
480×480 round LCD — so this app owns the display and renders the ring EchoMuse
would otherwise light.

- Full-screen clock and date.
- A 12-segment ring, smoothly interpolated with cross-faded frames, driven by
  the firmware's existing `led.Controller` cues: listening, thinking spinner,
  speaking meter, mute, volume arc, link-state pulses.
- Declares `HOME` + `LAUNCHER`, so it holds the screen once Amazon's own launcher
  packages are hidden.

It receives 12-pixel frames as JSON over the abstract Unix socket
`@com.echomuse.rookpanel/state`, written by
`device/internal/bindings/led/panel_rook.go`. Abstract namespace means no
filesystem path and no permissions to arrange between a root daemon and an
Android app uid.

## Build

```bash
./build.sh            # aapt2 -> javac -> d8 -> zipalign -> apksigner
```

No Gradle: one manifest and one Java file, matching the `crown_launcher`
precedent. Set `ANDROID_SDK` if your SDK is not at
`/opt/homebrew/share/android-commandlinetools`. Compiles against the newest
installed platform but targets **API 22** — Fire OS 5 is Android 5.1, and
anything newer compiles cleanly then fails at runtime on the device.

`build.sh` generates a throwaway `debug.keystore` on first run; it is
gitignored, and the signature is irrelevant because the APK is installed as a
system app rather than upgraded in place.

## Install

Fire OS refuses ordinary sideloads (`INSTALL_FAILED_USER_RESTRICTED`), so
install as a system app and reboot so PackageManager scans it:

```bash
adb push rook_panel.apk /sdcard/
adb shell su -c 'mount -o rw,remount /system && \
  mkdir -p /system/app/RookPanel && \
  cp /sdcard/rook_panel.apk /system/app/RookPanel/RookPanel.apk && \
  chmod 644 /system/app/RookPanel/RookPanel.apk && reboot'
```

See `docs/rook-bringup.md` for the packages that must be hidden for this app to
keep the screen, and for the rest of the bring-up.

## ringtest.py

Drives the ring through each state over the socket, for checking rendering
without a live turn.
