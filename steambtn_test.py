#!/usr/bin/env python3
"""End-to-end test of the Steam-button control center with virtual presses (no game needed: ES-DE in front).
Mirrors deck_curate's real flow: steam_down -> Steam opens its menu on release -> steam_up -> cc_poll."""
import prism_config  # noqa: E402  (paths)
import os, subprocess, sys, time
sys.path.insert(0, prism_config.PRISM)
import deck_curate as d
import deck_mcp

LOG = f"{prism_config.PRISM}/steambtn_test.log"
d.log = lambda m: open(LOG, "a").write(time.strftime("%T ") + "[curate] " + m + "\n")
# Keep the test pad plugged in for the whole test: deck_mcp unplugs a pad after 30 s idle, and the next press then
# plugs in a NEW controller - Steam takes that press (its menus / controller settings) and the movie player,
# which listed the pads when it started, never sees its B (2026-10-09).
deck_mcp.PAD_IDLE = 10 ** 6
pad = deck_mcp.gamepad()
time.sleep(1.5)


def say(m):
    open(LOG, "a").write(time.strftime("%T ") + m + "\n")


def snap(tag):
    try:
        st = d.cc_request({"cmd": "state"}, timeout=0.5)
    except OSError:
        st = "NA"
    blur = subprocess.run(["xprop", "-root", "GAMESCOPE_BLUR_MODE"], env={**deck_mcp.session_env(), "DISPLAY": ":0"},
                          capture_output=True, text=True).stdout.strip().split("=")[-1].strip()
    d._focus_cache[0] = 0
    return f"{tag}: card={st} focus={d.focus_now()} blur={blur}"


def open_card():
    """steam_down without the game check (no game running in this test)."""
    d.steam_menus_suppress()
    shot = d.grab_shot_fast("/tmp/deck_cc_shot.png")
    conn = d.cc_request({"cmd": "open", "name": "Steam button test", "shot": shot or "", "cover": "", "system": "psp"},
                        keep=True)
    conn.setblocking(False)
    d.cc_open.update(conn=conn, pid=d.esde_pid(), shot=shot, buf=b"")


def poll_for(sec):
    end = time.time() + sec
    while time.time() < end:
        d.cc_poll(pad); time.sleep(0.05)


def steam_press():
    deck_mcp.tool_gamepad(["STEAM"])      # Steam opens its own menu on release, like the real button


results = []
say("=== T1: open card, Steam's menu opens hidden and must be closed; card must stay up")
open_card(); time.sleep(0.2); steam_press(); d.steam_up(pad)
for i in range(6):
    poll_for(0.5); say(snap(f"T1 t+{(i + 1) * 0.5:.1f}s"))
t1 = snap("T1 end"); results.append(("T1 card stays, Steam menu closed", "card=ACTIVE" in t1 and "focus=769" not in t1))

say("=== T2: second Steam press closes the card and resumes; no Steam menu left")
d.steam_down(pad); steam_press(); d.steam_up(pad)
for i in range(6):
    poll_for(0.5); say(snap(f"T2 t+{(i + 1) * 0.5:.1f}s"))
t2 = snap("T2 end"); results.append(("T2 second press closes, no Steam menu", "card=IDLE" in t2 and "focus=769" not in t2 and not d.cc_open))

time.sleep(1.5)
say("=== T3: Save & go Home: RIGHT then A on the card -> Saving -> Saved -> gone, ES-DE in front")
open_card(); time.sleep(0.2); steam_press(); d.steam_up(pad)
poll_for(2.5); say(snap("T3 before choice"))
deck_mcp.tool_gamepad(["RIGHT"]); time.sleep(0.4); deck_mcp.tool_gamepad(["A"])
for i in range(10):
    poll_for(0.5); say(snap(f"T3 t+{(i + 1) * 0.5:.1f}s"))
t3 = snap("T3 end"); results.append(("T3 save&home ends in ES-DE, card gone", "card=IDLE" in t3 and "focus=769" not in t3 and not d.cc_open))

time.sleep(1)
sup = d.steam_js("SteamUIStore.WindowStore.GamepadUIMainWindowInstance.MenuStore.m_cSuppressRequests", "count")
results.append((f"Steam menus not suppressed afterwards (count={sup})", sup == 0))
say("=== RESULTS")
for name, ok in results:
    say(f"{'PASS' if ok else 'FAIL'}  {name}")
