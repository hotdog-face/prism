#!/usr/bin/env python3
"""Play a Plex movie/episode stub (roms/movies, roms/tv) full-screen with mpv, the way a game launches from Prism.

  plex_play.py <stub.plex>      (ES-DE passes the path shell-escaped)

Direct-plays the original file from the Plex server, resumes at Plex's saved position, reports progress to Plex
every 10 s (so other Plex apps continue from here) and marks it watched past 90 %. Touch: VLC/YouTube gestures (plex_touch.py). Controller (read here, sent to mpv over IPC):
A pause, B back, d-pad seek, L/R chapters, Y subtitles, X audio.
"""
import prism_config  # noqa: E402  (paths)
import json, os, re, socket, subprocess, sys, time, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HOME = os.path.expanduser("~")
CFG = json.load(open(f"{prism_config.PRISM}/plex.json"))
SOCK = f"/run/user/{os.getuid()}/app/io.mpv.Mpv/switchhome-mpv.sock"
HDR = {"X-Plex-Token": CFG["token"], "Accept": "application/json", "X-Plex-Client-Identifier": "deck-switch-home",
       "X-Plex-Product": "Prism", "X-Plex-Device-Name": "Steam Deck", "X-Plex-Platform": "Linux"}
LOG = f"{prism_config.PRISM}/plex/play.log"


def log(m):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    open(LOG, "a").write(time.strftime("%F %T ") + m + "\n")


NO_REPORT = os.path.exists(f"{prism_config.PRISM}/plex/no_report")   # agent tests: leave Plex progress alone


def plex(path, **params):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{CFG['url']}{path}{'?' + q if q else ''}", headers=HDR)
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read()
    return json.loads(body)["MediaContainer"] if body.strip().startswith(b"{") else None


def mpv_get(prop):
    try:
        s = socket.socket(socket.AF_UNIX); s.settimeout(1); s.connect(SOCK)
        s.sendall((json.dumps({"command": ["get_property", prop]}) + "\n").encode())
        data = s.recv(4096).decode(); s.close()
        for line in data.splitlines():
            r = json.loads(line)
            if "data" in r:
                return r["data"]
    except (OSError, ValueError):
        pass
    return None


def mpv_cmd(cmd):
    try:
        s = socket.socket(socket.AF_UNIX); s.settimeout(1); s.connect(SOCK)
        s.sendall((json.dumps({"command": cmd}) + "\n").encode()); s.recv(4096); s.close()
    except OSError:
        pass


def controller(proc):
    """This mpv build has no gamepad input: read the controller here and drive mpv over IPC (not grabbed).
    A pause, B back, d-pad seek 10 s / 1 min, L/R chapters, Y subtitles, X audio, Start/View progress."""
    import select
    import evdev
    from evdev import ecodes as E
    devs = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
            caps = d.capabilities().get(E.EV_KEY, [])
            if E.BTN_SOUTH in caps and not (d.info.vendor == 0x045e and d.info.product == 0x028e):
                devs.append(d)
            else:
                d.close()
        except OSError:
            pass
    keys = {E.BTN_SOUTH: ["cycle", "pause"], E.BTN_EAST: ["quit"], E.BTN_Y: ["cycle", "sub"], E.BTN_X: ["cycle", "audio"],
            E.BTN_TL: ["add", "chapter", -1], E.BTN_TR: ["add", "chapter", 1], E.BTN_START: ["show-progress"],
            E.BTN_SELECT: ["show-progress"], E.BTN_DPAD_LEFT: ["seek", -10], E.BTN_DPAD_RIGHT: ["seek", 10],
            E.BTN_DPAD_UP: ["seek", 60], E.BTN_DPAD_DOWN: ["seek", -60]}
    hats = {(E.ABS_HAT0X, -1): ["seek", -10], (E.ABS_HAT0X, 1): ["seek", 10], (E.ABS_HAT0Y, -1): ["seek", 60],
            (E.ABS_HAT0Y, 1): ["seek", -60]}
    while proc.poll() is None and devs:
        r, _, _ = select.select(devs, [], [], 0.2)
        for d in r:
            try:
                for ev in d.read():
                    cmd = None
                    if ev.type == E.EV_KEY and ev.value == 1:
                        cmd = keys.get(ev.code)
                    elif ev.type == E.EV_ABS and ev.value:
                        cmd = hats.get((ev.code, ev.value))
                    if cmd == ["cycle", "pause"] and getattr(controller, "skip", None) and controller.skip():
                        continue                     # A while the Skip pill is up = skip
                    if cmd:
                        t = time.time()
                        hud = getattr(controller, "hud", None)
                        before = mpv_get("time-pos") if hud and cmd[0] == "seek" else None
                        mpv_cmd(cmd)
                        if hud:                      # same VLC-style visuals as the touch gestures
                            if cmd[0] == "seek" and before is not None:
                                hud.seek(cmd[1], max(0, before + cmd[1]), mpv_get("duration") or 0, ms=800, force=True)
                            elif cmd == ["cycle", "pause"]:
                                hud.badge("pause" if mpv_get("pause") else "play")
                            elif cmd == ["show-progress"]:
                                hud.bar()
                        if time.time() - t > 0.5 or time.time() - ev.timestamp() > 1.0:
                            log(f"slow control {cmd}: pad->here {t - ev.timestamp():.1f}s, mpv took {time.time() - t:.1f}s")
            except OSError:
                pass


