package profile

import "testing"

// An unknown board must behave exactly as this firmware did before profiles
// existed: biscuit's geometry, not a refusal to start and not an empty profile.
func TestUnknownBoardFallsBackToBiscuit(t *testing.T) {
	for _, name := range []string{"", "crown", "checkers", "  ", "BISCUIT"} {
		if got := forName(name); got != biscuit {
			t.Fatalf("forName(%q) = %v, want biscuit", name, got.Name)
		}
	}
}

func TestKnownBoardsResolve(t *testing.T) {
	if got := forName("rook"); got != rook {
		t.Fatalf("forName(rook) = %s, want rook", got.Name)
	}
	if got := forName(" biscuit "); got != biscuit {
		t.Fatalf("whitespace should be trimmed, got %s", got.Name)
	}
}

// The values that used to be constants. Pinned so a future edit to the profile
// cannot silently change the shipped board's behaviour — the regression this
// package exists to prevent.
func TestBiscuitMatchesTheConstantsItReplaced(t *testing.T) {
	m, a := biscuit.Mic, biscuit.Array
	if m.Channels != 9 {
		t.Errorf("channels = %d, want 9", m.Channels)
	}
	if m.FrameBytes() != 27 {
		t.Errorf("frame = %d bytes, want 27", m.FrameBytes())
	}
	if m.WakeChannel != 6 {
		t.Errorf("wake channel = %d, want 6 (centre mic)", m.WakeChannel)
	}
	if a.Directions() != 6 {
		t.Errorf("directions = %d, want 6", a.Directions())
	}
	if len(m.RefChannels) != 1 || m.RefChannels[0] != 8 {
		t.Errorf("ref channels = %v, want [8] — the driver emits the right channel only", m.RefChannels)
	}
	if m.Card != 0 || m.Device != 24 {
		t.Errorf("mic pcm = card %d device %d, want 0/24", m.Card, m.Device)
	}
}

func TestRookGeometry(t *testing.T) {
	m := rook.Mic
	if m.Channels != 6 {
		t.Errorf("channels = %d, want 6 (driver reports min=max=6)", m.Channels)
	}
	if m.FrameBytes() != 18 {
		t.Errorf("frame = %d bytes, want 18", m.FrameBytes())
	}
	if m.WakeChannel != 2 {
		t.Errorf("wake channel = %d, want 2 (measured, not geometric)", m.WakeChannel)
	}
	if len(m.RefChannels) != 0 {
		t.Errorf("ref channels = %v, want none — ch4/ch5 are digital zero", m.RefChannels)
	}
}

// Every profile has to be internally consistent, whoever adds it. Each of these
// is a silent failure on hardware rather than a crash.
func TestProfilesAreSelfConsistent(t *testing.T) {
	for name, p := range profiles {
		t.Run(name, func(t *testing.T) {
			if p.Name != name {
				t.Errorf("keyed as %q but Name is %q", name, p.Name)
			}
			if p.Mic.Channels <= 0 {
				t.Fatalf("channels = %d", p.Mic.Channels)
			}
			if len(p.Array.CandidateAngles) != len(p.Array.DirectionToChannel) {
				t.Fatalf("%d angles but %d channel mappings",
					len(p.Array.CandidateAngles), len(p.Array.DirectionToChannel))
			}
			// Every channel referenced must exist in the stream. Indexing past
			// the frame reads another channel's sample, or past the buffer.
			check := func(what string, chs ...int) {
				for _, ch := range chs {
					if ch < 0 {
						continue // -1 means "absent", legitimate
					}
					if ch >= p.Mic.Channels {
						t.Errorf("%s = ch%d but the stream has %d channels", what, ch, p.Mic.Channels)
					}
				}
			}
			check("wake channel", p.Mic.WakeChannel)
			check("mic channels", p.Mic.MicChannels...)
			check("ref channels", p.Mic.RefChannels...)
			check("direction channels", p.Array.DirectionToChannel...)

			// A reference channel that is also a microphone would cancel the
			// near end against itself.
			for _, r := range p.Mic.RefChannels {
				for _, m := range p.Mic.MicChannels {
					if r == m {
						t.Errorf("ch%d is listed as both a mic and an AEC reference", r)
					}
				}
			}
			// The wake channel must be a microphone, not a reference or an
			// idle channel: scoring the wake word on silence is a deaf device.
			found := false
			for _, m := range p.Mic.MicChannels {
				if m == p.Mic.WakeChannel {
					found = true
				}
			}
			if !found {
				t.Errorf("wake channel ch%d is not in MicChannels %v",
					p.Mic.WakeChannel, p.Mic.MicChannels)
			}
		})
	}
}

// biscuit's button handling must be byte-for-byte what it has always been:
// the numbered nodes, no name guessing, no exclusive grab, no key translation.
// Every one of those would be an untested change to the only board with a fleet.
func TestBiscuitButtonsAreUnchanged(t *testing.T) {
	b := biscuit.Buttons
	if b.ActionPath != "/dev/input/event1" || b.VolumePath != "/dev/input/event2" {
		t.Errorf("paths = %s / %s, want event1 / event2", b.ActionPath, b.VolumePath)
	}
	if len(b.ActionNames) != 0 || len(b.VolumeNames) != 0 {
		t.Errorf("biscuit must not guess device names, got %v / %v", b.ActionNames, b.VolumeNames)
	}
	if b.Grab {
		t.Error("biscuit must not take the input devices exclusively — shipped behaviour is to share")
	}
	if b.MuteKeyCode != 0 {
		t.Errorf("MuteKeyCode = %d, want 0: biscuit reports KEY_MUTE and needs no translation", b.MuteKeyCode)
	}
}

func TestRookButtonsResolveByName(t *testing.T) {
	b := rook.Buttons
	if len(b.ActionNames) == 0 || len(b.VolumeNames) == 0 {
		t.Fatal("rook must resolve by name: event2 is a sensor stream that opens fine and never fires")
	}
	if b.VolumePath != "/dev/input/event4" {
		t.Errorf("volume fallback = %s, want event4", b.VolumePath)
	}
	if b.MuteKeyCode != 116 {
		t.Errorf("MuteKeyCode = %d, want 116 (KEY_POWER — no mute key on this board)", b.MuteKeyCode)
	}
	if !b.Grab {
		t.Error("rook must grab: otherwise Android sees the power key and raises the keyguard over the panel")
	}
}

// biscuit has a discrete LED under its mute button and must keep driving it;
// rook has none and must not try, or it logs a fault every boot and toggle.
func TestMuteLEDIsPerBoard(t *testing.T) {
	if !biscuit.Buttons.HasMuteLED {
		t.Error("biscuit has an LED under the mute button (gpio444) — it must still be driven")
	}
	if rook.Buttons.HasMuteLED {
		t.Error("rook has no mute-button LED; attempting it logs a fault every boot")
	}
}
