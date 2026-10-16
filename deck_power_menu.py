#!/usr/bin/env python3
"""console-style Power menu shown over ES-DE (Game Mode).

Started by deck_curate.py (hold B on the console row) with ES-DE's environment, so gamescope treats the window
as part of the running ES-DE shortcut. The current screen is captured, dimmed and blurred as the backdrop.
While open it grabs the gamepads (ES-DE doesn't see the presses). D-pad / stick to move, A to choose, B to close.
Touch works too.
"""
import prism_config  # noqa: E402  (paths)
import math, os, subprocess, sys, threading, time

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

import evdev  # noqa: E402
from evdev import ecodes as E  # noqa: E402

HOME = os.path.expanduser("~")
FONT = "Noto Sans"
SUDO = "sudo -n /usr/bin/systemctl {0} || /usr/bin/systemctl {0}"
ITEMS = [
    # Sleep through Steam (its sleep animation plays; systemctl suspend skips it), Restart / Power Off after Prism's
    # good-night animation (2026-10-09)
    ("Sleep", "moon", f"sync; /usr/bin/python3 {prism_config.PRISM}/prism_bridge.py suspend || " + SUDO.format("suspend")),
    ("Restart", "restart", f"sync; /usr/bin/python3 {prism_config.PRISM}/prism_off.py; " + SUDO.format("reboot")),
    ("Power Off", "power", f"sync; /usr/bin/python3 {prism_config.PRISM}/prism_off.py; " + SUDO.format("poweroff")),
    ("Switch to Desktop", "monitor", "/usr/bin/steamos-session-select plasma"),
    ("Exit to Steam", "exit", "pkill -TERM -x es-de"),
    ("Close", "close", None),
]
CYAN = (0x27 / 255, 0xB6 / 255, 0xD5 / 255)   # palette cyan (2026-10-08)


def capture(path):
    try:
        if os.path.exists(path):      # gamescope writes the file a moment later: an old one would be taken for it
            os.remove(path)          # (stale backdrops behind Who's playing? / Power, 2026-10-16)
        subprocess.run(["gamescopectl", "screenshot", path], timeout=5, capture_output=True)
        for _ in range(30):
            if os.path.exists(path) and os.path.getsize(path) > 0:
                time.sleep(0.15)
                return path
            time.sleep(0.05)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- glyphs (cairo, centred at 0,0, unit ~ 1 = 1 px)

def glyph(cr, kind, s):
    cr.new_path()
    cr.set_source_rgb(1, 1, 1)
    cr.set_line_width(2.6 * s)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if kind == "power":
        cr.new_sub_path(); cr.arc(0, 1 * s, 9 * s, math.radians(-60), math.radians(240)); cr.stroke()
        cr.move_to(0, -10 * s); cr.line_to(0, -1 * s); cr.stroke()
    elif kind == "moon":
        cr.save()
        cr.arc(0, 0, 10 * s, 0, 2 * math.pi); cr.clip()
        cr.new_path()
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(-12 * s, -12 * s, 24 * s, 24 * s)
        cr.new_sub_path(); cr.arc(6 * s, -4 * s, 9 * s, 0, 2 * math.pi)
        cr.fill()
        cr.restore()
    elif kind == "restart":
        cr.new_sub_path(); cr.arc(0, 0, 9 * s, math.radians(-40), math.radians(260)); cr.stroke()
        a = math.radians(-40); x, y = 9 * s * math.cos(a), 9 * s * math.sin(a)
        cr.move_to(x - 5 * s, y - 1 * s); cr.line_to(x + 3 * s, y - 4 * s); cr.line_to(x + 1 * s, y + 4 * s)
        cr.close_path(); cr.fill()
    elif kind == "monitor":
        cr.rectangle(-11 * s, -8 * s, 22 * s, 14 * s); cr.stroke()
        cr.move_to(0, 6 * s); cr.line_to(0, 10 * s); cr.stroke()
        cr.move_to(-6 * s, 10 * s); cr.line_to(6 * s, 10 * s); cr.stroke()
    elif kind == "exit":
        cr.move_to(2 * s, -10 * s); cr.line_to(-9 * s, -10 * s); cr.line_to(-9 * s, 10 * s); cr.line_to(2 * s, 10 * s)
        cr.stroke()
        cr.move_to(-3 * s, 0); cr.line_to(10 * s, 0); cr.stroke()
        cr.move_to(6 * s, -4 * s); cr.line_to(10 * s, 0); cr.line_to(6 * s, 4 * s); cr.stroke()
    elif kind == "close":
        cr.move_to(-7 * s, -7 * s); cr.line_to(7 * s, 7 * s); cr.stroke()
        cr.move_to(7 * s, -7 * s); cr.line_to(-7 * s, 7 * s); cr.stroke()


