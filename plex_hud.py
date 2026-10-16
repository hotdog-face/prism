#!/usr/bin/env python3
"""VLC-style on-screen visuals for the Prism movie player (2026-10-08), drawn inside mpv with one ASS
osd-overlay (no extra windows): side level bars for volume / brightness, a centred seek readout with a progress
bar, YouTube/VLC double-tap ripples, a centre play/pause badge and a bottom info bar. Coordinates are 1280x800.

  hud = Hud(send, get)          # send(cmd) = mpv IPC command (list or dict), get(prop) = mpv property
  hud.level("vol"|"bl", frac)   hud.seek(delta, target, dur)   hud.tap(+1|-1, seconds)
  hud.badge("pause"|"play")     hud.bar()                      hud.hide()
"""
import json, math, os, socket, threading, time

W, H = 1280, 800
OVL = 41
SOCK = f"/run/user/{os.getuid()}/app/io.mpv.Mpv/switchhome-mpv.sock"
ORANGE = "&H1AE0FB&"             # accent for single-colour bits: palette yellow (ASS colours are BGR)
NEON = ((0xF0, 0x43, 0x6F), (0xF6, 0x9F, 0x3C), (0xFB, 0xE0, 0x1A), (0xB6, 0xD3, 0x51), (0x27, 0xB6, 0xD5), (0x29, 0xAB, 0xEB))


def neon_at(f):
    """Redraw palette (2026-10-08, matches the home screen) at 0..1 as an ASS colour."""
    f = max(0.0, min(1.0, f)) * (len(NEON) - 1); i = min(int(f), len(NEON) - 2); t = f - i
    r, g, b = (round(NEON[i][k] * (1 - t) + NEON[i + 1][k] * t) for k in range(3))
    return f"&H{b:02X}{g:02X}{r:02X}&"
WHITE = "&HFFFFFF&"
BLACK = "&H000000&"


def fmt(sec, sign=False):
    neg = sec < 0
    sec = int(round(abs(sec)))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    t = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    return (("-" if neg else "+") + t) if sign else t


def rrect(x, y, w, h, r):
    r = max(0, min(r, w / 2, h / 2))
    return (f"m {x + r:.1f} {y:.1f} l {x + w - r:.1f} {y:.1f} b {x + w:.1f} {y:.1f} {x + w:.1f} {y:.1f} {x + w:.1f} {y + r:.1f} "
            f"l {x + w:.1f} {y + h - r:.1f} b {x + w:.1f} {y + h:.1f} {x + w:.1f} {y + h:.1f} {x + w - r:.1f} {y + h:.1f} "
            f"l {x + r:.1f} {y + h:.1f} b {x:.1f} {y + h:.1f} {x:.1f} {y + h:.1f} {x:.1f} {y + h - r:.1f} "
            f"l {x:.1f} {y + r:.1f} b {x:.1f} {y:.1f} {x:.1f} {y:.1f} {x + r:.1f} {y:.1f}")


def circle(cx, cy, r):
    k = 0.5523 * r
    return (f"m {cx:.1f} {cy - r:.1f} b {cx + k:.1f} {cy - r:.1f} {cx + r:.1f} {cy - k:.1f} {cx + r:.1f} {cy:.1f} "
            f"b {cx + r:.1f} {cy + k:.1f} {cx + k:.1f} {cy + r:.1f} {cx:.1f} {cy + r:.1f} "
            f"b {cx - k:.1f} {cy + r:.1f} {cx - r:.1f} {cy + k:.1f} {cx - r:.1f} {cy:.1f} "
            f"b {cx - r:.1f} {cy - k:.1f} {cx - k:.1f} {cy - r:.1f} {cx:.1f} {cy - r:.1f}")


def shape(path, color=WHITE, alpha="00", blur=0):
    return f"{{\\an7\\pos(0,0)\\bord0\\shad0\\blur{blur}\\1c{color}\\1a&H{alpha}&\\p1}}{path}{{\\p0}}"


def text(x, y, s, size, color=WHITE, align=5, bold=1, alpha="00"):
    return (f"{{\\an{align}\\pos({x:.0f},{y:.0f})\\fs{size}\\b{bold}\\bord1.2\\3c{BLACK}\\3a&H90&\\shad0"
            f"\\1c{color}\\1a&H{alpha}&}}{s}")


def speaker_icon(cx, cy, frac):
    out = [shape(f"m {cx - 15} {cy - 6} l {cx - 7} {cy - 6} l {cx + 3} {cy - 15} l {cx + 3} {cy + 15} "
                 f"l {cx - 7} {cy + 6} l {cx - 15} {cy + 6}")]
    if frac <= 0:
        out.append(f"{{\\an5\\pos({cx + 14},{cy})\\fs26\\b1\\bord0\\shad0\\1c{WHITE}}}×")
    else:
        for i, (dx, hh) in enumerate(((10, 8), (16, 15), (22, 22))):
            if frac > i / 3 or i == 0:
                out.append(shape(rrect(cx + dx - 1.5, cy - hh / 2, 3, hh, 1.5)))
    return out


