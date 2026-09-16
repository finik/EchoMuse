# Echo Spot 1st gen (`rook`) — hardware map and bring-up

Reference for running EchoMuse on an **Amazon Echo Spot 1st gen (2017, model
VN94DQ, codename `rook`)**: what the hardware is, how it differs from the Echo
Dot 2 (`biscuit`), and the procedure to bring up a unit.

Working on this board: wake word → Assist → spoken reply through the Spot's own
speaker, a clock on the round display with a ring that shows turn state, and the
mute and volume buttons driving EchoMuse rather than Android.

Every value below is measured on hardware. `SETUP.md` is the equivalent
reference for biscuit.

---

## 1. How rook differs from biscuit

rook is a **biscuit-shaped port**: same SoC family, same stock OS, same root
mechanism, so the existing compiler image and Magisk `service.d` autostart apply
unchanged, and none of crown's launcher-APK machinery is needed.

| | biscuit (Dot 2) | **rook (Spot)** |
|---|---|---|
| SoC | MT8163 | MT8163V |
| Stock OS | Fire OS 5 / Android 5.1 / API 22 | same |
| Kernel | 3.18 | same |
| Userspace | armv7a, `ro.zygote=zygote32` | same |
| Root | Magisk 17.3 + `service.d` | same |
| Playback codec | TLV320AIC32x4 | TLV320AIC32x4 |
| **Capture codec** | AIC32x4, 4 ADCs (A–D) | **AIC3101, 2 ADCs (A/B), 4 mics differential** |
| **Mic channels** | **9** — 6 perimeter, centre mic ch6, stereo loopback ch7/8 | **6** — 4 mics on ch0–3, ch4/5 idle |
| **Omni centre mic** | ch6 | **none** |
| **Hardware echo ref** | ch7/ch8 loopback | **none** |
| **Ring** | 12-LED i2c ring | **none — 480×480 round LCD** |
| Mute LED | sysfs gpio444 | none |
| Touchscreen | none | GT5668 |
| Camera | none | present, unused |

The consequences that drive the code:

- **6-channel capture, not 9.** Reading this stream with biscuit's 9-channel
  stride lands the frame boundary mid-sample. Nothing reports it as a channel
  mismatch.
- **No centre mic**, so the always-on wake stream needs a substitute. See §4.
- **No hardware echo reference.** `echoRefCh` is disabled rather than pointed at
  a channel that does not exist. ch4/ch5 are exact digital zero, not a loopback.
- **No LED ring**, so ring frames are rendered on the LCD by a panel app
  (`rook_panel/`) behind the existing `led.Controller` interface.
- **Same codec family, different control offsets.** The names match; the
  positional indices do not, because the mixer list concatenates machine-level
  and codec-level controls and rook's machine configuration is not biscuit's.
  Resolve by name.
- **None of the above is written into the code as a constant.** Channel counts,
  array geometry, the wake capsule and the button layout live in
  `internal/profile`, selected from `ro.product.device` at startup, so one binary
  serves both boards. An unrecognised board falls back to biscuit and logs it.

---

## 2. Hardware inventory

### Platform

| Property | Value |
|---|---|
| fastboot `product` | `ROOK` |
| Serial shape | `G070RQ…` |
| Display | 480×480 round, `hx8379c_dsi_…_rook` |
| Touchscreen | GT5668, `event5`/`event6`, reports `BTN_TOUCH` |
| Partitions | `boot` 16MB, `recovery` 16MB, `system` 1.75GB, `userdata` ~5.6GB |

### Audio

| Role | Value |
|---|---|
| Mic PCM | card 0, device 24 — **6ch fixed** (min=max=6), 16kHz, `S24_3LE`, period 257–2570, periods 1–10 |
| Speaker PCM | card 0, device 23 |
| Mic channels | ch0–ch3 are the capsules; ch4/ch5 are exact digital zero |
| Headphone jack | present, `CONFIG_MTK_AMZN_ACCDET=y` (same path as biscuit) |

Measured mixer indices. **Resolve these by name at runtime** — they are recorded
here for reference, not for hardcoding:

