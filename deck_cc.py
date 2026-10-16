#!/usr/bin/env python3
"""console-style control center for emulator games (2026-10-06). Opened by the Steam button (deck_curate).

  deck_cc.py --daemon                  always-ready card on Steam's own X display (:0), drawn as a gamescope overlay
                                       (STEAM_OVERLAY), so it sits ABOVE Steam's menu: the Steam button shows this
                                       card on press-down and Steam's menu opens and closes hidden underneath it.
                                       Control: JSON lines on the UNIX socket /tmp/deck_cc.sock
                                         {"cmd": "open", "name", "shot", "cover", "system"} -> RESUME/HOME/LASTSAVE/QUIT
                                         {"cmd": "close"}  closing animation, the open request gets RESUME
                                         {"cmd": "saved"}  after HOME: the card shows "Saved" and fades out
                                         {"cmd": "hide"}   gone at once, the open request gets CANCEL
                                         {"cmd": "state"}  IDLE / ACTIVE / SAVING
                                         {"cmd": "boot"}   Prism startup cover until /tmp/prism_ready
                                         {"cmd": "wakefade"} home background over the end of the wake video, faded out
                                         {"cmd": "dock", "label"} docked: the wordmark on black for ~1.5 s, a line
                                                           sweeps in, the label under it (2026-10-14)
                                         {"cmd": "off"}    power-off / restart animation (offtest: fades back afterwards)
  deck_cc.py <game name> <shot>        one-shot card (fallback)

Look (2026-10-07 "make that lower menu more visually appealing"): the paused frame blurs and darkens, box art + title +
console top-left, clock top-right, frosted cards with icon badges and a line of explanation; the selected card grows
with a cyan glow. Save & go Home stays on screen as "Saving your game..." (spinner + progress) until deck_curate says
it's done, then "Saved" and a fade into Prism ("show that something is saving if it makes me wait").

Structure (2026-10-13 refactor): one overlay window (CC) composites a stack of layers, top first: power-off video,
the card, boot/wake/quit cover, screensaver, disconnect notice, controller pop-ups, toast, controller chips. Each layer
has active() / draw() / tick(); CC draws the active ones down to the first exclusive one on a transparent frame, runs
one animation timer, and alone decides the window's visibility (opacity first, see _visibility). Before this, every
feature had its own show/tick/hide and opacity code, and an end path that redrew with nothing to show fell through to
the card's black backdrop and left the TV black (2026-10-10).
"""
import prism_config  # noqa: E402  (paths)
import json, math, os, select, socket, subprocess, sys, threading, time
DEMO_FLAG = "/tmp/prism_demo"   # touch it to keep personal details (Wi-Fi name) off screen

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402


def ui_call(fn, *a):
    """Run fn on the GTK loop AHEAD of drawing. Plain idle callbacks starved while the card animates (its 16 ms tick,
    blur, glow): button presses reached the card thread but were never applied (2026-10-09 card matrix)."""
    return GLib.idle_add(fn, *a, priority=GLib.PRIORITY_HIGH)
import evdev  # noqa: E402
from evdev import ecodes as E  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deck_power_menu import CYAN, FONT, NEON, NEON_STOPS, neon, rounded  # noqa: E402
import pads as pad_snapshot  # noqa: E402  (pads.py: which controllers exist; this module's pads() opens them)

ITEMS = [("RESUME", "Resume", "Back to the game", "play"),
         ("HOME", "Save & go Home", "Save here and quit", "home"),
         ("LASTSAVE", "Back to last save", "Reload your resume point", "rewind"),
         ("QUIT", "Quit without saving", "Lose progress since then", "close")]
SYSTEMS = {"movies": "Movies", "tv": "TV Shows", "music": "Music", "nes": "NES", "snes": "Super Nintendo", "n64": "Nintendo 64", "gc": "GameCube", "wii": "Wii",
           "gb": "Game Boy", "gbc": "Game Boy Color", "gba": "Game Boy Advance", "nds": "Nintendo DS",
           "psx": "PlayStation", "ps2": "PlayStation 2", "psp": "PSP", "psvita": "PS Vita", "dreamcast": "Dreamcast",
           "saturn": "Saturn", "genesis": "Genesis", "megadrive": "Mega Drive", "segacd": "Sega CD",
           "sega32x": "32X", "mastersystem": "Master System", "gamegear": "Game Gear", "pcengine": "PC Engine",
           "neogeo": "Neo Geo", "neogeocd": "Neo Geo CD", "arcade": "Arcade", "mame": "Arcade", "cps1": "CPS-1",
           "cps2": "CPS-2", "switch": "Switch", "wonderswancolor": "WonderSwan Color", "ngpc": "Neo Geo Pocket",
           "satellaview": "Satellaview", "virtualboy": "Virtual Boy", "msx": "MSX", "model2": "Model 2",
           "3ds": "Nintendo 3DS", "wiiu": "Wii U"}
SOUNDS = f"{prism_config.THEME_DIR}/switch/sounds"
SOCK = os.environ.get("DECK_CC_SOCK", "/tmp/deck_cc.sock")
OPEN_S, CLOSE_S, FADE_S = 0.30, 0.22, 0.30
SAVE_TIMEOUT = 25


OFF_VIDEO = os.path.expanduser("~/homebrew/data/SDH-AnimationChanger/animations/Prism/steam_os_suspend.webm")
SAVER_VIDEO = f"{prism_config.THEME_DIR}/switch/ambient_wave.webm"
SAVER_DIM = 0.55                         # TV screensaver: the home wave, dimmed, no UI on screen (OLED-safe)
OFF_FADE = 0.45                          # power off / restart: current screen -> the video's first frame (the home wave)
WAKE_HOLD, WAKE_FADE = 0.7, 0.9          # wake cover: seconds held over Steam's closing video, then the fade
COVER_IN = 0.35                          # game quit cover: fade-in over the closing game

# controller colours by player number, and where the chips sit (800-unit: right edge in from the screen edge, centre)
PAD_RGB = ((0xF0, 0x43, 0x6F), (0xF6, 0x9F, 0x3C), (0x27, 0xB6, 0xD5), (0xB6, 0xD3, 0x51),
           (0x29, 0xAB, 0xEB), (0xFB, 0xE0, 0x1A), (0xF0, 0x43, 0x6F), (0xF6, 0x9F, 0x3C))
PADS_RIGHT, PADS_CY = 62, 130