# Redraw-palette accent (2026-10-08): hot pink > orange > yellow > lime > cyan > blue, as in the home screen.
NEON_STOPS = ((0xF0, 0x43, 0x6F), (0xF6, 0x9F, 0x3C), (0xFB, 0xE0, 0x1A), (0xB6, 0xD3, 0x51), (0x27, 0xB6, 0xD5), (0x29, 0xAB, 0xEB))
NEON = "neon"


def neon(cr, a=1.0, box=None):
    """Use the neon gradient as the cairo source: across box, else the current path, else the visible area
    (bottom-left pink to top-right blue)."""
    if box is None:
        x0, y0, x1, y1 = cr.path_extents()
        if x1 - x0 < 1 and y1 - y0 < 1:
            x0, y0, x1, y1 = cr.clip_extents()
    else:
        x0, y0, x1, y1 = box
    g = cairo.LinearGradient(x0, y1, x1, y0)
    n = len(NEON_STOPS) - 1
    for i, c in enumerate(NEON_STOPS):
        g.add_color_stop_rgba(i / n, c[0] / 255, c[1] / 255, c[2] / 255, a)
    cr.set_source(g)


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class Menu(Gtk.Window):
    def __init__(self, backdrop):
        super().__init__(title="Power")
        self.sel = 0
        self.choice = None
        self.t0 = time.monotonic()
        self.set_decorated(False)
        self.fullscreen()
        self.bg = None
        if backdrop:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file(backdrop)
                small = pb.scale_simple(pb.get_width() // 8, pb.get_height() // 8, GdkPixbuf.InterpType.BILINEAR)
                self.bg = small.scale_simple(pb.get_width(), pb.get_height(), GdkPixbuf.InterpType.BILINEAR)
            except Exception:
                self.bg = None
        area = Gtk.DrawingArea()
        area.connect("draw", self.draw)
        area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.TOUCH_MASK)
        area.connect("button-press-event", self.click)
        self.add(area)
        self.area = area
        self.connect("key-press-event", self.key)
        self.connect("realize", self.tag_window)
        self.rects = []
        GLib.timeout_add(16, self.tick)

    def tag_window(self, *_):
        # gamescope shows windows that carry the focused app's STEAM_GAME id
        appid = os.environ.get("SteamAppId")
        if appid:
            xid = self.get_window().get_xid()
            subprocess.Popen(["xprop", "-id", str(xid), "-f", "STEAM_GAME", "32c", "-set", "STEAM_GAME", appid])

    def tick(self):
        self.area.queue_draw()
        return self.choice is None

    def draw(self, w, cr):
        W, H = w.get_allocated_width(), w.get_allocated_height()
        s = H / 800
        appear = min(1.0, (time.monotonic() - self.t0) / 0.18)
        if self.bg:
            Gdk.cairo_set_source_pixbuf(cr, self.bg.scale_simple(W, H, GdkPixbuf.InterpType.BILINEAR)
                                        if self.bg.get_width() != W else self.bg, 0, 0)
            cr.paint()
            cr.set_source_rgba(0, 0, 0, 0.55 + 0.15 * appear)
            cr.paint()
        else:
            cr.set_source_rgb(0, 0, 0); cr.paint()
        pw, ih = 460 * s, 64 * s
        ph = ih * len(ITEMS) + 90 * s
        px, py = (W - pw) / 2, (H - ph) / 2 + (1 - appear) * 20 * s
        rounded(cr, px, py, pw, ph, 18 * s)
        cr.set_source_rgba(0.12, 0.12, 0.13, 0.97); cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.06); cr.set_line_width(1.5 * s); cr.stroke()
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(26 * s); cr.set_source_rgb(1, 1, 1)
        cr.move_to(px + 32 * s, py + 50 * s); cr.show_text("Power")
        self.rects = []
        for i, (label, icon, _) in enumerate(ITEMS):
            y = py + 72 * s + i * ih
            r = (px + 16 * s, y, pw - 32 * s, ih - 8 * s)
            self.rects.append(r)
            if i == self.sel:
                pulse = 0.75 + 0.25 * math.sin((time.monotonic() - self.t0) * 4)
                rounded(cr, *r, 10 * s)
                cr.set_source_rgba(1, 1, 1, 0.07); cr.fill_preserve()
                neon(cr, pulse); cr.set_line_width(3 * s); cr.stroke()
            cr.save(); cr.translate(r[0] + 34 * s, y + (ih - 8 * s) / 2); glyph(cr, icon, s * 1.1); cr.restore()
            cr.set_source_rgb(1, 1, 1) if i == self.sel else cr.set_source_rgb(0.85, 0.85, 0.85)
            cr.set_font_size(22 * s)
            cr.move_to(r[0] + 70 * s, y + (ih - 8 * s) / 2 + 8 * s); cr.show_text(label)
        # button hints, drawn as filled circles with the letter knocked out
        cr.set_font_size(15 * s)
        x = px + pw - 28 * s
        for letter, text in (("B", "Back"), ("A", "OK")):
            cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            tw = cr.text_extents(text).x_advance
            x -= tw
            cr.set_source_rgb(0.7, 0.7, 0.7); cr.move_to(x, py + ph - 18 * s); cr.show_text(text)
            x -= 22 * s
            cr.new_path(); cr.arc(x + 9 * s, py + ph - 23 * s, 10 * s, 0, 2 * math.pi)
            cr.set_source_rgb(0.85, 0.85, 0.85); cr.fill()
            cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            e = cr.text_extents(letter)
            cr.set_source_rgb(0.12, 0.12, 0.13)
            cr.move_to(x + 9 * s - e.width / 2 - e.x_bearing, py + ph - 23 * s + e.height / 2); cr.show_text(letter)
            x -= 18 * s
        return False

    # input
    def move(self, d):
        self.sel = (self.sel + d) % len(ITEMS)

    def choose(self, i=None):
        if self.choice is None:
            self.choice = ITEMS[self.sel if i is None else i]
            Gtk.main_quit()

    def key(self, _, ev):
        k = Gdk.keyval_name(ev.keyval)
        if k in ("Up", "w"): self.move(-1)
        elif k in ("Down", "s"): self.move(1)
        elif k in ("Return", "space"): self.choose()
        elif k in ("Escape", "BackSpace"): self.choose(len(ITEMS) - 1)

    def click(self, _, ev):
        for i, (x, y, w, h) in enumerate(self.rects):
            if x <= ev.x <= x + w and y <= ev.y <= y + h:
                self.sel = i
                self.choose(i)
                return
        self.choose(len(ITEMS) - 1)