| Control | Index |
|---|---|
| `PCM Playback Volume` | 61, range **0–175** |
| `ADC_A Left/Right Mute` | 105 / 106 |
| `ADC_B Left/Right Mute` | 123 / 124 |
| `ADCFGA Left/Right Mute Switch` | 68 / 69 |
| `Mic PGA Switch` | 66 |
| `ADC_A/B MICPGA Volume Ctrl` | 92 / 110 |
| `ADC_A/B Digital Volume Control` | 89 / 107 |
| `ADC_A/B DIF1_L/R` capture routes | 136 / 143 / 152 / 159 |
| Speaker amp switch | 5 |
| `HP Driver Gain Volume` | 62 |

Two traps in that table:

- **Index 159 on rook is `ADC_A Left Ip Select ADC_A DIF1_L switch`** — a live
  capture route. On biscuit 159 is an ADC mute. Writing biscuit's mute value
  there tears down capture routing and leaves the device deaf, with nothing in
  any log.
- **`PCM Playback Volume` runs 0–175**, while biscuit's scaling assumes its own
  range and caps at 127.

### Volume ceiling: keep 127

`volumeMax = 127` is inherited from biscuit, where it is the codec's 0 dB unity
point, established by THD sweep. On rook it is **kept deliberately**, not
pending measurement.

Validated by listening rather than by sweep: dense, heavily-limited music
(near-full-scale material, which is the worst case — TTS sits well below full
scale and cannot exercise this) played at index 127 for 70 s, judged clean, with
1642 ALSA periods and zero underruns. At 127 the DAC applies no digital gain,
which is the whole reason the cap sits there: above unity, gain is applied to
already-full-scale samples and saturates inside the DAC.

**If more loudness is ever wanted, `Ext_Amp_Gain` (ctl 13) is the lever, not the
volume ceiling.** It is an analog stage after the DAC, currently at its lowest
setting of 6 dB with 12/18/24 dB available, so it adds level without consuming
digital headroom. Untested on this board. Note the same control is *inert* on
biscuit, so verify it does something here before relying on it.

Raising `volumeMax` above 127 is the one change on this board with a
hardware-damage downside, and it buys little: on biscuit THD went from 1.5% at
127 to 65% at 153, with output level flat above 153 because it had stopped being
able to get louder.

### MFP Gpio Mute is not a playback mute

`MFP Gpio Mute` (ctl 88) reads `On` both at idle and **during** playback, so
despite the name it does not gate audio on this board. It looks like an
amplifier mute and is not one.

### Gain stages

- **MICPGA (92/110) is the live analog stage, and 40 is the right value — not
  the maximum of 80.** 40 → 80 is a real +20.6 dB, taking the ADC from 1.2% to
  13.2% of full scale, and it makes wake detection substantially *worse*:

  | MICPGA | wake scores (same room, 15 min apart) | reported rms |
  |---|---|---|
  | **40** | 0.961 / 0.469 / 0.325 | ~0.0004 |
  | 80 | 0.296 / 0.326 / 0.271 | ~0.003 |

  This stage sits ahead of the converter, so overdriving it distorts without
  ever incrementing the digital clip counter: the audio gets louder and worse
  and nothing in any log says so. **Tune against wake-word scores, never
  against peak level or the clip count.** Set it in the per-device config as
  well as the startup script — the controller's push overwrites the script.
- **ADC digital volume (89/107) is inert on this board** — sweeping 88/64/32
  moves the floor under 2 dB. Note biscuit writes 88 to a control whose range
  here is 0–64.

### The ADCs come out of reset muted

`ADC_A/B Left/Right Mute = On`, `ADCFGA Left/Right Mute Switch = On`,
`Mic PGA Switch = Off`.

**A muted ADC on this part does not read as silence.** It reads as a healthy
capture stream at a flat −70 dBFS with no modulation — indistinguishable from a
quiet room in any level meter. Clearing the four mutes and enabling the PGA
yields 21.5 dB of speech modulation. The startup script does this before the
daemon starts.

### Buttons

Resolved **by name**, never by number: on rook `event2` is `hwmdata`, a sensor
stream that opens successfully and delivers events forever.

| Node | Name | Role |
|---|---|---|
| `event1` | `mtk-kpd` | mute/action button, reports **`KEY_POWER` (116)** — there is no mute key on this board |
| `event4` | `keys` | volume up/down (114/115) |
| `event5`/`event6` | GT5668 | touchscreen, unused |

**`EVIOCGRAB` both nodes.** Reading an evdev node does not consume its events,
so Android receives them too: without the grab the mute button reaches Android
as the power key and raises the keyguard over the panel, and the volume keys
raise Android's own slider.

---

## 3. Bring-up procedure

### 3.1 Unlock and root

