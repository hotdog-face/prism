#!/usr/bin/env python3
"""One Steam-UI command through the SwitchHomeBridge Decky plugin, from the shell (2026-10-09).
`prism_bridge.py suspend` -> exit 0 if Steam took it, 1 otherwise (callers fall back to systemctl)."""
import json, os, socket, sys

try:
    c = socket.socket(socket.AF_UNIX); c.settimeout(2.5); c.connect(f"/run/user/{os.getuid()}/switchhome_bridge.sock")
    c.sendall((json.dumps({"cmd": sys.argv[1], "arg": None}) + "\n").encode())
    r = json.loads(c.makefile().readline())
    print(r.get("data")); sys.exit(0 if r.get("ok") else 1)
except Exception as e:
    print(e); sys.exit(1)
