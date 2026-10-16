#!/usr/bin/env python3
"""Prism nightly self-test (architecture option 1, 2026-10-08). Run by esde_night_scrape.finish_night() after
Prism is reopened and before the Deck sleeps; or by hand: python3 ~/prism/selftest/run.py [--force].

Checks the things that went wrong before (stuck audio sink, orphan players, leftover touch readers, Steam menus left
suppressed) and replays the playtest paths with virtual input: Steam-button card (menus / in a game / taps), rescue
chord, hint-bar taps, movies (+ Up Next), music, the movie player's touch HUD. Speaker muted while it runs, Plex
progress untouched (plex/no_report). Report: ~/prism/selftest/reports/<date>.txt (+ latest.txt / latest.json).
"""
import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Prism root
import prism_config  # noqa: E402  (paths)
import fixtures  # noqa: E402  (this Deck's games: ~/.config/prism/selftest.json)
import datetime, glob, json, os, re, subprocess, sys, time

HOME = os.path.expanduser("~")
DM = prism_config.PRISM
HERE = f"{DM}/selftest"
REP = f"{HERE}/reports"
sys.path.insert(0, DM)
results = []                      # (group, name, ok, detail)


def add(group, name, ok, detail=""):
    results.append((group, name, bool(ok), detail))


def sh(*a, timeout=30):
    return subprocess.run(list(a), capture_output=True, text=True, timeout=timeout).stdout


def idle_minutes():
    try:
        return (time.time() - os.path.getmtime("/tmp/deck_last_input")) / 60
    except OSError:
        return 999


def run_test(group, script, log=None, timeout=420, args=()):
    """Run one test script; collect its PASS/FAIL lines (stdout, or the log file it writes)."""
    if log:
        open(log, "w").close()
    t0 = time.time()
    try:
        p = subprocess.run(["python3", script, *args], capture_output=True, text=True, timeout=timeout, cwd=DM)
        out = p.stdout + (open(log, errors="replace").read().replace("\0", "") if log else "")
    except subprocess.TimeoutExpired:
        add(group, f"{os.path.basename(script)} finished", False, f"timed out after {timeout}s"); out = ""
    n = 0
    for line in out.splitlines():
        m = re.search(r"\b(PASS|FAIL)\s+(.*)$", line)
        if m:
            add(group, m.group(2).strip()[:110], m.group(1) == "PASS"); n += 1
    if n == 0 and out:
        add(group, f"{os.path.basename(script)} reported results", False, out.strip().splitlines()[-1][:160])
    cleanup(f"after {group}")
    return time.time() - t0


def cleanup(why):
    """Never leave a game, card, player or test flag behind between tests."""
    import deck_curate as d
    try:
        if d.cc_request({"cmd": "state"}, timeout=0.5) != "IDLE":
            d.cc_request({"cmd": "hide"}, timeout=0.5)
    except OSError:
        pass
    if sh("bash", "-c", "ps -eo args | grep -c '/Emulation/roms/[a-z]'").strip() not in ("0", ""):
        d.rescue(); time.sleep(1); d.quit_game(); time.sleep(2)
    for pat in ("plex_play.py", "deck_music.py"):
        for pid in sh("pgrep", "-f", pat).split():
            subprocess.run(["kill", pid])
    subprocess.run(["systemctl", "--user", "stop", "deck-music-player"], capture_output=True)
    d.steam_menus_release()
    for _ in range(3):
        if d.selected() is None:
            break
        d.esde_keys(["BackSpace"]); time.sleep(0.8)


def to_test_game(tries=3):
    """Prism on Puyo Pop (GBA) in its game list. Checks each step; True when there."""
    import deck_curate as d
    for attempt in range(tries):
        subprocess.run(["python3", f"{DM}/esde_nav.py", "gba"], capture_output=True, timeout=120)
        for _ in range(4):                       # open the list (a key can be lost right after another test)
            if d.selected() is not None:
                break
            d.esde_keys(["Return"]); time.sleep(1.5)
        for _ in range(260):
            s = d.selected()
            if s and (fixtures.gba_game() or s[0]) in s[0]:
                return True
            if s is None:
                break
            d.esde_keys(["Down"]); time.sleep(0.12)
        cleanup(f"retry {attempt + 1} to reach the test game")
    return False


def step(group, fn, *a, **k):
    """Run one part of the suite; a crash becomes a FAIL line instead of ending the run."""
    try:
        return fn(*a, **k)
    except Exception as e:
        add(group, "ran without an error", False, f"{type(e).__name__}: {e}"[:160])
        try:
            cleanup(f"after crash in {group}")
        except Exception:
            pass