1. **Unlock** with `amonet-rook-v2.0.0` (XDA). Option 1, `./fastbrick.sh`,
   device in fastboot. The bundled `fastboot` is a Linux x86-64 binary, so this
   needs a Linux VM — a UTM Ubuntu ARM64 guest with `qemu-user-static` +
   binfmt works. Extract the zip **inside** the VM.

   Expected after: `unlock_status: true`, `kaeru-version: 2.0.0`, preloader
   rolled back, `brom-cmd-dis: 0` — BROM/USBDL open, which is the brick
   recovery path.

   Button map afterwards: **Vol Down** → fastboot, **Vol Up** → TWRP,
   **Mute + Vol Down** → preloader USBDL.

2. **Escrow the boot image before anything else.**
   `dd if=/dev/block/mmcblk0p9 of=…`, and verify SHA256 on both sides. TWRP's
   `dd` rejects `bs=1M` — use `bs=1048576`, and never discard its stderr:
   absent record counts mean it did not run.

3. **Patch the boot image.** Unpack with Magisk 17.3's `arm/magiskboot`, set
   `default.prop` to `persist.sys.usb.config=adb`, `ro.adb.secure=0`,
   `ro.debuggable=1`, `ro.secure=0`, repack, then append
   `androidboot.selinux=permissive` to the cmdline on the host.

   `magiskboot` is a 32-bit binary needing `/system` mounted and
   `LD_LIBRARY_PATH=/system/lib`. Scope that per-invocation — exporting it
   globally breaks TWRP's own 64-bit tools.

4. **Flash, then install Magisk 17.3** via `twrp install`. Magisk's repack
   preserves the patched cmdline.

5. **Pre-seed root policy**: push a known `magisk.db` to `/data/adb/magisk.db`,
   mode 600, so `su` grants without an on-screen prompt.

### 3.2 Install

| Path | Contents |
|---|---|
| `/data/local/bin/server` | firmware |
| `/system/app/RookPanel/RookPanel.apk` | panel app |
| `/data/adb/service.d/` **and** `/sbin/.core/img/.core/service.d/` | the four startup scripts |

**Magisk 17.3 runs `service.d` from inside `magisk.img`**, at
`/sbin/.core/img/.core/service.d/`. Scripts placed only in
`/data/adb/service.d/` are silently ignored. Install to both.

Startup scripts:

- `00-rook-discovery.sh` — writes a hardware inventory to `/data/local/tmp`
- `01-rook-usb-adb.sh` — forces MUSB device mode and `adbd`; this is what keeps
  adb available across reboots
- `02-rook-block-ota.sh` — hides the OTA packages, since an OTA reflashes boot
- `50-start_server_rook.sh` — mixer setup by name, capture unmute, OOM
  protection, single-instance lock, supervision

**Fire OS refuses ordinary sideloads** (`INSTALL_FAILED_USER_RESTRICTED`; the
restriction is in Amazon's framework, not in user 0's record). Install APKs by
copying into `/system/app/<Name>/` and rebooting so PackageManager scans them.

### 3.3 Android configuration

Hide **exactly these two** packages:

```
pm hide com.amazon.kindle.otter.oobe    # language picker
pm hide com.amazon.paladin              # multimodal Alexa launcher
```

Use `pm hide`, not `pm disable` — Fire OS ignores disable for persistent system
apps. `com.amazon.paladin` only becomes visible once OOBE is gone.

**Do not apply the project's debloat list on this board.** It is validated for
biscuit; on rook it drops captured speech by roughly 25 dB and disables the wake
word. Only the two packages above are known safe.

The likely mechanism, and the rule it implies: on Fire OS, Amazon's audio
service configures the codec before EchoMuse takes it over — which is why
`50-start_server_rook.sh` waits for `echoaudio` to appear before starting the
daemon, and why biscuit's speaker init depends on `mediaserver` coming back.
`com.amazon.device.echoaudioservice` is present on rook. Removing an audio,
media or speech package therefore leaves the codec unconfigured, and the symptom
is a quiet microphone with no error anywhere.

So if further debloating is attempted: never touch anything matching
audio/media/speech/alexa, change **one** package at a time, and measure captured
speech level and a wake score after each — the failure mode is a level drop, not
a crash, so it will not announce itself. Note the culprit within the original
23-package batch was never isolated; only the batch as a whole is known bad.