def item_info(key):
    """(meta, stream url, duration ms, resume seconds, display title) for a Plex movie/episode."""
    meta = plex(f"/library/metadata/{key}")["Metadata"][0]
    part = meta["Media"][0]["Part"][0]
    url = f"{CFG['url']}{part['key']}?X-Plex-Token={CFG['token']}"
    duration = int(meta.get("duration") or part.get("duration") or 0)
    offset = int(meta.get("viewOffset") or 0)
    start = offset / 1000 if offset > 60000 and (not duration or offset < duration * 0.95) else 0
    title = meta.get("title", "")
    if meta.get("type") == "episode":
        title = f"{meta.get('grandparentTitle', '')} - S{int(meta.get('parentIndex') or 0):02d}E{int(meta.get('index') or 0):02d} - {title}"
    return meta, url, duration, start, title


def next_episode(meta):
    """The episode after this one (next in season, or first of the next season), or None."""
    if meta.get("type") != "episode" or not meta.get("grandparentRatingKey"):
        return None
    try:
        leaves = plex(f"/library/metadata/{meta['grandparentRatingKey']}/allLeaves").get("Metadata", [])
    except Exception:
        return None
    leaves.sort(key=lambda e: (int(e.get("parentIndex") or 0), int(e.get("index") or 0)))
    keys = [e["ratingKey"] for e in leaves]
    i = keys.index(meta["ratingKey"]) if meta["ratingKey"] in keys else -1
    return keys[i + 1] if 0 <= i < len(keys) - 1 else None


def report(key, state, pos, duration):
    if NO_REPORT:
        return
    try:
        plex("/:/timeline", ratingKey=key, key=f"/library/metadata/{key}", state=state, time=int(pos * 1000),
             duration=duration)
        if state == "stopped" and duration and pos * 1000 > duration * 0.9:
            plex("/:/scrobble", key=key, identifier="com.plexapp.plugins.library")
            log(f"watched {key}")
    except Exception as e:
        log(f"report failed ({state}): {e}")


def up_next(proc, nxt_title):
    """10 s 'Up next' countdown at the end of an episode (mpv keeps the last frame, paused). A plays now (it
    un-pauses), B quits (mpv exits). Returns True to play the next episode."""
    for left in range(10, 0, -1):
        if proc.poll() is not None:
            return False
        mpv_cmd(["show-text", f"Up next:  {nxt_title}\nPlaying in {left}   (A play now  ·  B stop)", 1100])
        for _ in range(10):
            time.sleep(0.1)
            if proc.poll() is not None:
                return False
            if mpv_get("pause") is False and not mpv_get("eof-reached"):
                return True
    return proc.poll() is None


VOL_FILE = f"{prism_config.PRISM}/plex/volume"


def saved_volume():
    try:
        return max(0, min(130, int(open(VOL_FILE).read().strip())))
    except (OSError, ValueError):
        return 100


def first_unwatched(show_key):
    leaves = plex(f"/library/metadata/{show_key}/allLeaves").get("Metadata", [])
    leaves.sort(key=lambda e: (int(e.get("parentIndex") or 0), int(e.get("index") or 0)))
    for e in leaves:
        if not e.get("viewCount"):
            return e["ratingKey"]
    return leaves[0]["ratingKey"] if leaves else None


def now_playing(key, title):
    """For the Steam-button card's Watch later / Mark watched (plex_watch.now_playing)."""
    try:
        json.dump({"key": str(key), "title": title, "t": time.time()}, open("/tmp/plex_now.json", "w"))
    except OSError:
        pass


import sleep_timer  # noqa: E402


