#!/usr/bin/env python3
"""Nightly ES-DE scrape of games that have no video yet (ScreenScraper, the owner's account, 20k calls/day).

Drives ES-DE's own scraper with a virtual gamepad: Main menu > Scraper > Scrape these games: NO GAME VIDEO >
Scrape these systems: SELECT ALL > Start. Needs ES-DE in Game Mode with the Deck undocked (ES-DE only takes the
virtual pad in handheld mode). Skips the night if a game is running. Screenshots of each step go to
~/prism/night_scrape/ so a person or agent can check what happened. Run by night-scrape.timer.
"""
import prism_config  # noqa: E402  (paths)
import json, base64, os, subprocess, sys, time

sys.path.insert(0, prism_config.PRISM)
import deck_mcp  # noqa: E402
import prism_state  # noqa: E402

OUT = f"{prism_config.PRISM}/night_scrape"
ESDE_URL = f"steam://rungameid/{prism_config.PRISM_RUNGAMEID}"
GAMES_OPTION_STEPS = 4   # RIGHT presses from ALL GAMES to NO GAME VIDEO (checked 2026-10-06)


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def shot(name):
    os.makedirs(OUT, exist_ok=True)
    r = deck_mcp.tool_screenshot(max_width=960)
    data = [c for c in r if c.get("type") == "image"][0]["data"]
    open(f"{OUT}/{time.strftime('%Y%m%d')}-{name}.jpg", "wb").write(base64.b64decode(data))


def busy():
    """A game or app other than ES-DE is in front (background apps like Plex left open don't count)."""
    front = prism_state.front_app(fresh=True)
    if front is not None:
        return front not in (prism_state.PRISM_APP, prism_state.STEAM_UI)   # ES-DE, or Steam's own UI
    return prism_state.other_apps()


def idle_minutes():
    """Minutes since the owner last touched the Deck (deck_curate writes /tmp/deck_last_input on any button/touch)."""
    try:
        last = os.path.getmtime("/tmp/deck_last_input")
    except OSError:
        last = time.time() - float(open("/proc/uptime").read().split()[0])   # nothing since boot
    return (time.time() - last) / 60


FORCE = os.path.exists("/tmp/deck_run_now")   # the owner: "start it now" - skip the idle waits for this run


def wait_until_idle(need=20, give_up_h=10):
    """The 01:30 timer also fires late, right after the Deck wakes from sleep. Never restart ES-DE under someone
    using it: wait for `need` minutes without input (checking every 2 min), give up after `give_up_h` hours."""
    t0 = time.time()
    if FORCE:
        return True
    while idle_minutes() < need or busy():
        if time.time() - t0 > give_up_h * 3600:
            return False
        time.sleep(120)
    return True


def on_charger():
    try:
        return open("/sys/class/power_supply/ACAD/online").read().strip() == "1"
    except OSError:
        return True


def wait_for_scrape(max_h=4):
    """Keep this service running until ES-DE stops writing media (no new file for 6 min), so deck_autosleep.py
    does not put the Deck to sleep mid-scrape."""
    media = f"{prism_config.TOOLS}/downloaded_media"
    t0 = time.time(); last_new = time.time()
    while time.time() - t0 < max_h * 3600:
        time.sleep(60)
        r = subprocess.run(["find", media, "-type", "f", "-newermt", "-2 minutes", "-print", "-quit"],
                           capture_output=True, text=True).stdout.strip()
        if r:
            last_new = time.time()
        elif time.time() - last_new > 360:
            log("scrape finished (no new media for 6 min)"); return
    log("scrape still going after the time limit")


def night_tests(hours=2):
    """Boot-test games for up to `hours` on the charger (2026-10-06), then tidy up."""
    if not on_charger() or (idle_minutes() < 20 and not FORCE):
        log("skipping boot tests (unplugged or in use)"); return
    log(f"boot tests for up to {hours} h")
    subprocess.run(["systemctl", "--user", "reset-failed", "game-tester-night"], capture_output=True)
    subprocess.run(["systemd-run", "--user", "--unit=game-tester-night", "--wait", "--nice=5",
                    f"--property=RuntimeMaxSec={int(hours * 3600 + 900)}",
                    "/usr/bin/python3", "-u", f"{prism_config.PRISM}/game_tester.py",
                    "--retest", "--night", f"--hours={hours}"], capture_output=True)
    for p in subprocess.run(["pgrep", "-f", f"SteamLaunch AppId={prism_config.TESTER_APPID}"], capture_output=True, text=True).stdout.split():
        subprocess.run(["pkill", "-P", p]); subprocess.run(["kill", p])   # a test game left open
    log("boot tests done")


def night_video_trim(minutes=75):
    """Cut the fade-from-black off preview clips, a slice per night on the charger (video_trim.py, 2026-10-08)."""
    if not on_charger() or (idle_minutes() < 20 and not FORCE):
        log("skipping video trim (unplugged or in use)"); return
    log(f"video trim for up to {minutes} min")
    subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/video_trim.py", f"--minutes={minutes}"],
                   capture_output=True, timeout=minutes * 60 + 900)
    log("video trim done")


def night_selftest():
    """Prism self-test (selftest/run.py) with Prism freshly reopened; report in selftest/reports/."""
    if idle_minutes() < 20 and not FORCE:
        log("skipping self-test (Deck in use)"); return
    log("self-test")
    try:
        subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/selftest/run.py"] + (["--force"] if FORCE else []),
                       capture_output=True, timeout=2700)
    except subprocess.TimeoutExpired:
        log("self-test timed out")
    try:
        r = json.load(open(f"{prism_config.PRISM}/selftest/reports/latest.json"))
        log(f"self-test: {r['passed']}/{r['total']} passed" + (f"; failed: {'; '.join(r['failed'])}" if r["failed"] else ""))
    except Exception:
        pass


