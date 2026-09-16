package codec

import (
	"fmt"
	"log"
	"sync"

	"github.com/wilbowes/EchoMuse/internal/bindings/mixer"
)

// RouteNames is the same DAPM route table as Routes with the same values,
// addressed by control NAME rather than by positional index.
//
// The names belong to the TLV320AIC32x4 driver, so they are portable across
// boards carrying that codec — rook (Echo Spot) and biscuit (Echo Dot 2) both
// sit behind the same MT8163 AMZN machine driver and need identical routes
// closed, while Routes' indices are not portable because the mixer list
// interleaves machine-level and codec-level controls.
//
// See Routes for why each must be closed: an unconnected ASoC route leaves DAPM
// no reason to power the converter at either end, so capture returns a
// healthy-looking clock carrying silence and playback reports no underruns into
// a speaker that never moves.
var RouteNames = []Write{
	{Name: "ADC_D Right Ip Select ADC_D DIF1_R switch", Value: "1"},
	{Name: "ADC_D Left Ip Select ADC_D DIF1_L switch", Value: "1"},
	{Name: "ADC_C Right Ip Select ADC_C DIF1_R switch", Value: "1"},
	{Name: "ADC_C Left Ip Select ADC_C DIF1_L switch", Value: "1"},
	{Name: "ADC_B Right Ip Select ADC_B DIF1_R switch", Value: "1"},
	{Name: "ADC_B Left Ip Select ADC_B DIF1_L switch", Value: "1"},
	{Name: "ADC_A Right Ip Select ADC_A DIF1_R switch", Value: "1"},
	{Name: "ADC_A Left Ip Select ADC_A DIF1_L switch", Value: "1"},

	{Name: "HPR Output Mixer R_DAC Switch", Value: "1"},
	{Name: "HPL Output Mixer L_DAC Switch", Value: "1"},
}

var onceByName sync.Once

// EnsureRoutesByName applies RouteNames once per process, resolving each
// control's index from the card's own mixer listing.
//
// The name→index mapping is logged: running the firmware once on a new board
// yields the control offsets for its hardware map.
//
// A control that cannot be resolved is reported by name and does not stop the
// remaining routes being applied.
func EnsureRoutesByName(card int) error {
	var err error
	onceByName.Do(func() {
		m, loadErr := mixer.Load(card)
		if loadErr != nil {
			err = fmt.Errorf("codec: load mixer for card %d: %w", card, loadErr)
			return
		}

		// ABSENT and FAILED are different outcomes and must not be summed.
		// This table covers four ADCs because biscuit has four; a board with
		// two (rook) legitimately lacks the ADC_C/ADC_D routes, and reporting
		// those as failures produced "4 of 10 DAPM routes failed — audio may be
		// silent" on a device whose audio was fine. A warning that cries wolf on
		// every boot is worse than no warning, because the one time it means
		// something nobody reads it.
		var absent, failed int
		for _, w := range RouteNames {
			idx, idxErr := m.Index(w.Name)
			if idxErr != nil {
				absent++
				continue
			}
			if setErr := m.Set(w.Name, w.Value); setErr != nil {
				failed++
				log.Printf("[codec] route %q (ctl %d): %v", w.Name, idx, setErr)
				continue
			}
			log.Printf("[codec] route %q -> ctl %d = %s", w.Name, idx, w.Value)
		}
		if absent > 0 {
			log.Printf("[codec] %d of %d routes not present on this board — skipped",
				absent, len(RouteNames))
		}
		// Only a route that EXISTS and could not be written is a fault: an
		// unconnected converter is powered down by DAPM, so capture returns a
		// healthy-looking clock carrying silence.
		if failed > 0 {
			err = fmt.Errorf("codec: %d of %d DAPM routes present but unwritable — audio may be silent",
				failed, len(RouteNames))
			log.Printf("[codec] %v", err)
		}
	})
	return err
}
