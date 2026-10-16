"""Prism comes back after a Game Mode session restart (deck_home, 2026-10-09). A gamescope crash can't be part of a
test, so this reproduces what the supervisor's home thread sees: ES-DE closes while the Deck is idle, then
/tmp/prism_fake_restart says "the session restarted". It must relaunch Prism once Steam and Decky are ready: one copy,
one launch (no "Game already running"), back in front and known to Steam. Prism is restarted by hand if it doesn't."""
import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Prism root
import prism_config  # noqa: E402  (paths)
import os, subprocess, sys, time
import deck_curate as d
import deck_home as h

LOG = "/tmp/deck_home.log"
say = lambda ok, name: print(f"{'PASS' if ok else 'FAIL'}  {name}")
front = lambda: str(d.focus_now()) == h.ESDE_APP


def copies():
    """(ES-DE processes, Prism reapers)"""
    n = [0, 0]
    for p in h.prism_session_pids():
        try:
            n[open(f"/proc/{p}/comm").read().strip() != "es-de"] += 1
        except OSError:
            pass
    return tuple(n)


d._focus_cache[0] = 0
ok0 = bool(d.esde_pid()) and front() and h.steam_tracks_prism() is True and copies() == (1, 1)
say(ok0, f"before: Prism in front, known to Steam, one copy {copies()}")
if ok0:
    mark = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    subprocess.run(["kill", "-TERM", str(d.esde_pid())])            # ES-DE quits cleanly on SIGTERM
    for _ in range(40):
        time.sleep(0.5)
        if not h.prism_session_pids():
            break
    say(not h.prism_session_pids(), "ES-DE and its Steam session closed")
    open(h.FAKE_RESTART, "w").close()
    t0, back = time.time(), False
    while time.time() - t0 < 150:
        time.sleep(1)
        d._focus_cache[0] = 0
        if d.esde_pid() and front():
            back = True
            break
    took = time.time() - t0
    for _ in range(30):                                            # let it finish starting before the next test
        if os.path.exists("/tmp/prism_ready") and os.path.getmtime("/tmp/prism_ready") > t0:
            break
        time.sleep(1)
    time.sleep(3)
    new = open(LOG, errors="replace").read()[mark:]
    launches = new.count("starting Prism (")
    say(back, f"Prism relaunched and in front after the restart ({took:.0f}s)")
    say(launches == 1 and "after the Game Mode session restarted" in new, f"launched exactly once ({launches})")
    say(copies() == (1, 1), f"one ES-DE, one Prism Steam session {copies()}")
    say(h.steam_tracks_prism() is True, "Steam knows Prism is running")
    if not back and not h.prism_session_pids():
        h.start_esde("self-test: relaunch failed, putting Prism back")
        time.sleep(20)
    if os.path.exists(h.FAKE_RESTART):
        os.remove(h.FAKE_RESTART)
