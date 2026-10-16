import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Prism root
import prism_config  # noqa: E402  (paths)
import subprocess, sys, time
import deck_curate as d
import deck_mcp
import flow_nav as fn
LOG = f"{prism_config.PRISM}/media_card_test.log"
say = lambda m: open(LOG, "a").write(time.strftime("%T ") + m + "\n")
# Keep the test pad plugged in for the whole test: deck_mcp unplugs a pad after 30 s idle, and the next press then
# plugs in a NEW controller - Steam takes that press (its menus / controller settings) and the movie player,
# which listed the pads when it started, never sees its B (2026-10-09).
deck_mcp.PAD_IDLE = 10 ** 6
pad = deck_mcp.gamepad(); time.sleep(1.5)
nav = lambda *a: subprocess.run(["python3", f"{prism_config.PRISM}/esde_nav.py", *a], capture_output=True, text=True).stdout.strip()
pg = lambda pat: subprocess.run(["pgrep", "-f", pat], capture_output=True).returncode == 0
def seek_to(word, ok):
    """Cover flow: step Right (Down does nothing there) to the entry named like word (2026-10-09)."""
    return fn.walk(lambda s: ok(s) and word in s[0].rsplit("/", 1)[-1])
say("nav: " + nav("tv", "open").replace("\n", " | "))
s = seek_to("Arcane", fn.is_show); say(f"show: {s}")
if s:
    d.esde_keys(["Return"]); time.sleep(1.5)
    s = seek_to("Season 1", fn.is_season); say(f"season: {s}")
if s:
    d.esde_keys(["Return"]); time.sleep(1.5)
    s = seek_to("S01E01", fn.is_episode); say(f"episode: {s}")
if s:
    d.esde_keys(["Return"]); time.sleep(12)
dur = d.mpv_ipc(d.MOVIE_SOCK, ["get_property", "duration"])
t1 = d.mpv_ipc(d.MOVIE_SOCK, ["get_property", "media-title"])
d.mpv_ipc(d.MOVIE_SOCK, ["seek", (dur or 0) - 4, "absolute"])
t2 = None
for i in range(40):
    time.sleep(1)
    t2 = d.mpv_ipc(d.MOVIE_SOCK, ["get_property", "media-title"])
    if t2 and t2 != t1: break
say(f"up next: {t1} -> {t2} after {i + 1}s")
time.sleep(4)
if pg("plex_play.py"):                            # B only to a player (never into ES-DE's list)
    deck_mcp.tool_gamepad(["B"])
t0 = time.time(); seen = []
while time.time() - t0 < 12:
    seen.append((round(time.time() - t0, 1), d.focus_now(), pg("plex_play.py")))
    time.sleep(0.5)
say("after B: " + " ".join(f"{t}s:{f}{'*' if p else ''}" for t, f, p in seen))
res = [("Up Next plays the next episode", bool(t1 and t2 and t1 != t2)),
       ("B quits", bool(t1) and not pg("plex_play.py")),
       ("ES-DE in front", str(seen[-1][1]) == prism_config.PRISM_APPID)]
say("=== UPNEXT RESULTS")
for n, ok in res: say(f"{'PASS' if ok else 'FAIL'}  {n}")
