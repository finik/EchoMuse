package buttons

import (
	"context"
	"errors"
	evdev "github.com/gvalkov/golang-evdev"
	"github.com/wilbowes/EchoMuse/internal/profile"
	"github.com/wilbowes/EchoMuse/pkg/buttons"
	"log"
	"os/exec"
	"time"
)

// Fallbacks only; the real paths are resolved by name at open time (resolve.go).
// Which nodes to open, what the mute key reports and whether to take the
// devices exclusively are all per-board — see internal/profile's Buttons.

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

// Init the button listeners
// Kills alexa's native button functions
func (e *EvDevController) Init() error {
	cmd := exec.Command("stop", "acebutton")
	return cmd.Run()
}

func (e *EvDevController) SubscribeToButton(callback buttons.ButtonClickCallback) (*buttons.EventSubscription, error) {
	if callback == nil {
		return nil, errors.New("callback can't be nil")
	}

	dotBtn := e.GetDotButton()
	volBtn := e.GetVolumeButton()
	bp := profile.Active().Buttons
	dotDevice, err := evdev.Open(resolveOr(bp.ActionNames, bp.ActionPath))
	if err != nil {
		return nil, err
	}
	volDevice, err := evdev.Open(resolveOr(bp.VolumeNames, bp.VolumePath))
	if err != nil {
		return nil, err
	}

	// EVIOCGRAB both devices. Reading an evdev node does not consume its
	// events — every reader gets a copy — so without the grab Android's
	// InputReader also acts on them:
	//
	//   - The mute button reaches Android as KEY_POWER (rook has no mute key)
	//     and raises the keyguard over the panel, hiding the ring UI.
	//   - The volume keys raise Android's own volume slider and move a stream
	//     volume unrelated to the codec level EchoMuse controls.
	//
	// A grab failure is logged and tolerated: buttons still work, Android just
	// also sees the keys.
	for _, d := range []struct {
		name string
		dev  *evdev.InputDevice
	}{{"dot/mute", dotDevice}, {"volume", volDevice}} {
		if !bp.Grab {
			continue
		}
		if err := d.dev.Grab(); err != nil {
			log.Printf("[buttons] could not grab %s device exclusively (%v) — Android will also see these keys", d.name, err)
		} else {
			log.Printf("[buttons] grabbed %s device exclusively", d.name)
		}
	}

	ctx, cancel := context.WithCancel(context.Background())
	eventSub := buttons.NewEventSubscription(cancel)

	readBtn := func(btn buttons.Button, btnDevice *evdev.InputDevice) {
		defer btnDevice.Release()

		beforeClickType := buttons.ClickType(0)
		beforeDown := false
		// When each click type was pressed, so a release can report how long
		// it was held. Keyed by click type because the dot device carries the
		// mute button too, and interleaving the two must not attribute one
		// button's hold to the other.
		downAt := map[buttons.ClickType]time.Time{}

		for {
			if ctx.Err() != nil {
				return
			}

			inputEvent, err := btnDevice.ReadOne()
			if err != nil {
				return
			}

			// Only key events. Every key press is followed immediately by
			// an EV_SYN separator whose Code and Value are both 0 — and
			// without this filter that SYN fell through to the Code==0
			// branch, took the previous click type, computed Value==1 as
			// FALSE, and fired a "release" microseconds after the press.
			//
			// So the button has always acted on the SYN rather than on the
			// real release, which is why it felt instant and why the actual
			// release (a genuine transition to 0) was then swallowed as a
			// no-change. Invisible until something needed to know how long
			// the button was held: heldMs came out at ~0 every time.
			if inputEvent.Type != evdev.EV_KEY {
				continue
			}

			// rook's button is the mute button, wired to KEY_POWER (116); the
			// node reports only 0x72/0x74/0x8a and no separate mute key. Map it
			// to MuteClick so it drives the same mute path as biscuit's:
			// hardware ADC mute, red ring, and state persisted in state.json.
			if bp.MuteKeyCode != 0 && int(inputEvent.Code) == bp.MuteKeyCode {
				inputEvent.Code = uint16(buttons.MuteClick)
			}

			clickType := buttons.ClickType(inputEvent.Code)
			if inputEvent.Code != 0 {
				beforeClickType = clickType
			} else {
				clickType = beforeClickType
			}

			down := inputEvent.Value == 1
			if beforeDown == down {
				continue
			}
			beforeDown = down

			// Intercept volume events on volume device
			if btn.Type == buttons.VolumeButton && !down {
				switch clickType {
				case buttons.VolumeUpClick:
					if e.volumeCallback != nil {
						e.volumeCallback("up")
					}
				case buttons.VolumeDownClick:
					if e.volumeCallback != nil {
						e.volumeCallback("down")
					}
				}
				continue
			}

			// Intercept mute on dot device
			if btn.Type == buttons.DotButton && !down && clickType == buttons.MuteClick {
				if e.muteCallback != nil {
					e.muteCallback()
				}
				continue
			}

			var heldMs int64
			if down {
				downAt[clickType] = time.Now()
			} else if t, ok := downAt[clickType]; ok {
				heldMs = time.Since(t).Milliseconds()
				delete(downAt, clickType)
			}

			callback(buttons.ButtonClickEvent{
				Button:    btn,
				ClickType: clickType,
				Down:      down,
				HeldMs:    heldMs,
			})
		}
	}

	go readBtn(dotBtn, dotDevice)
	go readBtn(volBtn, volDevice)

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

// resolveOr returns the node for the first matching device name, or the
// fallback path when none match.
func resolveOr(names []string, fallback string) string {
	if p, err := resolveByName(names...); err == nil {
		log.Printf("[buttons] %v -> %s", names, p)
		return p
	} else {
		log.Printf("[buttons] %v not found (%v) — falling back to %s", names, err, fallback)
	}
	return fallback
}
