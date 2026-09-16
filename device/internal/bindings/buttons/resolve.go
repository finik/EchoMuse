package buttons

import (
	"bufio"
	"fmt"
	"os"
	"regexp"
	"strings"
)

// resolveByName finds /dev/input/eventN by the device's NAME in
// /proc/bus/input/devices. Event numbers are not portable and opening the wrong
// node succeeds silently, leaving the buttons dead: event2 is the volume buttons
// on biscuit, the `hwmdata` sensor stream on rook, and the touchscreen on
// checkers.
//
// Measured layouts:
//
//	biscuit  event1 = dot/mute+action     event2 = volume
//	rook     event1 = "mtk-kpd"  KEY_POWER(0x74) — the action button
//	         event4 = "keys"     VOLUMEUP/DOWN (0x72/0x73)
//	         event5/6 = touchscreen (BTN_TOUCH)
func resolveByName(names ...string) (string, error) {
	f, err := os.Open("/proc/bus/input/devices")
	if err != nil {
		return "", fmt.Errorf("open /proc/bus/input/devices: %w", err)
	}
	defer f.Close()

	var (
		curName string
		seen    []string
	)
	evRe := regexp.MustCompile(`event(\d+)`)

	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		switch {
		case strings.HasPrefix(line, "N: Name="):
			curName = strings.Trim(strings.TrimPrefix(line, "N: Name="), `"`)
			seen = append(seen, curName)
		case strings.HasPrefix(line, "H: Handlers="):
			m := evRe.FindString(line)
			if m == "" || curName == "" {
				continue
			}
			for _, want := range names {
				if strings.EqualFold(curName, want) {
					return "/dev/input/" + m, nil
				}
			}
		}
	}
	if err := sc.Err(); err != nil {
		return "", fmt.Errorf("read /proc/bus/input/devices: %w", err)
	}
	// Name what the board does offer.
	return "", fmt.Errorf("no input device named any of %v (board has: %s)",
		names, strings.Join(seen, ", "))
}
