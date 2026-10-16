#!/usr/bin/env python3
"""Background music player for Prism's Music app (deck_music.py), 2026-10-08.

Runs as the transient user unit deck-music-player (outside ES-DE's process tree, so music keeps playing after the
Music app closes). Wraps an audio-only mpv with a JSON IPC socket; deck_music.py loads queues and sends commands to
that socket. This wrapper scrobbles tracks to Plex past 50 %, keeps ~/prism/music/history.json (Recently played),
and exits after 15 minutes paused/stopped so the Deck can sleep.
"""
import prism_config  # noqa: E402  (paths)
import json, os, socket, subprocess, sys, time, urllib.parse, urllib.request

HOME = os.path.expanduser("~")
DIR = f"{prism_config.PRISM}/music"
SOCK = f"/run/user/{os.getuid()}/app/io.mpv.Mpv/music.sock"
QUEUE = f"{DIR}/queue.json"       # written by deck_music.py: [{"key", "title", "artist", "album", "thumb", ...}]
HISTORY = f"{DIR}/history.json"
CFG = json.load(open(f"{prism_config.PRISM}/plex.json"))
IDLE_EXIT = 15 * 60


def ipc(cmd):
    try:
        s = socket.socket(socket.AF_UNIX); s.settimeout(1); s.connect(SOCK)
        s.sendall((json.dumps({"command": cmd}) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        s.close()
        for line in data.decode().splitlines():
            r = json.loads(line)
            if "error" in r:
                return r.get("data")
    except (OSError, ValueError):
        pass
    return None


def scrobble(key):
    try:
        q = urllib.parse.urlencode({"key": key, "identifier": "com.plexapp.plugins.library"})
        urllib.request.urlopen(urllib.request.Request(f"{CFG['url']}/:/scrobble?{q}", headers={"X-Plex-Token": CFG["token"]}),
                               timeout=10).read()
    except Exception:
        pass


def remember(track):
    try:
        hist = json.load(open(HISTORY))
    except (OSError, ValueError):
        hist = []
    hist = [h for h in hist if h.get("key") != track.get("key")]
    hist.insert(0, {**track, "played": int(time.time())})
    json.dump(hist[:200], open(HISTORY, "w"))


def toast_if_browsing():
    """New song while the Music app is closed and nothing else is running (browsing Prism): show the
    now-playing card (deck_curate.music_toast via the overlay daemon)."""
    for line in subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True).stdout.splitlines():
        if "deck_music.py" in line or ("/Emulation/roms/" in line and "grep" not in line):
            return
    try:
        sys.path.insert(0, prism_config.PRISM)
        import deck_curate
        deck_curate.log = lambda m: None
        deck_curate.music_toast()
    except Exception:
        pass


import sleep_timer  # noqa: E402


def fade_and_pause():
    """Sleep timer: ease the volume down over ~10 s, pause, put the volume back for next time."""
    vol = ipc(["get_property", "volume"]) or 85
    for i in range(20):
        ipc(["set_property", "volume", vol * (1 - (i + 1) / 20)]); time.sleep(0.5)
    ipc(["set_property", "pause", True]); ipc(["set_property", "volume", vol])


def main():
    os.makedirs(DIR, exist_ok=True)
    if ipc(["quit"]) is not None or os.path.exists(SOCK):   # an mpv left from a player that was killed: never two
        time.sleep(1)                                        # players (the old one would play on, uncontrollable)
    try:
        os.remove(SOCK)
    except OSError:
        pass
    mpv = subprocess.Popen(["flatpak", "run", "io.mpv.Mpv", "--idle=yes", "--no-video", "--audio-display=no",
                            "--no-terminal", "--volume=85", "--gapless-audio=weak", f"--input-ipc-server={SOCK}"])
    import signal

    def stop(*_):                # systemctl stop / shutdown: take mpv down too (Flatpak runs it outside this unit)
        ipc(["quit"])
        try:
            mpv.wait(timeout=3)
        except subprocess.TimeoutExpired:
            mpv.kill()
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    idle_since, done, last_idx = None, set(), None
    while mpv.poll() is None:
        time.sleep(2)
        pos, dur = ipc(["get_property", "time-pos"]), ipc(["get_property", "duration"])
        paused, idx = ipc(["get_property", "pause"]), ipc(["get_property", "playlist-pos"])
        playing = isinstance(pos, (int, float)) and paused is False
        if playing and idx != last_idx:
            if last_idx is not None and sleep_timer.due("music", at_end=True):   # "end of this song" timer
                fade_and_pause(); sleep_timer.clear("music"); last_idx = idx; continue
            last_idx = idx
            toast_if_browsing()
        if playing and sleep_timer.due("music"):
            fade_and_pause(); sleep_timer.clear("music"); continue
        if playing:
            idle_since = None
            try:
                q = json.load(open(QUEUE))
                tr = q[idx] if isinstance(idx, int) and 0 <= idx < len(q) else None
            except (OSError, ValueError):
                tr = None
            if tr and dur and pos > dur * 0.5 and (tr["key"], q.__len__(), idx) not in done:
                done.add((tr["key"], q.__len__(), idx))
                scrobble(tr["key"]); remember(tr)
        else:
            idle_since = idle_since or time.time()
            if time.time() - idle_since > IDLE_EXIT:
                ipc(["quit"])
                break
    try:
        mpv.wait(timeout=5)
    except subprocess.TimeoutExpired:
        mpv.kill()


if __name__ == "__main__":
    main()
