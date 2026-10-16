#!/usr/bin/env python3
"""Real-game test: ES-DE game list with a light game selected -> launch it -> Steam press -> card (game paused) ->
Save & go Home -> game closed with a resume state, ES-DE alive and in front, Steam's menu never shown."""
import prism_config  # noqa: E402  (paths)
import glob, os, subprocess, sys, time
sys.path.insert(0, prism_config.PRISM)
import deck_curate as d
import deck_mcp

LOG = f"{prism_config.PRISM}/steambtn_test.log"
d.log = lambda m: open(LOG, "a").write(time.strftime("%T ") + "[curate] " + m + "\n")
say = lambda m: open(LOG, "a").write(time.strftime("%T ") + m + "\n")
# Keep the test pad plugged in for the whole test: deck_mcp unplugs a pad after 30 s idle, and the next press then
# plugs in a NEW controller - Steam takes that press (its menus / controller settings) and the movie player,
# which listed the pads when it started, never sees its B (2026-10-09).
deck_mcp.PAD_IDLE = 10 ** 6
pad = deck_mcp.gamepad(); time.sleep(1.5)
STATES = os.path.expanduser("~/.var/app/org.libretro.RetroArch/config/retroarch/states")


def snap(tag):
    try:
        st = d.cc_request({"cmd": "state"}, timeout=0.5)
    except OSError:
        st = "NA"
    blur = subprocess.run(["xprop", "-root", "GAMESCOPE_BLUR_MODE"], env={**deck_mcp.session_env(), "DISPLAY": ":0"},
                          capture_output=True, text=True).stdout.strip().split("=")[-1].strip()
    d._focus_cache[0] = 0
    games = [l for l in subprocess.run(["ps", "-eo", "stat=,args="], capture_output=True, text=True).stdout.splitlines()
             if "/Emulation/roms/" in l and "retroarch" in l]
    esde = subprocess.run(["pgrep", "-x", "es-de"], capture_output=True).returncode == 0
    return (f"{tag}: card={st} focus={d.focus_now()} blur={blur} esde={'up' if esde else 'DOWN'} "
            f"game={[g.split()[0] for g in games]}")


def poll_for(sec):
    end = time.time() + sec
    while time.time() < end:
        d.cc_poll(pad); time.sleep(0.05)


t_start = time.time()
sel = d.selected(); say(f"launching {sel}")
d.esde_keys(["Return"])
for _ in range(40):
    time.sleep(0.5)
    if d.game_running(d.esde_pid()):
        break
time.sleep(6); say(snap("game up"))
d.steam_down(pad); deck_mcp.tool_gamepad(["STEAM"]); d.steam_up(pad)
poll_for(2.5); s1 = snap("card up"); say(s1)
# RetroArch cards: Resume, Save state, [Load state when states exist], Save & go Home, ... (2026-10-08): step to
# "Save & go Home" by its place in the tiles this card shows, not a fixed count
to_home = 3 if d.cc_open.get("states") else 2
say(f"card tiles: {'with' if to_home == 3 else 'no'} Load state -> RIGHT x{to_home}")
for _ in range(to_home):
    deck_mcp.tool_gamepad(["RIGHT"]); time.sleep(0.4)
deck_mcp.tool_gamepad(["A"])
for i in range(16):
    poll_for(0.5); say(snap(f"after HOME t+{(i + 1) * 0.5:.1f}s"))
s2 = snap("end")
newstate = [f for f in glob.glob(f"{STATES}/**/*Puyo*", recursive=True) if os.path.getmtime(f) > t_start]
sup = d.steam_js("SteamUIStore.WindowStore.GamepadUIMainWindowInstance.MenuStore.m_cSuppressRequests", "count")
res = [("game paused under the card", "card=ACTIVE" in s1 and "'T" in s1 and "blur=0" in s1),
       ("game closed, ES-DE alive and in front", "game=[]" in s2 and "esde=up" in s2 and f"focus={prism_config.PRISM_APPID}" in s2),
       ("resume state written", bool(newstate)),
       ("card gone, Steam menus released", "card=IDLE" in s2 and sup == 0)]
say(f"new states: {newstate}")
say("=== RESULTS")
for n, ok in res:
    say(f"{'PASS' if ok else 'FAIL'}  {n}")
