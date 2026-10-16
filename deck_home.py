#!/usr/bin/env python3
"""Console-style behaviour for the Deck (2026-10-06), run as deck-home.service:

  * Boot straight into Prism: once Steam's Game Mode UI is up, start ES-DE.
  * Prism always comes back: if ES-DE is closed (Steam menu > Exit game, a crash, the power menu's Exit to
    Steam) and the Deck then sits in Steam's UI for 3 minutes without input, ES-DE is started again.
  * Prism comes back after the Game Mode session restarts (2026-10-09): a gamescope crash restarts gamescope and Steam
    (ES-DE dies with them) and is handled like a boot; after a Steam client restart ES-DE survives but Steam has
    lost it (not in its running apps, can't be brought to the front), so that idle orphan is closed and relaunched.
    Never while ES-DE or Prism's Steam session is still alive (no "Game already running"), never while an agent
    holds /tmp/prism_busy.
  * Sleep during games too: no input for 10 minutes while a game is in front (30 when docked to a TV) -> sleep;
    waking returns to the game. Never while Plex/Kodi are in front (video), never while an agent is working.
    (In ES-DE's menus deck_autosleep.py handles it: dim at 4 min, sleep 1 min later.)
"""
import prism_config  # noqa: E402  (paths)
import glob, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deck_autosleep   # noqa: E402  (agent_working, busy, idle_minutes)
import prism_state      # noqa: E402  (what's running: one implementation, 2026-10-13)

ESDE_APP = prism_config.PRISM_APPID
STEAM_UI = ("769", "0", "")
MEDIA_APPS = {str(a) for a in prism_config.get("media_appids", [])}   # media shortcuts (Plex HTPC, Kodi, Request Movies)
ESDE_URL = f"steam://rungameid/{prism_config.PRISM_RUNGAMEID}"
GAME_IDLE_MIN, GAME_IDLE_DOCKED_MIN, UI_IDLE_MIN = 10, 30, 3
LOG = "/tmp/deck_home.log"
BUSY_FLAG = "/tmp/prism_busy"     # an agent is restarting Steam / changing its setup on purpose: hands off
FAKE_RESTART = "/tmp/prism_fake_restart"   # selftest/relaunch_test.py: "the session just restarted"
RELAUNCH_GAP = 90                 # seconds between two launches of Prism, whatever asked for them


def log(msg):
    with open(LOG, "a") as f:
        f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")


