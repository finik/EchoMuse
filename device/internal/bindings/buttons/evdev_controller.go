package buttons

import (
	"context"
	"errors"
	"log"
	"os/exec"
	"time"

	evdev "github.com/gvalkov/golang-evdev"
	"github.com/wilbowes/EchoMuse/pkg/board"
	"github.com/wilbowes/EchoMuse/pkg/buttons"
)

// VolumeCallback is called on volume button release with direction "up" or "down".
type VolumeCallback func(direction string)

// MuteCallback is called on mute button release.
type MuteCallback func()

type EvDevController struct {
	volumeCallback func(direction string)
	muteCallback   func()
}

// SetVolumeCallback registers a function to be called on volume button events.
// Must be called before SubscribeToButton.
func (e *EvDevController) SetVolumeCallback(cb func(direction string)) {
	e.volumeCallback = cb
}

// SetMuteCallback registers a function to be called on mute button events.
// Must be called before SubscribeToButton.
func (e *EvDevController) SetMuteCallback(cb func()) {
	e.muteCallback = cb
}

// Init stops Amazon's button daemon, which is acebutton on FireOS 5 and
// acebuttond on FireOS 6. Both are asked, since missing the one that exists
// leaves Amazon acting on every press alongside us; it fails only if neither
// stop could run.
func (e *EvDevController) Init() error {
	errA := exec.Command("stop", "acebutton").Run()
	errB := exec.Command("stop", "acebuttond").Run()
	if errA != nil && errB != nil {
		return errA
	}
	return nil
}

// muteHoldCutoffMs separates a mute press from a hold on a board where one
// button is both. Below client.PairHoldDuration (7s) by a wide margin, so a
// hold meant for pairing is never eaten as a mute, and above any press a
// person makes meaning "mute".
const muteHoldCutoffMs = 1000

func (e *EvDevController) SubscribeToButton(callback buttons.ButtonClickCallback) (*buttons.EventSubscription, error) {
	if callback == nil {
		return nil, errors.New("callback can't be nil")
	}

	// The input devices are found by name (pkg/board): eventN is enumeration
	// order, and the wrong device opens without error and reads nothing.
	keys := board.CurrentLayout().Keys
	if len(keys) == 0 {
		return nil, errors.New("buttons: input devices not found on this board")
	}
	var devices []*evdev.InputDevice
	for _, path := range keys {
		d, err := evdev.Open(path)
		if err != nil {
			for _, open := range devices {
				open.Release()
			}
			return nil, err
		}
		devices = append(devices, d)
	}

	// EVIOCGRAB where the board asks for it. Reading an evdev node does not
	// consume its events, so without this Android acts on the same presses:
	// on the Spot the action button arrives as KEY_POWER and raises the
	// keyguard over the panel, and the volume keys raise Android's slider.
	// Tolerated on failure — the buttons still work, Android just sees them.
	muteKey := buttons.MuteClick
	if k := board.CurrentLayout().MuteKey; k != 0 {
		muteKey = buttons.ClickType(k)
	}

	if board.CurrentLayout().GrabInputs {
		for i, d := range devices {
			if err := d.Grab(); err != nil {
				log.Printf("[buttons] could not grab %s exclusively (%v) — "+
					"Android will also see these keys", keys[i], err)
			} else {
				log.Printf("[buttons] grabbed %s exclusively", keys[i])
			}
		}
	}

	ctx, cancel := context.WithCancel(context.Background())
	eventSub := buttons.NewEventSubscription(cancel)

	read := func(dev *evdev.InputDevice) {
		defer dev.Release()
		state := newKeyState()
		for ctx.Err() == nil {
			ev, err := dev.ReadOne()
			if err != nil {
				return
			}
			// Key events only: each press is followed by an EV_SYN whose code
			// and value are both 0.
			if ev.Type != evdev.EV_KEY {
				continue
			}
			k, ok := state.handle(ev.Code, ev.Value, time.Now())
			if !ok {
				continue
			}
			if isVolume(k.Code) {
				// Volume is an interaction like any other press, and this
				// branch returns before the WakeScreen below ever runs — so
				// turning the volume up on a dimmed Spot left it dark, which
				// is the one thing the wake is here to prevent.
				if k.Down {
					board.WakeScreen()
				}
				if !k.Down && e.volumeCallback != nil {
					e.volumeCallback(map[buttons.ClickType]string{
						buttons.VolumeUpClick: "up", buttons.VolumeDownClick: "down"}[k.Code])
				}
				continue
			}
			// Any press is an interaction, so wake a screen that dims
			// itself — a device that stays dark when you press it reads
			// as broken. Best effort: a failed write must never cost a
			// button press.
			if k.Down {
				board.WakeScreen()
			}
			// The board says which code its mic-off button reports.
			// A SHORT press of it is the mute; a long one still reaches
			// the action path, because on a board whose only button is
			// this one that hold is how pairing is asked for.
			if k.Code == muteKey {
				if !k.Down && k.HeldMs < muteHoldCutoffMs {
					if e.muteCallback != nil {
						e.muteCallback()
					}
					continue
				}
				if !k.Down {
					// long: fall through as the action button
				} else {
					continue
				}
			}
			callback(buttons.ButtonClickEvent{
				Button:    e.GetDotButton(),
				ClickType: k.Code,
				Down:      k.Down,
				HeldMs:    k.HeldMs,
			})
		}
	}
	for _, d := range devices {
		go read(d)
	}
	return eventSub, nil
}

func (e *EvDevController) GetVolumeButton() buttons.Button {
	return buttons.Button{
		Type: buttons.VolumeButton,
	}
}

func (e *EvDevController) GetDotButton() buttons.Button {
	return buttons.Button{
		Type: buttons.DotButton,
	}
}

func NewButtonController() (*EvDevController, error) {
	controller := &EvDevController{}
	if err := controller.Init(); err != nil {
		return nil, err
	}
	return controller, nil
}
