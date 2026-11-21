package server

import (
	"log"
	"time"

	internalLed "github.com/wilbowes/EchoMuse/internal/bindings/led"
)

// Keeping Amazon's privacy driver in step with our mute, on kernels that have
// it (internal/bindings/led/privacy.go). Both toggle on every press, so they
// agree until one changes without the other, which a reboot while muted does:
// we restore muted, the driver boots unmuted, and from then on each press
// leaves them opposite — including a live mic under a lit mute button. Found
// on 15LE 2026-09-26.

type privacyAction int

const (
	privacyInSync privacyAction = iota
	// We are muted and the driver is not: tell it. Software can do this.
	privacyEnterDriver
	// The driver is muted and we are not. Software cannot take the driver
	// out, so mute ourselves: both then say muted and the mic is off, and
	// the next press unmutes both.
	privacyMuteOurs
)

func decidePrivacy(ours, driver bool) privacyAction {
	switch {
	case ours && !driver:
		return privacyEnterDriver
	case !ours && driver:
		return privacyMuteOurs
	}
	return privacyInSync
}

// privacySettle outlasts the driver's 300ms entry delay.
const privacySettle = 700 * time.Millisecond

// reconcilePrivacySoon checks the two agree once a press has settled.
//
// atBoot marks the single reconcile that runs at startup rather than after a
// button press. The driver's reading cannot be trusted there. One Spot (the
// Family Room unit, 2026-11-20) comes up with privacy_state=1 on every boot
// whatever the button was last left at — unmuted by hand at 03:08, reading 1
// again at the 07:29 boot — and privacy_trigger=0 is ignored, so nothing in
// software can clear it. The Bedroom Spot, same board and same firmware,
// reads 0: this is a latch one unit is stuck in, not how rook boots. Which
// is the point — a boot reading can be a stale latch, it cannot be told
// apart from a real one, and acting on it costs that unit its microphone
// for good.
//
// Believing it cost the user their microphone once per power cycle: the boot
// reconcile muted us to match, and that Toggle persists, so state.json came
// back muted for every boot after it. The Family Room Spot runs on a light
// switch, so that was every time the light went on.
func (m *muteController) reconcilePrivacySoon(atBoot bool) {
	if !internalLed.PrivacyDriver() {
		return
	}
	time.AfterFunc(privacySettle, func() { m.reconcilePrivacy(5, atBoot) })
}

func (m *muteController) reconcilePrivacy(retries int, atBoot bool) {
	if entering, err := internalLed.PrivacyEntering(); err == nil && entering && retries > 0 {
		time.AfterFunc(privacySettle, func() { m.reconcilePrivacy(retries-1, atBoot) })
		return
	}
	driver, err := internalLed.PrivacyMuted()
	if err != nil {
		log.Printf("Mute: %v", err)
		return
	}
	m.applyPrivacy(decidePrivacy(m.IsMuted(), driver), atBoot)
}

// applyPrivacy carries out a decision. Separate from the sysfs reads above so
// the boot rule can be tested without a privacy driver to read.
func (m *muteController) applyPrivacy(action privacyAction, atBoot bool) {
	switch action {
	case privacyEnterDriver:
		log.Println("Mute: privacy driver was unmuted while we are muted — muting it")
		if err := internalLed.EnterPrivacy(); err != nil {
			log.Printf("Mute: %v", err)
		}
	case privacyMuteOurs:
		if atBoot {
			// Not a privacy state anyone asked for — see reconcilePrivacySoon.
			log.Println("Mute: privacy driver reads muted at boot — ignoring, only a button press is trusted")
			return
		}
		log.Println("Mute: privacy driver is muted and cannot be unmuted from software — muting to match")
		if !m.IsMuted() {
			m.Toggle()
		}
	}
}
