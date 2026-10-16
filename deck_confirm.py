#!/usr/bin/env python3
"""console-style "Are you sure?" card over ES-DE, for hold-L4 in deck_curate.py.

  deck_confirm.py TITLE NAME DETAIL OK_LABEL [BUSY_LABEL]

Also used for Prism's notice cards (prism_notice.py): an empty BUSY_LABEL closes the card on A, DECK_CONFIRM_CANCEL
names the B button (default "Cancel"), DECK_CONFIRM_BG is a picture to blur behind the card, and DECK_CONFIRM_BAR="0.98|SD card · 1 TB|18 GB free" draws a usage bar.

Prints CONFIRMED or CANCELLED on stdout. After CONFIRMED it lets go of the gamepads and stays up showing
"Removing…" (so deck_curate's ES-DE delete macro runs out of sight) until /tmp/deck_confirm_done appears,
it gets SIGTERM, or 20 s pass. A = confirm, B = cancel; touch works too. Same look as deck_power_menu.py.
"""
import math, os, select, signal, sys, threading, time

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402
from evdev import ecodes as E  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deck_power_menu import CYAN, FONT, capture, neon, pads, rounded  # noqa: E402

DONE = "/tmp/deck_confirm_done"
CARD_FLAG = "/tmp/prism_card_up"    # pid of the card on screen


class Card(Gtk.Window):
    def __init__(self, backdrop, title, name, detail, ok, busy):
        super().__init__(title="Confirm")
        self.title_, self.name, self.detail, self.ok, self.busy = title, name, detail, ok, busy
        self.cancel = os.environ.get("DECK_CONFIRM_CANCEL") or "Cancel"
        bar = os.environ.get("DECK_CONFIRM_BAR", "").split("|")
        self.bar = (float(bar[0]), bar[1] if len(bar) > 1 else "", bar[2] if len(bar) > 2 else "") if bar[0] else None
        self.state = "ask"          # ask -> busy -> (quit)
        self.answer = None
        self.sel = 1                # 0 = Cancel, 1 = OK (A on the default confirms)
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
        self.connect("realize", self.tag_window)
        self.btns = []
        GLib.timeout_add(16, self.tick)

    def tag_window(self, *_):
        appid = os.environ.get("SteamAppId")
        if appid:
            xid = self.get_window().get_xid()
            import subprocess
            subprocess.Popen(["xprop", "-id", str(xid), "-f", "STEAM_GAME", "32c", "-set", "STEAM_GAME", appid])

    def tick(self):
        self.area.queue_draw()
        if self.state == "busy" and (os.path.exists(DONE) or time.monotonic() - self.busy_t > 20):
            Gtk.main_quit(); return False
        return True

    def wrap(self, cr, text, width):
        words, lines, cur = text.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if cr.text_extents(t).x_advance > width and cur:
                lines.append(cur); cur = w
            else:
                cur = t
        if cur:
            lines.append(cur)
        return lines[:3]

    def draw(self, w, cr):
        W, H = w.get_allocated_width(), w.get_allocated_height()
        s = H / 800
        appear = min(1.0, (time.monotonic() - self.t0) / 0.16)
        if self.bg:
            Gdk.cairo_set_source_pixbuf(cr, self.bg.scale_simple(W, H, GdkPixbuf.InterpType.BILINEAR)
                                        if self.bg.get_width() != W else self.bg, 0, 0)
            cr.paint()
            cr.set_source_rgba(0, 0, 0, 0.55 + 0.15 * appear); cr.paint()
        else:
            cr.set_source_rgb(0, 0, 0); cr.paint()
        pw, ph = 620 * s, (346 if self.bar else 300) * s
        px, py = (W - pw) / 2, (H - ph) / 2 + (1 - appear) * 20 * s
        rounded(cr, px, py, pw, ph, 18 * s)
        cr.set_source_rgba(0.12, 0.12, 0.13, 0.97); cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.06); cr.set_line_width(1.5 * s); cr.stroke()
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        if self.state == "busy":
            cr.set_font_size(26 * s); cr.set_source_rgb(1, 1, 1)
            t = self.busy
            e = cr.text_extents(t); cr.move_to(W / 2 - e.x_advance / 2, py + ph / 2 + 30 * s); cr.show_text(t)
            a = (time.monotonic() - self.t0) * 6
            cr.set_line_width(4 * s); neon(cr)
            cr.new_path(); cr.arc(W / 2, py + ph / 2 - 30 * s, 20 * s, a, a + 4.2); cr.stroke()
            return False
        # title, game name, detail
        cr.set_font_size(26 * s); cr.set_source_rgb(1, 1, 1)
        e = cr.text_extents(self.title_); cr.move_to(W / 2 - e.x_advance / 2, py + 62 * s); cr.show_text(self.title_)
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(22 * s); neon(cr)
        y = py + 108 * s
        for line in self.wrap(cr, self.name, pw - 70 * s)[:2]:
            e = cr.text_extents(line); cr.move_to(W / 2 - e.x_advance / 2, y); cr.show_text(line); y += 30 * s
        if self.bar:       # usage bar (storage card): track, neon fill, labels under it
            frac, left, right = self.bar
            bx, bw, bh_ = px + 52 * s, pw - 104 * s, 12 * s
            rounded(cr, bx, y - 6 * s, bw, bh_, 6 * s); cr.set_source_rgba(1, 1, 1, 0.08); cr.fill()
            rounded(cr, bx, y - 6 * s, max(bh_, bw * min(1.0, frac)), bh_, 6 * s)
            neon(cr, 1.0, (bx, y - 6 * s, bx + bw, y - 6 * s + bh_)); cr.fill()
            cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(14 * s); cr.set_source_rgb(0.72, 0.72, 0.72)
            cr.move_to(bx, y + 26 * s); cr.show_text(left)
            e = cr.text_extents(right); cr.move_to(bx + bw - e.x_advance, y + 26 * s); cr.show_text(right)
            y += 46 * s
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(17 * s); cr.set_source_rgb(0.72, 0.72, 0.72)
        for line in self.wrap(cr, self.detail, pw - 90 * s)[:2]:
            e = cr.text_extents(line); cr.move_to(W / 2 - e.x_advance / 2, y + 6 * s); cr.show_text(line); y += 24 * s
        # buttons: [B Cancel] [A OK], Switch style (divider line, two halves)
        by, bh = py + ph - 74 * s, 74 * s
        cr.set_source_rgba(1, 1, 1, 0.10); cr.set_line_width(1.2 * s)
        cr.move_to(px, by); cr.line_to(px + pw, by); cr.stroke()
        cr.move_to(px + pw / 2, by); cr.line_to(px + pw / 2, py + ph); cr.stroke()
        self.btns = [(px, by, pw / 2, bh), (px + pw / 2, by, pw / 2, bh)]
        for i, (letter, label) in enumerate((("B", self.cancel), ("A", self.ok))):
            x0, y0, bw, _ = self.btns[i]
            if i == self.sel:
                pulse = 0.75 + 0.25 * math.sin((time.monotonic() - self.t0) * 4)
                rounded(cr, x0 + 10 * s, y0 + 10 * s, bw - 20 * s, bh - 20 * s, 10 * s)
                cr.set_source_rgba(1, 1, 1, 0.07); cr.fill_preserve()
                neon(cr, pulse); cr.set_line_width(3 * s); cr.stroke()
            cr.set_font_size(21 * s)
            tw = cr.text_extents(label).x_advance
            total = 30 * s + tw
            cx = x0 + bw / 2 - total / 2
            cy = y0 + bh / 2
            cr.new_path(); cr.arc(cx + 11 * s, cy, 11 * s, 0, 2 * math.pi)
            cr.set_source_rgb(0.85, 0.85, 0.85); cr.fill()
            cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(14 * s); e = cr.text_extents(letter)
            cr.set_source_rgb(0.12, 0.12, 0.13)
            cr.move_to(cx + 11 * s - e.x_advance / 2 - e.x_bearing / 2, cy + e.height / 2); cr.show_text(letter)
            cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(21 * s)
            cr.set_source_rgb(1, 1, 1) if i == self.sel else cr.set_source_rgb(0.8, 0.8, 0.8)
            cr.move_to(cx + 30 * s, cy + 7 * s); cr.show_text(label)
        return False

    def answer_with(self, yes):
        if self.answer is not None:
            return
        self.answer = yes
        print("CONFIRMED" if yes else "CANCELLED", flush=True)
        if yes and self.busy:
            self.state = "busy"; self.busy_t = time.monotonic()
        else:
            Gtk.main_quit()

    def move(self, d):
        if self.state == "ask":
            self.sel = max(0, min(1, self.sel + d))

    def press_a(self):
        self.answer_with(self.sel == 1)

    def click(self, _, ev):
        if self.state != "ask":
            return
        for i, (x, y, w, h) in enumerate(self.btns):
            if x <= ev.x <= x + w and y <= ev.y <= y + h:
                self.answer_with(i == 1); return


