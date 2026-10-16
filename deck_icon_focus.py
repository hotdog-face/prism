#!/usr/bin/env python3
"""Cursor for the Prism icon row (Controllers / Settings / Power), drawn as a transparent gamescope overlay.

deck_curate.py starts it when Down is pressed on the console row. While it runs it grabs the gamepads so ES-DE
doesn't see the presses: Left/Right move, A opens, B/Up (or 12 s idle) closes. The window shows a capture of the
screen with a cyan ring and the icon's name on top; it carries ES-DE's STEAM_GAME id so gamescope shows it.
"""
import prism_config  # noqa: E402  (paths)
import math, os, select, subprocess, sys, threading, time
import cairo, gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402
import evdev  # noqa: E402
from evdev import ecodes as E  # noqa: E402

HOME = os.path.expanduser("~")
# icon centres in 1280x800 screen pixels (theme: iconRow lower left, pos 0.035 0.94, size 0.191 0.0848, origin 0 0.5)
CENTRES, CY, R = (85, 167, 249), 752, 29
LABELS = ("Controllers", "Settings", "Power")
STEAM = 'unset LD_LIBRARY_PATH; exec /usr/bin/steam -ifrunning "steam://open/settings%s"'
CYAN = (0, 195 / 255, 227 / 255)


def act(i):
    if i == 0:                   # Controllers: "Who's playing?" (X there opens Steam's controller settings)
        subprocess.Popen(["systemd-run", "--user", "--collect", "--quiet", "/usr/bin/python3",
                          f"{prism_config.PRISM}/prism_screens.py", "join", "Controllers icon"], start_new_session=True)
    elif i == 1:                 # Settings: Prism Settings (Steam's settings are under System there)
        subprocess.Popen(["systemd-run", "--user", "--collect", "--quiet", "/usr/bin/python3",
                          f"{prism_config.PRISM}/prism_settings.py", "Settings icon"], start_new_session=True)
    else:
        subprocess.Popen(["/usr/bin/python3", f"{prism_config.PRISM}/deck_power_menu.py"], start_new_session=True,
                         stdout=open("/tmp/deck_power_menu.log", "w"), stderr=subprocess.STDOUT)


def capture(path="/tmp/deck_icon_focus_bg.png"):
    try:
        if os.path.exists(path):
            os.remove(path)
        subprocess.run(["gamescopectl", "screenshot", path], timeout=5, capture_output=True)
        for _ in range(40):
            if os.path.exists(path) and os.path.getsize(path) > 0:
                time.sleep(0.12)
                return GdkPixbuf.Pixbuf.new_from_file(path)
            time.sleep(0.03)
    except Exception:
        pass
    return None


class Overlay(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.bg = capture()
        self.sel, self.done, self.t0, self.last = 1, False, time.monotonic(), time.monotonic()
        self.set_decorated(False)
        self.fullscreen()
        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self.draw)
        self.add(self.area)
        self.connect("realize", self.tag)
        GLib.timeout_add(16, self.tick)

    def tag(self, *_):
        xid = str(self.get_window().get_xid())
        appid = os.environ.get("SteamAppId")
        if appid:
            subprocess.run(["xprop", "-id", xid, "-f", "STEAM_GAME", "32c", "-set", "STEAM_GAME", appid])

    def tick(self):
        if time.monotonic() - self.last > 12:
            self.finish(None)
        self.area.queue_draw()
        return not self.done

    def draw(self, w, cr):
        W, H = w.get_allocated_width(), w.get_allocated_height()
        if self.bg is not None:
            bg = self.bg if self.bg.get_width() == W else self.bg.scale_simple(W, H, GdkPixbuf.InterpType.BILINEAR)
            Gdk.cairo_set_source_pixbuf(cr, bg, 0, 0)
        else:
            cr.set_source_rgb(0, 0, 0)
        cr.paint()
        sx, sy = W / 1280, H / 800
        cx, cy = CENTRES[self.sel] * sx, CY * sy
        pulse = 0.7 + 0.3 * math.sin((time.monotonic() - self.t0) * 4)
        cr.set_line_width(4 * sy)
        cr.set_source_rgba(*CYAN, pulse)
        cr.arc(cx, cy, (R + 6) * sy, 0, 2 * math.pi)
        cr.stroke()
        # name pill above the icon
        cr.select_font_face("Noto Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(20 * sy)
        label = LABELS[self.sel]
        ext = cr.text_extents(label)
        pw, ph = ext.x_advance + 28 * sx, 34 * sy
        px, py = cx - pw / 2, cy - (R + 18) * sy - ph
        rad = ph / 2
        cr.new_path()
        cr.arc(px + rad, py + rad, rad, math.pi / 2, 3 * math.pi / 2)
        cr.arc(px + pw - rad, py + rad, rad, -math.pi / 2, math.pi / 2)
        cr.close_path()
        cr.set_source_rgba(0.13, 0.13, 0.15, 0.95)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(cx - ext.x_advance / 2, py + ph / 2 + ext.height / 2 - 1)
        cr.show_text(label)
        return False

    def move(self, d):
        self.sel = max(0, min(2, self.sel + d))
        self.last = time.monotonic()

    def finish(self, choice):
        if not self.done:
            self.done = True
            self.choice = choice
            Gtk.main_quit()


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


def pad_loop(ov, devs):
    for d in devs:
        try:
            d.grab()
        except OSError:
            pass
    axis = 0
    while not ov.done:
        r, _, _ = select.select(devs, [], [], 0.1)
        for d in r:
            try:
                for ev in d.read():
                    if ev.type == E.EV_KEY and ev.value == 1:
                        if ev.code == E.BTN_SOUTH:
                            GLib.idle_add(ov.finish, ov.sel)
                        elif ev.code in (E.BTN_EAST, E.BTN_DPAD_UP):
                            GLib.idle_add(ov.finish, None)
                        elif ev.code == E.BTN_DPAD_LEFT:
                            GLib.idle_add(ov.move, -1)
                        elif ev.code == E.BTN_DPAD_RIGHT:
                            GLib.idle_add(ov.move, 1)
                    elif ev.type == E.EV_ABS and ev.code == E.ABS_HAT0X and ev.value:
                        GLib.idle_add(ov.move, ev.value)
                    elif ev.type == E.EV_ABS and ev.code == E.ABS_HAT0Y and ev.value < 0:
                        GLib.idle_add(ov.finish, None)
                    elif ev.type == E.EV_ABS and ev.code == E.ABS_X:
                        v = 1 if ev.value > 20000 else -1 if ev.value < -20000 else 0
                        if v and v != axis:
                            GLib.idle_add(ov.move, v)
                        axis = v
            except OSError:
                pass
    time.sleep(0.2)  # let A/B releases drain while still grabbed
    for d in devs:
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
    ov = Overlay()
    ov.choice = None
    ov.show_all()
    threading.Thread(target=pad_loop, args=(ov, pads()), daemon=True).start()
    Gtk.main()
    ov.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()
    time.sleep(0.25)
    if ov.choice is not None:
        act(ov.choice)


if __name__ == "__main__":
    main()