def sound(name):
    p = f"{SOUNDS}/{name}.wav"
    if os.path.exists(p):
        subprocess.Popen(["pw-play", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ease_out(k):
    return 1 - (1 - max(0.0, min(1.0, k))) ** 3


def smooth(k):
    """ease in-out for k already in 0..1"""
    return k * k * (3 - 2 * k)


def pad_rgb(n):
    return [c / 255 for c in PAD_RGB[(n - 1) % len(PAD_RGB)]]


def pads():
    """Controllers to read (and grab) while the card is up: pads.card_inputs(), i.e. every controller's Steam virtual
    copy AND its raw device (a copy can vanish or go quiet), not the agent's injector pad (Steam must still see the B
    we send through it to close its menu). The same press arriving through two devices is folded into one action in
    pad_thread; raw Nintendo pads report by position, so they are read by label there."""
    out = []
    for p in pad_snapshot.card_inputs():
        try:
            out.append(evdev.InputDevice(p))
        except OSError:
            pass
    return out


def glyph(cr, kind, s, rgb=(1, 1, 1)):
    cr.new_path()
    if rgb == NEON:                 # gradient-filled icon, like the line art on the home screen
        neon(cr, box=(-10 * s, -10 * s, 10 * s, 10 * s))
    else:
        cr.set_source_rgb(*rgb)
    cr.set_line_width(3 * s)
    cr.set_line_cap(cairo.LINE_CAP_ROUND); cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if kind == "play":
        cr.move_to(-6 * s, -10 * s); cr.line_to(10 * s, 0); cr.line_to(-6 * s, 10 * s); cr.close_path(); cr.fill()
    elif kind == "home":
        cr.move_to(-12 * s, -1 * s); cr.line_to(0, -12 * s); cr.line_to(12 * s, -1 * s); cr.stroke()
        cr.move_to(-8 * s, -4 * s); cr.line_to(-8 * s, 11 * s); cr.line_to(8 * s, 11 * s); cr.line_to(8 * s, -4 * s); cr.stroke()
    elif kind == "rewind":
        cr.arc(0, 0, 10 * s, math.radians(-200), math.radians(110)); cr.stroke()
        a = math.radians(-200); x, y = 10 * s * math.cos(a), 10 * s * math.sin(a)
        cr.move_to(x - 5 * s, y - 2 * s); cr.line_to(x, y + 1 * s); cr.line_to(x + 3 * s, y - 5 * s); cr.stroke()
    elif kind == "pause":
        cr.rectangle(-8 * s, -10 * s, 6 * s, 20 * s); cr.rectangle(2 * s, -10 * s, 6 * s, 20 * s); cr.fill()
    elif kind == "next":
        cr.move_to(-9 * s, -9 * s); cr.line_to(4 * s, 0); cr.line_to(-9 * s, 9 * s); cr.close_path(); cr.fill()
        cr.rectangle(5 * s, -9 * s, 4 * s, 18 * s); cr.fill()
    elif kind == "restart":
        cr.arc(0, 0, 10 * s, math.radians(-100), math.radians(200)); cr.stroke()
        cr.move_to(-2 * s, -14 * s); cr.line_to(-1 * s, -9 * s); cr.line_to(-6 * s, -7 * s); cr.stroke()
    elif kind == "note":
        cr.move_to(-3 * s, 8 * s); cr.line_to(-3 * s, -10 * s); cr.line_to(8 * s, -12 * s); cr.line_to(8 * s, 5 * s); cr.stroke()
        cr.new_path(); cr.arc(-6 * s, 8 * s, 4 * s, 0, 2 * math.pi); cr.fill()
        cr.new_path(); cr.arc(5 * s, 6 * s, 4 * s, 0, 2 * math.pi); cr.fill()
    elif kind == "check":
        cr.move_to(-10 * s, 0); cr.line_to(-3 * s, 8 * s); cr.line_to(11 * s, -8 * s); cr.stroke()
    elif kind == "clock":
        cr.arc(0, 0, 11 * s, 0, 2 * math.pi); cr.stroke()
        cr.move_to(0, -6 * s); cr.line_to(0, 0); cr.line_to(5 * s, 4 * s); cr.stroke()
    elif kind == "moon":
        cr.arc(0, 0, 11 * s, math.radians(40), math.radians(320)); cr.arc_negative(5 * s, -3 * s, 9 * s, math.radians(300), math.radians(60))
        cr.close_path(); cr.fill()
    elif kind == "save":
        cr.rectangle(-10 * s, -10 * s, 20 * s, 20 * s); cr.stroke()
        cr.rectangle(-5 * s, -10 * s, 10 * s, 6 * s); cr.fill()
        cr.rectangle(-6 * s, 3 * s, 12 * s, 7 * s); cr.stroke()
    elif kind == "load":
        cr.rectangle(-11 * s, -8 * s, 22 * s, 16 * s); cr.stroke()
        cr.move_to(-11 * s, 4 * s); cr.line_to(-4 * s, -2 * s); cr.line_to(2 * s, 4 * s); cr.line_to(5 * s, 1 * s)
        cr.line_to(11 * s, 6 * s); cr.stroke()
        cr.new_path(); cr.arc(5 * s, -3 * s, 2 * s, 0, 2 * math.pi); cr.fill()
    elif kind == "sort":
        for i, w in enumerate((20, 14, 8)):
            cr.move_to(-10 * s, (-7 + i * 7) * s); cr.line_to((-10 + w) * s, (-7 + i * 7) * s)
        cr.stroke()
    elif kind == "info":
        cr.arc(0, 0, 11 * s, 0, 2 * math.pi); cr.stroke()
        cr.move_to(0, -1 * s); cr.line_to(0, 6 * s); cr.stroke()
        cr.new_path(); cr.arc(0, -5.5 * s, 1.8 * s, 0, 2 * math.pi); cr.fill()
    elif kind == "speaker":
        cr.move_to(-10 * s, -4 * s); cr.line_to(-5 * s, -4 * s); cr.line_to(1 * s, -9 * s); cr.line_to(1 * s, 9 * s)
        cr.line_to(-5 * s, 4 * s); cr.line_to(-10 * s, 4 * s); cr.close_path(); cr.fill()
        cr.arc(2 * s, 0, 6 * s, -0.8, 0.8); cr.stroke(); cr.arc(2 * s, 0, 10 * s, -0.8, 0.8); cr.stroke()
    elif kind == "sun":
        cr.arc(0, 0, 5 * s, 0, 2 * math.pi); cr.fill()
        for i in range(8):
            a = i * math.pi / 4
            cr.move_to(math.cos(a) * 8 * s, math.sin(a) * 8 * s); cr.line_to(math.cos(a) * 11 * s, math.sin(a) * 11 * s)
        cr.stroke()
    elif kind == "close":
        cr.move_to(-8 * s, -8 * s); cr.line_to(8 * s, 8 * s); cr.move_to(8 * s, -8 * s); cr.line_to(-8 * s, 8 * s); cr.stroke()


def content_box(pb):
    """The picture inside a frame's black borders (x, y, w, h); the whole frame if there are none."""
    w, h, n, rs = pb.get_width(), pb.get_height(), pb.get_n_channels(), pb.get_rowstride()
    px = pb.get_pixels()
    def dark_col(x):
        return all(sum(px[y * rs + x * n: y * rs + x * n + 3]) < 36 for y in range(0, h, max(1, h // 24)))
    def dark_row(y):
        return all(sum(px[y * rs + x * n: y * rs + x * n + 3]) < 36 for x in range(0, w, max(1, w // 32)))
    l, r, t, b = 0, w - 1, 0, h - 1
    while l < w // 3 and dark_col(l): l += 2
    while r > w * 2 // 3 and dark_col(r): r -= 2
    while t < h // 3 and dark_row(t): t += 2
    while b > h * 2 // 3 and dark_row(b): b -= 2
    return l, t, max(1, r - l + 1), max(1, b - t + 1)


def text(cr, txt, x, y, size, rgba, bold=False, align="left"):
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size); e = cr.text_extents(txt)
    dx = {"left": 0, "center": -e.x_advance / 2, "right": -e.x_advance}[align]
    cr.set_source_rgba(*rgba); cr.move_to(x + dx, y); cr.show_text(txt)
    return e.x_advance


def paint_bg(cr, pb, W, H, alpha):
    cr.save(); cr.scale(W / pb.get_width(), H / pb.get_height())
    Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0); cr.paint_with_alpha(alpha); cr.restore()


def draw_battery(cr, bx, by, bw, bh, it, a, s):
    """4-segment battery (Switch Pro reports levels, not percent). Charging: bolt beside it, segments show how
    far it has got; charged: green. Low and not charging: pink."""
    pct, chg = it.get("bat") or 0, it.get("chg")
    segs = 4 if pct >= 90 else 3 if pct >= 65 else 2 if pct >= 35 else 1
    full = chg and segs == 4
    col = (0.71, 0.83, 0.32) if full else (0.94, 0.26, 0.44) if segs == 1 and not chg else (1, 1, 1)
    if chg:                                            # bolt to the left of the battery
        u = bh * 0.62; cx, cy = bx - 6 * s, by + bh / 2
        cr.move_to(cx + 0.15 * u, cy - u); cr.line_to(cx - 0.45 * u, cy + 0.12 * u); cr.line_to(cx - 0.02 * u, cy + 0.12 * u)
        cr.line_to(cx - 0.2 * u, cy + u); cr.line_to(cx + 0.45 * u, cy - 0.15 * u); cr.line_to(cx + 0.02 * u, cy - 0.15 * u)
        cr.close_path(); cr.set_source_rgba(0.98, 0.88, 0.10, a); cr.fill()
    cr.rectangle(bx, by, bw, bh); cr.set_source_rgba(*col, 0.7 * a); cr.set_line_width(1.2 * s); cr.stroke()
    cr.rectangle(bx + bw, by + bh * 0.3, 2 * s, bh * 0.4); cr.fill()
    inner = bw - 3.6 * s; g = 1.0 * s; sw = (inner - 3 * g) / 4
    # charging: a sweep - the segments above the current level light up one after another, then start again
    base = max(0, segs - 1) if chg and not full else segs
    steps = 4 - base + 1                                         # frames: level .. full, plus a pause
    frame = int(time.monotonic() / 0.45) % (steps + 1) if chg and not full else 0
    for k in range(4):
        cr.rectangle(bx + 1.8 * s + k * (sw + g), by + 1.8 * s, sw, bh - 3.6 * s)
        if chg and not full:
            on = k < base or (k - base) < frame
            al = 0.92 if k < base else (0.85 if on else 0.12)
            c = (0.71, 0.83, 0.32) if (on and k >= base) else col     # the sweep in green
        else:
            on = k < segs; al = 0.92 if on else 0.12; c = col
        cr.set_source_rgba(*c, al * a); cr.fill()


def draw_chips(cr, s, items, right, cy, a, glow):
    """Controller chips: player number + battery per controller, right-aligned at `right`, centred on `cy`.
    One fixed place on every screen (2026-10-10 party, the owner: "the controller UI should never move"): the in-game
    card's spot, top right under the status icons / the card's clock. glow(n) lights a chip after an input."""
    r, gap = 12 * s, 8 * s
    chip_w = [2 * r + ((12 if it.get('chg') else 4) * s + 20 * s if it.get("bat") is not None else 0) + 10 * s for it in items]
    x = right - (sum(chip_w) + gap * (len(chip_w) - 1))
    for it, w in zip(items, chip_w):
        h = 2 * r + 6 * s; y = cy - h / 2; rr = h / 2
        cr.new_sub_path()
        cr.arc(x + w - rr, y + rr, rr, -math.pi / 2, math.pi / 2); cr.arc(x + rr, y + rr, rr, math.pi / 2, 3 * math.pi / 2)
        cr.close_path()
        col = pad_rgb(it["n"])
        gl = glow(it["n"])
        cr.set_source_rgba(64 / 255, 64 / 255, 67 / 255, (0.55 + 0.3 * gl) * a); cr.fill_preserve()
        if gl > 0:
            cr.set_source_rgba(*col, gl * a); cr.set_line_width(2.4 * s); cr.stroke_preserve()
        cr.set_source_rgba(1, 1, 1, 0.22 * a * (1 - gl)); cr.set_line_width(1.2 * s); cr.stroke()
        cx = x + 5 * s + r
        cr.new_path(); cr.arc(cx, cy, r - 1 * s, 0, 2 * math.pi); cr.set_source_rgba(*col, a); cr.fill()
        cr.select_font_face("Noto Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); cr.set_font_size(13 * s)
        e = cr.text_extents(str(it["n"]))
        cr.move_to(cx - e.x_advance / 2, cy + e.height / 2); cr.set_source_rgba(0.08, 0.08, 0.08, a); cr.show_text(str(it["n"]))
        if it.get("bat") is not None:
            draw_battery(cr, cx + r + (12 if it.get('chg') else 4) * s, cy - 4.5 * s, 18 * s, 9 * s, it, a, s)
        x += w + gap


def gst():
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    return Gst


class Video:
    """A webm decoded to BGRx frames that cairo paints (the power-off video, the screensaver's wave)."""

    def __init__(self, path, sink_opts, preroll=False, loop=False, on_end=None):
        self.Gst = Gst = gst()
        self.frame = self.size = None
        self.lock, self.stopped = threading.Lock(), False
        self.pipe = Gst.parse_launch(f'filesrc location="{path}" ! matroskademux ! vp9dec ! videoconvert ! '
                                     f'video/x-raw,format=BGRx ! appsink name=sink emit-signals=true {sink_opts}')
        sink = self.pipe.get_by_name("sink")
        if preroll:
            sink.connect("new-preroll", lambda k: self._sample(k.emit("pull-preroll")))
        sink.connect("new-sample", lambda k: self._sample(k.emit("pull-sample")))
        bus = self.pipe.get_bus(); bus.add_signal_watch()
        if loop:
            bus.connect("message::eos", lambda *_: self.pipe.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0))
        if on_end:                                              # end of the video, or it failed to play
            bus.connect("message::eos", lambda *_: on_end())
            bus.connect("message::error", lambda *_: on_end())

    def _sample(self, sample):
        if self.stopped or sample is None:
            return 0
        buf, st = sample.get_buffer(), sample.get_caps().get_structure(0)
        ok, info = buf.map(1)                                   # Gst.MapFlags.READ
        if ok:
            with self.lock:
                self.frame, self.size = bytearray(info.data), (st.get_value("width"), st.get_value("height"))
            buf.unmap(info)
        return 0                                                # Gst.FlowReturn.OK

    def state(self, name):
        self.pipe.set_state(getattr(self.Gst.State, name))

    def stop(self):
        self.stopped = True
        self.state("NULL")

    def paint(self, cr, W, H, alpha, cover=False):
        """The latest frame stretched to the screen (cover: scaled to fill it, centred)."""
        with self.lock:
            if self.frame is None:
                return
            w, h = self.size
            surf = cairo.ImageSurface.create_for_data(self.frame, cairo.FORMAT_RGB24, w, h, w * 4)
            cr.save()
            if cover:
                sc = max(W / w, H / h)
                cr.translate((W - w * sc) / 2, (H - h * sc) / 2); cr.scale(sc, sc)
            else:
                cr.scale(W / w, H / h)
            cr.set_source_surface(surf, 0, 0); cr.paint_with_alpha(alpha); cr.restore()


# ---- layers: CC (the window) draws the active ones, top first, down to the first exclusive one ----
class Layer:
    exclusive = True               # covers everything under it
    frame_ms = 16                  # animation pace while animating(); CC's one timer runs at the fastest one wanted

    def __init__(self, cc):
        self.cc = cc

    def active(self):
        return False

    def ready(self):
        """may the window become visible for it yet (a video's first frame decoded, ...)"""
        return True

    def animating(self):
        return self.active()

    def tick(self, now):
        pass

    def draw(self, cr, W, H):
        pass

    def redraw(self):
        self.cc.area.queue_draw()


class Off(Layer):
    """Power off / restart (2026-10-09): the approved Prism "good night" video, played by this overlay so it sits
    above everything (Steam's own shutdown is a 300 ms fade to black). Ends on black and stays black until the power
    goes; test=True fades back out afterwards."""

    def __init__(self, cc):
        super().__init__(cc)
        self.o = None

    def active(self):
        return self.o is not None

    def ready(self):
        return self.o["shown"]

    def show(self, test=False):
        if self.o is not None:
            return
        self.o = {"t0": time.monotonic(), "playing": False, "eos": None, "test": test, "shown": False}
        self.o["video"] = Video(OFF_VIDEO, "sync=true max-buffers=3", preroll=True,
                                on_end=lambda: self.o and self.o.update(eos=time.monotonic()))
        self.o["video"].state("PAUSED")                 # pre-roll: the first frame, held during the fade-in
        self.cc.layer_started()

    def tick(self, now):
        o = self.o
        if not o["shown"] and o["video"].frame is not None:
            o["shown"] = True; o["t1"] = now
        if o["shown"] and not o["playing"] and now - o["t1"] > OFF_FADE:
            o["playing"] = True; o["video"].state("PLAYING")
        if not o["shown"] and now - o["t0"] > 3:              # video didn't decode: plain black instead
            o["shown"] = o["playing"] = True; o["t1"] = now; o["eos"] = now
        if o["eos"] is not None and (o["test"] and now - o["eos"] > 1.2 or now - o["eos"] > 25):
            o["video"].stop()
            self.o = None

    def draw(self, cr, W, H):
        o = self.o; now = time.monotonic()
        if not o["shown"]:
            return
        if o["eos"] is not None and o["playing"]:
            a = 1.0 if not o["test"] else max(0.0, 1 - (now - o["eos"] - 0.6) / 0.6) if now - o["eos"] > 0.6 else 1.0
            cr.set_source_rgba(0, 0, 0, a); cr.paint()
            return
        k = min(1.0, (now - o["t1"]) / OFF_FADE)
        o["video"].paint(cr, W, H, smooth(k))


class Card(Layer):
    """The in-game Home card (and the game hub, and media cards): opened by deck_curate over a paused game."""
    HOLD_KEYS, HOLD_S = ("QUIT", "LASTSAVE"), 1.2
    BL = "/sys/class/backlight/amdgpu_bl0"

    def __init__(self, cc):
        super().__init__(cc)
        self.is_open, self.on_done, self.rects = False, None, []
        self.hub, self.states, self.picker, self.psel, self.prects = None, [], False, 0, []
        self.qs_focus, self.qs_rects, self.qs = None, [], {"vol": None, "bri": None, "bat": "", "wifi": ""}
        self.hint_rects, self.bg_t, self._bg_token = [], None, None
        self.reset()

    def active(self):
        return self.is_open

    def reset(self):
        self._items_orig, self.quit_arm = None, 0
        self.hold_t, self.hold_hint_t, self.hold_sel = None, 0.0, -1
        self.items, self.saving_ok, self.status = ITEMS, True, "Paused"
        self.name, self.system, self.sel, self.choice, self.sel_t = "", "", 0, None, 0.0
        self.t0 = self.closing = self.save_t = self.saved_t = self.fade_t = None
        self.bg = self.soft = self.cover = None

    # ---- lifecycle ----
    def begin(self, name, shot, on_done, cover="", system="", items=None, saving=True, status="Paused", states=None,
              hub=None):
        self.cc.toast.clear()
        self.reset()
        self.hub = hub                     # console-style game hub before playing (game_hub.py, 2026-10-08)
        if hub:
            for k, size in (("hero", 1280), ("logo", 900), ("resume", 900), ("cover", 600)):
                try:
                    hub[k + "_pb"] = GdkPixbuf.Pixbuf.new_from_file_at_scale(hub[k], size, size, True) if hub.get(k) else None
                except Exception:
                    hub[k + "_pb"] = None
        self.states, self.picker, self.psel, self.prects = states or [], False, 0, []
        self.qs_focus, self.qs_rects, self.qs = None, [], {"vol": None, "bri": None, "bat": "", "wifi": ""}
        threading.Thread(target=self.read_quick, daemon=True).start()
        for st in self.states:                    # save-state thumbnails for the Load state picker
            try:
                st["pb"] = GdkPixbuf.Pixbuf.new_from_file_at_scale(st["png"], 320, 200, True) if st.get("png") else None
            except Exception:
                st["pb"] = None
        self.items = [tuple(i) for i in items] if items else ITEMS     # media cards (movie / music) or the game set
        self.saving_ok, self.status = saving, status
        self.name, self.system, self.on_done = name, system, on_done
        if shot:
            # the paused frame loads in the background and fades in: docked, gamescope writes a 4K PNG that can take
            # longer than the card's opening (it showed black, 2026-10-09)
            token = object(); self._bg_token = token; self.bg_t = None
            def load():
                end = time.time() + 8
                while time.time() < end:
                    try:
                        with open(shot, "rb") as f:
                            f.seek(-12, 2)
                            if f.read().find(b"IEND") >= 0:
                                break
                    except OSError:
                        pass
                    time.sleep(0.05)
                try:
                    bg = GdkPixbuf.Pixbuf.new_from_file_at_scale(shot, 1600, 1000, True)
                    w, h = bg.get_width(), bg.get_height()          # soft blur: down to 1/18, back up
                    x0, y0, cw_, ch_ = content_box(bg)              # without pillar/letterbox bars (GBA, 4:3 games)
                    core = bg.new_subpixbuf(x0, y0, cw_, ch_)
                    small = core.scale_simple(max(1, cw_ // 18), max(1, ch_ // 18), GdkPixbuf.InterpType.BILINEAR)
                    soft = small.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
                except Exception:
                    return
                def apply():
                    if self._bg_token is token:
                        self.bg, self.soft, self.bg_t = bg, soft, time.monotonic()
                        self.redraw()
                    return False
                ui_call(apply)
            threading.Thread(target=load, daemon=True).start()
        if cover and os.path.exists(cover):
            try:
                self.cover = GdkPixbuf.Pixbuf.new_from_file_at_scale(cover, -1, 220, True)
            except Exception:
                self.cover = None
        self.is_open = True
        self.cc.layer_started()
        threading.Thread(target=pad_thread, args=(self, [] if os.environ.get("DECK_CC_NOPADS") else pads()),
                         daemon=True).start()

    def report(self, choice):
        cb, self.on_done = self.on_done, None
        if cb:
            cb(choice)

    def finish(self, choice):
        self.is_open = False
        self.cc.refresh()                             # invisible first, unmapped a few frames later
        self.report(choice)

    def close_as(self, choice):
        """Closing animation, then report `choice`."""
        if self.choice is None:
            self.choice = choice
            sound("cc_close" if choice == "RESUME" else "cc_choose")
            self.closing = time.monotonic()

    def start_saving(self):
        """Save & go Home: report HOME right away (deck_curate saves + quits), stay up as 'Saving...'."""
        if self.choice is None:
            self.choice = "HOME"; self.save_t = time.monotonic()
            sound("cc_choose")
            self.report("HOME")

    def saved(self):
        if self.save_t is not None and self.saved_t is None:
            self.saved_t = time.monotonic()
            sound("cc_open")

    def state(self):
        return "IDLE" if not self.is_open else ("SAVING" if self.save_t is not None else "ACTIVE")

    def progress(self):
        now = time.monotonic()
        if self.t0 is None:
            return 0.0
        if self.closing is not None:
            return 1 - ease_out((now - self.closing) / CLOSE_S)
        return ease_out((now - self.t0) / OPEN_S)

    def tick(self, now):
        if self.t0 is None:
            self.t0 = now; sound("cc_open")       # CC makes the window visible on this same tick
        if self.hold_t is not None:               # hold to confirm: done once A has been held for HOLD_S
            if self.choice is not None or self.sel != self.hold_sel:
                self.hold_t = None
            elif now - self.hold_t >= self.HOLD_S:
                self.hold_t = None
                self.close_as(self.items[self.sel][0])
        if self.save_t is not None:
            if self.saved_t is None and now - self.save_t > SAVE_TIMEOUT:
                self.saved()
            if self.saved_t is not None and self.fade_t is None and now - self.saved_t > 0.8:
                self.fade_t = now
            if self.fade_t is not None and now - self.fade_t > FADE_S:
                self.finish("DONE"); return
        if self.closing is not None and now - self.closing > CLOSE_S + 0.02:
            self.finish(self.choice)

    # ---- quick settings (PS5 control center / Switch quick settings, 2026-10-08)
    def read_quick(self):
        q = self.qs
        try:
            out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True, timeout=2).stdout
            q["vol"] = 0.0 if "MUTED" in out else float(out.split()[1])
        except Exception:
            q["vol"] = None
        try:
            q["bri"] = int(open(f"{self.BL}/brightness").read()) / int(open(f"{self.BL}/max_brightness").read())
        except Exception:
            q["bri"] = None
        try:
            cap = open("/sys/class/power_supply/BAT1/capacity").read().strip()
            st = open("/sys/class/power_supply/BAT1/status").read().strip()
            q["bat"] = f"{cap}%" + (" · Charging" if st == "Charging" else " · Full" if st == "Full" else "")
        except Exception:
            q["bat"] = ""
        try:
            out = subprocess.run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"], capture_output=True, text=True, timeout=3).stdout
            q["wifi"] = next((l.split(":", 1)[1] for l in out.splitlines() if l.startswith("yes:")), "")
        except Exception:
            q["wifi"] = ""
        if os.path.exists(DEMO_FLAG):     # demo mode (filming, screenshots): no Wi-Fi network name on screen
            q["wifi"] = ""
        ui_call(self.redraw)

    def set_quick(self, which, value):
        value = max(0.0, min(1.0, value))
        self.qs[which] = value
        def apply():
            try:
                if which == "vol":
                    subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{value:.2f}"], timeout=2)
                    subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if value <= 0 else "0"], timeout=2)
                else:
                    mx = int(open(f"{self.BL}/max_brightness").read())
                    open(f"{self.BL}/brightness", "w").write(str(int(max(0.04, value) * mx)))
            except Exception:
                pass
        threading.Thread(target=apply, daemon=True).start()
        self.redraw()

    def vmove(self, d):
        """Up from the tiles reaches the quick settings (volume, then brightness); Down goes back."""
        if self.picker or self.closing is not None or self.save_t is not None:
            return
        order = [None, "vol", "bri"]
        i = order.index(self.qs_focus) + (-d)
        new = order[max(0, min(2, i))]
        if new and self.qs.get(new) is None:
            return
        if new != self.qs_focus:
            self.qs_focus = new; sound("cc_move"); self.redraw()

    def draw_quick(self, cr, W, H, s, p):
        q = self.qs
        x1 = W - 56 * s
        info = " · ".join(v for v in (q["bat"], q["wifi"]) if v)
        if info:
            text(cr, info, x1, 104 * s, 15 * s, (0.80, 0.80, 0.78, p), align="right")
        self.qs_rects = []
        for i, (key, icon) in enumerate((("vol", "speaker"), ("bri", "sun"))):
            v = q.get(key)
            if v is None:
                continue
            w, h = 300 * s, 50 * s
            x, y = x1 - w, 124 * s + i * 62 * s
            foc = self.qs_focus == key
            self.qs_rects.append((key, x, y, w, h))
            if foc:
                rounded(cr, x - 4 * s, y - 4 * s, w + 8 * s, h + 8 * s, 29 * s)
                neon(cr, 0.95 * p); cr.set_line_width(3 * s); cr.stroke()
            rounded(cr, x, y, w, h, 25 * s)
            cr.set_source_rgba(0.25, 0.25, 0.24, 0.95 * p) if foc else cr.set_source_rgba(0.12, 0.12, 0.115, 0.82 * p)
            cr.fill()
            cr.push_group(); cr.save(); cr.translate(x + 30 * s, y + h / 2); glyph(cr, icon, s, NEON if foc else (0.9, 0.9, 0.92))
            cr.restore(); cr.pop_group_to_source(); cr.paint_with_alpha(p)
            bx, bw = x + 56 * s, w - 120 * s
            rounded(cr, bx, y + h / 2 - 3 * s, bw, 6 * s, 3 * s); cr.set_source_rgba(1, 1, 1, 0.16 * p); cr.fill()
            rounded(cr, bx, y + h / 2 - 3 * s, max(6 * s, bw * v), 6 * s, 3 * s)
            neon(cr, p) if foc else cr.set_source_rgba(0.92, 0.92, 0.94, p); cr.fill()
            text(cr, f"{round(v * 100)}%", x + w - 18 * s, y + h / 2 + 6 * s, 16 * s, (1, 1, 1, p), align="right")

    # ---- drawing ----
    def draw(self, cr, W, H):
        s = H / 800
        now = time.monotonic()
        p = self.progress()
        fade = 1 - ease_out((now - self.fade_t) / FADE_S) if self.fade_t is not None else 1.0
        cr.set_source_rgb(0, 0, 0); cr.paint()
        if self.hub:
            cr.push_group(); self.draw_hub(cr, W, H, s, p, now); cr.pop_group_to_source(); cr.paint_with_alpha(fade)
            return
        cr.push_group()
        # background: the paused frame -> soft blur, darker toward the bottom
        if self.bg:
            kb = 1.0 if self.bg_t is None else min(1.0, (now - self.bg_t) / 0.3)
            paint_bg(cr, self.bg, W, H, kb)
            if self.soft and p > 0:
                paint_bg(cr, self.soft, W, H, p * kb)
        g = cairo.LinearGradient(0, 0, 0, H)
        g.add_color_stop_rgba(0, 0.03, 0.03, 0.025, 0.62 * p); g.add_color_stop_rgba(0.5, 0.03, 0.03, 0.025, 0.50 * p)
        g.add_color_stop_rgba(1, 0.01, 0.01, 0.02, 0.86 * p)
        cr.set_source(g); cr.paint()
        if p > 0.001:
            self.draw_header(cr, W, H, s, p)
            if self.save_t is None and not self.picker:
                self.draw_quick(cr, W, H, s, p)
            if self.save_t is None and self.picker:
                self.draw_picker(cr, W, H, s, p)
            elif self.save_t is None:
                self.draw_cards(cr, W, H, s, p, now, 1.0)
            else:
                k = 1 - ease_out((now - self.save_t) / 0.25)
                if k > 0:
                    self.draw_cards(cr, W, H, s, p, now, k)
                self.draw_saving(cr, W, H, s, now)
            if self.save_t is None:
                self.draw_hints(cr, W, H, s, p)
        cr.pop_group_to_source(); cr.paint_with_alpha(fade)

    def draw_header(self, cr, W, H, s, p):
        y = 48 * s - (1 - p) * 20 * s
        x = 56 * s
        if self.cover:
            ch = 108 * s; cw = self.cover.get_width() * ch / self.cover.get_height()
            cr.save(); rounded(cr, x, y, cw, ch, 10 * s); cr.clip()
            cr.translate(x, y); cr.scale(ch / self.cover.get_height(), ch / self.cover.get_height())
            Gdk.cairo_set_source_pixbuf(cr, self.cover, 0, 0); cr.paint_with_alpha(p); cr.restore()
            rounded(cr, x, y, cw, ch, 10 * s); cr.set_source_rgba(1, 1, 1, 0.15 * p); cr.set_line_width(1.5 * s); cr.stroke()
            x += cw + 22 * s
        name = self.name if len(self.name) <= 44 else self.name[:42] + "…"
        text(cr, name, x, y + 44 * s, 30 * s, (1, 1, 1, p), bold=True)
        sub = SYSTEMS.get(self.system, self.system.upper())
        sub = f"{sub}  ·  {self.status}" if sub else self.status
        text(cr, sub, x, y + 76 * s, 18 * s, (0.80, 0.80, 0.78, p))
        text(cr, time.strftime("%-I:%M %p"), W - 56 * s, y + 30 * s, 22 * s, (1, 1, 1, 0.9 * p), align="right")
        items = self.cc.chips.items
        if items:                                        # who's connected, which player, battery (controller_bar)
            draw_chips(cr, s, items, W - PADS_RIGHT * s, PADS_CY * s, p, self.cc.chips.glow)

    def draw_cards(self, cr, W, H, s, p, now, k_all):
        cw, ch, gap = {4: (232 * s, 176 * s, 24 * s), 5: (206 * s, 176 * s, 18 * s)}.get(
            len(self.items), (232 * s, 176 * s, 24 * s) if len(self.items) < 4 else (176 * s, 176 * s, 14 * s))
        total = len(self.items) * cw + (len(self.items) - 1) * gap
        x0 = (W - total) / 2
        y0 = H - 268 * s + (1 - p) * 60 * s + (1 - k_all) * 40 * s
        self.rects = []
        for i, (_, label, desc, icon) in enumerate(self.items):
            k = (ease_out((now - self.t0 - 0.045 * i) / OPEN_S) if self.closing is None and self.t0 else p) * k_all
            x = x0 + i * (cw + gap); y = y0 + (1 - k) * 26 * s
            self.rects.append((x, y0, cw, ch))
            sel = i == self.sel and self.save_t is None and not self.qs_focus
            grow = ease_out((now - self.sel_t) / 0.14) if sel else 0.0
            sc = 1 + 0.045 * grow
            cx, cy = x + cw / 2, y + ch / 2
            cr.save(); cr.translate(cx, cy); cr.scale(sc, sc); cr.translate(-cw / 2, -ch / 2)
            if sel:      # lifted: soft shadow under the tile, cyan focus ring around it (Switch)
                for j, (d, a) in enumerate(((18, 0.10), (10, 0.16), (4, 0.22))):
                    rounded(cr, -d * s / 2, d * s / 3, cw + d * s, ch + d * s / 2, (22 + d / 2) * s)
                    cr.set_source_rgba(0, 0, 0, a * k * grow); cr.fill()
                rounded(cr, -5 * s, -5 * s, cw + 10 * s, ch + 10 * s, 27 * s)
                neon(cr, 0.95 * k); cr.set_line_width(3.5 * s); cr.stroke()
            rounded(cr, 0, 0, cw, ch, 22 * s)
            if sel:      # dark mode: the focused tile is a lighter graphite under the cyan ring
                fill = cairo.LinearGradient(0, 0, 0, ch)
                fill.add_color_stop_rgba(0, 0.25, 0.25, 0.29, 0.97 * k); fill.add_color_stop_rgba(1, 0.19, 0.19, 0.23, 0.97 * k)
                cr.set_source(fill); cr.fill()
            else:        # frosted dark glass
                cr.set_source_rgba(0.12, 0.12, 0.115, 0.80 * k); cr.fill_preserve()
                cr.set_source_rgba(1, 1, 1, 0.07 * k); cr.set_line_width(1.2 * s); cr.stroke()
            # icon on its own (no badge): cyan when focused
            cr.push_group(); cr.save(); cr.translate(cw / 2, 58 * s); cr.scale(1.45, 1.45)
            glyph(cr, icon, s, NEON if sel else (0.92, 0.93, 0.95)); cr.restore()
            cr.pop_group_to_source(); cr.paint_with_alpha(k)
            ink = (1, 1, 1)
            soft_ink = (0.80, 0.80, 0.78) if sel else (0.62, 0.64, 0.68)
            small = len(self.items) > 5                 # six tiles: a size down so long labels fit
            key = self.items[i][0]
            if sel and key in self.HOLD_KEYS:
                desc = "Hold A to confirm"
                if self.hold_t is not None:             # the ring fills while A is held
                    frac = min(1.0, (now - self.hold_t) / self.HOLD_S)
                    cr.new_path(); cr.arc(cw / 2, 58 * s, 40 * s, -math.pi / 2, -math.pi / 2 + 2 * math.pi * frac)
                    neon(cr, k); cr.set_line_width(5 * s); cr.stroke()
                elif now - self.hold_hint_t < 1.5:
                    soft_ink = (0.98, 0.88, 0.10)       # flash the hint after a tap
            text(cr, label, cw / 2, 120 * s, (16.5 if small else 19) * s, (*ink, k), bold=True, align="center")
            text(cr, desc, cw / 2, 146 * s, (12.5 if small else 14) * s, (*soft_ink, k), align="center")
            cr.restore()

    def draw_picker(self, cr, W, H, s, p):
        """Load state: up to 6 saves as thumbnails (newest first), the focused one ringed in cyan."""
        tw, th, gap = 300 * s, 188 * s, 26 * s
        cols = min(3, max(1, len(self.states)))
        x0 = (W - (cols * tw + (cols - 1) * gap)) / 2
        y0 = H - 560 * s
        text(cr, "Load a save state", W / 2, y0 - 26 * s, 24 * s, (1, 1, 1, p), bold=True, align="center")
        self.prects = []
        for i, st in enumerate(self.states[:6]):
            x = x0 + (i % cols) * (tw + gap); y = y0 + (i // cols) * (th + 64 * s)
            self.prects.append((x, y, tw, th + 40 * s))
            sel = i == self.psel
            if sel:
                rounded(cr, x - 6 * s, y - 6 * s, tw + 12 * s, th + 12 * s, 18 * s)
                neon(cr, 0.95 * p); cr.set_line_width(3.5 * s); cr.stroke()
            rounded(cr, x, y, tw, th, 14 * s); cr.set_source_rgba(0.12, 0.12, 0.115, 0.9 * p); cr.fill()
            pb = st.get("pb")
            if pb:
                cr.save(); rounded(cr, x, y, tw, th, 14 * s); cr.clip()
                sc = max(tw / pb.get_width(), th / pb.get_height())
                cr.translate(x + (tw - pb.get_width() * sc) / 2, y + (th - pb.get_height() * sc) / 2); cr.scale(sc, sc)
                Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0); cr.paint_with_alpha(p if sel else 0.75 * p); cr.restore()
            text(cr, st.get("label", ""), x + tw / 2, y + th + 28 * s, 16 * s, (1, 1, 1, p) if sel else (0.7, 0.72, 0.76, p),
                 bold=sel, align="center")

    def draw_hub(self, cr, W, H, s, p, now):
        h = self.hub
        if self.picker:                             # Load state from the hub: the picker over the hero art
            if h.get("hero_pb"):
                paint_bg(cr, h["hero_pb"], W, H, 1.0)
            cr.set_source_rgba(0.03, 0.03, 0.025, 0.78); cr.paint()
            self.draw_header(cr, W, H, s, p); self.draw_picker(cr, W, H, s, p); self.draw_hints(cr, W, H, s, p)
            return
        hero = h.get("hero_pb")
        if hero:
            sc = max(W / hero.get_width(), H / hero.get_height()) * (1.04 - 0.04 * p)     # slow settle, PS5-like
            cr.save(); cr.translate((W - hero.get_width() * sc) / 2, (H - hero.get_height() * sc) / 2); cr.scale(sc, sc)
            Gdk.cairo_set_source_pixbuf(cr, hero, 0, 0); cr.paint(); cr.restore()
        g = cairo.LinearGradient(0, 0, W, 0)
        g.add_color_stop_rgba(0, 0.03, 0.03, 0.025, 0.92); g.add_color_stop_rgba(0.55, 0.03, 0.03, 0.025, 0.55)
        g.add_color_stop_rgba(1, 0.03, 0.03, 0.025, 0.25)
        cr.set_source(g); cr.paint()
        g = cairo.LinearGradient(0, 0, 0, H)
        g.add_color_stop_rgba(0.55, 0, 0, 0, 0); g.add_color_stop_rgba(1, 0, 0, 0, 0.85)
        cr.set_source(g); cr.paint()
        x = 80 * s; y = 150 * s - (1 - p) * 20 * s
        logo = h.get("logo_pb")
        if logo:
            sc = min(520 * s / logo.get_width(), 150 * s / logo.get_height())
            cr.save(); cr.translate(x, y); cr.scale(sc, sc); Gdk.cairo_set_source_pixbuf(cr, logo, 0, 0); cr.paint_with_alpha(p)
            cr.restore(); y += logo.get_height() * sc + 34 * s
        else:
            name = h.get("name", "")
            text(cr, name if len(name) < 34 else name[:32] + "…", x, y + 50 * s, 50 * s, (1, 1, 1, p), bold=True)
            y += 92 * s
        text(cr, h.get("meta", ""), x, y, 18 * s, (0.80, 0.82, 0.86, p))
        y += 40 * s
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(17 * s)
        line, lines = "", []
        for word in (h.get("desc") or "").split():
            if cr.text_extents(line + " " + word).x_advance > 560 * s:
                lines.append(line); line = word
                if len(lines) == 4:
                    break
            else:
                line = (line + " " + word).strip()
        if line and len(lines) < 4:
            lines.append(line)
        for i, l in enumerate(lines):
            text(cr, l + ("…" if i == 3 else ""), x, y + i * 26 * s, 17 * s, (0.72, 0.74, 0.78, p))
        # buttons
        by = 590 * s; bx = x
        self.rects = []
        for i, (key, label, _desc, icon) in enumerate(self.items):
            primary = key == "PLAY"
            tw = text(cr, label, 0, -999, 20 * s, (0, 0, 0, 0)) + (88 if primary else 64) * s
            bw, bh = max(tw, (200 if primary else 150) * s), 62 * s
            foc = i == self.sel
            self.rects.append((bx, by, bw, bh))
            if foc:
                rounded(cr, bx - 5 * s, by - 5 * s, bw + 10 * s, bh + 10 * s, (bh + 10 * s) / 2)
                neon(cr, 0.95 * p); cr.set_line_width(3.5 * s); cr.stroke()
            rounded(cr, bx, by, bw, bh, bh / 2)
            if primary:
                cr.set_source_rgba(0.97, 0.97, 0.98, p)
            else:
                cr.set_source_rgba(0.17, 0.17, 0.16, 0.92 * p) if not foc else cr.set_source_rgba(0.27, 0.27, 0.26, 0.95 * p)
            cr.fill()
            ink = (0.08, 0.08, 0.10) if primary else (1, 1, 1)
            cr.push_group(); cr.save(); cr.translate(bx + 34 * s, by + bh / 2); glyph(cr, icon, s * 0.9, ink); cr.restore()
            cr.pop_group_to_source(); cr.paint_with_alpha(p)
            text(cr, label, bx + 58 * s, by + bh / 2 + 7 * s, 20 * s, (*ink, p), bold=True)
            bx += bw + 22 * s
        # last played: the frame you left off at, play time
        rx, rw = W - 500 * s, 420 * s
        img = h.get("resume_pb") or h.get("cover_pb")
        if img:
            rh = rw * img.get_height() / img.get_width() if h.get("resume_pb") else 300 * s
            rh = min(rh, 260 * s)
            ry = 300 * s
            text(cr, h.get("resume_title", "Last played"), rx, ry - 18 * s, 15 * s, (0.80, 0.82, 0.86, p), bold=True)
            cr.save(); rounded(cr, rx, ry, rw, rh, 16 * s); cr.clip()
            sc = max(rw / img.get_width(), rh / img.get_height())
            cr.translate(rx + (rw - img.get_width() * sc) / 2, ry + (rh - img.get_height() * sc) / 2); cr.scale(sc, sc)
            Gdk.cairo_set_source_pixbuf(cr, img, 0, 0); cr.paint_with_alpha(p); cr.restore()
            rounded(cr, rx, ry, rw, rh, 16 * s); cr.set_source_rgba(1, 1, 1, 0.14 * p); cr.set_line_width(1.5 * s); cr.stroke()
            if h.get("played"):
                text(cr, h["played"], rx, ry + rh + 30 * s, 16 * s, (0.80, 0.82, 0.86, p))
        # loading behind: small spinner until the game is ready (then "Ready")
        ready = os.path.exists("/tmp/game_hub_ready")
        lx, ly = W - 80 * s, H - 70 * s
        if ready:
            text(cr, "Ready", lx + 20 * s, ly + 6 * s, 16 * s, (*CYAN, p), bold=True, align="right")
        else:
            a = now * 5
            cr.set_line_width(3 * s); cr.set_line_cap(cairo.LINE_CAP_ROUND); neon(cr, p)
            cr.new_path(); cr.arc(lx, ly, 12 * s, a, a + 4.2); cr.stroke()
            text(cr, "Getting ready", lx - 24 * s, ly + 6 * s, 15 * s, (0.75, 0.77, 0.81, p), align="right")
        self.hint_rects = []

    def draw_saving(self, cr, W, H, s, now):
        t = now - self.save_t
        k = ease_out((t - 0.12) / 0.25)
        if k <= 0:
            return
        cx, cy = W / 2, H - 200 * s
        if self.saved_t is None:
            ang = t * 5.5
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.new_path(); cr.arc(cx, cy, 30 * s, 0, 2 * math.pi); cr.set_source_rgba(1, 1, 1, 0.12 * k)
            cr.set_line_width(5 * s); cr.stroke()
            cr.new_path(); cr.arc(cx, cy, 30 * s, ang, ang + 1.9); neon(cr, k); cr.stroke()
            label = "Saving your game" + "." * (1 + int(t * 2) % 3)
            e_w = text(cr, "Saving your game...", 0, -999, 24 * s, (0, 0, 0, 0))
            text(cr, label, cx - e_w / 2, cy + 66 * s, 24 * s, (1, 1, 1, k))
            frac = 1 - math.exp(-t / 3.0)
        else:
            k2 = ease_out((now - self.saved_t) / 0.2)
            r = 30 * s * (0.85 + 0.15 * k2)
            cr.new_path(); cr.arc(cx, cy, r, 0, 2 * math.pi); neon(cr, k2); cr.fill()
            cr.set_source_rgba(1, 1, 1, k2); cr.set_line_width(5 * s); cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.move_to(cx - 13 * s, cy); cr.line_to(cx - 3 * s, cy + 10 * s); cr.line_to(cx + 14 * s, cy - 12 * s); cr.stroke()
            text(cr, "Saved", cx, cy + 66 * s, 24 * s, (1, 1, 1, k2), align="center")
            frac = 1.0
        bw = 320 * s; bx = cx - bw / 2; by = cy + 88 * s
        rounded(cr, bx, by, bw, 5 * s, 2.5 * s); cr.set_source_rgba(1, 1, 1, 0.14 * k); cr.fill()
        if frac > 0.01:
            rounded(cr, bx, by, bw * frac, 5 * s, 2.5 * s); neon(cr, k); cr.fill()

    def draw_hints(self, cr, W, H, s, p):
        x = W - 56 * s; y = H - 30 * s
        self.hint_rects = []
        hints = (("B", "Back"), ("A", "Load")) if self.picker else (("B", self.items[0][1]), ("A", "OK"))
        for letter, txt in hints:
            right = x
            x -= text(cr, txt, x, y, 17 * s, (0, 0, 0, 0), align="right")
            text(cr, txt, x, y, 17 * s, (1, 1, 1, 0.92 * p))
            x -= 32 * s
            cr.new_path(); cr.arc(x + 12 * s, y - 6 * s, 12.5 * s, 0, 2 * math.pi)
            cr.set_source_rgba(1, 1, 1, p); cr.fill()
            text(cr, letter, x + 12 * s, y - 0.5 * s, 15 * s, (0.12, 0.12, 0.14, p), bold=True, align="center")
            self.hint_rects.append((letter, x - 6 * s, y - 28 * s, right - x + 12 * s, 40 * s))
            x -= 30 * s

    # ---- input ----
    def move(self, d):
        if self.qs_focus:
            self.set_quick(self.qs_focus, (self.qs.get(self.qs_focus) or 0) + 0.05 * d); return
        if self.picker:
            n = min(6, len(self.states))
            new = max(0, min(n - 1, self.psel + d))
            if new != self.psel:
                self.psel = new; sound("cc_move")
            return
        if not self.items:
            return
        new = (self.sel + d) % len(self.items)              # wraps around at either end (2026-10-10, the owner)
        if new != self.sel and self.closing is None and self.save_t is None:
            self.sel, self.sel_t = new, time.monotonic(); sound("cc_move")
            if self._items_orig:                            # moving away cancels a pending "Quit?"
                self.items, self._items_orig, self.quit_arm = self._items_orig, None, 0

    # ---- hold to confirm (2026-10-10 party: kids mashing A picked "Quit without saving" twice): the destructive choices
    # need A held for HOLD_S (checked in tick); a tap only shows "Hold A to confirm"
    def a_down(self):
        if self.choice is not None:
            return
        if self.picker or self.qs_focus or self.hub or not self.items or self.closing is not None \
                or self.save_t is not None:
            return self.choose()
        if self.items[self.sel][0] in self.HOLD_KEYS:
            self.hold_t, self.hold_sel = time.monotonic(), self.sel
            self.redraw()
        else:
            self.choose()

    def a_up(self):
        if self.hold_t is not None:
            if time.monotonic() - self.hold_t < self.HOLD_S:
                self.hold_hint_t = time.monotonic()          # let go too early: just the hint
            self.hold_t = None
            self.redraw()

    def choose(self, i=None):
        if self.choice is not None:
            return
        if self.hub and i == 0 and not self.picker:
            self.close_as("BACK"); return
        if self.picker:
            if i == 0:                             # B / "Back": out of the picker
                self.picker = False; sound("cc_move"); return
            self.close_as(f"LOAD:{self.states[self.psel]['slot']}"); return
        if self.qs_focus and i is None:
            self.qs_focus = None; self.redraw(); return
        pick = self.items[self.sel if i is None else i][0]
        if pick == "QUIT" and i is None and time.monotonic() - self.quit_arm > 4:
            # a stray A must never throw a game away: first press asks, second press (within 4 s) quits
            self._items_orig = self._items_orig or list(self.items)
            self.items = [(k, "Quit? Press A again", "Progress since your last save is lost", g) if k == "QUIT"
                          else (k, l, sb, g) for k, l, sb, g in self._items_orig]
            self.quit_arm = time.monotonic(); sound("cc_move"); self.redraw(); return
        if pick == "LOADSTATE" and self.states:
            self.picker, self.psel = True, 0; sound("cc_move"); return
        if pick == "HOME" and self.cc.overlay and self.saving_ok:
            self.start_saving()
        else:
            self.close_as(pick)

    def tap(self, tx, ty):
        """A finger tap: a tile chooses it (it lights up first, like a press); the hints act like their buttons."""
        if not self.is_open or self.closing is not None or self.save_t is not None:
            return False
        for key, x, y, w, h in self.qs_rects:
            if x <= tx <= x + w and y - 8 <= ty <= y + h + 8:
                bx, bw = x + 56 * (h / 50), w - 120 * (h / 50)
                self.qs_focus = key
                self.set_quick(key, (tx - bx) / bw if tx >= bx else (self.qs.get(key) or 0)); return False
        if self.picker:
            for i, (x, y, w, h) in enumerate(self.prects):
                if x <= tx <= x + w and y <= ty <= y + h:
                    self.psel = i; GLib.timeout_add(140, lambda: (self.choose(), False)[1]); return False
            for letter, x, y, w, h in self.hint_rects:
                if x <= tx <= x + w and y <= ty <= y + h:
                    self.choose(0 if letter == "B" else None); return False
            return False
        for i, (x, y, w, h) in enumerate(self.rects):
            if x - 8 <= tx <= x + w + 8 and y - 8 <= ty <= y + h + 8:
                if i != self.sel:
                    self.sel, self.sel_t = i, time.monotonic(); sound("cc_move")
                GLib.timeout_add(140, lambda: (self.choose(i), False)[1])
                return False
        for letter, x, y, w, h in self.hint_rects:
            if x <= tx <= x + w and y <= ty <= y + h:
                self.choose(0 if letter == "B" else None)
                return False
        return False


class Boot(Layer):
    """Prism startup cover (2026-10-09): the home wave + PRISM wordmark + thin spectrum line, drawn above Prism's own
    black loading window until its startup event script touches /tmp/prism_ready, then faded away. Also the wake
    hand-off (wake=True) and the game-quit cover (cover=True, held until "uncover")."""

    def __init__(self, cc):
        super().__init__(cc)
        self.b, self.bg, self.logo, self.loaded = None, None, None, False

    def active(self):
        return self.b is not None

    def show(self, wake=False, cover=False, dock=None):
        if self.cc.card.is_open:
            return
        if not self.loaded:
            self.loaded = True
            try:
                self.bg = GdkPixbuf.Pixbuf.new_from_file(f"{prism_config.THEME_DIR}/switch/ambient_still.png")
                self.logo = GdkPixbuf.Pixbuf.new_from_file(f"{prism_config.PRISM}/prism_wordmark.png")
            except Exception:
                self.bg = self.logo = None
        fresh = self.b is None
        self.b = {"t0": time.monotonic(), "wall0": time.time(), "ready_at": None, "closing": None}
        if wake:      # wake hand-off: the frame Steam's wake video ends on, held, then faded to reveal the home tiles
            self.b.update(wake=True, ready_at=time.monotonic())
        if cover:     # game quit (L4+R4): the home wave fades in over the game, held until "uncover", then fades out
            self.b.update(wake=True, cover=True, ready_at=None)
        if dock:      # docked (2026-10-14): the moment the TV takes over, instead of the black flicker
            self.b.update(dock=dock, ready_at=time.monotonic())
            sound("launch")
        self.cc.layer_started() if fresh else self.cc.refresh()

    def uncover(self):
        if self.b and self.b.get("cover"):
            self.b["ready_at"] = time.monotonic()
            self.cc.refresh()

    def tick(self, now):
        b = self.b
        if self.cc.card.is_open:          # the card took over the screen: the cover is done
            self.b = None
            return
        try:
            if b["ready_at"] is None and not b.get("cover") and os.path.getmtime("/tmp/prism_ready") > b["wall0"]:
                b["ready_at"] = now
        except OSError:
            pass
        hold, fade = (0.0, WAKE_FADE) if b.get("cover") else (WAKE_HOLD, WAKE_FADE) if b.get("wake") else \
            (1.5, 0.45) if b.get("dock") else (0.6, 0.5)
        if b["closing"] is None and ((b["ready_at"] is not None and now - b["ready_at"] > hold)
                                     or now - b["t0"] > (15 if b.get("cover") else 25)):
            b["closing"] = now
        if b["closing"] is not None and now - b["closing"] > fade + 0.05:
            self.b = None

    def draw(self, cr, W, H):
        b = self.b; now = time.monotonic(); s = H / 800
        fade = WAKE_FADE if b.get("wake") else 0.5
        k = min(1.0, (now - b["closing"]) / fade) if b["closing"] is not None else 0.0
        a = 1 - (smooth(k) if b.get("wake") else ease_out(k))      # wake: gentle ease in-out
        if b.get("cover"):
            a *= smooth(min(1.0, (now - b["t0"]) / COVER_IN))
        cr.push_group()
        if b.get("dock"):
            self.draw_dock(cr, W, H, s, now - b["t0"])
            cr.pop_group_to_source(); cr.paint_with_alpha(a)
            return
        cr.set_source_rgb(0.11, 0.11, 0.10); cr.paint()
        if self.bg:
            cr.save(); cr.scale(W / self.bg.get_width(), H / self.bg.get_height())
            Gdk.cairo_set_source_pixbuf(cr, self.bg, 0, 0); cr.paint(); cr.restore()
        if self.logo and not b.get("wake"):
            lw, lh = self.logo.get_width() * s, self.logo.get_height() * s
            cr.save(); cr.translate(W / 2 - lw / 2, 300 * s); cr.scale(s, s)
            Gdk.cairo_set_source_pixbuf(cr, self.logo, 0, 0); cr.paint(); cr.restore()
            bar = lw * 0.62; by = 300 * s + lh + 30 * s
            frac = 1.0 if b["ready_at"] is not None else 1 - math.exp(-(now - b["t0"]) / 2.5)
            cr.set_source_rgba(1, 1, 1, 0.12); cr.rectangle(W / 2 - bar / 2, by, bar, 2.5 * s); cr.fill()
            cr.rectangle(W / 2 - bar / 2, by, bar * frac, 2.5 * s); neon(cr); cr.fill()
        cr.pop_group_to_source(); cr.paint_with_alpha(a)


    def draw_dock(self, cr, W, H, s, t):
        cr.set_source_rgb(0.043, 0.043, 0.047); cr.paint()
        lw = lh = 0
        if self.logo:
            lw, lh = self.logo.get_width() * s, self.logo.get_height() * s
            cr.save(); cr.translate(W / 2 - lw / 2, H / 2 - lh / 2 - 30 * s); cr.scale(s, s)
            Gdk.cairo_set_source_pixbuf(cr, self.logo, 0, 0); cr.paint_with_alpha(smooth(min(1.0, t / 0.35)))
            cr.restore()
        bar = max(lw * 0.62, 300 * s); by = H / 2 + lh / 2 - 6 * s
        k = smooth(min(1.0, max(0.0, t - 0.15) / 0.9))          # the line sweeps in left to right
        rounded(cr, W / 2 - bar / 2, by, max(1.0, bar * k), 3.5 * s, 1.75 * s); neon(cr, 1.0, (W / 2 - bar / 2, by, W / 2 + bar / 2, by + 4 * s)); cr.fill()
        label = self.b.get("dock") or ""
        if label:
            cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(19 * s)
            spaced = " ".join(label)                               # letter-spaced, like the mockup
            e = cr.text_extents(spaced)
            cr.move_to(W / 2 - e.x_advance / 2, by + 52 * s)
            cr.set_source_rgba(0.64, 0.64, 0.67, smooth(min(1.0, max(0.0, t - 0.4) / 0.4))); cr.show_text(spaced)


class Saver(Layer):
    """TV screensaver (2026-10-09): docked and idle -> the moving home wave, dimmed, with nothing static on the TV.
    deck_screensaver sends "saver" / "unsaver"; the card and the power-off animation end it, the boot cover hides it."""
    frame_ms = 40                  # 25 fps is plenty for a slow wave

    def __init__(self, cc):
        super().__init__(cc)
        self.sv = None

    def active(self):
        return self.sv is not None

    def ready(self):
        return self.sv["video"].frame is not None

    def show(self):
        if self.sv is not None or self.cc.card.is_open:
            return
        self.sv = {"t0": time.monotonic(), "closing": None,
                   "video": Video(SAVER_VIDEO, "sync=true max-buffers=2 drop=true", loop=True)}
        self.sv["video"].state("PLAYING")
        self.cc.layer_started()

    def hide(self):
        if self.sv is not None and self.sv["closing"] is None:
            self.sv["closing"] = time.monotonic()

    def tick(self, now):
        sv = self.sv
        if self.cc.card.is_open or self.cc.off.active():
            sv["closing"] = sv["closing"] or now
        if sv["closing"] is not None and now - sv["closing"] > 0.5:
            sv["video"].stop()
            self.sv = None

    def draw(self, cr, W, H):
        sv = self.sv; now = time.monotonic()
        a = smooth(min(1.0, (now - sv["t0"]) / 1.5))
        if sv["closing"] is not None:
            a *= max(0.0, 1 - (now - sv["closing"]) / 0.5)
        cr.set_source_rgba(0, 0, 0, a); cr.paint()
        sv["video"].paint(cr, W, H, a * SAVER_DIM, cover=True)


class Notice(Layer):
    """Disconnect notice: the game is paused (controller_bar) until the pad is back or someone presses A."""
    frame_ms = 30

    def __init__(self, cc):
        super().__init__(cc)
        self.nt = None

    def active(self):
        return self.nt is not None

    def show(self, n, title, sub):
        self.nt = {"n": n, "title": title, "sub": sub, "t": time.monotonic(), "closing": None}
        self.cc.refresh()

    def hide(self):
        if self.nt and self.nt["closing"] is None:
            self.nt["closing"] = time.monotonic()

    def tick(self, now):
        if self.nt["closing"] is not None and now - self.nt["closing"] > 0.35:
            self.nt = None

    def draw(self, cr, W, H):
        nt = self.nt; now = time.monotonic(); s = H / 800
        a = smooth(min(1.0, (now - nt["t"]) / 0.3))
        if nt["closing"] is not None:
            a *= max(0.0, 1 - (now - nt["closing"]) / 0.35)
        cr.set_operator(cairo.OPERATOR_SOURCE); cr.set_source_rgba(0, 0, 0, 0.62 * a); cr.paint(); cr.set_operator(cairo.OPERATOR_OVER)
        # text wraps inside the panel (2026-10-10: the hint ran off the right edge); the panel grows to fit
        w = 640 * s; tx = 125 * s; tw = w - tx - 34 * s
        cr.select_font_face("Noto Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        tfs = 30 * s; cr.set_font_size(tfs)
        while tfs > 20 * s and cr.text_extents(nt["title"]).x_advance > tw:
            tfs -= 1 * s; cr.set_font_size(tfs)
        cr.select_font_face("Noto Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(17 * s)
        lines, cur = [], ""
        for word in (nt["sub"] or "").split():
            t = (cur + " " + word).strip()
            if cur and cr.text_extents(t).x_advance > tw:
                lines.append(cur); cur = word
            else:
                cur = t
        if cur:
            lines.append(cur)
        lh = 24 * s
        h = max(190 * s, 112 * s + lh * len(lines)); x, y = W / 2 - w / 2, H / 2 - h / 2; r = 22 * s
        block = 22 * s + 34 * s + lh * max(0, len(lines) - 1) + 5 * s     # title cap .. last hint line's descent
        ty = y + h / 2 - block / 2 + 22 * s                               # title baseline; the hint lines follow
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0); cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi); cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2); cr.close_path()
        cr.set_source_rgba(40 / 255, 40 / 255, 43 / 255, 0.92 * a); cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.25 * a); cr.set_line_width(1.4 * s); cr.stroke()
        col = pad_rgb(max(1, nt["n"]))
        pulse = 0.6 + 0.4 * abs(math.sin(now * 2.4))
        cr.new_path(); cr.arc(x + 70 * s, y + h / 2, 30 * s, 0, 2 * math.pi); cr.set_source_rgba(*col, a * pulse); cr.fill()
        cr.select_font_face("Noto Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); cr.set_font_size(30 * s)
        e = cr.text_extents(str(nt["n"])); cr.move_to(x + 70 * s - e.x_advance / 2, y + h / 2 + e.height / 2)
        cr.set_source_rgba(0.08, 0.08, 0.08, a); cr.show_text(str(nt["n"]))
        cr.set_source_rgba(1, 1, 1, a); cr.set_font_size(tfs)
        cr.move_to(x + tx, ty); cr.show_text(nt["title"])
        cr.select_font_face("Noto Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(17 * s)
        cr.set_source_rgba(0.75, 0.75, 0.78, a)
        for i, line in enumerate(lines):
            cr.move_to(x + tx, ty + 34 * s + i * lh); cr.show_text(line)


class PadToasts(Layer):
    """Controller joined / left (2026-10-10, the owner: Steam's small bottom-right pop-up -> bigger, in Prism's style).
    Steam's own controller pop-ups are switched off (deck_curate.steam_pad_toasts_off); controller_bar sends these."""
    exclusive = False
    SHOW_S = 3.6

    def __init__(self, cc):
        super().__init__(cc)
        self.lst = []

    def active(self):
        return bool(self.lst)

    def show(self, it, joined):
        now = time.monotonic()
        fresh = not self.lst
        lst = [t for t in self.lst if t["until"] + 0.4 > now and t["it"].get("n") != it.get("n")]
        lst.append({"it": it, "joined": joined, "t0": now, "until": now + self.SHOW_S})
        self.lst = lst[-4:]
        self.cc.layer_started() if fresh else self.cc.refresh()

    def tick(self, now):
        self.lst = [t for t in self.lst if t["until"] + 0.4 > now]
        if self.cc.card.is_open:            # the card covers the screen: drop them
            self.lst = []

    def draw(self, cr, W, H):
        s = H / 800; now = time.monotonic()
        cw, ch, gap = 540 * s, 124 * s, 14 * s
        y = H - 74 * s                                             # above the help bar
        for t in reversed(self.lst):                               # newest at the bottom
            k = ease_out((now - t["t0"]) / 0.32)
            if now > t["until"]:
                k *= 1 - ease_out((now - t["until"]) / 0.35)
            it, joined = t["it"], t["joined"]
            x = W - cw - 32 * s + (1 - k) * 80 * s; yy = y - ch
            cr.push_group()
            # glass: deep graphite, a soft top sheen, hairline border
            rounded(cr, x, yy, cw, ch, 24 * s)
            g = cairo.LinearGradient(0, yy, 0, yy + ch)
            g.add_color_stop_rgba(0, 0.17, 0.17, 0.19, 0.93); g.add_color_stop_rgba(1, 0.09, 0.09, 0.10, 0.93)
            cr.set_source(g); cr.fill()
            rounded(cr, x, yy, cw, ch, 24 * s); cr.set_source_rgba(1, 1, 1, 0.16); cr.set_line_width(1.4 * s); cr.stroke()
            # neon line along the bottom, the menu highlight's rainbow (grey when a controller left)
            cr.save(); rounded(cr, x, yy, cw, ch, 24 * s); cr.clip()
            lg = cairo.LinearGradient(x, 0, x + cw, 0)
            for i, c in enumerate(NEON_STOPS):
                if joined:
                    lg.add_color_stop_rgba(i / (len(NEON_STOPS) - 1), c[0] / 255, c[1] / 255, c[2] / 255, 0.95)
                else:
                    lg.add_color_stop_rgba(i / (len(NEON_STOPS) - 1), 0.5, 0.5, 0.52, 0.6)
            cr.rectangle(x, yy + ch - 4 * s, cw, 4 * s); cr.set_source(lg); cr.fill(); cr.restore()
            # player disc with a glow
            col = pad_rgb(max(1, it.get("n", 1)))
            dx, dy, dr = x + 66 * s, yy + ch / 2 - 1 * s, 36 * s
            if joined:
                rg = cairo.RadialGradient(dx, dy, dr * 0.6, dx, dy, dr * 1.7)
                rg.add_color_stop_rgba(0, *col, 0.45); rg.add_color_stop_rgba(1, *col, 0)
                cr.arc(dx, dy, dr * 1.7, 0, 2 * math.pi); cr.set_source(rg); cr.fill()
                cr.arc(dx, dy, dr, 0, 2 * math.pi); cr.set_source_rgba(*col, 1); cr.fill()
                num_rgb = (0.08, 0.08, 0.08, 1)
            else:
                cr.arc(dx, dy, dr - 1.5 * s, 0, 2 * math.pi); cr.set_source_rgba(*col, 0.85); cr.set_line_width(3 * s); cr.stroke()
                num_rgb = (*col, 0.9)
            text(cr, str(it.get("n", "?")), dx, dy + 13 * s, 36 * s, num_rgb, bold=True, align="center")
            # words
            tx = x + 124 * s; mw = cw - 124 * s - 26 * s
            title = f"Player {it.get('n', '?')} {'connected' if joined else 'disconnected'}"
            fs = 27 * s
            while fs > 18 * s and text(cr, title, 0, -999, fs, (0, 0, 0, 0), bold=True) > mw:
                fs -= 1 * s
            text(cr, title, tx, yy + 56 * s, fs, (1, 1, 1, 1 if joined else 0.75), bold=True)
            sub = it.get("name") or "Controller"
            sw = text(cr, sub, tx, yy + 90 * s, 19 * s, (0.78, 0.78, 0.82, 1))
            if joined and it.get("bat") is not None:
                draw_battery(cr, tx + sw + (26 if it.get("chg") else 16) * s, yy + 77 * s, 30 * s, 14 * s, it, 1, s)
            cr.pop_group_to_source(); cr.paint_with_alpha(k)
            y -= (ch + gap) * max(k, 0.0001)


class Toast(Layer):
    """Now-playing / notification toast (mini player, 2026-10-08): a small card top right for ~3 s, when the song
    changes or R5 is pressed. Never while the card is up."""

    def __init__(self, cc):
        super().__init__(cc)
        self.t = None

    @property
    def exclusive(self):
        # the toast takes the chips' corner and hides them, except while controller pop-ups are up too
        return not self.cc.padtoasts.active()

    def active(self):
        return self.t is not None

    def clear(self):
        self.t = None

    def show(self, title, sub, cover):
        if self.cc.card.is_open:
            return
        pb = None
        if cover and os.path.exists(cover):
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(cover, 96, 96, True)
            except Exception:
                pb = None
        fresh = self.t is None
        self.t = {"title": title, "sub": sub, "pb": pb, "music": bool(cover),
                  "t0": time.monotonic() if fresh else self.t["t0"],
                  "until": time.monotonic() + 3.2}
        self.cc.layer_started() if fresh else self.cc.refresh()

    def tick(self, now):
        if now > self.t["until"] + 0.35:
            self.t = None

    @staticmethod
    def icon(title):
        """Notification toasts without cover art: an icon from what the message says."""
        t = title.lower()
        if any(w in t for w in ("couldn't", "failed", "error")):
            return "close", (1, 0.42, 0.42)
        if "watch later" in t:
            return "clock", NEON
        if "sleep" in t:
            return "moon", NEON
        if "sort" in t:
            return "sort", NEON
        if any(w in t for w in ("not in", "not available", "nothing")):
            return "info", NEON
        return "check", NEON

    def draw(self, cr, W, H):
        t = self.t; now = time.monotonic(); s = H / 800
        k = ease_out((now - t["t0"]) / 0.28) * (1 - ease_out((now - t["until"]) / 0.35) if now > t["until"] else 1)
        cw, ch = 470 * s, 118 * s
        x, y = W - cw - 28 * s + (1 - k) * 60 * s, 26 * s
        cr.push_group()
        rounded(cr, x, y, cw, ch, 20 * s); cr.set_source_rgba(0.10, 0.10, 0.095, 0.94); cr.fill()
        rounded(cr, x, y, cw, ch, 20 * s); cr.set_source_rgba(1, 1, 1, 0.08); cr.set_line_width(1.5 * s); cr.stroke()
        ax, ay, asz = x + 14 * s, y + 11 * s, 96 * s
        if t["pb"]:
            cr.save(); rounded(cr, ax, ay, asz, asz, 12 * s); cr.clip()
            sc = asz / max(t["pb"].get_width(), t["pb"].get_height())
            cr.translate(ax, ay); cr.scale(sc, sc); Gdk.cairo_set_source_pixbuf(cr, t["pb"], 0, 0); cr.paint(); cr.restore()
        else:                                   # notification: icon on a graphite square, no cover
            rounded(cr, ax, ay, asz, asz, 18 * s); cr.set_source_rgba(1, 1, 1, 0.07); cr.fill()
            icon, rgb = self.icon(t["title"])
            cr.save(); cr.translate(ax + asz / 2, ay + asz / 2); cr.scale(1.7, 1.7); glyph(cr, icon, s, rgb); cr.restore()
        tx = ax + asz + 16 * s; mw = cw - (tx - x) - 16 * s
        title, sub = t["title"], t["sub"]
        if t.get("music") or t["pb"]:
            text(cr, "NOW PLAYING", tx, y + 34 * s, 13 * s, (0xF0 / 255, 0x43 / 255, 0x6F / 255), bold=True)
            ty, sy = 64, 90
        else:
            ty, sy = 52, 82                     # two lines, centred on the card
        while title and text(cr, title, 0, -999, 21 * s, (0, 0, 0, 0)) > mw:
            title = title[:-2] + "…"
        text(cr, title, tx, y + ty * s, 21 * s, (1, 1, 1, 1), bold=True)
        while sub and text(cr, sub, 0, -999, 16 * s, (0, 0, 0, 0)) > mw:
            sub = sub[:-2] + "…"
        text(cr, sub, tx, y + sy * s, 16 * s, (0.75, 0.75, 0.78, 1))
        cr.pop_group_to_source(); cr.paint_with_alpha(k)


class Chips(Layer):
    """Controller bar (2026-10-09): connected controllers, their player numbers and batteries. controller_bar.py
    (supervised) sends {"cmd": "pads", "show", "items"} every 2 s while it's shown; the card draws the same chips."""
    exclusive = False

    def __init__(self, cc):
        super().__init__(cc)
        self.st, self.acts = {}, {}

    @property
    def items(self):
        return self.st.get("items") or []

    def active(self):
        return bool(self.st.get("show") and self.st.get("items"))

    def set(self, show, items, mode="full"):
        was = self.active()
        self.st = {"show": show, "items": items or [], "mode": mode, "t": self.st.get("t") if was else time.monotonic()}
        self.cc.refresh()

    def act(self, n):
        """An input from player n: their chip lights up"""
        self.acts[n] = time.monotonic()
        self.cc.refresh()

    def glow(self, n):
        """0..1 glow for player n's chip after an input (holds briefly, then fades)"""
        t = self.acts.get(n)
        if t is None:
            return 0.0
        age = time.monotonic() - t
        return 1.0 if age < 0.25 else max(0.0, 1 - (age - 0.25) / 0.6)

    def glowing(self):
        return any(time.monotonic() - t < 0.9 for t in self.acts.values())

    def animating(self):
        # the charging sweep and the input glow move; the 0.4 s fade-in is drawn on the controller bar's next update
        return self.glowing() or (self.active() and any(it.get("chg") for it in self.items))

    @property
    def frame_ms(self):
        return 30 if self.glowing() else 60

    def draw(self, cr, W, H):
        s = H / 800
        a = smooth(min(1.0, (time.monotonic() - (self.st.get("t") or 0)) / 0.4))
        draw_chips(cr, s, self.items, W - PADS_RIGHT * s, PADS_CY * s, a, self.glow)


class CC(Gtk.Window):
    """The overlay window and its compositor."""

    def __init__(self, overlay=False):
        super().__init__(title="Control Center")
        self.overlay = overlay
        self.shown = self.drawn = self.unmapping = self.in_tick = False
        self.timer = self.timer_ms = None
        self.set_decorated(False); self.fullscreen()
        vis = self.get_screen().get_rgba_visual()      # see-through: only the layers' own pixels cover the game
        if vis:
            self.set_visual(vis); self.set_app_paintable(True)
        area = Gtk.DrawingArea(); area.connect("draw", self.draw)
        area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.TOUCH_MASK)
        area.connect("button-press-event", self.click); self.add(area); self.area = area
        self.connect("realize", self.tag)
        self.off, self.card, self.boot, self.saver = Off(self), Card(self), Boot(self), Saver(self)
        self.notice, self.padtoasts, self.toast, self.chips = Notice(self), PadToasts(self), Toast(self), Chips(self)
        self.layers = [self.off, self.card, self.boot, self.saver, self.notice, self.padtoasts, self.toast, self.chips]

    def opacity(self, full):
        if self.get_window():
            subprocess.run(["xprop", "-id", str(self.get_window().get_xid()), "-f", "_NET_WM_WINDOW_OPACITY", "32c",
                            "-set", "_NET_WM_WINDOW_OPACITY", "4294967295" if full else "0"])

    def tag(self, *_):
        xid = str(self.get_window().get_xid())
        if self.overlay:     # gamescope overlay layer (above Steam's own UI); starts invisible
            for prop, val in (("STEAM_OVERLAY", "1"), ("_NET_WM_WINDOW_OPACITY", "0")):
                subprocess.run(["xprop", "-id", xid, "-f", prop, "32c", "-set", prop, val])
        elif os.environ.get("SteamAppId"):
            subprocess.Popen(["xprop", "-id", xid, "-f", "STEAM_GAME", "32c", "-set", "STEAM_GAME",
                              os.environ["SteamAppId"]])

    def click(self, _, ev):
        self.card.tap(ev.x, ev.y)

    def draw(self, w, cr):
        W, H = w.get_allocated_width(), w.get_allocated_height()
        self.drawn = True
        cr.set_operator(cairo.OPERATOR_SOURCE); cr.set_source_rgba(0, 0, 0, 0); cr.paint()   # nothing up: transparent
        cr.set_operator(cairo.OPERATOR_OVER)
        stack = []
        for layer in self.layers:
            if layer.active():
                stack.append(layer)
                if layer.exclusive:
                    break
        for layer in reversed(stack):
            layer.draw(cr, W, H)
        return False

    def layer_started(self):
        """A layer comes up: map the window and raise it (it becomes visible once a frame has been drawn)."""
        if not self.get_visible():
            self.drawn = False; self.show_all()
        self.present()
        self.refresh()

    def refresh(self):
        """Something changed: redraw, settle visibility, keep the animation timer at the pace the layers want."""
        self.area.queue_draw()
        self._visibility()
        if not self.in_tick:
            ms = self._frame_ms()
            if ms != self.timer_ms:
                if self.timer is not None:
                    GLib.source_remove(self.timer)
                self.timer, self.timer_ms = (GLib.timeout_add(ms, self._tick) if ms else None), ms

    def _visibility(self):
        """gamescope keeps compositing an overlay's last buffer after it's unmapped (the 'frozen game' frame), so the
        window is shown and hidden with _NET_WM_WINDOW_OPACITY like Steam's own overlay: made visible only after a
        frame has been drawn, transparent first when nothing is left, unmapped a few frames later."""
        live = [layer for layer in self.layers if layer.active()]
        if live:
            if not self.get_visible():
                self.drawn = False; self.show_all()
            if self.overlay and not self.shown and self.drawn and any(layer.ready() for layer in live):
                self.shown = True; self.opacity(True)
        elif self.get_visible():
            if not self.overlay:
                self.hide()
            else:
                if self.shown:
                    self.shown = self.drawn = False; self.opacity(False)
                if not self.unmapping:
                    self.unmapping = True
                    GLib.timeout_add(120, self._unmap)

    def _unmap(self):
        self.unmapping = False
        if not any(layer.active() for layer in self.layers):
            self.hide()
        return False

    def _frame_ms(self):
        ms = [layer.frame_ms for layer in self.layers if layer.animating()]
        if self.overlay and not self.shown and any(layer.active() and layer.ready() for layer in self.layers):
            ms.append(16)                    # becoming visible: tick until a frame is drawn
        return min(ms) if ms else None

    def _tick(self):
        now = time.monotonic()
        self.in_tick = True
        try:
            for layer in self.layers:
                if layer.active():
                    try:
                        layer.tick(now)
                    except Exception:
                        import traceback
                        traceback.print_exc()
        finally:
            self.in_tick = False
        self.area.queue_draw()
        self._visibility()
        ms = self._frame_ms()
        if ms == self.timer_ms:
            return True
        self.timer, self.timer_ms = (GLib.timeout_add(ms, self._tick) if ms else None), ms
        return False


def pad_thread(cc, devices):
    for d in devices:
        try:
            d.grab()
        except OSError:
            pass
    axes = {}                       # (device, axis) -> -1 / 0 / 1: one move per push, per controller
    last_act = {}                   # action -> time: one press seen through two devices counts once

    def ilog(msg):
        try:
            with open("/tmp/deck_cc_input.log", "a") as f:
                f.write(time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d} {msg}\n")
        except OSError:
            pass

    def act(name, fn, *a):
        t = time.monotonic()
        if t - last_act.get(name, 0) < 0.15:
            ilog(f"  dup {name} ({cur_dev[0]})")
            return
        last_act[name] = t
        ilog(f"ACT {name} ({cur_dev[0]})")
        ui_call(fn, *a)
    cur_dev = [""]
    ilog("card open, reading: " + ", ".join(f"{d.path.rsplit('/', 1)[-1]}={d.name}" for d in devices))
    rescan = time.monotonic()
    while cc.is_open and cc.choice is None:
        if time.monotonic() - rescan > 1.0:
            # controllers can be recreated while the card is up (a pad reconnecting makes Steam rebuild its virtual
            # pad): drop dead ones, pick up new ones, so a press can't go nowhere (2026-10-09, docked MK8D)
            rescan = time.monotonic()
            live = []
            for d in devices:
                if os.path.exists(d.path) and os.path.realpath(f"/proc/self/fd/{d.fd}") == d.path:
                    live.append(d)
                else:
                    try: d.close()
                    except Exception: pass
            have = {d.path for d in live}
            for d in pads():
                if d.path in have:
                    d.close(); continue
                try:
                    d.grab()
                except OSError:
                    pass
                live.append(d)
            devices[:] = live
        try:
            r, _, _ = select.select(devices, [], [], 0.1)
        except (OSError, ValueError):
            rescan = 0; continue
        for d in r:
            cur_dev[0] = d.name
            try:
                for ev in d.read():
                    if ev.type == E.EV_KEY or (ev.type == E.EV_ABS and ev.code in (E.ABS_HAT0X, E.ABS_HAT0Y)):
                        ilog(f"ev {d.name}: type {ev.type} code {ev.code} value {ev.value}")
                    if ev.type == E.EV_KEY and ev.value == 0:
                        code = ev.code
                        if d.info.vendor == pad_snapshot.NINTENDO and code in (E.BTN_SOUTH, E.BTN_EAST):
                            code = E.BTN_EAST if code == E.BTN_SOUTH else E.BTN_SOUTH
                        if code == E.BTN_SOUTH: act("okup", cc.a_up)
                    if ev.type == E.EV_KEY and ev.value == 1:
                        code = ev.code
                        if d.info.vendor == pad_snapshot.NINTENDO and code in (E.BTN_SOUTH, E.BTN_EAST):   # raw Nintendo pad: by label
                            code = E.BTN_EAST if code == E.BTN_SOUTH else E.BTN_SOUTH
                        if code == E.BTN_SOUTH: act("ok", cc.a_down)
                        elif code == E.BTN_EAST: act("back", cc.choose, 0)
                        elif code == E.BTN_DPAD_LEFT: act("l", cc.move, -1)
                        elif code == E.BTN_DPAD_RIGHT: act("r", cc.move, 1)
                        elif code == E.BTN_DPAD_UP: act("u", cc.vmove, -1)
                        elif code == E.BTN_DPAD_DOWN: act("d", cc.vmove, 1)
                    elif ev.type == E.EV_ABS and ev.code == E.ABS_HAT0X and ev.value:
                        act("r" if ev.value > 0 else "l", cc.move, 1 if ev.value > 0 else -1)
                    elif ev.type == E.EV_ABS and ev.code == E.ABS_HAT0Y and ev.value:
                        act("d" if ev.value > 0 else "u", cc.vmove, 1 if ev.value > 0 else -1)
                    elif ev.type == E.EV_ABS and ev.code == E.ABS_Y:
                        vy = 1 if ev.value > 20000 else -1 if ev.value < -20000 else 0
                        if vy and vy != axes.get((d.path, "y"), 0):
                            act("d" if vy > 0 else "u", cc.vmove, vy)
                        axes[(d.path, "y")] = vy
                    elif ev.type == E.EV_ABS and ev.code == E.ABS_X:
                        v = 1 if ev.value > 20000 else -1 if ev.value < -20000 else 0
                        if v and v != axes.get((d.path, "x"), 0):
                            act("r" if v > 0 else "l", cc.move, v)
                        axes[(d.path, "x")] = v
            except OSError:
                pass
    time.sleep(0.25)
    for d in devices:
        try:
            for _ in d.read():
                pass
        except (OSError, BlockingIOError):
            pass
        try:
            d.ungrab()
        except OSError:
            pass
        d.close()


def daemon():
    cc = CC(overlay=True)
    cc.realize()                 # tag it as an overlay once; it's only mapped while something is up
    card = cc.card
    try:
        os.remove(SOCK)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); srv.bind(SOCK); srv.listen(4)
    pending = {"conn": None}

    def reply(conn, text_):
        try:
            conn.sendall((text_ + "\n").encode()); conn.close()
        except OSError:
            pass

    def done(choice):
        c, pending["conn"] = pending["conn"], None
        if pending.get("watch"):             # drop the fd watch BEFORE closing the socket: a watch on a closed fd
            GLib.source_remove(pending.pop("watch"))   # makes GLib poll POLLNVAL in a tight loop (100% CPU, card lag)
        if c:
            reply(c, choice)

    def on_conn(_src, _cond):
        try:
            return on_conn_inner(_src, _cond)
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                c = pending.get("conn")
                if c is not None:                        # a card that failed to open: answer, so deck_curate unfreezes
                    pending["conn"] = None
                    reply(c, "RESUME")
            except Exception:
                pass
            return True                                  # keep serving

    def on_conn_inner(_src, _cond):
        conn, _ = srv.accept()
        conn.settimeout(2)
        try:
            msg = json.loads(conn.makefile().readline() or "{}")
        except (OSError, ValueError):
            conn.close(); return True
        cmd = msg.get("cmd")
        if cmd == "open":
            if card.is_open:                    # already up: a second Steam press (ignored while saving)
                if card.save_t is None:
                    card.close_as("RESUME")
                reply(conn, "BUSY")
            else:
                pending["conn"] = conn
                card.begin(msg.get("name", ""), msg.get("shot", ""), done, msg.get("cover", ""), msg.get("system", ""),
                           msg.get("items"), msg.get("saving", True), msg.get("status", "Paused"), msg.get("states"))

                def gone(_fd, _cond, c=conn):
                    """The requester (deck_curate) closed its end: never leave a frozen card on screen."""
                    if pending["conn"] is not c:
                        return False
                    try:
                        if c.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT):
                            return True
                    except BlockingIOError:
                        return True
                    except OSError:
                        pass
                    pending.pop("watch", None)     # returning False removes this watch
                    if card.is_open:
                        card.choice = card.choice or "CANCEL"; card.finish("CANCEL")
                    return False
                pending["watch"] = GLib.io_add_watch(conn.fileno(), GLib.PRIORITY_DEFAULT,
                                                     GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR | GLib.IO_NVAL, gone)
        elif cmd == "close":
            if card.is_open and card.save_t is None:
                card.close_as("RESUME")
            reply(conn, "OK")
        elif cmd == "saved":
            if card.is_open:
                card.saved()
            reply(conn, "OK")
        elif cmd == "padtoast":
            ui_call(lambda m=msg: (cc.padtoasts.show(m.get("item") or {}, bool(m.get("joined", True))), False)[1])
            reply(conn, "OK")
        elif cmd == "toast":
            cc.toast.show(msg.get("title", ""), msg.get("sub", ""), msg.get("cover", ""))
            reply(conn, "OK")
        elif cmd == "hub":
            if card.is_open:
                reply(conn, "BUSY")
            else:
                pending["conn"] = conn
                card.begin(msg.get("name", ""), "", done, "", msg.get("system", ""), msg.get("items"), False, "",
                           msg.get("states"), msg.get("hub"))
        elif cmd == "boot":
            ui_call(lambda: (cc.boot.show(), False)[1])
            reply(conn, "OK")
        elif cmd == "pads":
            ui_call(lambda m=msg: (cc.chips.set(bool(m.get("show")), m.get("items") or [], m.get("mode", "full")), False)[1])
            reply(conn, "OK")
        elif cmd == "info":        # for tests: is the card up, what it offers, what's selected
            try:
                its = [it[0] for it in (card.items or [])]
            except Exception:
                its = []
            reply(conn, json.dumps({"active": bool(card.is_open), "sel": card.sel, "items": its, "choice": card.choice,
                                    "closing": card.closing is not None,
                                    "layers": [type(layer).__name__.lower() for layer in cc.layers if layer.active()],
                                    "shown": cc.shown}))
        elif cmd == "notice":
            ui_call(lambda m=msg: (cc.notice.show(int(m.get("n", 0)), m.get("title", ""), m.get("sub", "")), False)[1])
            reply(conn, "OK")
        elif cmd == "unnotice":
            ui_call(lambda: (cc.notice.hide(), False)[1])
            reply(conn, "OK")
        elif cmd == "pads_act":
            ui_call(lambda m=msg: (cc.chips.act(int(m.get("n", 0))), False)[1])
            reply(conn, "OK")
        elif cmd == "saver":
            ui_call(lambda: (cc.saver.show(), False)[1])
            reply(conn, "OK")
        elif cmd == "unsaver":
            ui_call(lambda: (cc.saver.hide(), False)[1])
            reply(conn, "OK")
        elif cmd in ("off", "offtest"):
            ui_call(lambda t=cmd == "offtest": (cc.off.show(t), False)[1])
            reply(conn, f"OK {OFF_FADE + 1.95:.2f}")
        elif cmd == "cover":
            ui_call(lambda: (cc.boot.show(False, True), False)[1])
            reply(conn, "OK")
        elif cmd == "uncover":
            ui_call(lambda: (cc.boot.uncover(), False)[1])
            reply(conn, "OK")
        elif cmd == "wakefade":
            ui_call(lambda: (cc.boot.show(True), False)[1])
            reply(conn, "OK")
        elif cmd == "dock":
            ui_call(lambda m=msg: (cc.boot.show(dock=m.get("label") or "TV"), False)[1])
            reply(conn, "OK")
        elif cmd == "tap":
            ui_call(card.tap, float(msg.get("x", 0)), float(msg.get("y", 0)))
            reply(conn, "OK")
        elif cmd == "state":
            reply(conn, card.state())
        elif cmd == "hide":
            if card.is_open:
                card.choice = card.choice or "CANCEL"; card.finish("CANCEL")
            reply(conn, "OK")
        else:
            reply(conn, "PONG")
        return True

    GLib.io_add_watch(srv.fileno(), GLib.IO_IN, on_conn)
    Gtk.main()


def main():
    if "--daemon" in sys.argv:
        return daemon()
    name, shot = (sys.argv[1:3] + ["", ""])[:2]
    result = {"c": None}
    cc = CC()
    cc.card.begin(name, shot, lambda c: (result.update(c=c), Gtk.main_quit()))
    Gtk.main()
    print(result["c"] or "RESUME", flush=True)


if __name__ == "__main__":
    main()
