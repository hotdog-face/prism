"""Movie: open -> plays -> Steam card (paused) -> Resume -> playing -> Steam card -> Back to Movies -> player and touch
reader gone, ES-DE in front. Then a TV episode near its end -> Up Next plays the next episode -> B quits."""
import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Prism root
import prism_config  # noqa: E402  (paths)
import subprocess, sys, time
import deck_curate as d
import deck_mcp
import flow_nav as fn
LOG = f"{prism_config.PRISM}/media_card_test.log"
say = lambda m: open(LOG, "a").write(time.strftime("%T ") + m + "\n")
d.log = lambda m: say("[curate] " + m)
# Keep the test pad plugged in for the whole test: deck_mcp unplugs a pad after 30 s idle, and the next press then
# plugs in a NEW controller - Steam takes that press (its menus / controller settings) and the movie player,
# which listed the pads when it started, never sees its B (2026-10-09).
deck_mcp.PAD_IDLE = 10 ** 6
pad = deck_mcp.gamepad(); time.sleep(1.5)
nav = lambda *a: subprocess.run(["python3", f"{prism_config.PRISM}/esde_nav.py", *a], capture_output=True, text=True).stdout.strip()
def card():
    try: return d.cc_request({"cmd": "state"}, timeout=0.5)
    except OSError: return "NA"
def poll(sec):
    end = time.time() + sec
    while time.time() < end: d.cc_poll(pad); time.sleep(0.05)
def press(): d.steam_down(pad); deck_mcp.tool_gamepad(["STEAM"]); d.steam_up(pad)
pg = lambda pat: subprocess.run(["pgrep", "-f", pat], capture_output=True).returncode == 0
CUR = subprocess.run(["systemctl", "--user", "show", "switchhome", "-p", "MainPID", "--value"], capture_output=True, text=True).stdout.strip()
def stray_readers():
    """touch readers not owned by the deck-curate service"""
    out = []
    procs = [l.split(None, 2) for l in subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True,
                                                     text=True).stdout.splitlines()]
    tr = {p[0]: p for p in procs if len(p) == 3 and "touch_reader.py" in p[2]}
    for pid_, p in tr.items():
        if p[1] not in tr and p[1] != CUR: out.append(pid_)
    return out
def esde_front(wait=6):
    end = time.time() + wait
    while time.time() < end:
        if str(d.focus_now()) == prism_config.PRISM_APPID: return True
        time.sleep(0.25)
    return False
res = []
if "skipmovie" not in sys.argv:
  say("nav: " + nav("movies", "open").replace("\n", " | "))
s = fn.walk(fn.is_movie); say(f"movie: {s and s[0][-70:]}")   # cover flow: Right past the folders to a real movie
res.append(("a library movie selected in the cover flow", bool(s)))
if s:
    d.esde_keys(["Return"]); time.sleep(12)
pos1 = d.mpv_ipc(d.MOVIE_SOCK, ["get_property", "time-pos"]); time.sleep(2)
pos2 = d.mpv_ipc(d.MOVIE_SOCK, ["get_property", "time-pos"])
res.append((f"movie plays ({pos1}->{pos2})", pos1 is not None and pos2 is not None and pos2 > pos1))
if res[-1][1]:                                   # only with a movie playing: never press into ES-DE's list
    press(); poll(2.5)
    paused = d.mpv_ipc(d.MOVIE_SOCK, ["get_property", "pause"])
    res.append(("card up, movie paused", card() == "ACTIVE" and paused is True))
    deck_mcp.tool_gamepad(["A"]); poll(2.5)      # Resume (first item)
    paused = d.mpv_ipc(d.MOVIE_SOCK, ["get_property", "pause"])
    res.append(("Resume: card gone, playing", card() == "IDLE" and paused is False and d.focus_now() != d.STEAM_UI))
    press(); poll(2.5)
    # "Back to Movies" is the LAST tile (Resume, Start over, Sleep timer, Watch later, Mark watched, Back to Movies
    # since 2026-10-09); the card's selection stops at the ends, so step right past the end
    for _ in range(7):
        deck_mcp.tool_gamepad(["RIGHT"]); time.sleep(0.25)
    deck_mcp.tool_gamepad(["A"])
    poll(5)
res.append(("Back to Movies: player closed", not pg("plex_play.py") and card() == "IDLE"))
res.append((f"touch reader ended {stray_readers()}", not stray_readers()))
res.append(("ES-DE in front", d.esde_pid() is not None and esde_front()))
# ---- Up Next on a TV episode
say("nav: " + nav("tv", "open").replace("\n", " | "))
s = fn.walk(fn.is_show); say(f"show: {s and s[0][-60:]}")      # past Continue Watching / Discover / Watch Later
if s:
    d.esde_keys(["Return"]); time.sleep(1.5)
    if fn.walk(lambda x: fn.is_season(x) or fn.is_episode(x), steps=3) and fn.is_season(d.selected()):
        d.esde_keys(["Return"]); time.sleep(1.5)     # into the first season
    s = fn.walk(fn.is_episode, steps=40); say(f"episode: {s and s[0][-70:]}")
res.append(("a TV episode selected in the cover flow", bool(s and fn.is_episode(s))))
if res[-1][1]:
    d.esde_keys(["Return"]); time.sleep(12)
    dur = d.mpv_ipc(d.MOVIE_SOCK, ["get_property", "duration"])
    t1 = d.mpv_ipc(d.MOVIE_SOCK, ["get_property", "media-title"])
    d.mpv_ipc(d.MOVIE_SOCK, ["seek", (dur or 0) - 4, "absolute"]); time.sleep(20)
    t2 = d.mpv_ipc(d.MOVIE_SOCK, ["get_property", "media-title"])
    say(f"up next: {t1} -> {t2}")
    res.append((f"episode plays ({t1})", t1 is not None))
    # Up Next is covered by upnext_test.py (this picks whatever episode is first, often a show's last one)
    if pg("plex_play.py"):
        deck_mcp.tool_gamepad(["B"]); time.sleep(5)
res.append(("B quits the episode", res[-1][1] and not pg("plex_play.py")))
time.sleep(1)
res.append((f"ES-DE in front after B, no stray reader {stray_readers()}", esde_front() and not stray_readers()))
sup = d.steam_js("SteamUIStore.WindowStore.GamepadUIMainWindowInstance.MenuStore.m_cSuppressRequests", "count")
res.append((f"Steam menus released (count={sup})", sup == 0))
say("=== MOVIE RESULTS")
for n, ok in res: say(f"{'PASS' if ok else 'FAIL'}  {n}")
