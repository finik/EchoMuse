// Package mixer resolves tinymix control NAMES to the positional indices
// tinymix takes on the command line.
//
// tinymix addresses controls by position (`tinymix -D 0 61` is the DAC volume
// on biscuit), but the positional list concatenates the machine driver's
// controls with the codec's: names belong to the codec driver, indices to the
// whole sound card. So two boards can run the same codec and enumerate it at
// different offsets — biscuit (Echo Dot 2) and rook (Echo Spot) both carry a
// TLV320AIC32x4. Resolve by name at startup and fail with the name that could
// not be found; writing to the wrong control is silent.
//
// Format tolerance is required: tinymix's listing has shipped with several
// column layouts and Amazon builds its own. Only two things every layout agrees
// on are parsed — the line begins with the control's index, and the name
// appears in the line. Matching is on the name as a whole word-sequence,
// anchored so "ADC_A Left Mute" does not match "ADC_A Left Mute Switch".
package mixer

import (
	"bufio"
	"fmt"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"sync"
)

// leadingIndex matches the control index at the start of a listing line.
var leadingIndex = regexp.MustCompile(`^\s*(\d+)\b`)

// Mixer is a parsed tinymix control listing for one card.
type Mixer struct {
	card   int
	mu     sync.Mutex
	byName map[string]int
	names  []string // every name seen, for error messages
}

// Load runs `tinymix -D <card>` and parses its control listing.
func Load(card int) (*Mixer, error) {
	out, err := exec.Command("tinymix", "-D", strconv.Itoa(card)).Output()
	if err != nil {
		return nil, fmt.Errorf("tinymix -D %d: %w", card, err)
	}
	return parse(card, string(out))
}

// parse is split out from Load so the format tolerance is testable without a
// device present.
func parse(card int, listing string) (*Mixer, error) {
	m := &Mixer{card: card, byName: map[string]int{}}

	sc := bufio.NewScanner(strings.NewReader(listing))
	for sc.Scan() {
		line := sc.Text()
		match := leadingIndex.FindStringSubmatch(line)
		if match == nil {
			continue // header lines ("Number of controls: 236", column titles)
		}
		idx, err := strconv.Atoi(match[1])
		if err != nil {
			continue
		}
		name := extractName(line[len(match[0]):])
		if name == "" {
			continue
		}
		// First occurrence wins on a duplicate name.
		if _, seen := m.byName[name]; !seen {
			m.byName[name] = idx
			m.names = append(m.names, name)
		}
	}
	if err := sc.Err(); err != nil {
		return nil, fmt.Errorf("reading tinymix listing: %w", err)
	}
	if len(m.byName) == 0 {
		return nil, fmt.Errorf("tinymix listing had no parseable controls")
	}
	return m, nil
}

// Control names contain spaces and underscores ("ADC_A Digital Volume
// Control"), so whitespace splitting cannot delimit them. Every known layout
// puts two short fields between the index and the name — the type and the value
// count — and the current value after it: drop the type and count, then keep
// tokens until one looks like a value rather than a word.
var (
	typeToken  = regexp.MustCompile(`^(INT|BOOL|ENUM|BYTE|IEC958|INT64)$`)
	valueToken = regexp.MustCompile(`^(\d+|On|Off|-?\d+dB.*|0x[0-9a-fA-F]+)$`)
)

func extractName(rest string) string {
	fields := strings.Fields(rest)
	i := 0
	if i < len(fields) && typeToken.MatchString(fields[i]) {
		i++ // the type
		if i < len(fields) {
			if _, err := strconv.Atoi(fields[i]); err == nil {
				i++ // the value count
			}
		}
	}
	var name []string
	for ; i < len(fields); i++ {
		// Stop at the first token that reads as the control's value, but only
		// after one name token has been taken: a name may start with a
		// digit-ish token.
		if len(name) > 0 && valueToken.MatchString(fields[i]) {
			break
		}
		name = append(name, fields[i])
	}
	return strings.Join(name, " ")
}

// Index returns the positional index of the named control.
func (m *Mixer) Index(name string) (int, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if idx, ok := m.byName[name]; ok {
		return idx, nil
	}
	return 0, fmt.Errorf("mixer control %q not found on card %d (%d controls listed)",
		name, m.card, len(m.byName))
}

// Names returns every control name parsed, in listing order, for logging an
// unmatched control against what the board offers.
func (m *Mixer) Names() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]string(nil), m.names...)
}

// Set writes values to the named control, resolving its index first.
func (m *Mixer) Set(name string, values ...string) error {
	idx, err := m.Index(name)
	if err != nil {
		return err
	}
	args := append([]string{"-D", strconv.Itoa(m.card), strconv.Itoa(idx)}, values...)
	if out, err := exec.Command("tinymix", args...).CombinedOutput(); err != nil {
		return fmt.Errorf("tinymix %s (%q): %w: %s",
			strings.Join(args, " "), name, err, strings.TrimSpace(string(out)))
	}
	return nil
}
