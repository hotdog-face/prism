#!/usr/bin/env python3
"""Turn the Steam Deck's screen off when nobody is using it, without sleeping the Deck.

Sleep would pause downloads and remote agents, so in Game Mode this blanks only the panel
(gamescope's drm_sleep_internal_screen) after IDLE_SECONDS without physical input while no
game or app is running, and lights it again on the first button, stick, trackpad, touch or
power/volume press. Virtual devices (the MCP server's pad, keyboard, touchscreen) are ignored.
Runs as a systemd *user* service (~/.config/systemd/user/deck-screensaver.service) so SteamOS updates,
which can reset /etc, don't remove it. As the deck user it can read the controller (hidraw ACL) and the
power/volume buttons; the touchscreen node needs root, so screen taps only wake it when run as root.
"""
import glob
import json
import os
import select
import subprocess
import sys
import time

import evdev

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_state      # noqa: E402  (what's running: one implementation, 2026-10-13)
import prism_profile    # noqa: E402  (docked / handheld settings: one profile)
import prism_config     # noqa: E402  (paths)

IDLE_SECONDS = int(os.environ.get("IDLE_SECONDS", "300"))
STICK_WAKE = 6000           # stick deflection (of 32767) that counts as input; sticks jitter at rest
DECK_USER, DECK_UID = "deck", 1000
CONTROLLER_HID_ID = "000028DE:00001205"
PHYSICAL_EVDEV = ("FTS3528", "Power Button", "AT Translated Set 2 keyboard", "Video Bus")


def log(*a):
    print("[screensaver]", *a, file=sys.stderr, flush=True)


def gamescope_running():
    return subprocess.run(["pgrep", "-x", "gamescope-wl"], capture_output=True).returncode == 0


PLEX_APPID = str(prism_config.get("plex_appid", ""))   # a Plex HTPC shortcut, if any: movies play for hours without input
APP_IDLE_SECONDS = int(os.environ.get("APP_IDLE_SECONDS", "1800"))
VIDEO_IDLE_SECONDS = int(os.environ.get("VIDEO_IDLE_SECONDS", "10800"))


def running_appids():
    """Steam launches every game and non-Steam app (Plex, emulators) under `reaper SteamLaunch AppId=N`."""
    return prism_state.running_appids()


def something_running():
    return bool(running_appids())