def main():
    arg = " ".join(sys.argv[1:])
    path = re.sub(r"\\(.)", r"\1", arg)
    if not os.path.exists(path) and path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    stubdata = json.load(open(path))
    if stubdata.get("type") in ("discover", "overseerr"):   # Discover shelf: request it (discover_request.py)
        os.execv("/usr/bin/python3", ["python3", os.path.join(os.path.dirname(os.path.abspath(__file__)), "discover_request.py"), path])
    key = stubdata["key"]
    if stubdata.get("type") == "show":                  # Watch Later show: its first unwatched episode
        key = first_unwatched(key) or key
    meta, url, duration, start, title = item_info(key)
    now_playing(key, title)
    log(f"play {key} {title!r} from {start:.0f}s")
    try:
        os.remove(SOCK)
    except OSError:
        pass
    p = subprocess.Popen(["flatpak", "run", "io.mpv.Mpv", "--fs", "--force-window=immediate", f"--start={start:.0f}",
                          f"--input-ipc-server={SOCK}", f"--force-media-title={title}", "--keep-open=yes",
                          "--hwdec=auto-safe", "--volume-max=130", f"--volume={saved_volume()}", url])
    import threading
    threading.Thread(target=controller, args=(p,), daemon=True).start()
    import plex_touch                                   # VLC/YouTube-style swipes and taps (plex_touch.py)
    touch = plex_touch.run(mpv_cmd, mpv_get, lambda: p.poll() is None)
    controller.hud = touch.hud
    import plex_markers                                 # Skip Intro / Recap / Credits (introdb.app, 2026-10-08)
    segs = {"key": None, "list": []}

    def load_segments(k):
        segs["list"] = plex_markers.segments(k); segs["key"] = k
    threading.Thread(target=load_segments, args=(key,), daemon=True).start()

    def skip():
        """Jump past the segment the Skip pill is showing; credits = to the end (Up Next / done)."""
        pos, dur = mpv_get("time-pos"), mpv_get("duration") or 0
        s = plex_markers.active(segs["list"], pos or 0, dur) if isinstance(pos, (int, float)) else None
        if not s:
            return False
        target = (s["end"] if s["end"] else dur) if s["kind"] != "credits" else max(0, dur - 0.5)
        mpv_cmd(["seek", round(target, 1), "absolute"])
        touch.hud.skip(None)
        log(f"skipped {s['kind']} to {target:.0f}s")
        return True
    controller.skip = skip
    touch.on_skip = skip
    last_pos, last_report = start, 0.0
    while p.poll() is None:
        time.sleep(1)
        pos = mpv_get("time-pos")
        if isinstance(pos, (int, float)):
            last_pos = pos
            if segs["key"] == key:
                s = plex_markers.active(segs["list"], pos, (duration or 0) / 1000)
                touch.hud.skip(plex_markers.LABEL[s["kind"]] if s else None)
        if sleep_timer.due("movie"):                    # sleep timer ran out: stop here (Plex keeps the place)
            log("sleep timer: stopping"); sleep_timer.clear("movie")
            mpv_cmd(["quit"]); break
        if mpv_get("eof-reached") is True:              # the end: next episode, or done
            report(key, "stopped", duration / 1000 if duration else last_pos, duration)
            if sleep_timer.due("movie", at_end=True):
                log("sleep timer: end of this one, stopping"); sleep_timer.clear("movie")
                mpv_cmd(["quit"]); key = None; break
            nxt = next_episode(meta)
            if not nxt:
                mpv_cmd(["quit"]); break
            nmeta, nurl, ndur, nstart, ntitle = item_info(nxt)
            if not up_next(p, ntitle):
                if p.poll() is None:
                    mpv_cmd(["quit"])
                key = None; break
            mpv_cmd(["set_property", "force-media-title", ntitle])
            mpv_cmd(["loadfile", nurl, "replace", -1, f"start={nstart:.0f}"])
            mpv_cmd(["set_property", "pause", False])
            key, meta, duration, last_pos, last_report = nxt, nmeta, ndur, nstart, 0.0
            touch.hud.skip(None)
            threading.Thread(target=load_segments, args=(key,), daemon=True).start()
            log(f"up next: play {key} {ntitle!r}")
            now_playing(key, ntitle)
            time.sleep(2)
            continue
        if isinstance(pos, (int, float)) and time.time() - last_report > 10:
            # in the background: a slow Plex server (busy NAS) must never hold up the player loop (2026-10-08)
            threading.Thread(target=report, args=(key, "paused" if mpv_get("pause") is True else "playing", pos,
                                                  duration), daemon=True).start()
            last_report = time.time()
    if getattr(touch, "vol_last", None) is not None:   # the next video starts at the volume you left it at
        open(VOL_FILE, "w").write(str(round(touch.vol_last * 100)))
    try:
        os.remove("/tmp/plex_now.json")
    except OSError:
        pass
    touch.restore()                                     # brightness back to what it was before the movie
    touch.stop()                                        # and the touch reader ends with the player
    if key is None:
        log("stopped at the up-next countdown"); return
    if NO_REPORT:
        log(f"test run (no_report): stopped {key} at {last_pos:.0f}s, nothing sent to Plex"); return
    report(key, "stopped", last_pos, duration)
    log(f"stopped {key} at {last_pos:.0f}s of {duration / 1000:.0f}s")
    # refresh Continue Watching (shows up at ES-DE's next idle restart via esde_restart_pending)
    subprocess.Popen(["systemd-run", "--user", "--nice=10", "--collect", "/usr/bin/python3",
                      f"{prism_config.PRISM}/plex_sync.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    open(f"{prism_config.PRISM}/esde_restart_pending", "a").close()


if __name__ == "__main__":
    main()