def health():
    import deck_curate as d
    g = "health"
    add(g, "switchhome supervisor running", sh("systemctl", "--user", "is-active", "switchhome").strip() == "active")
    log = open("/tmp/switchhome.log", errors="replace").read() if os.path.exists("/tmp/switchhome.log") else ""
    since = log.rsplit("buttons: starting", 1)[-1]
    add(g, "no supervised part crashed since the last start", "crashed" not in since,
        "; ".join(l for l in since.splitlines() if "crashed" in l)[:160])
    pid = sh("systemctl", "--user", "show", "switchhome", "-p", "MainPID", "--value").strip()
    try:
        a = sum(map(int, open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[11:13])); time.sleep(10)
        b = sum(map(int, open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[11:13]))
        cpu = (b - a) / 10   # % of one core (100 ticks/s)
        add(g, f"supervisor CPU {cpu:.0f}% of a core (limit 8%)", cpu <= 8)
    except (OSError, ValueError):
        add(g, "supervisor CPU measurable", False)
    hid = [p for p in sh("pgrep", "-f", prism_config.PRISM + "/").split()
           if any("hidraw" in os.path.realpath(f) for f in glob.glob(f"/proc/{p}/fd/*"))]
    add(g, f"one controller reader ({len(hid)})", len(hid) == 1)
    procs = [l.split(None, 2) for l in sh("ps", "-eo", "pid=,ppid=,args=").splitlines()]
    tr = {p[0]: p for p in procs if len(p) == 3 and "touch_reader.py" in p[2]}
    readers = [p[1] + " " + p[2] for p in tr.values() if p[1] not in tr]       # top of each chain (sudo or not)
    add(g, f"touch readers: only the supervisor's ({len(readers)})", len(readers) == 1 and readers[0].split()[0] == pid)
    add(g, "no orphan video/music players", sh("pgrep", "-c", "-x", "mpv-bin").strip() in ("0", ""))
    add(g, "Plex test flag not left behind", not os.path.exists(f"{DM}/plex/no_report"))
    sink = sh("pactl", "get-default-sink").strip()
    add(g, f"audio output is a real device ({sink.split('.')[-1] or 'none'})", sink and "clip_rec" not in sink)
    add(g, "no leftover recorder sink", "clip_rec" not in sh("pactl", "list", "short", "sinks"))
    br = d.bridge("state")
    add(g, "Decky bridge answers", br is not None)
    add(g, "Steam menus not left suppressed", br is not None and br["data"].get("suppress", 0) == 0)
    add(g, "Steam toasts not left hidden", br is not None and not br["data"].get("toastsOff"))
    add(g, "Prism running and in front", d.esde_pid() and str(d.focus_now()) == prism_config.PRISM_APPID)
    import deck_home
    n = len(sh("pgrep", "-x", "es-de").split())
    add(g, f"one copy of Prism ({n})", n == 1)
    add(g, "Steam knows Prism is running (not orphaned by a Steam restart)", deck_home.steam_tracks_prism() is True)


def main():
    force = "--force" in sys.argv
    os.makedirs(REP, exist_ok=True)
    day = datetime.date.today().isoformat()
    if idle_minutes() < 20 and not force:
        print("Deck in use, self-test skipped"); return
    import deck_curate as d
    t_all = time.time()
    health()
    mute_before = "MUTED" in sh("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@")
    subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"])
    open(f"{DM}/plex/no_report", "w").close()
    try:
        step("start", cleanup, "start")
        step("buttons", run_test, "buttons", f"{HERE}/chord_test.py", timeout=60)
        step("card", run_test, "card", f"{DM}/steambtn_test.py", log=f"{DM}/steambtn_test.log")
        for group, script, log in (("card in a game", f"{DM}/steambtn_game_test.py", f"{DM}/steambtn_test.log"),
                                   ("rescue", f"{HERE}/rescue_test.py", f"{DM}/rescue_test.log")):
            if step(group, to_test_game):
                step(group, run_test, group, script, log=log)
            else:
                add(group, "reach the test game (Puyo Pop, GBA)", False)
        step("touch", run_test, "touch", f"{HERE}/hint_card_test.py", timeout=200)
        import prism_features
        if prism_features.enabled("plex"):        # Movies & TV (and Music) only when that extra is on
            step("movies", run_test, "movies", f"{HERE}/movie_test.py", log=f"{DM}/media_card_test.log", timeout=500)
            step("up next", run_test, "up next", f"{HERE}/upnext_test.py", log=f"{DM}/media_card_test.log", timeout=300)
            step("music", run_test, "music", f"{HERE}/music_test.py", log=f"{DM}/media_card_test.log", timeout=150)
            step("movie touch HUD", run_test, "movie touch HUD", f"{HERE}/tl_test.py", timeout=90)
            step("cover flow + For You", run_test, "cover flow + For You", f"{HERE}/flow_store_test.py", timeout=180)
            step("music shortcut", run_test, "music shortcut", f"{HERE}/music_router_test.py", timeout=90)
        else:
            add("extras", "Movies & TV tests skipped (that extra is off)", True)
        step("relaunch", run_test, "relaunch after a session restart", f"{HERE}/relaunch_test.py", timeout=240)
    finally:
        step("end", cleanup, "end")
        try:
            os.remove(f"{DM}/plex/no_report")
        except OSError:
            pass
        subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if mute_before else "0"])
    health_after = len(results)
    step("after tests", health)               # the same checks again: nothing left behind by the tests
    for i in range(health_after, len(results)):
        g, n, ok, det = results[i]; results[i] = ("after tests", n, ok, det)
    fails = [r for r in results if not r[2]]
    lines = [f"Prism self-test {datetime.datetime.now():%Y-%m-%d %H:%M}  -  "
             f"{len(results) - len(fails)}/{len(results)} passed  ({(time.time() - t_all) / 60:.0f} min)", ""]
    group = None
    for g, n, ok, det in results:
        if g != group:
            lines.append(f"[{g}]"); group = g
        lines.append(f"  {'PASS' if ok else 'FAIL'}  {n}" + (f"   ({det})" if det else ""))
    text = "\n".join(lines) + "\n"
    open(f"{REP}/{day}.txt", "w").write(text)
    open(f"{REP}/latest.txt", "w").write(text)
    json.dump({"date": day, "passed": len(results) - len(fails), "total": len(results),
               "failed": [f"{g}: {n}" for g, n, ok, det in fails]}, open(f"{REP}/latest.json", "w"), indent=1)
    print(text)


if __name__ == "__main__":
    main()