def audio_playing():
    """True if some app other than Steam itself has an uncorked (playing) audio stream."""
    try:
        r = subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True, text=True,
                           timeout=5, env={**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{DECK_UID}"})
        streams = json.loads(r.stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return True                 # can't tell: assume playing so a movie never blanks
    return any(not st.get("corked") and st.get("properties", {}).get("application.process.binary")
               not in ("steamwebhelper", "steam") for st in streams)


MOVIE_SOCK = f"/run/user/{os.getuid()}/app/io.mpv.Mpv/switchhome-mpv.sock"


def movie_playing():
    """A Prism movie/episode is playing (not paused): it gets the long video limit (2026-10-08: movies
    moved from the Plex app into Prism, which only got the 30-minute app limit)."""
    import json, socket
    try:
        c = socket.socket(socket.AF_UNIX); c.settimeout(0.5); c.connect(MOVIE_SOCK)
        c.sendall(b'{"command": ["get_property", "pause"]}\n'); r = c.recv(4096).decode(); c.close()
        return any(json.loads(l).get("data") is False for l in r.splitlines() if l.strip())
    except (OSError, ValueError):
        return False


def idle_limit():
    """How long without input before the screen goes dark, depending on what is open. Movies (Prism's
    player, or the Plex app) get the long limit only while actually playing."""
    try:                            # agent test mode (2026-10-09): keep the panel on so screen captures see the UI
        if time.time() - os.path.getmtime("/tmp/prism_testmode") < 1200:
            return 10 ** 9
    except OSError:
        pass
    if os.path.exists(f"{prism_config.PRISM}/party_mode"):     # party (party_mode.py): never dim / blank
        return 10 ** 9
    if movie_playing():
        return VIDEO_IDLE_SECONDS
    ids = running_appids()
    if not ids:
        return IDLE_SECONDS
    return VIDEO_IDLE_SECONDS if PLEX_APPID in ids and audio_playing() else APP_IDLE_SECONDS


# ---- gentle dimming (2026-10-09): instead of a hard cut to black, the backlight eases down DIM_BEFORE seconds before
# the panel goes off, fades out just before it does, and eases back up on any input (also when waking a dark panel).
BL = "/sys/class/backlight/amdgpu_bl0"
BL_SAVE = "/tmp/prism_backlight_orig"   # the user's brightness while we hold it lower (survives a restart of this service)
DIM_BEFORE, DIM_LEVEL = 20, 0.30
_bl_gen = [0]


def bl_get():
    try:
        return int(open(BL + "/brightness").read()), int(open(BL + "/max_brightness").read())
    except (OSError, ValueError):
        return None, None


def bl_ramp(target, secs, then=None):
    """ease the backlight to target over secs in a thread; a newer ramp cancels an older one"""
    import threading
    _bl_gen[0] += 1
    gen = _bl_gen[0]

    def run():
        cur, mx = bl_get()
        if cur is None:
            then and then(); return
        tgt = max(int(mx * 0.01), min(mx, int(target)))
        steps = max(1, int(secs / 0.025))
        for i in range(1, steps + 1):
            if _bl_gen[0] != gen:
                return
            k = i / steps
            k = k * k * (3 - 2 * k)
            try:
                with open(BL + "/brightness", "w") as f:
                    f.write(str(int(cur + (tgt - cur) * k)))
            except OSError:
                return
            time.sleep(0.025)
        then and then()
    threading.Thread(target=run, daemon=True).start()


def bl_hold():
    """remember the user's brightness (once) before lowering it"""
    if not os.path.exists(BL_SAVE):
        cur, _ = bl_get()
        if cur:
            open(BL_SAVE, "w").write(str(cur))
    try:
        return int(open(BL_SAVE).read())
    except (OSError, ValueError):
        return None


def bl_release(secs):
    """ease back to the user's brightness and forget the saved value"""
    try:
        orig = int(open(BL_SAVE).read())
    except (OSError, ValueError):
        return
    bl_ramp(orig, secs, then=lambda: os.path.exists(BL_SAVE) and _bl_gen and os.remove(BL_SAVE))


def docked():
    """On the TV (dock's DisplayPort connected), cached 3 s: the Deck's own panel should stay off then."""
    return prism_profile.docked()


def cc(cmd):
    """one command to the deck_cc overlay (TV screensaver)"""
    import socket
    for attempt in (1, 2):
        try:
            c = socket.socket(socket.AF_UNIX); c.settimeout(1); c.connect("/tmp/deck_cc.sock")
            c.sendall((json.dumps({"cmd": cmd}) + "\n").encode()); c.recv(64); c.close()
            return True
        except OSError:
            if attempt == 2 or cmd == "unsaver":
                return False
            try:                        # the overlay isn't running (died at boot): start it, then try once more
                import deck_curate
                deck_curate.cc_daemon()
            except Exception:
                return False


_TV = prism_profile.PROFILES["docked"]
TV_SAVER_MIN = _TV["saver_min"]     # docked: at least 10 min without input before the TV screensaver (longer limits apply)
TV_SLEEP_MENUS, TV_SLEEP_GAME = _TV["sleep_menus"], _TV["sleep_game"]   # docked: sleep after 60 / 90 min idle (menus /
                                                                         # game; never in movies)


PARTY_SLEEP = 2 * 3600            # party day: nobody has touched a controller for 2 h -> the party's over


def tv_limits():
    """Docked idle limits (2026-10-09: 'behave like any device'): (screensaver, sleep or None). Party mode
    keeps the screensaver and sleeps only after PARTY_SLEEP untouched (2026-10-10 party: "never sleeps" left it on
    all night); agent test mode does neither."""
    try:
        if time.time() - os.path.getmtime("/tmp/prism_testmode") < 1200:
            return None, None
    except OSError:
        pass
    party = False                   # the party itself = the 24 h before party mode ends (the night before still sleeps)
    try:
        import datetime
        until = datetime.datetime.fromisoformat(open(f"{prism_config.PRISM}/party_mode").read().split()[0])
        party = until - datetime.timedelta(hours=24) <= datetime.datetime.now() < until
    except (OSError, ValueError, IndexError):
        pass
    if movie_playing():
        return VIDEO_IDLE_SECONDS, None
    ids = set(prism_state.other_apps())                  # Prism itself is a "running app" to Steam
    if ids and PLEX_APPID in ids and audio_playing():
        return VIDEO_IDLE_SECONDS, None
    if ids or prism_state.game_running(prism_state.esde_pid()):  # a Steam game / shortcut, or a game ES-DE launched
        return max(TV_SAVER_MIN, APP_IDLE_SECONDS), (PARTY_SLEEP if party else TV_SLEEP_GAME)
    if prism_state.music_playing():                       # music playing in Prism: screensaver yes, sleep no
        return TV_SAVER_MIN, None
    return TV_SAVER_MIN, (PARTY_SLEEP if party else TV_SLEEP_MENUS)


def tv_sleep():
    """Sleep the way the power button does (Steam's sleep animation plays); the TV loses its signal and goes to standby
    (or switches off at once with HDMI-CEC)."""
    try:                            # controllers to sleep properly first (a dropped link left Switch Pros lit)
        subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/pad_sleep.py", "all"],
                       capture_output=True, timeout=20)
    except Exception:
        pass
    try:                            # the Switch 2 Pro too: its bridge puts it to sleep on request (2026-10-10)
        req = os.path.expanduser("~deck/.cache/switch2-bridge/sleep_request")
        if os.path.isdir(os.path.dirname(req)):
            open(req, "w").write(str(time.time()))
            time.sleep(2)
    except Exception:
        pass
    r = subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/prism_bridge.py", "suspend"],
                       capture_output=True, timeout=10)
    if r.returncode != 0:
        subprocess.run(["systemctl", "suspend"])


