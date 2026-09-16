// Package profile describes the audio hardware of a supported device.
//
// Card/device numbers, channel counts and mic-array geometry were compile-time
// constants specific to the Echo Dot gen 2 ("biscuit"), scattered across the
// bindings and the beamformer. Supporting a second device means naming those
// values and selecting them at runtime, so one binary serves every board.
//
// Shape and field names deliberately mirror the profile package proposed for
// checkers (Echo Show 5) in PR #36, so the two can converge mechanically rather
// than becoming two competing abstractions. This version covers only what a
// second board demanded — the capture stream and the array geometry — and
// leaves that PR's ALSA backend, mixer-init sequences and wake-cue fields to
// it. biscuit stays on tinyalsa either way.
//
// A profile carries facts no kernel name can supply: how many channels the
// driver hands over, which of them are microphones, where the array's capsules
// point. Everything a name CAN answer is still resolved by name at the point of
// use — see internal/bindings/mixer and bindings/buttons. A profile must never
// become a second, staler source of truth for something probeable.
package profile

import (
	"log"
	"os/exec"
	"strings"
	"sync"
)

// Mic describes the capture stream.
type Mic struct {
	Card, Device int
	// Channels is what the driver hands over per frame, microphones and
	// anything else alike. Reading a stream with the wrong count is silent:
	// the frame stride lands mid-sample and every value is garbage.
	Channels   int
	SampleRate int
	PeriodSize int
	Periods    int

	// MicChannels are the channel indices carrying microphone audio.
	MicChannels []int
	// RefChannels carry a hardware loopback of the playback signal, suitable
	// as an AEC reference. Empty when the device provides no such feed.
	RefChannels []int

	// WakeChannel is the channel the always-on wake stream listens through.
	//
	// It is also the channel used whenever no direction is locked, so on a board
	// with an omnidirectional centre mic this IS that mic — biscuit's ch6 — and
	// wake detection hears the room rather than a direction. A board with no
	// centre capsule must pick one of its perimeter mics instead, and which one
	// is a MEASURED property of the device and its placement rather than a
	// geometric deduction; see the rook entry below.
	WakeChannel int
}

// FrameBytes is the size of one interleaved frame across all channels.
// S24_3LE throughout: three bytes per sample on every board so far.
func (m Mic) FrameBytes() int { return m.Channels * BytesPerSample }

// BytesPerSample is the capture sample width. S24_3LE on every supported
// board; a board that differs makes this a Mic field rather than a constant.
const BytesPerSample = 3

// Buttons describes how to find the physical controls, and what to do with
// them once found.
//
// Names come first and the numbered path is a fallback, because node numbers
// are not portable and opening the wrong one succeeds silently: on rook event2
// is `hwmdata`, a sensor stream that delivers events forever while no button
// works. A board whose names have not been read off real hardware keeps its
// known-good path and no name list, so nothing changes for it.
type Buttons struct {
	// ActionNames / VolumeNames are candidate device names, most specific
	// first. Empty means "use the fallback path only".
	ActionNames []string
	VolumeNames []string
	// ActionPath / VolumePath are used when no name matches.
	ActionPath string
	VolumePath string

	// MuteKeyCode is the evdev code the mute/action button reports, where it
	// is not the obvious one. rook has no mute key and reports KEY_POWER
	// (116); biscuit reports KEY_MUTE (113) and needs no translation. 0 means
	// no translation.
	MuteKeyCode int

	// HasMuteLED is true where a discrete LED sits under the mic-off button
	// (biscuit: sysfs gpio444, active-high). rook has none, and a board without
	// one must not log an export failure on every boot and every mute toggle:
	// noise that looks like a fault trains people to ignore the log.
	HasMuteLED bool

	// Grab takes the input devices exclusively (EVIOCGRAB), so Android does
	// not also act on the presses. Needed wherever an Android UI is running on
	// top: without it rook's action button reaches Android as the power key and
	// raises the keyguard over the panel, and the volume keys raise Android's
	// own slider. Off where the shipped behaviour has always been to share,
	// since taking a device exclusively is not a change to make untested.
	Grab bool
}

// Array describes the microphone array's geometry for direction estimation.
type Array struct {
	// CandidateAngles are the steering directions tested, in degrees clockwise
	// from 12 o'clock, one per steerable mic.
	CandidateAngles []float64
	// DirectionToChannel maps a CandidateAngles index to its ALSA channel.
	DirectionToChannel []int
}

// Directions is the number of candidate steering directions.
func (a Array) Directions() int { return len(a.CandidateAngles) }

// Profile is the hardware description for one device.
type Profile struct {
	// Name matches ro.product.device.
	Name string
	// Model is decorative — logs and dashboards only. Never branch on it;
	// branch on a capability or on a probe.
	Model string

	Mic     Mic
	Array   Array
	Buttons Buttons
}

