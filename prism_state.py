#!/usr/bin/env python3
"""What is running on the Deck, and is Prism idle: the one place that answers it (refactor, 2026-10-13).

Before this there were four diverging checks (prism_restart.playing() matched substrings of `ps` output, missed Steam
games and non-Steam shortcuts such as Project Reclaimer, and matched an agent's own ssh command line; controller_bar's
in_menus; card_matrix's running()/paused(); deck_curate's focus_now()/esde_idle()). Everything here reads /proc and
matches processes by their exact argv / comm / process tree, never by a substring that can appear in another command
line.

  front_app()        Steam app id gamescope has in front ("2768122926" = Prism, "769" = Steam's UI), or None
  steam_launches()   {app id: [reaper pids]}: every Steam game / non-Steam shortcut runs under `reaper SteamLaunch AppId=N`
  other_apps()       running app ids other than Prism (a Steam game, Reclaimer, Movies & TV, ...)
  esde_pid()         ES-DE (comm "es-de"), lowest pid; esde_pids() all copies; prism_reapers() Prism's Steam session
  game_procs(pid)    the game ES-DE launched (emulator + wrappers), from ES-DE's and its reaper's process tree
  game_running(pid), esde_idle(pid), in_menus()
  media_players()    Prism's movie / music players; selftest_running()
  cc_state()         the deck_cc overlay: IDLE / ACTIVE / SAVING (Home card)
  playing()          why something counts as being played ("" = nothing)
  not_idle()         why Prism is not idle ("" = idle: in front, in its menus, nothing played, no card, no busy flag)
  idle()             not not_idle()

`python3 prism_state.py` prints a snapshot."""
import prism_config  # noqa: E402  (paths)
import glob, json, os, socket, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
PRISM_APP = prism_config.PRISM_APPID   # Prism's (ES-DE's) non-Steam shortcut (config.toml steam_appid)
STEAM_UI = "769"                  # gamescope's app id for Steam's own UI / menus
BUSY = "/tmp/prism_busy"          # an agent or job is restarting / launching something: hands off
CC_SOCK = "/tmp/deck_cc.sock"
ESDE_SCREENSAVER = "/tmp/esde_screensaver"
SCRIPTS = b"/ES-DE/scripts/"      # ES-DE event scripts run as its children too; they are not a game
MEDIA_SCRIPTS = ("plex_play.py", "deck_music.py", "deck_music_player.py")


# ---- /proc
def _pids():
    return [int(d) for d in os.listdir("/proc") if d.isdigit()]


