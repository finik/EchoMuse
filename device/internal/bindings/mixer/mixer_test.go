package mixer

import "testing"

// Three layouts tinymix has shipped: tab-separated, space-padded columns, and a
// variant with no value column at all.
const tabbed = `Number of controls: 5
ctl	type	num	name	value
0	INT	1	PGA Gain	0
61	INT	2	DAC Playback Volume	100 100
62	INT	2	HP Driver Gain Volume	9 9
170	BOOL	1	ADC_D Right Ip Select ADC_D DIF1_R switch	Off
56	BOOL	1	Ext_Speaker_Amp_Switch	On
`

const padded = `Number of controls: 5
ctl   type     num name                                      value
0     INT      1   PGA Gain                                  0
61    INT      2   DAC Playback Volume                       100 100
62    INT      2   HP Driver Gain Volume                     9 9
170   BOOL     1   ADC_D Right Ip Select ADC_D DIF1_R switch Off
56    BOOL     1   Ext_Speaker_Amp_Switch                    On
`

const noValues = `0	INT	1	PGA Gain
61	INT	2	DAC Playback Volume
170	BOOL	1	ADC_D Right Ip Select ADC_D DIF1_R switch
`

func TestParseAcrossLayouts(t *testing.T) {
	for name, listing := range map[string]string{
		"tabbed": tabbed,
		"padded": padded,
	} {
		t.Run(name, func(t *testing.T) {
			m, err := parse(0, listing)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			for ctl, want := range map[string]int{
				"PGA Gain":              0,
				"DAC Playback Volume":   61,
				"HP Driver Gain Volume": 62,
				"ADC_D Right Ip Select ADC_D DIF1_R switch": 170,
				"Ext_Speaker_Amp_Switch":                    56,
			} {
				got, err := m.Index(ctl)
				if err != nil {
					t.Errorf("Index(%q): %v", ctl, err)
					continue
				}
				if got != want {
					t.Errorf("Index(%q) = %d, want %d", ctl, got, want)
				}
			}
		})
	}
}

func TestParseWithoutValueColumn(t *testing.T) {
	m, err := parse(0, noValues)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	got, err := m.Index("ADC_D Right Ip Select ADC_D DIF1_R switch")
	if err != nil {
		t.Fatalf("Index: %v", err)
	}
	if got != 170 {
		t.Errorf("Index = %d, want 170", got)
	}
}

// A name that is a strict prefix of another must not match it: that is the
// failure that silently writes to the wrong control.
func TestPrefixDoesNotMatch(t *testing.T) {
	const listing = `0	BOOL	1	ADC_A Left Mute	Off
1	BOOL	1	ADC_A Left Mute Switch	Off
`
	m, err := parse(0, listing)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if got, err := m.Index("ADC_A Left Mute"); err != nil || got != 0 {
		t.Errorf("Index(exact) = %d, %v; want 0, nil", got, err)
	}
	if got, err := m.Index("ADC_A Left Mute Switch"); err != nil || got != 1 {
		t.Errorf("Index(longer) = %d, %v; want 1, nil", got, err)
	}
}

func TestMissingControlNamesItself(t *testing.T) {
	m, err := parse(0, tabbed)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	_, err = m.Index("Nonexistent Control")
	if err == nil {
		t.Fatal("expected an error for a control that is not present")
	}
	if !contains(err.Error(), "Nonexistent Control") {
		t.Errorf("error should name the control it could not find, got: %v", err)
	}
}

func TestEmptyListingIsAnError(t *testing.T) {
	if _, err := parse(0, "Number of controls: 0\n"); err == nil {
		t.Fatal("expected an error for a listing with no controls")
	}
}

func contains(haystack, needle string) bool {
	return len(haystack) >= len(needle) && (func() bool {
		for i := 0; i+len(needle) <= len(haystack); i++ {
			if haystack[i:i+len(needle)] == needle {
				return true
			}
		}
		return false
	})()
}