// biscuit — Echo Dot gen 2. The values these replaced were constants in
// internal/beamformer and internal/bindings/mic; behaviour is unchanged.
//
// 9 channels: six perimeter mics on ch0-5, an omnidirectional centre mic on
// ch6, and a stereo loopback of the device's own playback on ch7/ch8. The
// driver emits the RIGHT channel only, so ch8 is the usable reference and ch7
// carries a signal the speaker never emitted.
var biscuit = &Profile{
	Name:  "biscuit",
	Model: "Echo Dot Gen 2 (biscuit)",
	Mic: Mic{
		Card: 0, Device: 24,
		Channels:    9,
		SampleRate:  16000,
		PeriodSize:  512,
		Periods:     5,
		MicChannels: []int{0, 1, 2, 3, 4, 5, 6},
		RefChannels: []int{8},
		WakeChannel: 6,
	},
	Array: Array{
		// 6 perimeter mics at r=36mm, 60° apart, 30° off 12 o'clock.
		CandidateAngles:    []float64{330, 30, 90, 150, 210, 270},
		DirectionToChannel: []int{0, 1, 2, 3, 4, 5},
	},
	// Deliberately the numbers this firmware has always used, with no name
	// list: biscuit's device names have not been read off hardware, and
	// guessing them risks opening the wrong node on the one board with a
	// fleet. Its mute button is KEY_MUTE (113) and needs no translation, and
	// sharing the devices with Android is the shipped behaviour.
	Buttons: Buttons{
		ActionPath: "/dev/input/event1",
		VolumePath: "/dev/input/event2",
		HasMuteLED: true,
	},
}

// rook — Echo Spot 1st gen (2017). Measured on hardware; see
// docs/rook-bringup.md.
//
// The capture codec is a TLV320AIC3101 with two ADCs taking four mics
// differentially, and the driver reports a FIXED six channels
// (`tinypcminfo -D 0 -d 24`: channels min=max=6). Four mics on ch0-3; ch4 and
// ch5 are exact digital zero, not a loopback, so there is no hardware AEC
// reference and no centre mic.
//
// WakeChannel is ch2 by MEASUREMENT, not geometry. The four capsules sit within
// 0.5dB of each other in level and SNR, so loudness says nothing about which
// one the classifier prefers. Utterances that fire out of 5, two recordings
// scored offline against the controller's own model and threshold:
//
//	         rec1  rec2
//	ch0        2     2
//	ch1        1     2
//	ch2        3     4      <- matches max-of-4 with no protocol change
//	ch3        1     3
//	max-of-4   3     5
//
// Two alternatives are worse and should not be reinstated: averaging the four
// comb-filters the speech, since the capsules are physically separated (0.478
// against 0.906 for the best capsule on one utterance); selecting by energy
// picks the loudest capsule rather than the clearest.
//
// This is a property of which capsule faces the room, so re-measure per unit and
// after moving a device — and that is the weakness. biscuit also takes its wake
// stream from a fixed channel, but ch6 is its omnidirectional CENTRE mic and so is
// placement-independent by construction; this is a rim mic picked in one room. A
// board with no centre mic really wants runtime selection here, and the criterion
// the beamformer uses for turns does not transfer: onset energy chose ch0, the
// loudest, where ch2 scored about twice as well.
//
// Do NOT "fix" this by summing the capsules. biscuit selects a single mic too,
// and that is settled with measurements behind it (see device/CLAUDE.md): at this
// aperture diffuse-field noise is 0.84-0.99 correlated below 1.5kHz, so a sum has
// nothing uncorrelated to cancel. Scoring several capsules in parallel and taking
// the best IS worth doing — max-of-4 fired 8 of 10 against ch2's 7 of 10 — but it
// costs N times the inference and a protocol change, since the wire carries one
// mono stream.
//
// The capsules' physical bearings are unmeasured, so CandidateAngles below are
// evenly-spaced placeholders: they affect the REPORTED direction only. Channel
// selection is by onset energy and is unaffected, and rook has no ring for a
// direction arc. Measuring the real bearings is an open item.
var rook = &Profile{
	Name:  "rook",
	Model: "Echo Spot Gen 1 (rook)",
	Mic: Mic{
		Card: 0, Device: 24,
		Channels:    6,
		SampleRate:  16000,
		PeriodSize:  512,
		Periods:     5,
		MicChannels: []int{0, 1, 2, 3},
		RefChannels: nil,
		WakeChannel: 2,
	},
	Array: Array{
		CandidateAngles:    []float64{0, 90, 180, 270},
		DirectionToChannel: []int{0, 1, 2, 3},
	},
	// event1 is "mtk-kpd" and carries the action button as KEY_POWER — there is
	// no mute key on this board. event4 is "keys" and carries volume. event2 is
	// `hwmdata`, a sensor stream, which is why these are resolved by name.
	Buttons: Buttons{
		ActionNames: []string{"mtk-kpd"},
		VolumeNames: []string{"keys"},
		ActionPath:  "/dev/input/event1",
		VolumePath:  "/dev/input/event4",
		MuteKeyCode: 116, // KEY_POWER
		Grab:        true,
	},
}

var profiles = map[string]*Profile{
	biscuit.Name: biscuit,
	rook.Name:    rook,
}

var (
	once   sync.Once
	active *Profile
)

// Active returns the profile for this device, memoised.
//
// Falls back to biscuit for an unrecognised board. That is deliberate: the
// existing fleet is biscuit, so an unknown name must behave exactly as this
// firmware did before profiles existed, rather than refusing to start. The
// fallback is logged, because a device silently running another board's
// geometry is the failure this package exists to prevent.
func Active() *Profile {
	once.Do(func() { active = forName(prop("ro.product.device")) })
	return active
}

// forName maps a ro.product.device value to a profile. Split out from Active so
// the mapping is testable without a device.
func forName(name string) *Profile {
	if p, ok := profiles[strings.TrimSpace(name)]; ok {
		return p
	}
	log.Printf("[profile] unknown board %q — using %s", name, biscuit.Name)
	return biscuit
}

// ByName returns a profile for tests and tools. nil when unknown.
func ByName(name string) *Profile { return profiles[name] }

// Names lists the supported boards.
func Names() []string {
	out := make([]string, 0, len(profiles))
	for n := range profiles {
		out = append(out, n)
	}
	return out
}

func prop(key string) string {
	out, err := exec.Command("getprop", key).Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}
