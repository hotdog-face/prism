#!/usr/bin/env python3
"""Prism startup cover (2026-10-09), run by the ES-DE launcher (es-de.sh) just before ES-DE starts: asks the deck_cc
overlay (always running under switchhome, drawn above everything) for the boot cover; if it isn't up yet (early in a
cold boot) falls back to deck_splash.py boot."""
import prism_config  # noqa: E402  (paths)
import json, socket, subprocess
try:
    s = socket.socket(socket.AF_UNIX); s.settimeout(1.0); s.connect("/tmp/deck_cc.sock")
    s.sendall(json.dumps({"cmd": "boot"}).encode() + b"\n"); s.recv(64)
except OSError:
    subprocess.Popen(["/usr/bin/python3", f"{prism_config.PRISM}/deck_splash.py", "boot"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