def set_screen(on):
    ok = prism_profile.set_panel(on)
    log("screen", "on" if on else "off", "" if ok else "(gamescopectl failed)")


def open_controller():
    """The hidraw node of the built-in controller that is streaming state reports."""
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(node + "/device/uevent") as f:
                if CONTROLLER_HID_ID not in f.read():
                    continue
            fd = os.open("/dev/" + os.path.basename(node), os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            return fd
        os.close(fd)
    return None


def open_evdevs():
    devs = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        if d.name.startswith(PHYSICAL_EVDEV):
            devs.append(d)
        else:
            d.close()
    return devs


def controller_active(report, baseline):
    """True if buttons, trackpads or triggers changed, or a stick is pushed."""
    if len(report) < 56:
        return False
    if baseline is not None and (report[8:24] != baseline[8:24] or report[44:48] != baseline[44:48]):
        return True
    for off in (48, 50, 52, 54):
        if abs(int.from_bytes(report[off:off + 2], "little", signed=True)) > STICK_WAKE:
            return True
    return False


def main(shared_input=None):
    """shared_input: a function returning the time of the last controller/touch input. Inside switchhome.py the
    button reader already sees every report, so the screensaver doesn't open the controller a second time."""
    log(f"idle timeout {IDLE_SECONDS}s" + (" (input shared with the button reader)" if shared_input else ""))
    last_input = time.time()
    screen_off = False
    apps_at_off = set()
    ctrl, baseline = None, None
    devs = []
    last_scan = last_check = 0
    dock_off = docked()
    saver_on = False
    # the whole docked / handheld profile at start (boot, or switchhome restarted after a dock change it didn't see:
    # 2026-10-12 Prism stayed 1920x1080 on the Deck's panel); docked the Deck's own panel goes off
    import threading
    threading.Thread(target=prism_profile.apply, args=("switchhome start",), kwargs={"force": True}, daemon=True).start()
    bl_release(0.4)                 # a restart while dimmed must not leave the screen dark
    dimmed = False
    while True:
        now = time.time()
        if now - last_scan > 30 or (ctrl is None and shared_input is None):   # devices come and go (sleep, modes)
            for d in devs:
                d.close()
            devs = open_evdevs()
            if ctrl is not None:
                os.close(ctrl)
            ctrl, baseline = (open_controller() if shared_input is None else None), None
            last_scan = now
        fds = {d.fd: d for d in devs}
        watch = list(fds) + ([ctrl] if ctrl is not None else [])
        try:
            ready, _, _ = select.select(watch, [], [], 1 if shared_input else 5)
        except (OSError, ValueError):
            last_scan = 0
            continue
        active = bool(shared_input and shared_input() > last_input)
        for fd in ready:
            try:
                if fd == ctrl:
                    while True:
                        rep = os.read(ctrl, 64)
                        if controller_active(rep, baseline):
                            active = True
                        baseline = rep
                else:
                    for ev in fds[fd].read():
                        if ev.type in (evdev.ecodes.EV_KEY, evdev.ecodes.EV_ABS):
                            active = True
            except BlockingIOError:
                pass
            except OSError:
                last_scan = 0
        now = time.time()
        # docked / undocked: checked before the input handling (a game played straight after undocking kept the old
        # profile while input never stopped)
        if docked() != dock_off and gamescope_running():
            dock_off = docked()
            screen_off = dimmed = False
            if saver_on:
                cc("unsaver"); saver_on = False
            if not dock_off:
                bl_release(0.4)
            last_input = now
            # panel, Prism's render size, audio, Steam menus; ES-DE restarted for the new screen once idle
            prism_profile.apply("docked" if dock_off else "undocked", restart_esde=True)
            try:                                  # the moment itself: logo on the TV / pause card when undocked mid-game
                import prism_screens
                prism_screens.on_dock_change(dock_off)
            except Exception as e:
                print("[screensaver] dock screens:", e, flush=True)
            continue
        if active and dock_off:
            last_input = now            # on the TV: input never wakes the Deck's own panel
            if saver_on:
                cc("unsaver"); saver_on = False
            continue
        if active:
            last_input = now
            if screen_off:
                set_screen(True)            # comes back at the faded-out level...
                screen_off = False
                bl_release(0.7)             # ...and eases up
            elif dimmed:
                bl_release(0.35)
            dimmed = False
            continue
        # the controller streams ~250 reports/s even untouched: the checks below spawn pgrep and scan /proc, so run
        # them at most every 5 s (they ran on every report: ~15 % of a core around the clock, 2026-10-08)
        if now - last_check < 5:
            continue
        last_check = now
        if dock_off:                    # no dimming / panel-off for a panel that is already off; the TV gets the saver
            saver_lim, sleep_lim = tv_limits()
            idle = now - last_input
            if saver_lim and not saver_on and gamescope_running() and idle > saver_lim:
                saver_on = cc("saver")
                if saver_on:
                    log("TV screensaver on")
            if sleep_lim and gamescope_running() and idle > sleep_lim:
                log(f"TV idle {int(idle / 60)} min: sleeping")
                if saver_on:
                    cc("unsaver"); saver_on = False
                last_input = now        # after waking, a fresh hour
                tv_sleep()
            continue
        limit = idle_limit()
        if not screen_off and gamescope_running() and now - last_input > limit:
            orig = bl_hold()
            done = []
            bl_ramp((orig or 1) * 0.02, 1.2, then=lambda: done.append(1))     # fade out, then the panel off
            for _ in range(80):
                if done:
                    break
                time.sleep(0.025)
            set_screen(False)
            screen_off = True
            dimmed = False
            apps_at_off = running_appids()
        elif (not screen_off and not dimmed and gamescope_running() and limit < 10 ** 8
              and now - last_input > limit - DIM_BEFORE):
            orig = bl_hold()
            if orig:
                bl_ramp(orig * DIM_LEVEL, 2.5)
                dimmed = True
        elif screen_off and (not gamescope_running() or running_appids() - apps_at_off):
            set_screen(True)       # left Game Mode, or something new was launched (e.g. by an agent)
            screen_off = False
            bl_release(0.7)
            last_input = now


if __name__ == "__main__":
    main()
