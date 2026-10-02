// Package codec brings up the DAPM routes the audio path needs, instead of
// inheriting them from Amazon's audio HAL.
//
// Until 2026-09-04 nothing here existed, because nothing had to: on FireOS the
// HAL configures the codec long before our process opens a PCM, so both the
// microphone array and the speaker worked and we never learned we were relying
// on it. Running EchoMuse on a device with no Android userspace made the
// dependency visible in the least helpful way available — capture returned a
// steady rms≈0.00035 with a perfectly healthy ALSA clock (300.6s of audio over
// 300.3s of wall, zero stalls), and playback reported
// "voice stream complete, periods=35 underruns=0" while the room stayed silent.
//
// Both halves had the same cause. An ASoC route that is not connected leaves
// DAPM no reason to power the converter at either end of it, so the hardware is
// powered DOWN rather than merely misrouted. Read off the codec (i2c 2-0018) on
// a device with no Android, against a stock FireOS Dot running the same
// firmware:
//
//	          stock   ours     meaning
//	  0012      85      05     NADC clock divider, bit7 = powered
//	  0013      83      03     MADC clock divider
//	  0026      11      00     ADC flags: left+right converting
//	  003f      d6      16     DAC data path, bit7/6 = left/right powered
//	  0089      30      00     output driver power
//	  008c/8d   08      00     HPL/HPR output mixer routing
//
// Applying the routes below took every one of those to stock's exact value.
//
// This is written unconditionally, on FireOS as well, and that is deliberate:
// the values are the ones the HAL would set anyway, so a device that still has
// Android loses nothing, and one that does not gains a working audio path. The
// point of the project is not to need Amazon's userspace, and inheriting
// hardware state from it is a dependency whether or not it currently holds.
package codec

import (
	"log"
	"sync"

	"github.com/wilbowes/EchoMuse/internal/bindings/mixer"
	"github.com/wilbowes/EchoMuse/internal/profile"
)

// Write sets one mixer control, found by name.
type Write struct {
	Name  string
	Value string
}

// The route table below is every DAPM switch that must be closed for audio to
// flow.
//
// By NAME, never by control id (#546). These were ids until 2026-09-17, and on
// the FireOS 6 kernel every one of them named a different control: the eight
// capture writes closed the single-ended IN2 inputs, and the two playback
// writes hit input-mixer switches. Each was a valid write, so nothing failed.
//
// CAPTURE: the microphone array reaches the codec on the DIFFERENTIAL inputs,
// not the single-ended ones. Nothing routed DIF1 into any of the four ADCs, so
// all four sat powered down. Note the neighbouring "ADC_x DIF1_L/R Input Gain"
// controls are a DIFFERENT thing in the same register block and were the first
// thing tried; changing them does nothing, and they are not listed here.
//
// PLAYBACK: the DAC was not connected to the output mixer, so it powered down
// with the firmware streaming correctly into it.
// The capture half is generated from the board's converter list rather than
// written out A..D. biscuit's AIC32x4 has four ADCs; rook's AIC3101 has two,
// and writing C and D there fails — which is harmless to the audio but makes a
// healthy boot log "2 of 10 DAPM routes failed - audio may be silent", a
// warning that points at nothing. Amazon's own rook config sets exactly A and
// B (its audio_device.xml, read via techo5's firmware dump).
func captureRoutes() []Write { return captureRoutesFor(profile.Active().Mic.ADCs) }

// captureRoutesFor is split out so both boards' tables can be checked on a
// host, where there is no idme to detect a board from.
func captureRoutesFor(adcs []string) []Write {
	out := make([]Write, 0, len(adcs)*2)
	for i := len(adcs) - 1; i >= 0; i-- {
		a := adcs[i]
		out = append(out,
			Write{"ADC_" + a + " Right Ip Select ADC_" + a + " DIF1_R switch", "1"},
			Write{"ADC_" + a + " Left Ip Select ADC_" + a + " DIF1_L switch", "1"},
		)
	}
	return out
}

// playbackRoutes is the same on every board so far: one TLV320AIC32x4 DAC.
var playbackRoutes = []Write{
	{"HPR Output Mixer R_DAC Switch", "1"},
	{"HPL Output Mixer L_DAC Switch", "1"},
}

// Routes is every DAPM switch this board needs closed, capture then playback.
func Routes() []Write { return append(captureRoutes(), playbackRoutes...) }

var once sync.Once

// EnsureRoutes applies Routes exactly once per process.
//
// Called from both the microphone and the speaker Init, because either may run
// first and each needs the routes closed BEFORE it opens its PCM — DAPM decides
// what to power at stream open.
//
// A control that does not resolve is now a loud failure rather than a write to
// whatever happens to hold that id on this kernel.
func EnsureRoutes() {
	once.Do(func() {
		routes := Routes()
		var failed int
		for _, w := range routes {
			if err := mixer.Set(w.Name, w.Value); err != nil {
				failed++
			}
		}
		if failed > 0 {
			log.Printf("[codec] %d of %d DAPM routes failed — audio may be silent",
				failed, len(routes))
		} else {
			log.Printf("[codec] %d DAPM routes closed", len(routes))
		}
	})
}
