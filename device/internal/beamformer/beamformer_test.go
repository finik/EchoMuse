package beamformer

import (
	"testing"

	"github.com/wilbowes/EchoMuse/internal/profile"
)

// warmBeamformer returns a Beamformer for the named board with its baseline
// warmed up and a uniform noise floor, as if it had been running in a quiet
// room.
func warmBeamformer(t *testing.T, board string, baseline float64) *Beamformer {
	t.Helper()
	p := profile.ByName(board)
	if p == nil {
		t.Fatalf("no profile for %q", board)
	}
	b := NewFor(p)
	b.baselineReady = 100
	for di := 0; di < b.nDirections; di++ {
		b.energyBaseline[di] = baseline
	}
	return b
}

// TestLockBackPicksPastBurst is the scenario that motivated lock-back:
// the wake word was spoken ~1s ago from direction 2, the fast smoother has
// since decayed and (thanks to a TV) now points at direction 5. Live onset
// selection picks the TV; lock-back must pick the speaker.
func TestLockBackPicksPastBurst(t *testing.T) {
	b := warmBeamformer(t, "biscuit", 1e-6)

	// Fill the ring with baseline-level noise…
	for i := 0; i < historyPeriods; i++ {
		for di := 0; di < b.nDirections; di++ {
			b.energyHistory[i][di] = 1e-6
		}
	}
	b.historyCount = historyPeriods

	// …with a wake-word burst on direction 2, ~10 periods long, in the
	// middle of the window (well before "now").
	for i := 20; i < 30; i++ {
		b.energyHistory[i][2] = 5e-4
	}

	// TV on direction 5: elevated steady energy in both the ring and the
	// live smoother — loud in absolute terms, but not a burst relative to
	// its own baseline.
	b.energyBaseline[5] = 4e-4
	for i := 0; i < historyPeriods; i++ {
		b.energyHistory[i][5] = 5e-4
	}
	b.energySmooth[5] = 5e-4 // live smoother points at the TV
	b.energySmooth[2] = 2e-6 // speaker's onset has decayed

	b.Lock(true)

	if b.lockedChannel != b.directionToChannel[2] {
		t.Fatalf("lock-back picked ch%d, want ch%d (direction 2 burst)",
			b.lockedChannel, b.directionToChannel[2])
	}
}

// TestLockFallsBackToOnsetRatioWithoutHistory — fresh start: baseline warm
// (carried into the ready state quickly) but ring not yet populated. Must
// use the live onset ratio, not a zero-filled ring.
func TestLockFallsBackToOnsetRatioWithoutHistory(t *testing.T) {
	b := warmBeamformer(t, "biscuit", 1e-6)
	b.historyCount = 0
	b.energySmooth[4] = 3e-4 // live onset on direction 4

	b.Lock(true)

	if b.lockedChannel != b.directionToChannel[4] {
		t.Fatalf("fallback picked ch%d, want ch%d (live onset direction 4)",
			b.lockedChannel, b.directionToChannel[4])
	}
}

// TestLockDisabledIsNoOp — beamforming off must leave the channel unlocked
// (omni output path).
func TestLockDisabledIsNoOp(t *testing.T) {
	b := warmBeamformer(t, "biscuit", 1e-6)
	b.historyCount = historyPeriods
	b.energyHistory[0][3] = 1.0

	b.Lock(false)

	if b.lockedChannel != -1 {
		t.Fatalf("Lock(false) locked to ch%d, want unlocked (-1)", b.lockedChannel)
	}
}

// TestBurstRatioTopNMean checks the allocation-free partial selection:
// history 1..64 on direction 0 → top 8 are 57..64, mean 60.5.
func TestBurstRatioTopNMean(t *testing.T) {
	b := warmBeamformer(t, "biscuit", 1.0)
	for i := 0; i < historyPeriods; i++ {
		b.energyHistory[i][0] = float64(i + 1)
	}
	b.historyCount = historyPeriods

	got := b.burstRatio(0)
	want := 60.5 // mean of 57..64, baseline 1.0
	if got != want {
		t.Fatalf("burstRatio = %v, want %v", got, want)
	}
}

// TestBurstRatioPartialHistory — fewer samples than burstTopN averages what
// exists instead of diluting with zeros.
func TestBurstRatioPartialHistory(t *testing.T) {
	b := warmBeamformer(t, "biscuit", 1.0)
	b.energyHistory[0][0] = 4.0
	b.energyHistory[1][0] = 2.0
	b.historyCount = 2

	got := b.burstRatio(0)
	want := 3.0
	if got != want {
		t.Fatalf("burstRatio = %v, want %v", got, want)
	}
}

// ─── Board geometry ──────────────────────────────────────────────────────────