def sun_icon(cx, cy):
    out = [shape(circle(cx, cy, 7))]
    for i in range(8):
        a = i * math.pi / 4
        x, y = cx + math.cos(a) * 13, cy + math.sin(a) * 13
        out.append(shape(circle(x, y, 2.2)))
    return out


class Hud:
    def __init__(self, send, get, sock=SOCK):
        # mpv removes an osd-overlay when the IPC connection that made it closes, so the HUD keeps its own
        # connection open (plex_play opens a new one per command); a reader thread drains mpv's replies/events.
        self.get, self.sock_path, self.conn, self.slock = get, sock, None, threading.Lock()
        self.hide_at, self.lock = 0.0, threading.Lock()
        self.last_draw, self.pending = 0.0, None
        self.bar_until = 0.0            # the bottom info bar is up until then (its timeline can be dragged)
        threading.Thread(target=self._reaper, daemon=True).start()

    # ---- plumbing
    def send(self, cmd):
        data = (json.dumps({"command": cmd}) + "\n").encode()
        with self.slock:
            for _ in range(2):
                try:
                    if self.conn is None:
                        self.conn = socket.socket(socket.AF_UNIX)
                        self.conn.settimeout(1)
                        self.conn.connect(self.sock_path)
                        threading.Thread(target=self._drain, args=(self.conn,), daemon=True).start()
                    self.conn.sendall(data)
                    return
                except OSError:
                    try:
                        self.conn and self.conn.close()
                    except OSError:
                        pass
                    self.conn = None

    def _drain(self, conn):
        conn.settimeout(None)
        try:
            while conn.recv(65536):
                pass
        except OSError:
            pass

    def _draw(self, events, ms, force=False):
        now = time.monotonic()
        with self.lock:
            self.hide_at = now + ms / 1000
            if not force and now - self.last_draw < 0.025:     # ~40 fps is plenty; the last frame is drawn on release
                self.pending = events
                return
            self.last_draw, self.pending = now, None
        self.send({"name": "osd-overlay", "id": OVL, "format": "ass-events", "data": "\n".join(events),
                   "res_x": W, "res_y": H, "z": 10})

    def _reaper(self):
        while True:
            time.sleep(0.03)
            with self.lock:
                pend, self.pending = self.pending, None
                expired = self.hide_at and time.monotonic() > self.hide_at
                if expired:
                    self.hide_at = 0.0
            if pend:
                self._draw(pend, max(50, (self.hide_at - time.monotonic()) * 1000) if self.hide_at else 300, True)
            elif expired:
                self.hide()

    SKIP_OVL = 42
    SKIP_RECT = (W - 300, H - 150, 260, 64)        # x, y, w, h (also the touch target)

    def skip(self, label):
        """Netflix-style "Skip Intro" pill bottom-right (None removes it). A or a tap skips."""
        if not label:
            if getattr(self, "skip_label", None):
                self.send({"name": "osd-overlay", "id": self.SKIP_OVL, "format": "none", "data": ""})
            self.skip_label = None
            return
        if label == getattr(self, "skip_label", None):
            return
        self.skip_label = label
        x, y, w, h = self.SKIP_RECT
        ev = [shape(rrect(x, y, w, h, h / 2), WHITE, "08", 1),
              shape(circle(x + 34, y + h / 2, 15), BLACK),
              f"{{\\an5\\pos({x + 34},{y + h / 2 + 1})\\fs20\\b1\\bord0\\shad0\\1c{WHITE}}}A",
              text(x + 62, y + h / 2, label, 26, BLACK, align=4)]
        self.send({"name": "osd-overlay", "id": self.SKIP_OVL, "format": "ass-events", "data": "\n".join(ev),
                   "res_x": W, "res_y": H, "z": 20})

    def hide(self):
        self.send({"name": "osd-overlay", "id": OVL, "format": "none", "data": ""})

    # ---- visuals
    def level(self, kind, frac, ms=1200):
        """VLC side bar: rounded pill, orange fill from the bottom, icon on top, percent underneath."""
        pct = round(max(0.0, frac) * 100)
        boost = frac > 1.0                      # VLC shows the boost range above 100 % in its accent colour
        frac = max(0.0, min(1.0, frac))
        bw, bh, top = 14, 260, 250
        cx = W - 90 if kind == "vol" else 90
        ev = [shape(rrect(cx - 46, top - 78, 92, bh + 150, 26), BLACK, "70", 1),
              shape(rrect(cx - bw / 2, top, bw, bh, bw / 2), WHITE, "B0")]
        fh = bh * frac
        if fh > 1:
            n = 24                                                   # gradient fill in slices, pink at the bottom
            for i in range(n):
                y0 = top + bh - fh + fh * i / n
                ev.append(shape(rrect(cx - bw / 2, y0, bw, fh / n + 0.6, 0), neon_at(1 - (bh - fh + fh * (i + 0.5) / n) / bh)))
        ev += speaker_icon(cx - 4, top - 38, frac) if kind == "vol" else sun_icon(cx, top - 38)
        ev.append(text(cx, top + bh + 36, f"{pct}%", 26, ORANGE if boost else WHITE))
        self._draw(ev, ms)

    def seek(self, delta, target, dur, ms=700, force=False):
        """VLC scrub: big "+01:30" with the target time underneath, and the progress bar showing where you land."""
        ev = [shape(rrect(W / 2 - 170, H / 2 - 80, 340, 140, 22), BLACK, "70", 1),
              text(W / 2, H / 2 - 22, fmt(delta, sign=True), 64),
              text(W / 2, H / 2 + 34, f"{fmt(target)}" + (f"  /  {fmt(dur)}" if dur else ""), 26, alpha="20")]
        ev += self._progress(target, dur)
        self._draw(ev, ms, force)

    def tap(self, zone, total, ms=650):
        """YouTube/VLC double tap: a soft ripple on that side with >> or << and the seconds."""
        cx = W + 180 if zone > 0 else -180
        ev = [shape(circle(cx, H / 2, 560), WHITE, "D8", 2)]
        ax = W - 250 if zone > 0 else 250
        for i in range(2):
            ox = ax - 22 + i * 22 if zone > 0 else ax + 22 - i * 22
            ev.append(shape(f"m {ox - 11 * zone} {H / 2 - 30} l {ox + 11 * zone} {H / 2 - 18} l {ox - 11 * zone} {H / 2 - 6}"))
        ev.append(text(ax, H / 2 + 24, f"{total} seconds", 26))
        self._draw(ev, ms, True)

    def badge(self, which, ms=650):
        """Centre play / pause badge."""
        cx, cy = W / 2, H / 2
        ev = [shape(circle(cx, cy, 58), BLACK, "60", 1)]
        if which == "pause":
            ev += [shape(rrect(cx - 20, cy - 24, 13, 48, 3)), shape(rrect(cx + 7, cy - 24, 13, 48, 3))]
        else:
            ev.append(shape(f"m {cx - 16} {cy - 27} l {cx + 26} {cy} l {cx - 16} {cy + 27}"))
        self._draw(ev, ms, True)

    BAR_X0, BAR_X1, BAR_Y = 40, W - 40, H - 70

    def bar(self, ms=3000, pos=None, dur=None, scrub=False):
        """Bottom info bar (single tap): title, elapsed / total and the progress line. While a finger drags the
        timeline (scrub=True) the knob grows and the elapsed time shows where you will land."""
        if pos is None:
            pos = self.get("time-pos") or 0
        if dur is None:
            dur = self.get("duration") or 0
        self.bar_until = time.monotonic() + ms / 1000
        title = (self.get("media-title") or "").replace("{", "(").replace("}", ")")
        ev = [shape(f"m 0 {H - 150} l {W} {H - 150} l {W} {H} l 0 {H}", BLACK, "80", 30),
              text(40, H - 104, title, 28, align=4),
              text(40, H - 40, fmt(pos), 22, ORANGE if scrub else WHITE, align=4, bold=1 if scrub else 0),
              text(W - 40, H - 40, fmt(dur), 22, align=6, bold=0)]
        ev += self._progress(pos, dur, y=self.BAR_Y, x0=self.BAR_X0, x1=self.BAR_X1, knob=14 if scrub else 10)
        if self.get("pause") is True:
            ev.append(text(W - 40, H - 104, "Paused", 24, ORANGE, align=6))
        self._draw(ev, ms, not scrub)

    def bar_visible(self):
        return time.monotonic() < self.bar_until

    def bar_time(self, x, dur):
        """Timeline x (screen px) -> seconds."""
        f = (x - self.BAR_X0) / (self.BAR_X1 - self.BAR_X0)
        return max(0.0, min(1.0, f)) * (dur or 0)

    def _progress(self, pos, dur, y=H - 60, x0=60, x1=W - 60, knob=10):
        if not dur:
            return []
        f = max(0.0, min(1.0, pos / dur))
        x = x0 + (x1 - x0) * f
        out = [shape(rrect(x0, y - 3, x1 - x0, 6, 3), WHITE, "A0")]
        n, w = 32, max(6, x - x0)                                    # played part: the neon gradient, left to right
        for i in range(n):
            out.append(shape(rrect(x0 + w * i / n, y - 3, w / n + 0.6, 6, 0), neon_at((w * (i + 0.5) / n) / (x1 - x0))))
        out.append(shape(circle(x, y, knob), neon_at((x - x0) / (x1 - x0))))
        return out
