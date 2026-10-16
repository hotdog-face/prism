#!/usr/bin/env python3
"""Docked / handheld profile: every setting that depends on the TV, in one place (refactor, 2026-10-13).

These used to be applied piecemeal (the resolution override only in deck_screensaver's dock-change branch, menu
suppression in deck_curate, TV idle limits in deck_screensaver...), and 2026-10-12 a dock change the screensaver
never saw left Prism at 1920x1080 on the Deck's panel (zoomed and cropped). Now apply() sets the whole profile: when
switchhome starts (boot) and on every dock change (deck_screensaver); keep() (every ~5 s, controller_bar) re-applies
what didn't take (Steam's UI not up yet, the TV's audio sink appearing late) and keeps Steam's menus suppressed docked.

                  docked (TV)                       handheld
  Prism render    SetAppResolutionOverride 1920x1080  Default (1280x800)
  Deck panel      off                               on
  audio           the TV (HDMI/DP sink) by default  (PipeWire falls back to the Deck by itself)
  Steam menus     suppressed (kids mashing Home)    released (unless the Home card holds them)
  idle            TV screensaver 10 min, sleep 60 / 90 min (deck_screensaver.tv_limits)
  ES-DE layout    16:9 theme block (ThemeAspectRatio=automatic, picked at ES-DE start -> restart when it differs)

`python3 prism_profile.py` shows the mode and checks; `apply` applies it."""
import prism_config  # noqa: E402  (paths)
import glob, json, os, re, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prism_state as ps     # noqa: E402

DECK_UID = 1000
PROFILES = {
    "docked":   {"resolution": "1920x1080", "panel": False, "esde_size": (1920, 1080),
                 "saver_min": 600, "sleep_menus": 3600, "sleep_game": 5400},
    "handheld": {"resolution": "Default", "panel": True, "esde_size": (1280, 800),
                 "saver_min": None, "sleep_menus": None, "sleep_game": None},
}
THEME = f"{prism_config.THEME_DIR}/theme.xml"
AUDIO_RETRY_S = 90          # the TV's sink can show up a while after the DisplayPort does


def log(*a):
    print("[profile]", *a, file=sys.stderr, flush=True)


_dock = [0.0, False]


def docked():
    """On the TV: the dock's DisplayPort is connected (cached 3 s)."""
    now = time.monotonic()
    if now - _dock[0] > 3:
        try:
            _dock[1] = any(open(f).read().strip() == "connected" for f in glob.glob("/sys/class/drm/card*-DP-*/status"))
        except OSError:
            _dock[1] = False
        _dock[0] = now
    return _dock[1]


def mode():
    return "docked" if docked() else "handheld"


def current():
    return PROFILES[mode()]


# ---- the pieces
def set_panel(on):
    """The Deck's own screen on / off (gamescope's drm_sleep_internal_screen)."""
    cmd = ["env", f"XDG_RUNTIME_DIR=/run/user/{DECK_UID}", "GAMESCOPE_WAYLAND_DISPLAY=gamescope-0",
           "gamescopectl", "drm_sleep_internal_screen", "0" if on else "1"]
    if os.getuid() == 0:            # as root, run gamescopectl as the session user
        cmd = ["runuser", "-u", "deck", "--"] + cmd
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return r.returncode == 0


def set_resolution(res):
    """Prism's per-app render size in Steam (ES-DE reads it when it starts)."""
    import deck_curate
    return deck_curate.steam_js('(SteamClient.Apps.SetAppResolutionOverride(%s, "%s"), 1)' % (ps.PRISM_APP, res),
                                "prism resolution") == 1


