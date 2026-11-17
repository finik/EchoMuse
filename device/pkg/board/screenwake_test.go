package board

import (
	"os"
	"regexp"
	"strconv"
	"testing"
)

// The wake writes the backlight directly so the Spot is lit while the person
// is still speaking, and the dim script keeps it there for the hold. Both
// write the same sysfs node, so a disagreement would show as the screen
// flickering between two brightnesses every two seconds.
func TestTheDayBrightnessMatchesTheDimScript(t *testing.T) {
	src, err := os.ReadFile("../../scripts/03-rook-backlight.sh")
	if err != nil {
		t.Fatalf("cannot read the dim script: %v", err)
	}
	m := regexp.MustCompile(`(?m)^DAY=(\d+)`).FindSubmatch(src)
	if m == nil {
		t.Fatal("no DAY= in the dim script; this pin needs updating with it")
	}
	day, err := strconv.Atoi(string(m[1]))
	if err != nil {
		t.Fatalf("DAY is not a number: %v", err)
	}
	if rookHardware.ScreenDayBrightness != day {
		t.Errorf("rookHardware.ScreenDayBrightness = %d, the script's DAY = %d — "+
			"the two write the same node and would fight",
			rookHardware.ScreenDayBrightness, day)
	}
}

// The script owns the backlight path; the board must name the same one.
func TestTheBacklightPathMatchesTheDimScript(t *testing.T) {
	src, err := os.ReadFile("../../scripts/03-rook-backlight.sh")
	if err != nil {
		t.Fatalf("cannot read the dim script: %v", err)
	}
	m := regexp.MustCompile(`(?m)^BL=(\S+)`).FindSubmatch(src)
	if m == nil {
		t.Fatal("no BL= in the dim script; this pin needs updating with it")
	}
	if rookHardware.ScreenBacklight != string(m[1]) {
		t.Errorf("rookHardware.ScreenBacklight = %q, the script's BL = %q",
			rookHardware.ScreenBacklight, string(m[1]))
	}
}

// A board with no screen must not try to write one.
func TestBoardsWithoutAScreenWakeNothing(t *testing.T) {
	for name, hw := range map[string]*Hardware{"biscuit": biscuitHardware, "donut": donutHardware} {
		if hw.ScreenBacklight != "" || hw.ScreenDayBrightness != 0 {
			t.Errorf("%s names a backlight but has no screen to wake", name)
		}
	}
}