def env():
    try:
        import deck_mcp
        return {**os.environ, **deck_mcp.session_env(), "DISPLAY": ":0"}
    except Exception:
        return {**os.environ, "DISPLAY": ":0", "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}


def focused_app():
    try:
        out = subprocess.run(["xprop", "-root", "GAMESCOPE_FOCUSED_APP"], env=env(), capture_output=True,
                             text=True, timeout=3).stdout
        return out.split("=")[-1].strip() if "=" in out else ""
    except Exception:
        return None   # no gamescope (Desktop Mode) or X not ready


def esde_running():
    return bool(prism_state.esde_pids())


def steam_ready():
    return subprocess.run(["pgrep", "-f", "steamwebhelper"], capture_output=True).returncode == 0


def docked():
    import prism_profile
    return prism_profile.docked()


def game_in_front(app):
    """Something other than Steam's UI / ES-DE's menus is in front: an emulator ES-DE launched, or a Steam game."""
    if app is None or app in STEAM_UI or app in MEDIA_APPS:
        return False
    if app == ESDE_APP:
        # ES-DE itself is in front: a game is running only if ES-DE has launched something
        try:
            import deck_curate
            pid = deck_curate.esde_pid()
            return bool(pid) and not deck_curate.esde_idle(pid)
        except Exception:
            return False
    return True


def start_esde(why):
    log(f"starting Prism ({why})")
    subprocess.Popen(["/usr/bin/steam", "-ifrunning", ESDE_URL], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, env=env())


def decky_settled(since):
    """Decky Loader restarts Steam's UI once when it hooks in; launching Prism before that leaves Steam's home
    screen on top of it (2026-10-08). Ready = Decky not installed/running, its loader is in Steam's UI, or 2 min."""
    if subprocess.run(["systemctl", "is-active", "-q", "plugin_loader"]).returncode != 0:
        return True
    if time.time() - since > 120:
        return True
    try:
        import deck_curate
        return deck_curate.steam_js("String(!!window.DeckyPluginLoader)", "decky") == "true"
    except Exception:
        return False


def movie_playing():
    """A Prism movie/episode is playing (plex_play.py's mpv, not paused): watching doesn't need input, so the
    in-game idle sleep must not fire (2026-10-08). A paused movie sleeps normally."""
    import json, socket
    if not prism_state.scripts_running(["plex_play.py"]):
        return False
    try:
        s = socket.socket(socket.AF_UNIX); s.settimeout(1)
        s.connect(f"/run/user/{os.getuid()}/app/io.mpv.Mpv/switchhome-mpv.sock")
        s.sendall(b'{"command": ["get_property", "pause"]}\n')
        reply = s.recv(4096).decode(); s.close()
        return '"data":false' in reply.replace(" ", "")
    except OSError:
        return True      # player starting up / socket not ready: assume watching


def webhelper_pid():
    out = subprocess.run(["pgrep", "-o", "-f", "steamwebhelper.*-type=gpu|steamwebhelper$|steamwebhelper "],
                         capture_output=True, text=True).stdout.split()
    return out[0] if out else None


def session_sig():
    """(gamescope pid, Steam client pid) of the Game Mode session. A gamescope crash restarts the whole session (both
    change, ES-DE dies with it); a Steam client restart changes the second (ES-DE survives, Steam forgets it)."""
    def oldest(*names):
        for n in names:
            out = subprocess.run(["pgrep", "-o", "-x", n], capture_output=True, text=True).stdout.split()
            if out:
                return out[0]
        return None
    return oldest("gamescope-wl", "gamescope"), oldest("steam")


def prism_session_pids():
    """ES-DE and any reaper still holding Prism's Steam session (reaper SteamLaunch AppId=2768122926). Read from /proc,
    not pgrep -f: that pattern would also match the command line of whoever is asking."""
    return sorted(prism_state.esde_pids() + prism_state.prism_reapers())


def busy_flag():
    try:
        return time.time() - os.path.getmtime(BUSY_FLAG) < 1800     # a flag older than 30 min was left behind
    except OSError:
        return False


def steam_tracks_prism():
    """Steam's own running-apps list has Prism: True / False, or None when Steam's UI can't be asked."""
    try:
        import deck_curate
        r = deck_curate.steam_js("String((SteamUIStore.RunningApps || []).some(a => String(a.appid) == '%s' || "
                                 "String(a.gameid) == '%s'))" % (ESDE_APP, ESDE_URL.rsplit("/", 1)[1]), "running apps")
    except Exception:
        return None
    return {"true": True, "false": False}.get(r)


def esde_occupied():
    """ES-DE has a game going, or a movie / music player runs: never close that."""
    for pid in prism_state.esde_pids():
        for k in prism_state.children(pid):
            if os.path.exists(f"/proc/{k}") and not any(prism_state.SCRIPTS in x for x in prism_state.argv(k)):
                return True
    return bool(prism_state.scripts_running(["plex_play.py", "deck_music.py"]))


def end_orphaned_prism():
    """After a Steam client restart ES-DE keeps running but Steam no longer knows it: it can't come to the front, and
    launching Prism again only starts a second copy. Close the idle orphan(s) so Prism can be launched cleanly."""
    if esde_occupied():
        log("Prism outlived a Steam restart but has a game or player open: leaving it")
        return False
    pids = subprocess.run(["pgrep", "-x", "es-de"], capture_output=True, text=True).stdout.split()
    log(f"Steam restarted and lost Prism (ES-DE {' '.join(pids)} still running): closing it to relaunch")
    for p in pids:
        subprocess.run(["kill", "-TERM", p])
    for _ in range(30):
        time.sleep(0.5)
        if not prism_session_pids():
            return True
    log(f"old Prism session still open after 15 s ({prism_session_pids()}): not relaunching on top of it")
    return False


def started(pid):
    try:
        return int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return 0


def end_older_copies(pids):
    """pids oldest first: close every copy of ES-DE but the newest, unless it has a game open."""
    for p in pids[:-1]:
        busy = False
        for task in glob.glob(f"/proc/{p}/task/*/children"):
            try:
                busy |= any(b"/ES-DE/scripts/" not in open(f"/proc/{k}/cmdline", "rb").read()
                            for k in open(task).read().split())
            except OSError:
                pass
        if busy:
            continue
        log(f"two copies of Prism running ({' '.join(pids)}): closing the older one ({p}), Steam lost it")
        subprocess.run(["kill", "-TERM", p])


def end_leftover_session(reapers):
    """ES-DE is gone but Prism's Steam session isn't (a helper it started still runs inside it): Steam would answer a
    launch with "Game already running". End what is left in it, like the rescue chord does."""
    import deck_curate
    todo = [str(r) for r in reapers]
    while todo:                                   # the launcher chain itself is protected: look below it
        for k in subprocess.run(["pgrep", "-P", todo.pop()], capture_output=True, text=True).stdout.split():
            if deck_curate.protected(int(k)):
                todo.append(k)
            else:
                subprocess.run(["pkill", "-TERM", "-P", k], capture_output=True)
                subprocess.run(["kill", "-TERM", k], capture_output=True)
                subprocess.run(["sudo", "-n", "pkill", "-TERM", "-P", k], capture_output=True)
                subprocess.run(["sudo", "-n", "kill", "-TERM", k], capture_output=True)
                log(f"ended leftover {k} in Prism's old Steam session")


def back_to_switch_home_after_ui_restart(state):
    """Decky (or a Steam update) restarts Steam's UI, which comes up on its home screen OVER a running Prism.
    If nobody has touched the Deck since, return to Prism the way Steam's own menu does (2026-10-08)."""
    pid = webhelper_pid()
    if pid != state.get("wh"):
        if state.get("wh") is not None:
            state["restart_at"] = time.time()
            log(f"Steam's UI restarted (webhelper {state.get('wh')} -> {pid})")
        state["wh"] = pid
    t = state.get("restart_at")
    if not t or time.time() - t > 120:
        return
    if not esde_running() or focused_app() not in STEAM_UI:
        return
    try:
        import deck_curate
        if deck_curate._input_at[0] > t:      # someone is using Steam's UI on purpose
            state["restart_at"] = None; return
        r = deck_curate.bridge("to_game")
        if r is None:
            deck_curate.steam_js("SteamUIStore.WindowStore.GamepadUIMainWindowInstance.NavigateToRunningApp(), 1", "to_game")
        log("Steam's UI covered Prism after its restart: back to Prism")
        state["restart_at"] = None
    except Exception as e:
        log(f"back to Prism failed: {e}")


def main():
    booted = False          # started ES-DE once since this service began / since the session last restarted
    restarted = False       # ...and that was a Game Mode session restart, not the boot
    ui_since = None         # Steam UI in front with no ES-DE since...
    wh = {}                 # Steam UI restart watch
    sig = None              # (gamescope, Steam) pids last seen
    resync = time.time()    # since when to check that Steam still knows a running Prism (start / session restart)
    last_launch = 0.0
    waiting = None          # last "not launching because..." logged
    held_since = 0.0
    while True:
        for _ in range(5):              # every 3 s: catch Steam's UI coming up over Prism quickly
            time.sleep(3)
            back_to_switch_home_after_ui_restart(wh)
        app = focused_app()
        if app is None or not steam_ready():
            continue        # Desktop Mode, or Steam still starting
        idle = deck_autosleep.idle_minutes()
        agent = deck_autosleep.agent_working()

        # Game Mode session restart (gamescope crash, Steam client restart): bring Prism back like at boot. The idle
        # watchdog below is for a Prism someone closed on purpose (3 min, waits for agents): it left the Deck on
        # Steam's home after the 2026-10-08 21:51 gamescope crash.
        s = session_sig()
        if all(s) and s != sig:
            if sig is not None:
                log(f"Game Mode session restarted (gamescope {sig[0]} -> {s[0]}, Steam {sig[1]} -> {s[1]}): "
                    "Prism comes back once Steam and Decky are ready")
                booted, restarted, ui_since, resync, waiting = False, True, None, time.time(), None
            sig = s
        if os.path.exists(FAKE_RESTART):            # the self-test can't crash gamescope: it closes ES-DE and says so
            os.remove(FAKE_RESTART)
            log("self-test: treating this as a Game Mode session restart")
            booted, restarted, ui_since, resync, waiting = False, True, None, time.time(), None

        # boot / watchdog
        if not esde_running() and app in STEAM_UI:
            ui_since = ui_since or time.time()
            old = prism_session_pids()
            hold = ("an agent holds " + BUSY_FLAG if busy_flag() else
                    f"Prism's old Steam session is still open ({old[0]})" if old else
                    "launched it less than %d s ago" % RELAUNCH_GAP if time.time() - last_launch < RELAUNCH_GAP else None)
            if hold and hold != waiting and not booted:
                log(f"not starting Prism yet: {hold}")
                held_since = time.time()
            if old and not booted and hold == waiting and time.time() - held_since > 60 and not busy_flag():
                end_leftover_session(old); held_since = time.time()   # stuck a minute without ES-DE: clear it
            waiting = hold
            if not hold and not booted and time.time() - ui_since > 20 and decky_settled(ui_since):
                start_esde("after the Game Mode session restarted" if restarted else "boot")
                booted, last_launch, ui_since = True, time.time(), None; continue
            if (not hold and booted and idle >= UI_IDLE_MIN and time.time() - ui_since > UI_IDLE_MIN * 60
                    and not agent):
                start_esde("it was closed and the Deck sat idle in Steam")
                last_launch, ui_since = time.time(), None; continue
        else:
            ui_since = None
            if esde_running():
                booted = True

        # after a restart (or when this service starts): a Prism that Steam no longer knows is an orphan
        if resync:
            if app == ESDE_APP:
                resync = None                         # Prism (or a game it launched) is in front: all well
            elif (esde_running() and app in STEAM_UI and time.time() - resync > 20 and not busy_flag()
                  and decky_settled(resync) and steam_tracks_prism() is False):
                time.sleep(3)                         # ask twice: Steam fills the list a moment after its UI starts
                if steam_tracks_prism() is False and end_orphaned_prism():
                    booted, restarted, ui_since = False, True, None
                resync = None
            if resync and time.time() - resync > 600:
                resync = None
        # two copies: Prism was launched again after Steam lost the first one (12:00 on 2026-10-09). Steam answers a
        # launch with "already running" while it tracks one, so the older copies are the orphans.
        copies = subprocess.run(["pgrep", "-x", "es-de"], capture_output=True, text=True).stdout.split()
        if len(copies) > 1 and not busy_flag():
            end_older_copies(sorted(copies, key=started))

        # safety: back to the full power limit whenever no game is running (game-end may not fire after a crash)
        if not game_in_front(app):
            try:
                cap = open("/sys/class/hwmon/hwmon5/power1_cap").read().strip()
                if cap != "15000000":
                    subprocess.run([f"{prism_config.PRISM}/power_profile.sh", "restore"])
            except OSError:
                pass

        # sleep during games
        limit = GAME_IDLE_DOCKED_MIN if docked() else GAME_IDLE_MIN
        if game_in_front(app) and idle >= limit and not agent and not deck_autosleep.busy() and not movie_playing():
            log(f"no input for {idle:.0f} min in a game ({app}), sleeping")
            subprocess.run(["systemctl", "suspend"])
            time.sleep(30)


if __name__ == "__main__":
    main()