def _pactl(*a):
    return subprocess.run(["pactl", *a], capture_output=True, text=True, timeout=5,
                          env={**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{DECK_UID}"}).stdout


def audio(m):
    """Docked: the TV is the default sink, unless it already is or a Bluetooth headset is chosen. Handheld: nothing
    (the TV sink is gone, PipeWire falls back to the Deck). True = as it should be."""
    if m != "docked":
        return True
    cur = _pactl("get-default-sink").strip()
    if "hdmi" in cur or "bluez" in cur:
        return True
    try:
        sinks = json.loads(_pactl("-f", "json", "list", "sinks") or "[]")
    except ValueError:
        return False
    tv = [s["name"] for s in sinks if "hdmi" in s.get("name", "")
          and any(p.get("availability") != "not available" for p in s.get("ports", [{}]))]
    if not tv:
        return False                                   # not there yet: keep() tries again
    _pactl("set-default-sink", tv[0])
    log(f"audio to the TV ({tv[0]}, was {cur})")
    return True


def steam_menus(m):
    """Docked: Steam's menus suppressed while Prism runs (deck_curate.steam_menus_guard). Handheld: released again, but
    not while the Home card holds them or Steam's menu was opened on purpose."""
    import deck_curate
    if m == "docked":
        deck_curate.steam_menus_guard()
        return True
    if ps.card_up() or os.path.exists(deck_curate.STEAM_MENU_WINDOW):
        return True
    st = deck_curate.bridge("state")
    if st and (st.get("data") or {}).get("held"):
        deck_curate.steam_ui("release", deck_curate.JS_RELEASE)
        log("Steam menus released (handheld)")
    return True


def esde_size():
    """ES-DE's window size (its render resolution), or None."""
    pid = ps.esde_pid()
    if not pid:
        return None
    try:
        disp = next((kv.split(b"=", 1)[1].decode() for kv in open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
                     if kv.startswith(b"DISPLAY=")), ":1")
        env = {**ps._xenv(), "DISPLAY": disp}
        best = None
        for w in subprocess.run(["xdotool", "search", "--pid", str(pid)], env=env, capture_output=True, text=True,
                                timeout=3).stdout.split():
            g = re.search(r"Geometry: (\d+)x(\d+)", subprocess.run(["xdotool", "getwindowgeometry", w], env=env,
                                                                   capture_output=True, text=True, timeout=3).stdout)
            if g:
                size = (int(g.group(1)), int(g.group(2)))
                if best is None or size[0] * size[1] > best[0] * best[1]:
                    best = size
        return best
    except Exception:
        return None


def esde_matches(m=None):
    """ES-DE runs at this mode's size: True / False, or None when it can't tell (not running, no window)."""
    size = esde_size()
    return None if size is None else size == PROFILES[m or mode()]["esde_size"]


def check():
    """Static settings the profile relies on; a list of problems."""
    out = []
    try:
        import prism_esde
        if prism_esde.setting("ThemeAspectRatio") != "automatic":
            out.append(f"ES-DE ThemeAspectRatio is {prism_esde.setting('ThemeAspectRatio')!r}, not 'automatic'")
    except Exception as e:
        out.append(f"es_settings: {e}")
    try:
        if '<aspectRatio name="16:9">' not in open(THEME).read():
            out.append("theme has no 16:9 block")
    except OSError as e:
        out.append(f"theme: {e}")
    return out


# ---- apply / keep
_lock = threading.Lock()
_last = {"mode": None, "t": 0.0, "ok": {}}


def apply(why, restart_esde=False, force=False):
    """Set the whole profile for the current mode. A repeat for a mode already applied does nothing unless force.
    ES-DE picks its size and 16:9 / 16:10 layout when it starts, so it is restarted once idle (prism_restart, which
    skips it if ES-DE is already right): restart_esde=True (a dock change) unless ES-DE is known to be right, otherwise
    only when it's known to be wrong (switchhome starting while ES-DE runs at the other size)."""
    with _lock:
        m = mode()
        if _last["mode"] == m and not force:
            return _last["ok"]
        p = PROFILES[m]
        ok = {}
        for name, fn in (("panel", lambda: set_panel(p["panel"])), ("resolution", lambda: set_resolution(p["resolution"])),
                         ("audio", lambda: audio(m)), ("steam_menus", lambda: steam_menus(m))):
            try:
                ok[name] = bool(fn())
            except Exception as e:
                log(f"{name}: {e}"); ok[name] = False
        _last.update(mode=m, t=time.time(), ok=ok)
        log(f"{m} profile ({why}): " + ", ".join(f"{k} {'ok' if v else 'RETRY'}" for k, v in ok.items()))
        for problem in check():
            log("check:", problem)
    match = esde_matches(m)
    if match is False or (restart_esde and match is not True):
        import prism_restart
        prism_restart.request(m)
    return ok


_seen = {"mode": None, "n": 0}


def keep():
    """Every few seconds: keep the applied profile in force and catch a dock change nobody applied yet."""
    m = mode()
    if _last["mode"] is None:
        return
    if m != _last["mode"]:          # deck_screensaver applies dock changes; this only catches one it missed (~10 s)
        _seen["n"] = _seen["n"] + 1 if _seen["mode"] == m else 1
        _seen["mode"] = m
        if _seen["n"] >= 2:
            apply("dock change (caught by keep)", restart_esde=True)
        return
    redo = [k for k, v in _last["ok"].items() if not v]
    for name in redo:
        if name == "audio" and time.time() - _last["t"] > AUDIO_RETRY_S:
            continue
        if name == "panel" and m != "docked":
            continue                # handheld the screensaver owns the panel (idle blanking)
        try:
            done = {"panel": lambda: set_panel(current()["panel"]), "resolution": lambda: set_resolution(current()["resolution"]),
                    "audio": lambda: audio(m), "steam_menus": lambda: steam_menus(m)}[name]()
        except Exception:
            done = False
        if done:
            _last["ok"][name] = True
            log(f"{name} applied on retry ({m})")
    if m == "docked":
        import deck_curate
        deck_curate.steam_menus_guard()


if __name__ == "__main__":
    if sys.argv[1:2] == ["apply"]:
        print(apply("by hand", force=True))
    else:
        print(json.dumps({"mode": mode(), "profile": current(), "esde_size": esde_size(), "esde_matches": esde_matches(),
                          "default_sink": _pactl("get-default-sink").strip(), "check": check()}, indent=1))
