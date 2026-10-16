#!/usr/bin/env python3
"""VLC / YouTube-style touch gestures for the Prism movie player (plex_play.py), 2026-10-08.

  swipe up/down, right half   player volume 0-130 % like VLC (not the system volume: no Steam popup)
  swipe up/down, left half    screen brightness (put back when the movie closes)
  swipe left/right            scrub: live "+1:30 -> 45:10" preview, seeks on release
  double-tap right / left     +10 s / -10 s; keep tapping to add more (YouTube)
  double-tap middle           pause / play
  single tap                  show the bottom bar; touch / drag its timeline to scrub (VLC)

Touches come from touch_reader.py (sudo -n; prints "D|M|U x y" in 1280x800 screen pixels). mpv is driven over its
IPC socket through the send() callback plex_play.py passes in.
"""
import prism_config  # noqa: E402  (paths)
import glob, os, subprocess, threading, time

HOME = os.path.expanduser("~")
W, H = 1280, 800
BL = (glob.glob("/sys/class/backlight/*/brightness") or [None])[0]
SWIPE_PX = 28          # movement before a touch counts as a swipe
DOUBLE_S = 0.30        # second tap within this = double tap
SEEK_CHAIN_S = 0.75    # after a double tap, more taps within this keep seeking (YouTube)
SCRUB_FULL_S = 150     # a full-width swipe scrubs this many seconds