def argv(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            a = f.read().split(b"\0")
    except OSError:
        return []
    return a[:-1] if a and a[-1] == b"" else a


def comm(pid):
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except OSError:
        return ""


def _stat(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return []


def proc_state(pid):
    """R / S / T (stopped) / Z ..., or "" when gone"""
    s = _stat(pid)
    return s[0] if s else ""


def ppid(pid):
    s = _stat(pid)
    try:
        return int(s[1])
    except (IndexError, ValueError):
        return 0


def children(pid):
    out = []
    for t in glob.glob(f"/proc/{pid}/task/*/children"):
        try:
            with open(t) as f:
                out += [int(k) for k in f.read().split()]
        except (OSError, ValueError):
            pass
    return out


def script(a):
    """The script a Python process runs (argv[1] after options), or None for anything that isn't Python."""
    if not a or not os.path.basename(a[0]).startswith(b"python"):
        return None
    for x in a[1:]:
        if x in (b"-m", b"-c"):
            return None
        if not x.startswith(b"-"):
            return x.decode(errors="replace")
    return None


def scripts_running(names):
    """pids of Python processes whose script's file name is one of `names`"""
    names = set(names)
    return [p for p in _pids() if os.path.basename(script(argv(p)) or "") in names]


# ---- Steam
def steam_launches():
    """{app id: [reaper pids]} for every Steam game, non-Steam shortcut (Prism, Reclaimer, Movies & TV...) running."""
    out = {}
    for p in _pids():
        a = argv(p)
        if a and os.path.basename(a[0]) == b"reaper" and b"SteamLaunch" in a:
            ids = [x[6:].decode() for x in a if x.startswith(b"AppId=")] or ["?"]
            for i in ids:
                out.setdefault(i, []).append(p)
    return out


def running_appids():
    return set(steam_launches())


def other_apps():
    """App ids running besides Prism itself (sorted)."""
    return sorted(i for i in steam_launches() if i != PRISM_APP)


def prism_reapers():
    return steam_launches().get(PRISM_APP, [])


def steam_running_appid():
    """Steam's own idea of the main running app (SteamUIStore.MainRunningAppID) through the CEF port, or None. Slower
    than steam_launches() (a CEF round trip); for checks that want Steam's view."""
    try:
        import deck_curate
        v = deck_curate.steam_js("String(SteamUIStore.MainRunningAppID || '')", "main running app")
        return v or None
    except Exception:
        return None


_env = [0.0, None]


def _xenv():
    """The Game Mode session's environment (re-read every 30 s: a gamescope restart changes it)."""
    if _env[1] is None or time.monotonic() - _env[0] > 30:
        try:
            import deck_mcp
            e = {**deck_mcp.session_env(), "DISPLAY": ":0"}
        except Exception:
            e = {**os.environ, "DISPLAY": ":0", "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
        _env[:] = [time.monotonic(), e]
    return _env[1]


_focus = [0.0, None]


def front_app(fresh=False):
    """The Steam app id gamescope has in front (GAMESCOPE_FOCUSED_APP on the :0 root window) as a string, or None
    (no gamescope / no answer). Cached 0.4 s unless fresh."""
    now = time.monotonic()
    if not fresh and now - _focus[0] < 0.4:
        return _focus[1]
    val = None
    try:
        out = subprocess.run(["xprop", "-root", "GAMESCOPE_FOCUSED_APP"], env=_xenv(), capture_output=True, text=True,
                             timeout=2).stdout
        if "=" in out:
            v = out.split("=", 1)[1].strip()
            val = v if v.isdigit() else None
    except Exception:
        pass
    _focus[:] = [now, val]
    return val


# ---- ES-DE
def esde_pids():
    return sorted(p for p in _pids() if comm(p) == "es-de")


def esde_pid():
    p = esde_pids()
    return p[0] if p else None


def esde_app(pid):
    """The Steam app id ES-DE runs under (its SteamAppId), normally Prism's."""
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            for kv in f.read().split(b"\0"):
                if kv.startswith(b"SteamAppId="):
                    return kv[11:].decode()
    except OSError:
        pass
    return ""


def other_in_front(pid):
    """Something other than ES-DE is in front (a Steam game, Movies & TV, Steam's menu...). If gamescope doesn't answer:
    another Steam app is running at all. Background apps don't count when it does answer."""
    own = esde_app(pid) if pid else ""
    front = front_app()
    if front is not None:
        return front != own
    return any(i != own for i in steam_launches())


def game_procs(pid):
    """PIDs of the game ES-DE launched (emulator + wrappers): ES-DE's children, plus its reaper's other children (game
    processes whose launch shell exited get re-parented to that reaper). ES-DE event scripts are left out. Empty in the
    menus and for Steam / PC games (those run under their own Steam launch)."""
    if not pid:
        return []
    chain, p = [], pid
    while p > 1:
        chain.append(p); p = ppid(p)
    reaper = next((c for c in chain if (lambda a: a and os.path.basename(a[0]) == b"reaper" and b"SteamLaunch" in a)
                   (argv(c))), None)
    branch = set(chain)
    todo = children(pid) + ([k for k in children(reaper) if k not in branch] if reaper else [])
    out = []
    while todo:
        k = todo.pop()
        if any(SCRIPTS in x for x in argv(k)) or not os.path.exists(f"/proc/{k}"):
            continue
        out.append(k); todo += children(k)
    return out


def game_running(pid):
    return any(proc_state(k) not in ("Z", "") for k in game_procs(pid))


def esde_idle(pid):
    """ES-DE is running, in front, and has not launched anything (event scripts don't count)."""
    if not pid or other_in_front(pid):
        return False
    for k in children(pid):
        if os.path.exists(f"/proc/{k}") and not any(SCRIPTS in x for x in argv(k)):
            return False             # (a child that already exited but isn't reaped yet still counts, as before)
    return True


def screensaver_up():
    return os.path.exists(ESDE_SCREENSAVER)


def in_menus():
    """Prism's menus are what's on screen: ES-DE in front with nothing launched, ES-DE's own screensaver not up."""
    pid = esde_pid()
    return bool(pid) and esde_idle(pid) and front_app(fresh=True) == PRISM_APP and not screensaver_up()


# ---- players, tests, overlay, agents
def media_players():
    """Prism's movie player (plex_play + its mpv), the Music app and its player."""
    return scripts_running(MEDIA_SCRIPTS) + [p for p in _pids() if comm(p) == "mpv"]


def music_playing():
    return bool(scripts_running(["deck_music_player.py"]))


_st = [0.0, False]


def selftest_running():
    """The self-test (selftest/run.py) or one of its *_test.py scripts runs (cached 3 s)."""
    if time.monotonic() - _st[0] > 3:
        found = False
        for p in _pids():
            s = script(argv(p))
            if s and (s.endswith("_test.py") or (os.path.basename(s) == "run.py"
                                                 and os.path.basename(os.path.dirname(os.path.abspath(s))) == "selftest")):
                found = True; break
        _st[:] = [time.monotonic(), found]
    return _st[1]


def cc_request(msg, timeout=0.5):
    """One command to the deck_cc overlay; its reply (parsed JSON or text), None when the overlay doesn't answer."""
    try:
        c = socket.socket(socket.AF_UNIX); c.settimeout(timeout); c.connect(CC_SOCK)
        c.sendall((json.dumps(msg) + "\n").encode()); r = c.recv(4096).decode().strip(); c.close()
    except OSError:
        return None
    try:
        return json.loads(r) if r.startswith("{") else r
    except ValueError:
        return r


def cc_state():
    """The Home card: IDLE / ACTIVE / SAVING. No overlay running counts as IDLE."""
    r = cc_request({"cmd": "state"})
    return r if isinstance(r, str) and r else "IDLE"


def card_up():
    """The Home card / game hub (deck_cc) or one of Prism's standalone cards (deck_confirm.py) is on screen."""
    try:
        pid = open("/tmp/prism_card_up").read().strip()
        if pid and os.path.exists(f"/proc/{pid}"):
            return True
    except OSError:
        pass
    return cc_state() != "IDLE"


def busy_flag(max_age=None):
    """Who holds /tmp/prism_busy ("" = nobody). max_age: a flag older than that many seconds was left behind."""
    try:
        if max_age is not None and time.time() - os.path.getmtime(BUSY) > max_age:
            return ""
        with open(BUSY) as f:
            return f.read().strip() or "?"
    except OSError:
        return ""


# ---- verdicts
def playing():
    """Why something counts as being played right now ("" = nothing): a game ES-DE launched, any other Steam app or
    shortcut, a movie / music player, the self-test."""
    pid = esde_pid()
    if pid and game_running(pid):
        names = sorted({comm(k) for k in game_procs(pid)} - {"", "bash", "sh"})
        return "game under ES-DE (" + ", ".join(names[:4]) + ")"
    apps = other_apps()
    if apps:
        return "Steam app " + ",".join(apps)
    if media_players():
        return "movie / music player"
    if selftest_running():
        return "self-test"
    return ""


def not_idle(ignore_busy=False):
    """Why Prism is not idle ("" = idle): the busy flag, ES-DE not running, something played, the Home card up, or
    something other than Prism in front (Steam's menu, a game)."""
    if not ignore_busy:
        b = busy_flag()
        if b:
            return f"busy flag ({b})"
    pid = esde_pid()
    if not pid:
        return "Prism not running"
    why = playing()
    if why:
        return why
    if card_up():
        return "Home card up"
    front = front_app(fresh=True)
    if front != PRISM_APP:
        return f"app {front} in front"
    if not esde_idle(pid):
        return "ES-DE has something open"
    return ""


def idle(ignore_busy=False):
    return not not_idle(ignore_busy)


def wait_idle(checks=4, every=15, timeout=12 * 3600, log=None):
    """Block until Prism has been idle for `checks` checks in a row (`every` s apart); False after `timeout`."""
    end, calm = time.time() + timeout, 0
    while time.time() < end:
        why = not_idle()
        calm = 0 if why else calm + 1
        if calm >= checks:
            return True
        if log and why:
            log(f"waiting: {why}")
        time.sleep(every)
    return False


def snapshot():
    pid = esde_pid()
    return {"front": front_app(fresh=True), "esde": esde_pids(), "esde_app": esde_app(pid) if pid else None,
            "launches": steam_launches(), "game_procs": [(k, comm(k), proc_state(k)) for k in game_procs(pid)],
            "media": media_players(), "selftest": selftest_running(), "card": cc_state(), "busy": busy_flag(),
            "in_menus": in_menus(), "playing": playing(), "not_idle": not_idle()}


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=1))
