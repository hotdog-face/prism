#!/usr/bin/env python3
"""Prism power-off / restart animation (2026-10-09): asks the deck_cc overlay to play the "good night" video, then
waits for it, so the caller can power off / reboot right after. `prism_off.py test` plays it and fades back."""
import json, socket, sys, time

try:
    s = socket.socket(socket.AF_UNIX); s.settimeout(1.5); s.connect("/tmp/deck_cc.sock")
    s.sendall((json.dumps({"cmd": "offtest" if "test" in sys.argv[1:] else "off"}) + "\n").encode())
    r = s.recv(64).decode().split()
    secs = float(r[1]) if len(r) > 1 and r[0] == "OK" else 0
except Exception:
    secs = 0
time.sleep(secs + 0.2)