def fmt(sec):
    sec = int(max(0, sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def read_int(p):
    try:
        return int(open(p).read().strip())
    except (OSError, ValueError):
        return None


class Touch:
    def __init__(self, send, get):
        self.send, self.get = send, get
        self.max_bl = read_int(os.path.join(os.path.dirname(BL), "max_brightness")) if BL else None
        self.bl_start = read_int(BL) if BL else None
        self.last_tap = (0.0, 0, 0)     # time, x, y of the previous single tap
        self.chain_until, self.chain_dir, self.chain_total = 0.0, 0, 0
        self.pending = None             # timer for a single tap waiting to see if it becomes a double tap
        # Volume / brightness drags: move() only records the newest target; this worker applies it (max ~30/s) and
        # drops the values in between. Applying every touch event inline (2 wpctl calls each, ~120 events/s) built a
        # backlog: the volume kept changing long after the finger was lifted (2026-10-08).
        self.want, self.cv, self.muted = {}, threading.Condition(), None
        import plex_hud                 # VLC-style visuals drawn inside mpv (plex_hud.py)
        self.hud = plex_hud.Hud(send, get)
        send(["set_property", "osd-bar", False])      # mpv's own seek bar would sit on top of ours
        threading.Thread(target=self.levels, daemon=True).start()

    def levels(self):
        while True:
            with self.cv:
                while not self.want:
                    self.cv.wait()
                want, self.want = self.want, {}
            if "vol" in want:
                self.apply_volume(want["vol"])
            if "bl" in want:
                self.apply_brightness(want["bl"])
            time.sleep(0.033)

    def request(self, what, value):
        with self.cv:
            self.want[what] = value
            self.cv.notify()

    # ---- system controls
    def volume(self):
        """The PLAYER's volume (like VLC), not the Deck's: changing the system volume pops up Steam's own volume
        indicator top-left (2026-10-08). 1.0 = 100 %, up to 1.3 = VLC-style boost."""
        try:
            return float(self.get("volume")) / 100
        except (TypeError, ValueError):
            return 1.0

    def set_volume(self, v):
        v = max(0.0, min(1.3, v))
        self.vol_last = v
        self.request("vol", v)
        self.hud.level("vol", v)

    def apply_volume(self, v):
        self.send(["set_property", "volume", round(v * 100, 1)])
        mute = v <= 0
        if mute != self.muted:
            self.send(["set_property", "mute", mute])
            self.muted = mute

    def set_brightness(self, frac):
        if not (BL and self.max_bl):
            return
        frac = max(0.03, min(1.0, frac))
        self.request("bl", frac)
        self.hud.level("bl", frac)

    def apply_brightness(self, frac):
        try:
            open(BL, "w").write(str(int(frac * self.max_bl)))
        except OSError:
            pass


    def restore(self):
        if BL and self.bl_start is not None:
            try:
                open(BL, "w").write(str(self.bl_start))
            except OSError:
                pass

    def osd(self, text, ms=900):
        self.send(["show-text", text, ms])

    # ---- gestures
    def down(self, x, y):
        self.x0, self.y0, self.t0, self.axis = x, y, time.monotonic(), None
        self.side = "left" if x < W / 2 else "right"
        # VLC: with the bottom bar up, touching its timeline scrubs (jump to the finger, drag, seek on release)
        self.bar_scrub = self.hud.bar_visible() and y > H - 150
        if self.bar_scrub:
            self.dur = self.get("duration") or 0
            self.bar_target = self.hud.bar_time(x, self.dur)
            self.hud.bar(4000, pos=self.bar_target, dur=self.dur, scrub=True)
            return
        self.vol0 = self.volume() if x >= W / 2 else None
        self.bl0 = (read_int(BL) / self.max_bl) if (BL and self.max_bl and x < W / 2) else None
        self.pos0 = self.get("time-pos") or 0
        self.dur = self.get("duration") or 0

    def move(self, x, y):
        if self.bar_scrub:
            self.bar_target = self.hud.bar_time(x, self.dur)
            self.hud.bar(4000, pos=self.bar_target, dur=self.dur, scrub=True)
            return
        dx, dy = x - self.x0, y - self.y0
        if self.axis is None and max(abs(dx), abs(dy)) > SWIPE_PX:
            self.axis = "h" if abs(dx) > abs(dy) else "v"
        if self.axis == "h":
            delta = dx / W * SCRUB_FULL_S
            target = max(0, min(self.dur or 1e9, self.pos0 + delta))
            self.hud.seek(delta, target, self.dur)
        elif self.axis == "v":
            change = -dy / (H * 0.7)                      # up = more; ~70 % of the screen height = full range
            if self.side == "right" and self.vol0 is not None:
                self.set_volume(self.vol0 + change)
            elif self.side == "left" and self.bl0 is not None:
                self.set_brightness(self.bl0 + change)

    def up(self, x, y):
        now = time.monotonic()
        sx, sy, sw, sh = self.hud.SKIP_RECT
        if getattr(self.hud, "skip_label", None) and self.axis is None and sx <= x <= sx + sw and sy <= y <= sy + sh:
            if getattr(self, "on_skip", None):
                self.on_skip()
            return
        if self.bar_scrub:
            self.bar_scrub = False
            self.bar_target = self.hud.bar_time(x, self.dur)
            self.send(["seek", round(self.bar_target, 1), "absolute"])
            self.hud.bar(2500, pos=self.bar_target, dur=self.dur)
            return
        if self.axis == "h":
            delta = (x - self.x0) / W * SCRUB_FULL_S
            self.send(["seek", round(delta, 1), "relative"])
            target = max(0, min(self.dur or 1e9, self.pos0 + delta))
            self.hud.seek(delta, target, self.dur, ms=600, force=True)
            return
        if self.axis == "v":
            return                      # the HUD draws the last value itself
        if now - self.t0 > 0.35:
            return
        zone = -1 if x < W * 0.35 else (1 if x > W * 0.65 else 0)
        # YouTube: after a double tap, single taps on the same side keep seeking
        if zone and zone == self.chain_dir and now < self.chain_until:
            self.seek_tap(zone, now)
            return
        t_prev, x_prev, _ = self.last_tap
        if now - t_prev < DOUBLE_S and abs(x - x_prev) < 220:
            if self.pending:
                self.pending.cancel(); self.pending = None
            self.last_tap = (0.0, 0, 0)
            if zone:
                self.chain_total = 0
                self.seek_tap(zone, now)
            else:
                self.send(["cycle", "pause"])
                paused = self.get("pause")
                self.hud.badge("pause" if paused else "play")
            return
        self.last_tap = (now, x, y)
        self.pending = threading.Timer(DOUBLE_S, self.single_tap)
        self.pending.start()

    def seek_tap(self, zone, now):
        self.chain_dir, self.chain_until = zone, now + SEEK_CHAIN_S
        self.chain_total += 10
        self.send(["seek", 10 * zone, "relative"])
        self.hud.tap(zone, self.chain_total)

    def single_tap(self):
        self.pending = None
        self.hud.bar()


def run(send, get, alive):
    """Read touches until alive() is False. Returns the Touch object (call .restore() when the movie ends)."""
    t = Touch(send, get)
    import touch_reader
    p = subprocess.Popen(touch_reader.command(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    def stop():
        """End the root touch reader. A leftover one stays in Prism's Steam session and makes Steam refuse
        to relaunch ES-DE ("Game already running", 2026-10-08)."""
        for c in subprocess.run(["pgrep", "-P", str(p.pid)], capture_output=True, text=True).stdout.split() + [str(p.pid)]:
            if subprocess.run(["kill", c], capture_output=True).returncode:
                subprocess.run(["sudo", "-n", "kill", c], capture_output=True)
    t.stop = stop
    import atexit
    atexit.register(stop)

    def loop():
        try:
            for line in p.stdout:
                if not alive():
                    break
                parts = line.split()
                if len(parts) != 3:
                    continue
                kind, x, y = parts[0], int(parts[1]), int(parts[2])
                try:
                    if kind == "D":
                        t.down(x, y)
                    elif kind == "M" and hasattr(t, "x0"):
                        t.move(x, y)
                    elif kind == "U" and hasattr(t, "x0"):
                        t.up(x, y)
                except Exception:
                    pass
        finally:
            p.terminate()

    threading.Thread(target=loop, daemon=True).start()
    return t
