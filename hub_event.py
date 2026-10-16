#!/usr/bin/env python3
"""ES-DE game-start -> /tmp/esde_game_start for the game hub thread (game_hub.py)."""
import json, sys, time
rom, name, system = (sys.argv[1:4] + ["", "", ""])[:3]
json.dump({"rom": rom, "name": name, "system": system, "t": time.time()}, open("/tmp/esde_game_start", "w"))