Never `pm hide com.android.systemui` (it provides window-manager scaffolding;
hiding it leaves `mCurrentFocus=null` and a black screen) and never
`stop surfaceflinger` (`system_server` depends on it and the device reboots).

Keep the keyguard from ever triggering, which is what keeps the panel visible:

```
settings put global stay_on_while_plugged_in 7
```

This is sufficient on its own for a mains-powered device, and is what the
working unit relies on — `screen_off_timeout` is left at its stock 300000.
Raising the timeout as well is harmless but redundant.

### 3.3.1 Complete on-device change inventory

Everything altered on a brought-up unit, so a second one can be diffed against
it. Nothing else is modified; the other 135 packages are stock.

**Hidden** (`pm hide`, `hidden=true`, excluded from `pm list packages`):

| Package | Reason |
|---|---|
| `com.amazon.kindle.otter.oobe` | language picker, owns the screen |
| `com.amazon.paladin` | multimodal Alexa launcher, owns the screen |

**Disabled** (still listed by `pm list packages -d`):

| Package | Disabled by | Reason |
|---|---|---|
| `com.amazon.device.software.ota` | `02-rook-block-ota.sh` | an OTA reflashes boot and removes root |
| `com.amazon.device.software.ota.override` | `02-rook-block-ota.sh` | same |
| `com.amazon.comms.rooktachyon` | **by hand** | Alexa calling/comms |

`02-rook-block-ota.sh` also targets `com.amazon.kindle.otter.oobe.forced.ota`,
`com.amazon.dcp` and `com.amazon.otaverifier`; none are present on this build, so
it logs them as absent. `rooktachyon` is **not** in that script — it was disabled
manually, so a fresh unit will not have it disabled unless you do it again.

**Settings:** `global stay_on_while_plugged_in = 7`.

**Files added:** `/data/local/bin/server`,
`/system/app/RookPanel/RookPanel.apk`, the four scripts in both `service.d`
directories, `/data/adb/magisk.db`, and `/data/local/etc/echomuse/` (device
credentials and `state.json`).

Package counts on a correct unit: **138** listed, **140** installed, **3**
disabled, **2** hidden.

### 3.4 Controller

Create the device's config (see §5). Two fleet defaults must be overridden per
device:

- **`owwOnDevice` defaults to `on`** fleet-wide. A new device inherits it, has
  no ONNX models, and the controller stands down — nothing fires and only near
  misses accumulate. Set it to `off`.
- **`owwSpeexNs` must be `false`.** With `speexdsp_ns` absent from the
  controller's Python, `OWWModel(enable_speex_noise_suppression=True)` hangs, so
  the wake listener never reaches `mic_start` and the device goes deaf silently.
  Tell-tale: `OWW: loading model` with no following `OWW: starting`.

The controller **overwrites** mixer gains on every config push, so `adcMicpga`
and `micGainDb` must be right in its config or the startup script's values are
undone seconds after each connect.

Then add ESPHome in Home Assistant at `<controller-ip>:<device port>`.

### 3.5 Measure the wake capsule

Required per unit — see §4.

---

## 4. The wake capsule

biscuit takes the always-on wake stream from ch6, its omnidirectional centre
mic, because wake detection wants to hear the room rather than a direction. rook
has four perimeter capsules and no centre, and the two obvious substitutes are
both wrong:

- **Averaging all four is harmful.** The capsules are physically separated, so
  summing comb-filters the speech: 0.478 against 0.906 for the best single
  capsule on the same utterance.
- **Selecting by energy picks the loudest capsule, not the clearest.** The four
  sit within 0.5 dB of each other in level and SNR, so level carries no
  information about which one the classifier prefers.

So it is pinned by measurement. `beamformer.wakeCh` is the result. On this unit,
utterances that fire out of 5, across two recordings:

|  | rec1 | rec2 |
|---|---|---|
| ch0 | 2 | 2 |
| ch1 | 1 | 2 |
| ch2 | **3** | **4** |
| ch3 | 1 | 3 |
| max-of-4 | 3 | 5 |

ch2 matches max-of-4 without a protocol change. Deployed result: **9 of 10
recorded utterances fire**.

**`wakeCh` is a property of which capsule faces the room, so re-measure it per
unit and after moving a device.** Procedure:

```bash
python3 -m venv /tmp/owwenv && /tmp/owwenv/bin/pip install openwakeword
/tmp/owwenv/bin/python -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"
```

