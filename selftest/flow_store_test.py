import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Prism root
import prism_config  # noqa: E402  (paths)
import subprocess, sys, time
import deck_curate as d, deck_mcp
d.log = lambda m: None
# Keep the test pad plugged in for the whole test: deck_mcp unplugs a pad after 30 s idle, and the next press then
# plugs in a NEW controller - Steam takes that press (its menus / controller settings) and the movie player,
# which listed the pads when it started, never sees its B (2026-10-09).
deck_mcp.PAD_IDLE = 10 ** 6
pad = deck_mcp.gamepad(); time.sleep(1.5)
def open_list(system):
    for _ in range(3):
        if d.selected() is None: break
        d.esde_keys(["BackSpace"]); time.sleep(0.8)
    subprocess.run(["python3", f"{prism_config.PRISM}/esde_nav.py", system], capture_output=True)
    for _ in range(4):
        if d.selected() is not None: return
        d.esde_keys(["Return"]); time.sleep(1.5)
# --- cover flow touch
open_list("movies")
names = lambda: (d.selected() or ("", "?", ""))[1]
start = names()
def swipe(x0, x1, y=430, dur=0.35):
    t = d.Touch(pad, x0, y); n = 18
    for i in range(1, n + 1):
        t.move(int(x0 + (x1 - x0) * i / n), y); time.sleep(dur / n)
    t.up(x1, y); time.sleep(0.8)
swipe(900, 540)                                   # slow drag left ~360 px -> about 3 posters on
a = names()
t = d.Touch(pad, 640 - 183, 430); t.up(640 - 183, 430); time.sleep(0.8)   # tap the poster left of centre
b = names()
print(f"PASS  cover flow: drag moves posters ({start} -> {a})" if a != start else f"FAIL  cover flow drag ({start} -> {a})")
print(f"PASS  cover flow: tapping a side poster selects it ({a} -> {b})" if b != a else f"FAIL  side poster tap ({a} -> {b})")
# --- For You: Steam Store first, opens the store, Prism comes back
open_list("foryou")
first = None
for _ in range(25):                               # walk up (ES-DE remembers the last position) until the store
    first = d.selected()
    if first and first[1] == "Steam Store": break
    d.esde_keys(["Up"]); time.sleep(0.2)
d.esde_keys(["Up"]); time.sleep(0.3)
above = d.selected(); d.esde_keys(["Down"]); time.sleep(0.3)   # one above the store wraps to the last pick
print(("PASS" if first and first[1] == "Steam Store" and above and above[1] != "Steam Store" else "FAIL")
      + f"  For You starts with the Steam Store (above it: {above and above[1]})")
d.esde_keys(["Return"]); time.sleep(6)                     # opens the whole store
f1 = str(d.focus_now())
print(("PASS" if f1 == "769" else "FAIL") + f"  Steam Store opens in Steam (focus {f1})")
d.bridge("to_game"); time.sleep(2)
f2 = str(d.focus_now())
print(("PASS" if f2 == prism_config.PRISM_APPID else "FAIL") + f"  back to Prism afterwards (focus {f2})")
import glob
yt = [f.rsplit("/", 1)[-1] for f in glob.glob(f"{prism_config.ROMS}/foryou/*.sh") if "youtube.com" in open(f).read()]
print(("PASS" if not yt else "FAIL") + f"  every For You pick opens a store, none YouTube ({len(yt)} on YouTube)")
for _ in range(3):
    if d.selected() is None: break
    d.esde_keys(["BackSpace"]); time.sleep(0.8)
