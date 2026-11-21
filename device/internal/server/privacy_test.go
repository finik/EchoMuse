package server

import "testing"

func TestDecidePrivacyAlwaysLandsOnMuted(t *testing.T) {
	cases := []struct {
		ours, driver bool
		want         privacyAction
	}{
		{false, false, privacyInSync},
		{true, true, privacyInSync},
		// A reboot while muted: we restore muted, the driver boots unmuted.
		{true, false, privacyEnterDriver},
		// Opposite states the other way round: a lit mute button over a live
		// mic. Software cannot unmute the driver, so we mute.
		{false, true, privacyMuteOurs},
	}
	for _, c := range cases {
		if got := decidePrivacy(c.ours, c.driver); got != c.want {
			t.Errorf("decidePrivacy(ours=%v, driver=%v) = %v, want %v", c.ours, c.driver, got, c.want)
		}
	}
}

// One Spot comes up with privacy_state=1 on every boot whatever the button
// was last left at, and the state cannot be cleared from software. Trusting
// it muted the microphone once per power cycle — and persisted, so it stayed
// muted. Measured on the Family Room Spot 2026-11-20: unmuted by button at
// 03:08, `privacy driver is muted … muting to match` at the 07:29 boot.
func TestABootTimeDriverMuteIsIgnored(t *testing.T) {
	m := newMuteController(nil, nil)
	persisted := 0
	m.persist = func() { persisted++ }

	m.applyPrivacy(privacyMuteOurs, true)

	if m.IsMuted() {
		t.Error("the driver's boot reading muted the microphone")
	}
	if persisted != 0 {
		t.Errorf("a boot reading wrote state.json %d times", persisted)
	}
}