def pads():
    out = []
    for p in evdev.list_devices():
        try:
            d = evdev.InputDevice(p)
            keys = d.capabilities().get(E.EV_KEY, [])
            if E.BTN_SOUTH in keys and E.BTN_EAST in keys:
                out.append(d)
            else:
                d.close()
        except OSError:
            pass
    return out


def pad_thread(menu, devices):
    import select
    for d in devices:
        try:
            d.grab()
        except OSError:
            pass
    held = {}
    last_axis = 0
    while menu.choice is None:
        r, _, _ = select.select(devices, [], [], 0.1)
        for d in r:
            try:
                for ev in d.read():
                    if ev.type == E.EV_KEY and ev.value == 1:
                        if ev.code in (E.BTN_SOUTH,):
                            GLib.idle_add(menu.choose)
                        elif ev.code in (E.BTN_EAST,):
                            GLib.idle_add(menu.choose, len(ITEMS) - 1)
                        elif ev.code == E.BTN_DPAD_UP:
                            GLib.idle_add(menu.move, -1)
                        elif ev.code == E.BTN_DPAD_DOWN:
                            GLib.idle_add(menu.move, 1)
                    elif ev.type == E.EV_ABS and ev.code == E.ABS_HAT0Y and ev.value:
                        GLib.idle_add(menu.move, 1 if ev.value > 0 else -1)
                    elif ev.type == E.EV_ABS and ev.code == E.ABS_Y:
                        v = 1 if ev.value > 20000 else -1 if ev.value < -20000 else 0
                        if v and v != last_axis:
                            GLib.idle_add(menu.move, v)
                        last_axis = v
            except OSError:
                pass
    time.sleep(0.25)  # let the releases of A/B drain before ES-DE sees the pad again
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


def main():
    shot = capture("/tmp/deck_power_menu_bg.png")
    menu = Menu(shot)
    menu.show_all()
    devices = pads()
    threading.Thread(target=pad_thread, args=(menu, devices), daemon=True).start()
    GLib.timeout_add_seconds(60, lambda: menu.choose(len(ITEMS) - 1) or False)  # don't linger
    Gtk.main()
    menu.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()
    label, _, cmd = menu.choice
    print("choice:", label, flush=True)
    time.sleep(0.4)
    if cmd:
        subprocess.Popen(["/bin/bash", "-c", cmd], start_new_session=True)


if __name__ == "__main__":
    main()