Stop the daemon (it holds the PCM exclusively), capture five utterances with
`device/tools/capture_mics`, then score each channel offline against the same
model and threshold the controller uses, and pin the winner.

`tinycap` cannot capture this device — it cannot request 3-byte-packed
`S24_3LE`.

Scoring offline beats live testing here: it answers "would this have fired?" per
channel, per filter, per gain and per threshold in seconds.

---

## 5. Working configuration

```json
{"adcMicpga": 40, "adcDigitalGain": 88, "micGainDb": 10, "startupVolume": 110,
 "owwThreshold": 0.25, "owwModel": "hey_jarvis_v0.1", "owwOnDevice": "off",
 "owwSpeexNs": false, "aecEnabled": false, "beamformingEnabled": true,
 "bargeInEnabled": true, "bargeInThreshold": 0.05, "saveUtterances": true}
```

Typical wake scores on this unit are 0.44–0.94, with marginal utterances near
0.25 — hence the threshold. Raising it to 0.35 loses the quiet ones.

---

## 6. Open items

- **Volume ceiling above 127 is unmeasured, but 127 itself is validated.** See
  §2's volume note — the inherited cap is kept deliberately.
- **The capsules' physical bearings are unmeasured**, so `CandidateAngles` in
  the profile are evenly-spaced placeholders. They affect the *reported*
  direction only — channel selection is by onset energy and is unaffected — and
  rook has no ring for a direction arc to paint on.
- **The wake capsule is fixed at BUILD time, and that is the weak point.**
  Both boards use a fixed channel for the always-on wake stream, so "hardcoded"
  is not the difference — biscuit hardcodes ch6. The difference is *why*: ch6 is
  its omnidirectional CENTRE mic, which is placement-independent by construction.
  rook's ch2 is a rim mic chosen by one measurement in one room, so there is no
  reason it stays best when the device moves. A board with no centre mic wants
  RUNTIME selection for this stream, and the criterion the beamformer already has
  does not work for it: onset energy picked ch0, the loudest, where ch2 scored
  roughly twice as well. Loudness is not clarity, and all four capsules sit within
  0.5dB of each other.

  Turn audio is unaffected and genuinely matches biscuit: selection is at runtime
  there, by onset ratio, locked for the turn.

  Summing the capsules is NOT the answer either. biscuit does not combine
  microphones; it selects. `device/CLAUDE.md` is
  explicit that this is settled — "a selector, not a summing beamformer, do not
  propose delay-and-sum" — because at a 72mm aperture diffuse-field noise is
  0.84-0.99 correlated below 1.5kHz, so a sum has nothing uncorrelated to cancel,
  and 36mm spacing aliases at 4.76kHz. A proper frequency-domain implementation
  measured only marginally better than selection.

  What is left, and is NOT beamforming: score several capsules in parallel and
  take the best score. Max-of-4 fired 8 of 10 against ch2's 7 of 10. It costs N
  times the inference and a protocol change (the wire carries one mono stream), so
  it is a real piece of work rather than a tweak.

  The project's own conclusion is worth carrying over: far-field reach here is not
  a beamforming problem, it is room noise floor, distance and placement. This
  unit's wake audio arrives at roughly the noise floor (rms 0.0026 against a floor
  of 0.0028), which says the same thing.
- **No audible wake cue.** rook has a speaker and no ring, so a short tone would
  suit it better than the Dot's LED-only indication.
- **Touchscreen unused.**
- **HA dashboard on the panel** is untested — Android 5.1's WebView against
  HA's modern frontend.
- **Panel keyguard flags** (`SHOW_WHEN_LOCKED` / `DISMISS_KEYGUARD`) do not take
  effect on the running window. Worked around by never letting the keyguard
  trigger (grabbed buttons, no screen sleep) rather than by defeating it.

---

## 7. Next unit, short form

1. Unlock with amonet-rook v2.0.0 from a Linux VM; escrow boot; verify SHA256.
2. Patch `default.prop` + permissive cmdline; flash; Magisk 17.3; seed
   `magisk.db`.
3. Install the four `service.d` scripts **into `magisk.img`**, the firmware to
   `/data/local/bin/server`, the panel APK to `/system/app/`; reboot.
4. `pm hide` the two Amazon UI packages; set the two screen settings.
5. Create the §5 config — especially `owwOnDevice: off` and
   `owwSpeexNs: false`.
6. Add ESPHome in HA at `<controller-ip>:<device port>`.
7. Measure `wakeCh` per §4 and rebuild.
