#!/usr/bin/env python3
"""Drive rook_panel's ring through each state over its abstract Unix socket.

For checking the panel's rendering without a live turn.
"""
import json, socket, sys, time

SOCK = "\0com.echomuse.rookpanel/state"
N = 12

def send(s, px):
    s.sendall((json.dumps({"px": px}) + "\n").encode())

def main():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    print("connected")

    def hold(px, secs, step=0.08):
        end = time.time() + secs
        while time.time() < end:
            send(s, px); time.sleep(step)

    # 1) LISTENING — solid green ring, biscuit's default scene (0,180,0)
    print("listening (solid green)")
    hold([0x00B400] * N, 2.5)

    # 2) THINKING — bright head + dim trail, rotating one position per frame
    print("thinking (rotating spinner)")
    end = time.time() + 5
    pos = 0
    while time.time() < end:
        px = [0] * N
        px[pos % N] = 0x00C8FF                  # head
        px[(pos - 1) % N] = 0x004C64            # trail
        px[(pos - 2) % N] = 0x001A22            # fading trail
        send(s, px)
        pos += 1
        time.sleep(0.09)

    # 3) SPEAKING — meter: ring brightness tracking a fake RMS envelope
    print("speaking (meter throb)")
    import math
    end = time.time() + 5
    t0 = time.time()
    while time.time() < end:
        lvl = 0.35 + 0.65 * abs(math.sin((time.time() - t0) * 3.1))
        v = int(0xB4 * lvl)
        send(s, [(0 << 16) | (v << 8) | v] * N)   # cyan, scaled
        time.sleep(0.05)

    # 4) MUTE — solid red
    print("muted (solid red)")
    hold([0xD00000] * N, 2)

    # 5) IDLE — ring off, clock alone
    print("idle")
    send(s, [0] * N)
    s.close()

if __name__ == "__main__":
    main()
