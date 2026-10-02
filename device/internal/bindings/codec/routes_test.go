package codec

import "testing"

// A wrong name here is silence rather than an error, and the two ends failed
// independently: the capture routes leave the ADCs powered down, the playback
// routes leave the DAC powered down, and either alone is a device that looks
// healthy in every log it writes.
//
// Both boards are checked by name rather than through profile.Active(), since
// a host has no idme to be detected from and would always see the fallback.
func TestCaptureRoutesCoverEveryConverterAndNoOther(t *testing.T) {
	cases := []struct {
		board string
		adcs  []string
		want  []string
	}{{
		// biscuit — AIC32x4, four ADCs. The DIFFERENTIAL inputs: the
		// single-ended "IN2" switches beside them are the wrong ones.
		board: "biscuit",
		adcs:  []string{"A", "B", "C", "D"},
		want: []string{
			"ADC_A Left Ip Select ADC_A DIF1_L switch",
			"ADC_A Right Ip Select ADC_A DIF1_R switch",
			"ADC_B Left Ip Select ADC_B DIF1_L switch",
			"ADC_B Right Ip Select ADC_B DIF1_R switch",
			"ADC_C Left Ip Select ADC_C DIF1_L switch",
			"ADC_C Right Ip Select ADC_C DIF1_R switch",
			"ADC_D Left Ip Select ADC_D DIF1_L switch",
			"ADC_D Right Ip Select ADC_D DIF1_R switch",
		},
	}, {
		// rook — AIC3101, two ADCs. C and D do not exist; writing them is
		// what made a healthy boot warn that audio may be silent.
		board: "rook",
		adcs:  []string{"A", "B"},
		want: []string{
			"ADC_A Left Ip Select ADC_A DIF1_L switch",
			"ADC_A Right Ip Select ADC_A DIF1_R switch",
			"ADC_B Left Ip Select ADC_B DIF1_L switch",
			"ADC_B Right Ip Select ADC_B DIF1_R switch",
		},
	}}

	for _, c := range cases {
		got := map[string]bool{}
		for _, w := range captureRoutesFor(c.adcs) {
			if got[w.Name] {
				t.Errorf("%s: %s listed twice", c.board, w.Name)
			}
			if w.Value != "1" {
				t.Errorf("%s: %s value %q, want \"1\" — every route here is a switch to close",
					c.board, w.Name, w.Value)
			}
			got[w.Name] = true
		}
		want := map[string]bool{}
		for _, n := range c.want {
			want[n] = true
			if !got[n] {
				t.Errorf("%s: missing %s", c.board, n)
			}
		}
		for n := range got {
			if !want[n] {
				t.Errorf("%s: unexpected %s", c.board, n)
			}
		}
	}
}

// The DAC half is board-independent so far, and a capture-only table is a
// device that records fine and never speaks.
func TestPlaybackRoutesConnectTheDac(t *testing.T) {
	want := map[string]bool{
		"HPR Output Mixer R_DAC Switch": true,
		"HPL Output Mixer L_DAC Switch": true,
	}
	if len(playbackRoutes) != len(want) {
		t.Fatalf("playbackRoutes has %d entries, want %d", len(playbackRoutes), len(want))
	}
	for _, w := range playbackRoutes {
		if !want[w.Name] {
			t.Errorf("unexpected %s", w.Name)
		}
		if w.Value != "1" {
			t.Errorf("%s: value %q, want \"1\"", w.Name, w.Value)
		}
	}
}
