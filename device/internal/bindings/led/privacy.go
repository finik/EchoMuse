package led

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// Amazon's privacy driver, present on the FireOS 6 kernel (amz_priv.c, driven
// from kpd.c) and absent on FireOS 5's. Where it exists it owns gpio444 — the
// mute button LED, which it names amz_priv_trig — so the sysfs GPIO path above
// fails with EBUSY and the LED is the driver's to light.
//
// What the source says, confirmed on 15LE 2026-09-26:
//   - It toggles its own state on every mute-button release: entering waits
//     300ms (privacy_timer_on reads 1 meanwhile), leaving is immediate.
//   - Software can ENTER privacy (write 1 to privacy_trigger) and can never
//     leave it: "ignore exit privacy mode from software". Only the button
//     unmutes the LED.
//   - It always boots unmuted, whatever our persisted mute says.
//
// Found by name under /sys/devices/soc, never by the keypad's address.
// Never read power_button_state: it reads a mute GPIO biscuit's device tree
// does not define, and it blocked the console on read.
var (
	privacyOnce sync.Once
	privacyPath string
)

// Two layouts, because the driver is not packaged the same way on every board.
// biscuit's FireOS 6 kernel puts the attributes in an `amz_privacy` subnode;
// rook's FireOS 5 kernel exposes them DIRECTLY on the keypad
// (/sys/devices/soc/10010000.keypad/privacy_state), with no subnode.
//
// Matching only the first shape left PrivacyDriver() false on rook, so the
// reconciliation never ran there and the two mutes drifted apart — ours and
// Amazon's, each toggled by the same button press and neither aware of the
// other. One of them is then always muting the microphone, the red LED and
// the red ring disagree, and no sequence of presses clears both because a
// press moves them together. Seen on the Bedroom Spot 2026-10-03 and the
// Family Room 10-04.
//
// This also corrects a belief recorded elsewhere, that "FireOS 5's kernel has
// no such driver". True of biscuit, false of rook: a FireOS 5 board can have
// it. Which is why no Dot ever hit this — a FireOS 5 Dot genuinely has no
// second mute, so EchoMuse's own is the only one.
//
// Still resolved by GLOB rather than a fixed path: 10010000 is an address,
// not a name, and the resolve-by-name rule applies here as it does to event
// nodes and i2c.
func privacyDir() string {
	privacyOnce.Do(func() {
		for _, pat := range []string{
			"/sys/devices/soc/*/amz_privacy/privacy_state",
			"/sys/devices/soc/*/privacy_state",
		} {
			if matches, _ := filepath.Glob(pat); len(matches) > 0 {
				privacyPath = filepath.Dir(matches[0])
				return
			}
		}
	})
	return privacyPath
}

// PrivacyDriver reports whether Amazon's privacy driver owns the mute LED.
func PrivacyDriver() bool { return privacyDir() != "" }

func readPrivacyFlag(name string) (bool, error) {
	b, err := os.ReadFile(filepath.Join(privacyDir(), name))
	if err != nil {
		return false, fmt.Errorf("privacy driver: read %s: %w", name, err)
	}
	return strings.TrimSpace(string(b)) == "1", nil
}

// PrivacyMuted is the driver's own mute state (and so its LED).
func PrivacyMuted() (bool, error) { return readPrivacyFlag("privacy_state") }

// PrivacyEntering reports a button-started entry still in its 300ms wait.
func PrivacyEntering() (bool, error) { return readPrivacyFlag("privacy_timer_on") }

// EnterPrivacy puts the driver in its muted state, lighting the LED. There is
// no inverse.
func EnterPrivacy() error {
	if err := os.WriteFile(filepath.Join(privacyDir(), "privacy_trigger"), []byte("1"), 0644); err != nil {
		return fmt.Errorf("privacy driver: write privacy_trigger: %w", err)
	}
	return nil
}
