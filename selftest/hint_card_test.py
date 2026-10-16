"""Touch paths: a tap on the hint bar's Back (game list -> console row), and a tap on the card's Resume tile."""
import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Prism root
import prism_config  # noqa: E402  (paths)
import fixtures  # noqa: E402  (this Deck's games: ~/.config/prism/selftest.json)
import subprocess, sys, time
import deck_curate as d, deck_mcp
d.log = lambda m: None
# Keep the test pad plugged in for the whole test: deck_mcp unplugs a pad after 30 s idle, and the next press then
# plugs in a NEW controller - Steam takes that press (its menus / controller settings) and the movie player,
# which listed the pads when it started, never sees its B (2026-10-09).
deck_mcp.PAD_IDLE = 10 ** 6
pad = deck_mcp.gamepad(); time.sleep(1.5)


def open_gba():
    subprocess.run(["python3", f"{prism_config.PRISM}/esde_nav.py", "gba"], capture_output=True)
    for _ in range(4):
        if d.selected() is not None:
            return
        d.esde_keys(["Return"]); time.sleep(1.5)

open_gba()
before = d.selected()
t = d.Touch(pad, 520, 752); t.up(520, 752); time.sleep(1.2)
print(("PASS" if before and d.selected() is None else "FAIL") + "  hint bar: tapping Back leaves the game list")
open_gba()
for i in range(200):
    s = d.selected()
    if s and (fixtures.gba_game() or s[0]) in s[0]: break
    d.esde_keys(["Down"]); time.sleep(0.12)
d.esde_keys(["Return"])
for _ in range(40):
    time.sleep(0.5)
    if d.game_running(d.esde_pid()): break
time.sleep(6)
games = lambda: [l.split()[0] for l in subprocess.run(["ps", "-eo", "stat=,args="], capture_output=True, text=True).stdout.splitlines()
                 if "/Emulation/roms/" in l and "retroarch" in l]
d.steam_down(pad); deck_mcp.tool_gamepad(["STEAM"]); d.steam_up(pad)
end = time.time() + 2.5
while time.time() < end: d.cc_poll(pad); time.sleep(0.05)
up = d.cc_request({"cmd": "state"}, timeout=1) == "ACTIVE" and any(g.startswith("T") for g in games())
d.cc_request({"cmd": "tap", "x": 256, "y": 620}, timeout=1)
end = time.time() + 2.5
while time.time() < end: d.cc_poll(pad); time.sleep(0.05)
ok = up and d.cc_request({"cmd": "state"}, timeout=1) == "IDLE" and games() and not any(g.startswith("T") for g in games())
print(("PASS" if ok else "FAIL") + "  card: tapping Resume closes it and the game runs again")
d.quit_game(); time.sleep(2)
print(("PASS" if not games() else "FAIL") + "  card test game closed")