// The whole point of the profile: one binary, two boards, and the shipped one
// keeps the numbers it has always had.
func TestGeometryComesFromTheProfile(t *testing.T) {
	for _, tc := range []struct {
		board                string
		nChannels, frameSize int
		nDirections          int
		wakeCh, echoRef      int
	}{
		{"biscuit", 9, 27, 6, 6, 8},
		{"rook", 6, 18, 4, 2, -1},
	} {
		t.Run(tc.board, func(t *testing.T) {
			b := NewFor(profile.ByName(tc.board))
			if b.nChannels != tc.nChannels {
				t.Errorf("nChannels = %d, want %d", b.nChannels, tc.nChannels)
			}
			if b.frameSize != tc.frameSize {
				t.Errorf("frameSize = %d, want %d", b.frameSize, tc.frameSize)
			}
			if b.nDirections != tc.nDirections {
				t.Errorf("nDirections = %d, want %d", b.nDirections, tc.nDirections)
			}
			if b.wakeCh != tc.wakeCh {
				t.Errorf("wakeCh = %d, want %d", b.wakeCh, tc.wakeCh)
			}
			if b.echoRefCh != tc.echoRef {
				t.Errorf("echoRefCh = %d, want %d", b.echoRefCh, tc.echoRef)
			}
			// Per-direction state must be sized for THIS board. A short slice
			// panics on the mic goroutine; a long one silently averages a
			// direction that does not exist.
			if len(b.energySmooth) != tc.nDirections ||
				len(b.energyBaseline) != tc.nDirections ||
				len(b.chanBuf) != tc.nDirections ||
				len(b.hfBuf) != tc.nDirections {
				t.Errorf("per-direction state mis-sized: smooth=%d baseline=%d chan=%d hf=%d, want %d",
					len(b.energySmooth), len(b.energyBaseline), len(b.chanBuf), len(b.hfBuf), tc.nDirections)
			}
			for i := range b.energyHistory {
				if len(b.energyHistory[i]) != tc.nDirections {
					t.Fatalf("energyHistory[%d] has %d directions, want %d",
						i, len(b.energyHistory[i]), tc.nDirections)
				}
			}
		})
	}
}

// ─── Hardware echo reference (#385) ──────────────────────────────────────────

// rawPeriod builds one period of S24_3LE for b's frame size, with a
// per-channel constant so each channel is identifiable by value alone.
func rawPeriod(b *Beamformer, frames int, valueFor func(ch int) int32) []byte {
	buf := make([]byte, frames*b.frameSize)
	for f := 0; f < frames; f++ {
		for ch := 0; ch < b.nChannels; ch++ {
			v := valueFor(ch)
			i := f*b.frameSize + ch*byteSample
			buf[i] = byte(v)
			buf[i+1] = byte(v >> 8)
			buf[i+2] = byte(v >> 16)
		}
	}
	return buf
}

func TestEchoRefReadsTheReferenceChannel(t *testing.T) {
	b := NewFor(profile.ByName("biscuit"))
	raw := rawPeriod(b, periodFrames, func(ch int) int32 {
		if ch == b.echoRefCh {
			return 0x200000 // +2097152 of 2^23 → 8192 after the 24→16 shift
		}
		return int32(ch) << 12
	})
	out := b.EchoRef(raw)
	if len(out) != periodFrames*2 {
		t.Fatalf("expected %d bytes, got %d", periodFrames*2, len(out))
	}
	got := int16(uint16(out[0]) | uint16(out[1])<<8)
	if got != 8192 {
		t.Fatalf("EchoRef read the wrong channel or gain: got %d, want 8192", got)
	}
}

// TestEchoRefIsUnityGain is the one that matters. Mic extraction applies
// micGainDb (+24dB by default) pre-truncation because speech sits at about
// -70dBFS. The reference is the playback stream at full digital scale — it
// measured -7.3dBFS on hardware — and the same gain on that is 17dB of hard
// clipping, which does not merely cancel badly: it teaches the adaptive
// filter a distorted echo path.
func TestEchoRefIsUnityGain(t *testing.T) {
	b := NewFor(profile.ByName("biscuit"))
	raw := rawPeriod(b, periodFrames, func(ch int) int32 {
		if ch == b.echoRefCh {
			return 0x7F0000 // 8323072 — close to the 2^23 ceiling
		}
		return 0
	})
	out := b.EchoRef(raw)
	got := int16(uint16(out[0]) | uint16(out[1])<<8)
	if got == 32767 || got == -32768 {
		t.Fatalf("reference clipped at %d — EchoRef must extract at unity gain", got)
	}
	if b.ClippedSamples() != 0 {
		t.Fatalf("reference extraction clipped %d samples", b.ClippedSamples())
	}
}

func TestEchoRefRejectsShortBuffer(t *testing.T) {
	b := NewFor(profile.ByName("biscuit"))
	if out := b.EchoRef(make([]byte, b.frameSize-1)); out != nil {
		t.Fatal("a short period must report no reference, not a partial one")
	}
}

// A board with no loopback must report no reference rather than reading a
// channel that does not exist — on rook that index would be past the frame.
func TestEchoRefAbsentOnBoardWithoutLoopback(t *testing.T) {
	b := NewFor(profile.ByName("rook"))
	raw := rawPeriod(b, periodFrames, func(ch int) int32 { return int32(ch) << 12 })
	if out := b.EchoRef(raw); out != nil {
		t.Fatalf("board has no reference channel; EchoRef returned %d bytes", len(out))
	}
}

// The wake stream takes the profile's measured capsule, not a hardcoded one.
func TestWakeSelectUsesTheProfileChannel(t *testing.T) {
	for _, board := range []string{"biscuit", "rook"} {
		t.Run(board, func(t *testing.T) {
			b := NewFor(profile.ByName(board))
			// Only the wake channel carries signal; everything else is silent.
			raw := rawPeriod(b, periodFrames, func(ch int) int32 {
				if ch == b.wakeCh {
					return 0x080000
				}
				return 0
			})
			out := b.wakeSelect(raw, 1.0)
			got := int16(uint16(out[0]) | uint16(out[1])<<8)
			if got == 0 {
				t.Fatalf("wake stream is silent — it is not reading ch%d", b.wakeCh)
			}
		})
	}
}
