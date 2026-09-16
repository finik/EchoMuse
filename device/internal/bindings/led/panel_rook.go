package led

import (
	"encoding/json"
	"log"
	"net"
	"sync"
	"time"

	"github.com/wilbowes/EchoMuse/pkg/led"
)

// PanelController is rook's stand-in for biscuit's LED ring. The Echo Spot has
// no ring — its only LED-class device is lcd-backlight — so ring frames are
// drawn as 12 arc segments around the edge of its 480x480 round display.
//
// It satisfies led.Controller so every cue the firmware already produces through
// SetLEDs (listening ring, thinking spinner, speaker-RMS meter, mute red, volume
// arc, end-of-turn outcome rhythms) reaches the panel with no second
// notification path to keep in step.
//
// The Go daemon cannot draw Android UI, so the drawing lives in rook_panel.apk
// and frames are forwarded to it over a Unix socket. The ABSTRACT namespace
// (leading NUL, shown as "@" in /proc/net/unix) leaves nothing stale to clean up
// after a crash and no filesystem permission question between an app-uid
// listener and a root daemon.
const panelSocket = "@com.echomuse.rookpanel/state"

// panelFrame is one newline-delimited JSON line: the whole 12-pixel ring, as
// packed 0xRRGGBB values.
//
// The entire frame is sent rather than a state name because the cues carrying
// the most information are animations: "thinking" is a bright head with a dim
// trail rotating one position at a time, and "speaking" is ring brightness
// tracking the speaker's RMS. A state name flattens both to one colour. 12 ints
// at ~10fps is nothing on a local socket.
type panelFrame struct {
	Px []int `json:"px"`
}

type PanelController struct {
	mu       sync.Mutex
	conn     net.Conn
	last     string
	lastSend time.Time
}

func NewPanelController() *PanelController { return &PanelController{} }

// Init connects to the panel and REPORTS whether it is there.
//
// The error must be returned, not swallowed. This controller is selected in
// preference to the i2c ring, so a nil return on a board with no panel claims
// the ring's job and then paints nothing — on a board that has a real ring,
// that turns it off. Absence of the panel is exactly the signal the caller
// needs in order to choose the ring instead.
//
// It retries, because the two are racing: the firmware starts from service.d
// while the panel is an ordinary Android activity still being launched, so a
// single failed dial says "not yet" far more often than "not this board". A
// board with no panel pays this wait once at boot and then uses its ring.
func (p *PanelController) Init() error {
	const (
		attempts = 6
		gap      = 2 * time.Second
	)
	var err error
	for i := 0; i < attempts; i++ {
		if err = p.dial(); err == nil {
			return nil
		}
		if i < attempts-1 {
			time.Sleep(gap)
		}
	}
	return err
}

func (p *PanelController) dial() error {
	c, err := net.DialTimeout("unix", panelSocket, 2*time.Second)
	if err != nil {
		return err
	}
	p.conn = c
	p.last = ""
	log.Printf("[panel] connected to %s", panelSocket)
	return nil
}

// GetNumLEDs reports 12, biscuit's ring size: every scene and animation in the
// controller is written for 12 positions, and the panel draws 12 arc segments.
func (p *PanelController) GetNumLEDs() (int, error) { return 12, nil }

// SetLEDs forwards the frame to the panel.
func (p *PanelController) SetLEDs(leds ...led.Led) error {
	px := make([]int, len(leds))
	for i, l := range leds {
		px[i] = int(l.R)<<16 | int(l.G)<<8 | int(l.B)
	}
	line, err := json.Marshal(panelFrame{Px: px})
	if err != nil {
		return err
	}

	p.mu.Lock()
	defer p.mu.Unlock()

	s := string(line)
	// Identical frames are suppressed (a solid ring repaints for as long as it
	// is shown) but re-sent every 5s, so a restarted panel is not left showing
	// a stale ring.
	if s == p.last && time.Since(p.lastSend) < 5*time.Second {
		return nil
	}

	if p.conn == nil {
		if err := p.dial(); err != nil {
			return nil // panel absent; never fail a turn over the display
		}
	}
	if _, err := p.conn.Write(append(line, '\n')); err != nil {
		log.Printf("[panel] write failed, will redial: %v", err)
		p.conn.Close()
		p.conn = nil
		return nil
	}
	p.last = s
	p.lastSend = time.Now()
	return nil
}
