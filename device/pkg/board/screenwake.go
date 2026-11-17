package board

import (
	"os"
	"strconv"
	"time"
)

// WakeScreen records that day brightness should hold, for boards whose screen
// dims itself overnight (Hardware.ScreenWakeFile).
//
// The Spot's night dim is a root script polling that file; the panel writes it
// on a tap. Anything the person does to the device means the same thing as a
// tap — pressing a button, or saying the wake word — and a device that stays
// dark while it is listening to you reads as broken.
//
// Best effort by design: a screen that failed to brighten must never cost a
// button press or a wake word. Boards with no screen to wake do nothing.
func WakeScreen() {
	l := CurrentLayout()
	if l == nil || l.ScreenWakeFile == "" {
		return
	}
	until := time.Now().UnixMilli() + l.ScreenWakeMs
	// The hold first: the dim script must already agree before the backlight
	// is touched, or its next pass would undo this write.
	_ = os.WriteFile(l.ScreenWakeFile, []byte(strconv.FormatInt(until, 10)), 0o644)
	// Then the backlight itself. The script polls the hold every two seconds,
	// which is two seconds of darkness while the person is already speaking.
	// Writing the node here makes it immediate; the script then just keeps it
	// there for the length of the hold.
	if l.ScreenBacklight != "" && l.ScreenDayBrightness > 0 {
		_ = os.WriteFile(l.ScreenBacklight,
			[]byte(strconv.Itoa(l.ScreenDayBrightness)), 0o644)
	}
}