def pad_thread(card, devices):
    for d in devices:
        try:
            d.grab()
        except OSError:
            pass
    while card.answer is None:
        r, _, _ = select.select(devices, [], [], 0.1)
        for d in r:
            try:
                for ev in d.read():
                    if ev.type == E.EV_KEY and ev.value == 1:
                        if ev.code == E.BTN_SOUTH:
                            GLib.idle_add(card.press_a)
                        elif ev.code == E.BTN_EAST:
                            GLib.idle_add(card.answer_with, False)
                        elif ev.code == E.BTN_DPAD_LEFT:
                            GLib.idle_add(card.move, -1)
                        elif ev.code == E.BTN_DPAD_RIGHT:
                            GLib.idle_add(card.move, 1)
                    elif ev.type == E.EV_ABS and ev.code == E.ABS_HAT0X and ev.value:
                        GLib.idle_add(card.move, 1 if ev.value > 0 else -1)
            except OSError:
                pass
    time.sleep(0.25)   # let A/B releases drain before ES-DE sees the pads again
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
    print("RELEASED", flush=True)


def main():
    title, name, detail, ok, busy = (sys.argv[1:6] + ["", "", "", "OK", "Removing…"][len(sys.argv[1:6]):])[:5]
    if busy == "-":          # notice cards: no busy step after A
        busy = ""
    try:
        os.remove(DONE)
    except OSError:
        pass
    shot = os.environ.get("DECK_CONFIRM_BG") or capture("/tmp/deck_confirm_bg.png")   # a picture to blur behind
    card = Card(shot, title, name, detail, ok, busy)
    card.show_all()
    try:                     # "a Prism card is up" for everyone else (prism_state.card_up): no self-heal unpause, no
        open(CARD_FLAG, "w").write(str(os.getpid()))           # notices on top, no second card
    except OSError:
        pass
    threading.Thread(target=pad_thread, args=(card, pads()), daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: GLib.idle_add(Gtk.main_quit))
    secs = int(os.environ.get("DECK_CONFIRM_TIMEOUT", "30"))     # 0: wait for an answer (the undock pause card)
    if secs:
        GLib.timeout_add_seconds(secs, lambda: card.answer_with(False) or False)   # don't linger
    Gtk.main()
    try:
        if open(CARD_FLAG).read().strip() == str(os.getpid()):
            os.remove(CARD_FLAG)
    except OSError:
        pass
    card.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()


if __name__ == "__main__":
    main()