def finish_night():
    """Leave ES-DE open for the morning and go back to sleep (nobody is using the Deck)."""
    # apply tonight's test results (unhide games that now pass, new per-game emulators) before the morning
    subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/esde_pending.py"])
    time.sleep(30)
    if subprocess.run(["pgrep", "-x", "es-de"], capture_output=True).returncode != 0:
        subprocess.Popen(["/usr/bin/steam", "-ifrunning", ESDE_URL], env=__import__("deck_mcp").session_env(), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(30)
    night_selftest()                     # with Prism freshly reopened, before sleeping
    if FORCE:
        try:
            os.remove("/tmp/deck_run_now")
        except OSError:
            pass
    if idle_minutes() > 20 or FORCE:
        log("night work done, sleeping")
        subprocess.run(["systemctl", "suspend"])


def main():
    try:                        # daily copy of every gamelist, 14 days kept (2026-10-13), whatever the night does
        import gamelist
        lost = gamelist.guard()  # entries gone from a gamelist although the ROM is there (restored below)
        if lost:
            log("gamelist losses: " + ", ".join(f"{s} {len(f['lost'])}+{len(f['thin'])}" + (" (broken)" if f["broken"] else "")
                                                for s, f in lost.items()))
        made = gamelist.daily_backup()
        log(f"gamelists backed up: {made}" if made else "gamelists: today's backup exists")
    except Exception as e:
        log(f"gamelist backup failed: {e}")
    # night-scrape.timer wakes the Deck at 01:30 (WakeSystem): only work on the charger, otherwise sleep again
    # an open-ended boot-test run the owner asked for (continuous_run.py) owns the Deck until he stops it
    for unit in ("deck-continuous", "game-tester-night", "deck-clips"):
        if subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit]).returncode == 0:
            log(f"{unit} is running, skipping tonight"); return
    if not on_charger():
        log("on battery, skipping tonight")
        if idle_minutes() > 30:
            subprocess.run(["systemctl", "suspend"])
        return
    if not wait_until_idle():
        log("the Deck stayed in use, skipping tonight"); return
    if busy():
        log("a game is running, skipping tonight"); return
    # fresh ES-DE session: the scraper menu then starts at ALL GAMES, so the option steps land on NO GAME VIDEO;
    # gamelist upkeep runs while it is closed (prism_esde.esde_closed: busy flag, cover, relaunch)
    import prism_esde
    if prism_state.game_running(prism_state.esde_pid()) or prism_state.media_players():
        log("a game or player is open in Prism, skipping tonight"); return
    try:                         # (busy() above already let background Steam apps through, as before)
        with prism_esde.esde_closed("nightly maintenance", require_idle=False, relaunch="always"):
            try:
                import gamelist
                for s, f in gamelist.guard(fix=True).items():
                    log(f"gamelist {s}: restored {f['restored']} lost entries, {len(f['thin'])} thinned, from the daily backups")
            except Exception as e:
                log(f"gamelist guard failed: {e}")
            subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/esde_maintenance.py"])
    except prism_esde.NotIdle as e:
        log(f"not idle ({e}), skipping tonight"); return
    time.sleep(22)               # ES-DE up ~30 s before the keys (esde_closed waited 8 s after it appeared)
    # keyboard to ES-DE's window (xdotool): works docked or not (the virtual gamepad is ignored by ES-DE when docked)
    KEYS = {"A": "Return", "B": "BackSpace", "X": "Delete", "Y": "Insert", "START": "Escape", "SELECT": "F1",
            "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right"}
    esde = subprocess.run(["pgrep", "-x", "es-de"], capture_output=True, text=True).stdout.split()
    env = dict(os.environ)
    if esde:
        for kv in open(f"/proc/{esde[0]}/environ", "rb").read().split(b"\0"):
            if kv.startswith(b"DISPLAY="):
                env["DISPLAY"] = kv.split(b"=", 1)[1].decode()
    win = subprocess.run(["xdotool", "search", "--pid", esde[0]], env=env, capture_output=True, text=True).stdout.split()[:1] if esde else []

    def tap(btn, wait=0.6):
        if win:
            subprocess.run(["xdotool", "key", "--window", win[0], KEYS[btn]], env=env)
        time.sleep(wait)

    for _ in range(4):          # close dialogs, back out of any game list or menu to the console row
        tap("B", 0.7)
    tap("START", 1.2)            # Main menu, SCRAPER is the first row
    tap("A", 1.2)
    shot("1-scraper")
    tap("DOWN")                  # SCRAPE THESE GAMES (resets to ALL GAMES each time the menu opens)
    for _ in range(GAMES_OPTION_STEPS):
        tap("RIGHT", 0.4)
    tap("DOWN")                  # SCRAPE THESE SYSTEMS
    tap("A", 1.0)
    for _ in range(80):          # to the button row (BACK, SELECT ALL, SELECT NONE); DOWN doesn't wrap
        tap("DOWN", 0.08)
    tap("RIGHT", 0.4)            # SELECT ALL
    tap("A", 0.8)
    shot("2-systems")
    tap("B", 1.0)
    shot("3-ready")
    tap("Y", 5)                  # start scraper
    shot("4-running")
    log("scrape started")
    wait_for_scrape()
    night_tests()
    night_video_trim()
    finish_night()


if __name__ == "__main__":
    main()
