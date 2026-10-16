"""Stuck-state rescue: game frozen under the card with Steam menus suppressed -> rescue() -> everything released."""
import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Prism root
import prism_config  # noqa: E402  (paths)
import subprocess, sys, time
import deck_curate as d
import deck_mcp
LOG = f"{prism_config.PRISM}/rescue_test.log"
say = lambda m: open(LOG, "a").write(time.strftime("%T ") + m + "\n")
d.log = lambda m: say("[curate] " + m)
# Keep the test pad plugged in for the whole test: deck_mcp unplugs a pad after 30 s idle, and the next press then
# plugs in a NEW controller - Steam takes that press (its menus / controller settings) and the movie player,
# which listed the pads when it started, never sees its B (2026-10-09).
deck_mcp.PAD_IDLE = 10 ** 6
pad = deck_mcp.gamepad(); time.sleep(1.5)

def games():
    return [l.split()[0] for l in subprocess.run(["ps", "-eo", "stat=,args="], capture_output=True, text=True)
            .stdout.splitlines() if "/Emulation/roms/" in l and "retroarch" in l]

def snap(tag):
    try: st = d.cc_request({"cmd": "state"}, timeout=0.5)
    except OSError: st = "NA"
    d._focus_cache[0] = 0
    sup = d.steam_js("SteamUIStore.WindowStore.GamepadUIMainWindowInstance.MenuStore.m_cSuppressRequests", "count")
    s = f"{tag}: card={st} focus={d.focus_now()} game={games()} suppress={sup}"
    say(s); return s

say(f"launching {d.selected()}")
d.esde_keys(["Return"])
for _ in range(40):
    time.sleep(0.5)
    if d.game_running(d.esde_pid()): break
time.sleep(6); snap("game up")
d.steam_down(pad); deck_mcp.tool_gamepad(["STEAM"]); d.steam_up(pad)
end = time.time() + 2.5
while time.time() < end: d.cc_poll(pad); time.sleep(0.05)
s1 = snap("stuck (card up, game frozen)")
d.cc_open.clear()            # simulate the daemon losing track of its card: only the rescue can get out now
time.sleep(1)
d.rescue(); time.sleep(2.5)
s2 = snap("after rescue")
res = [("stuck state reproduced", "card=ACTIVE" in s1 and "'T" in s1),
       ("card gone", "card=IDLE" in s2),
       ("game running again (no T state)", "'T" not in s2 and ("'S" in s2 or "'R" in s2)),
       ("Steam menus released", "suppress=0" in s2),
       ("game in front", f"focus={prism_config.PRISM_APPID}" in s2)]
say("=== RESULTS")
for n, ok in res: say(f"{'PASS' if ok else 'FAIL'}  {n}")
# clean exit back to Prism
d.quit_game(); time.sleep(3); snap("after quit")
