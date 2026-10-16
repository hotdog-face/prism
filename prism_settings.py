#!/usr/bin/env python3
"""Prism Settings (2026-10-14, approved mockup): the Settings icon on the home screen opens this.

Categories on the left (Games, Extras, Feel, Controllers, System), their settings on the right. D-pad / stick
moves, A opens or flips a switch, B goes back (from the list of categories: closes). Every change takes effect at once
(prism_features); rows on the home screen come and go at the next refresh, when nobody is playing. Steam's own
settings are under System.
    prism_settings.py [why]
"""
import glob, json, math, os, select, shutil, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_config  # noqa: E402  (paths)

if not os.environ.get("DISPLAY"):           # started by systemd-run: take ES-DE's display / Steam session
    try:
        import deck_curate as _d
        _pid = _d.esde_pid()
        os.environ.update(_d.esde_env(_pid) if _pid else {"DISPLAY": ":0"})
    except Exception:
        os.environ["DISPLAY"] = ":0"

import cairo  # noqa: E402
import gi  # noqa: E402
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402
from evdev import ecodes as E  # noqa: E402

from deck_power_menu import FONT, capture, neon, pads, rounded  # noqa: E402
import prism_features as pf  # noqa: E402

LOG = "/tmp/prism_settings.log"
STEAM_URL = 'unset LD_LIBRARY_PATH; exec /usr/bin/steam -ifrunning "steam://open/settings%s"'
CATS = ["Games", "Extras", "Feel", "Controllers", "System"]
TEST_INPUT = "/tmp/prism_settings_input"


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def ago(t):
    s = time.time() - t
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} minutes ago"
    if s < 86400:
        return f"{int(s // 3600)} hours ago"
    return "yesterday" if s < 172800 else f"{int(s // 86400)} days ago"


def last_backup():
    try:
        lines = [l for l in open(f"{prism_config.PRISM}/save_backup.log") if " backup " in l and "rc=0" in l]
    except OSError:
        return ""
    if not lines:
        return ""
    from datetime import datetime
    t = datetime.strptime(lines[-1][:19], "%Y-%m-%d %H:%M:%S")
    days = (datetime.now().date() - t.date()).days
    when = "today" if days == 0 else "last night" if days == 1 and t.hour >= 18 else "yesterday" if days == 1 \
        else t.strftime("%b %-d")
    return f"Last backup: {when} at {t.strftime('%-I:%M %p')}"


def version():
    try:
        rec = json.load(open(f"{prism_config.HOME}/.config/prism/installed.json"))
        if rec.get("version"):
            return rec["version"], rec.get("updated", "")
    except (OSError, ValueError):
        pass
    try:
        r = subprocess.run(["git", "-C", prism_config.PRISM, "log", "-1", "--format=%h %cs"], capture_output=True,
                           text=True, timeout=5).stdout.split()
        return (r[0], r[1]) if len(r) == 2 else ("", "")
    except Exception:
        return "", ""


class Settings:
    def __init__(self):
        self.cat, self.row, self.focus = 0, 0, "rail"      # focus: rail / panel
        self.view = None                                   # None, ("fixed",), ("setup", key, url)
        self.answer = None
        self.t0 = time.monotonic()
        self.status = {}                                   # per-row status lines changed by actions
        self.last_act = {}
        shot = capture("/tmp/prism_settings_bg.png")
        self.bg = None
        try:
            bg = GdkPixbuf.Pixbuf.new_from_file(f"{prism_config.THEME_DIR}/switch/ambient_still.png")
            self.bg = bg
        except Exception:
            pass
        self.rebuild()

    # ---- what each category holds ------------------------------------------------------------------------------
    def rebuild(self):
        import prism_shelf, prism_storage
        look = len(prism_shelf.listing())
        drives = []
        for key, label, path in prism_storage.drives():
            try:
                du = shutil.disk_usage(path)
                drives.append((f"{label} · {prism_storage.size_label(du.total)}", f"{du.free / 1e9:.0f} GB free",
                               du.used / du.total))
            except OSError:
                pass
        games = [
            {"t": "Add games from your phone", "s": "Scan a code, then drop games in from any phone or computer on "
             "your Wi-Fi", "act": self.add_games},
            {"t": "Needs a look", "s": "Games that didn't start in the nightly check",
             "pill": (f"{look} game{'s' if look != 1 else ''}", "warn") if look else ("Nothing to check", ""),
             "act": self.needs_a_look},
            {"t": "Look for new games now", "s": self.status.get("sync", "Prism also checks by itself every 15 minutes"),
             "act": self.sync_now},
            {"t": "Storage", "bars": drives, "value": "See the biggest games", "act": self.biggest},
        ]
        extras = []
        for key, (title, desc, *_r) in pf.EXTRAS.items():
            sub = last_backup() or desc if key == "backup" and pf.configured(key) else desc
            if pf.configured(key):
                extras.append({"t": title, "s": sub, "switch": pf.enabled(key),
                               "act": lambda k=key: self.toggle_extra(k)})
            elif key in ("plex", "overseerr", "backup"):
                extras.append({"t": title, "s": desc, "value": "Set up on your phone",
                               "act": lambda k=key: self.setup(k)})
        feel = [{"t": title, "s": desc, "switch": pf.feel(key), "act": lambda k=key: self.toggle_feel(k)}
                for key, (title, desc, _d) in pf.FEEL.items()]
        controllers = [
            {"t": "Who's playing?", "s": "Player numbers, and the order players go in", "act": self.whos_playing},
            {"t": "Pair a controller", "s": "Bluetooth controllers: put the controller in pairing mode first",
             "act": lambda: self.steam("/bluetooth", "Bluetooth")},
            {"t": "Controller settings", "s": "Steam's controller settings", "act": lambda: self.steam("/controller",
                                                                                                    "controllers")},
        ]
        ver, when = version()
        fixed = __import__("prism_notice").fixed_recently()
        system = [
            {"t": "Prism", "s": f"Version {ver}" + (f" · {when}" if when else "") if ver else "Prism",
             "pill": ("Installed", "ok"), "act": None},
            {"t": "Steam settings", "s": "Wi-Fi, Bluetooth, display, sound and everything else on the Deck",
             "act": lambda: self.steam("", "settings")},
            {"t": "Restart Prism", "s": "Reloads the home screen. Your games and settings stay as they are",
             "act": self.restart},
            {"t": "Prism fixed recently", "s": fixed[0]["text"] if fixed else "Nothing needed fixing",
             "act": (lambda: self.open_view(("fixed",))) if fixed else None},
        ]
        self.rows = [games, extras, feel, controllers, system]

    # ---- actions -------------------------------------------------------------------------------------------------
    def close_then(self, fn, *a):
        """Leave Settings, then do something on the home screen."""
        self.answer = "CLOSE"
        Gtk.main_quit()
        self.after = (fn, a)

    def add_games(self):
        self.close_then(subprocess.Popen, ["systemd-run", "--user", "--collect", "--quiet", "/usr/bin/python3",
                                           f"{prism_config.PRISM}/prism_screens.py", "addgames", "Settings"])

    def needs_a_look(self):
        import prism_shelf
        if prism_shelf.listing():
            self.close_then(subprocess.Popen, ["/usr/bin/python3", f"{prism_config.PRISM}/esde_nav.py",
                                               "needsalook", "open"])

    def sync_now(self):
        subprocess.Popen(["systemctl", "--user", "start", "prism-sync.service"])
        self.status["sync"] = "Looking… new games show up as soon as nobody is playing"
        self.rebuild()

    def biggest(self):
        self.close_then(subprocess.Popen, ["systemd-run", "--user", "--collect", "--quiet", "/usr/bin/python3",
                                           f"{prism_config.PRISM}/prism_storage.py", "biggest"])

    def toggle_extra(self, key):
        on = not pf.enabled(key)
        pf.set_enabled(key, on)
        log(f"extra {key}: {'on' if on else 'off'}")
        self.rebuild()

    def toggle_feel(self, key):
        pf.set_feel(key, not pf.feel(key))
        log(f"feel {key}: {pf.feel(key)}")
        self.rebuild()

    def whos_playing(self):
        self.close_then(subprocess.Popen, ["systemd-run", "--user", "--collect", "--quiet", "/usr/bin/python3",
                                           f"{prism_config.PRISM}/prism_screens.py", "join", "Settings"])

    def steam(self, page, label):
        self.close_then(subprocess.Popen, ["/bin/bash", "-c", STEAM_URL % page])
        log(f"Steam {label}")

    def restart(self):
        self.close_then(subprocess.Popen, ["systemd-run", "--user", "--collect", "--quiet", "/usr/bin/python3", "-c",
                                           "import sys; sys.path.insert(0, %r); import prism_esde\n"
                                           "with prism_esde.esde_closed('Settings: Restart Prism', require_idle=False):"
                                           " pass" % prism_config.PRISM])

    def setup(self, key):
        import prism_addgames as ag
        url = ag.serve(setup=key)
        qr = "/tmp/prism_setup_qr.png"
        subprocess.run(["qrencode", "-o", qr, "-s", "12", "-m", "0", url], capture_output=True)
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(qr)
        except Exception:
            pb = None
        self.open_view(("setup", key, url, pb))

    def open_view(self, view):
        self.view = view

    def close_view(self):
        if self.view and self.view[0] == "setup":
            import prism_addgames as ag
            ag.stop()
        self.view = None
        self.rebuild()

    # ---- input -------------------------------------------------------------------------------------------------
    def press(self, name):
        now = time.monotonic()
        if now - self.last_act.get(name, 0) < 0.12:          # the same press seen through two devices
            return False
        self.last_act[name] = now
        if self.view:
            if name in ("b", "a"):
                self.close_view()
            return False
        rows = self.rows[self.cat]
        if self.focus == "rail":
            if name in ("up", "down"):
                self.cat = (self.cat + (1 if name == "down" else -1)) % len(CATS); self.row = 0
                sound("cc_move")
            elif name in ("a", "right") and rows:
                self.focus, self.row = "panel", 0
                sound("cc_move")
            elif name == "b":
                self.answer = "CLOSE"; Gtk.main_quit()
        else:
            if name in ("up", "down"):
                self.row = max(0, min(len(rows) - 1, self.row + (1 if name == "down" else -1)))
                sound("cc_move")
            elif name in ("b", "left"):
                self.focus = "rail"; sound("back")
            elif name == "a":
                act = rows[self.row].get("act")
                if act:
                    sound("cc_choose")
                    act()
        return False

    # ---- drawing -----------------------------------------------------------------------------------------------
    def draw(self, w, cr):
        W, H = w.get_allocated_width(), w.get_allocated_height()
        u = H / 62.5
        now = time.monotonic()
        pulse = 0.72 + 0.28 * (0.5 + 0.5 * math.cos((now - self.t0) * 2 * math.pi / 1.6))
        cr.set_source_rgb(0.05, 0.05, 0.055); cr.paint()
        if self.bg:
            cr.save(); cr.scale(W / self.bg.get_width(), H / self.bg.get_height())
            Gdk.cairo_set_source_pixbuf(cr, self.bg, 0, 0); cr.paint(); cr.restore()
        cr.set_source_rgba(0, 0, 0, 0.42); cr.paint()
        # status: gear + Settings, clock + battery
        gx, gy = 3 * u, 3.6 * u
        cr.new_path(); cr.arc(gx + 1.4 * u, gy, 1.4 * u, 0, 2 * math.pi); neon(cr, 1.0, (gx, gy - 1.4 * u, gx + 2.8 * u, gy + 1.4 * u)); cr.fill()
        cr.new_path(); cr.arc(gx + 1.4 * u, gy, 0.55 * u, 0, 2 * math.pi); cr.set_source_rgb(1, 1, 1); cr.set_line_width(0.35 * u); cr.stroke()
        self.text(cr, "Settings", gx + 3.9 * u, gy + 0.8 * u, 2.2 * u, (1, 1, 1, 1), bold=True)
        try:
            bat = open("/sys/class/power_supply/BAT1/capacity").read().strip() + "%"
        except OSError:
            bat = ""
        right = f"{time.strftime('%-I:%M %p')}   {bat}"
        cr.set_font_size(1.6 * u); e = cr.text_extents(right)
        self.text(cr, right, W - 3 * u - e.x_advance, gy + 0.55 * u, 1.6 * u, (0.87, 0.87, 0.87, 1))
        # rail
        y = 8.5 * u
        for i, name in enumerate(CATS):
            h = 4.9 * u
            if i == self.cat:
                rounded(cr, 3 * u, y, 21 * u, h, 1 * u); cr.set_source_rgba(1, 1, 1, 0.09); cr.fill()
                if self.focus == "rail" and not self.view:
                    rounded(cr, 3 * u, y, 21 * u, h, 1 * u); neon(cr, pulse, (3 * u, y, 24 * u, y + h))
                    cr.set_line_width(0.24 * u); cr.stroke()
            rounded(cr, 4.5 * u, y + h / 2 - 1.25 * u, 2.5 * u, 2.5 * u, 0.7 * u); cr.set_source_rgba(1, 1, 1, 0.08); cr.fill()
            self.icon(cr, name, 4.5 * u + 1.25 * u, y + h / 2, u)
            self.text(cr, name, 8.2 * u, y + h / 2 + 0.6 * u, 1.75 * u,
                      (1, 1, 1, 1) if i == self.cat else (0.79, 0.79, 0.8, 1), bold=i == self.cat)
            y += h + 0.6 * u
        # panel
        px, py, pw, ph = 27 * u, 8.5 * u, W - 30 * u, H - 17 * u
        rounded(cr, px, py, pw, ph, 1.4 * u); cr.set_source_rgba(0.12, 0.12, 0.13, 0.94); cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.06); cr.set_line_width(1); cr.stroke()
        if self.view:
            self.draw_view(cr, px, py, pw, ph, u)
        else:
            self.draw_rows(cr, px, py, pw, ph, u, pulse)
        # hints
        hy = H - 3.25 * u
        cr.rectangle(0, H - 6.5 * u, W, 6.5 * u); cr.set_source_rgba(0.06, 0.06, 0.065, 0.9); cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.10); cr.move_to(0, H - 6.5 * u); cr.line_to(W, H - 6.5 * u); cr.stroke()
        if self.view:
            a_label = "Done"
        elif self.focus == "rail":
            a_label = "Choose"
        else:
            r = self.rows[self.cat][self.row] if self.rows[self.cat] else {}
            a_label = ("Turn off" if r.get("switch") else "Turn on") if "switch" in r else "Open"
        if self.focus == "panel" and not self.view and self.rows[self.cat] and \
                not self.rows[self.cat][self.row].get("act"):
            a_label = None                         # nothing to open on this row
        hints = ([("B", "Back")] + ([("A", a_label)] if a_label else [])) if not self.view else [("B", "Done")]
        cr.set_font_size(1.55 * u)
        widths = [2.4 * u + 0.8 * u + cr.text_extents(lab).x_advance for _, lab in hints]
        x = W - 3 * u - sum(widths) - 3 * u * (len(hints) - 1)
        for (letter, lab), wd in zip(hints, widths):
            self.badge(cr, x, hy, letter, u)
            self.text(cr, lab, x + 3.2 * u, hy + 0.55 * u, 1.55 * u, (0.87, 0.87, 0.87, 1))
            x += wd + 3 * u
        return False

    def draw_rows(self, cr, px, py, pw, ph, u, pulse):
        rows = self.rows[self.cat]
        y = py + 1.4 * u
        for i, r in enumerate(rows):
            h = 6.6 * u if not r.get("bars") else 6.6 * u + 2.9 * u * len(r["bars"])
            x0, x1 = px + 1.4 * u, px + pw - 1.4 * u
            sel = self.focus == "panel" and i == self.row
            if i and not sel and not (self.focus == "panel" and i - 1 == self.row):
                cr.set_source_rgba(1, 1, 1, 0.10); cr.set_line_width(1)
                cr.move_to(x0 + 1.8 * u, y - 0.3 * u); cr.line_to(x1 - 1.8 * u, y - 0.3 * u); cr.stroke()
            if sel:
                rounded(cr, x0, y, x1 - x0, h, 1 * u); cr.set_source_rgba(1, 1, 1, 0.06); cr.fill()
                rounded(cr, x0, y, x1 - x0, h, 1 * u); neon(cr, pulse, (x0, y, x1, y + h)); cr.set_line_width(0.24 * u)
                cr.stroke()
            tx = x0 + 1.8 * u
            # right side first, so the text knows its room
            rx = x1 - 1.8 * u
            cy = y + 3.3 * u
            if "switch" in r:
                sw, sh = 5 * u, 2.6 * u
                rounded(cr, rx - sw, cy - sh / 2, sw, sh, sh / 2)
                if r["switch"]:
                    neon(cr, 1.0, (rx - sw, cy - sh / 2, rx, cy + sh / 2))
                else:
                    cr.set_source_rgb(0.23, 0.23, 0.24)
                cr.fill()
                kx = rx - sh / 2 if r["switch"] else rx - sw + sh / 2
                cr.new_path(); cr.arc(kx, cy, 1 * u, 0, 2 * math.pi)
                cr.set_source_rgb(1, 1, 1) if r["switch"] else cr.set_source_rgb(0.81, 0.81, 0.83); cr.fill()
                rx -= sw + 1.5 * u
            else:
                if r.get("act"):
                    cr.set_source_rgb(0.6, 0.6, 0.63); cr.set_line_width(0.22 * u)
                    cr.move_to(rx - 0.9 * u, cy - 0.65 * u); cr.line_to(rx - 0.25 * u, cy); cr.line_to(rx - 0.9 * u, cy + 0.65 * u)
                    cr.stroke(); rx -= 2 * u
                if r.get("value"):
                    cr.set_font_size(1.45 * u); e = cr.text_extents(r["value"])
                    self.text(cr, r["value"], rx - e.x_advance, cy + 0.5 * u, 1.45 * u, (0.64, 0.64, 0.67, 1))
                    rx -= e.x_advance + 1.2 * u
                if r.get("pill"):
                    label, kind = r["pill"]
                    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(1.15 * u)
                    e = cr.text_extents(label); pw_ = e.x_advance + 1.8 * u
                    rounded(cr, rx - pw_, cy - 1.1 * u, pw_, 2.2 * u, 1.1 * u)
                    fill, ink = {"warn": ((0.965, 0.698, 0.235, 0.16), (0.965, 0.698, 0.235)),
                                 "ok": ((0.714, 0.827, 0.318, 0.14), (0.714, 0.827, 0.318))}.get(
                        kind, ((1, 1, 1, 0.08), (0.87, 0.87, 0.87)))
                    cr.set_source_rgba(*fill); cr.fill()
                    cr.set_source_rgb(*ink); cr.move_to(rx - pw_ + 0.9 * u, cy + 0.42 * u); cr.show_text(label)
                    rx -= pw_ + 1 * u
            room = rx - tx - 1 * u
            self.text(cr, r["t"], tx, y + 2.9 * u, 1.7 * u, (1, 1, 1, 1), width=room)
            if r.get("s"):
                self.text(cr, r["s"], tx, y + 5 * u, 1.25 * u, (0.64, 0.64, 0.67, 1), width=room)
            if r.get("bars"):
                by = y + 4.7 * u
                for left, right, frac in r["bars"]:
                    bw = room
                    self.text(cr, left, tx, by + 0.9 * u, 1.2 * u, (0.74, 0.74, 0.76, 1))
                    cr.set_font_size(1.2 * u); e = cr.text_extents(right)
                    self.text(cr, right, tx + bw - e.x_advance, by + 0.9 * u, 1.2 * u, (0.74, 0.74, 0.76, 1))
                    rounded(cr, tx, by + 1.4 * u, bw, 0.9 * u, 0.45 * u); cr.set_source_rgba(1, 1, 1, 0.08); cr.fill()
                    rounded(cr, tx, by + 1.4 * u, max(0.9 * u, bw * frac), 0.9 * u, 0.45 * u)
                    neon(cr, 1.0, (tx, by, tx + bw, by + 2 * u)); cr.fill()
                    by += 2.9 * u
            y += h + 0.6 * u

    def draw_view(self, cr, px, py, pw, ph, u):
        if self.view[0] == "fixed":
            self.text(cr, "Prism fixed recently", px + 3 * u, py + 4 * u, 2 * u, (1, 1, 1, 1), bold=True)
            y = py + 7.5 * u
            for item in __import__("prism_notice").fixed_recently()[:7]:
                self.text(cr, item["text"], px + 3 * u, y, 1.45 * u, (1, 1, 1, 1), width=pw - 6 * u)
                self.text(cr, ago(item["t"]).capitalize(), px + 3 * u, y + 1.9 * u, 1.2 * u, (0.64, 0.64, 0.67, 1))
                y += 4.6 * u
            return
        _, key, url, pb = self.view
        title = pf.EXTRAS[key][0]
        qs = min(22 * u, ph - 6 * u)
        qx, qy = px + 3 * u, py + (ph - qs) / 2
        rounded(cr, qx, qy, qs, qs, 1 * u); cr.set_source_rgb(0.953, 0.953, 0.953); cr.fill()
        if pb:
            inner = qs - 2.4 * u
            cr.save(); cr.translate(qx + 1.2 * u, qy + 1.2 * u); cr.scale(inner / pb.get_width(), inner / pb.get_height())
            Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0); cr.get_source().set_filter(cairo.FILTER_NEAREST); cr.paint()
            cr.restore()
        tx, tw = qx + qs + 4.5 * u, px + pw - (qx + qs + 4.5 * u) - 3 * u
        y = qy + 3.4 * u
        self.text(cr, f"Set up {title}", tx, y, 2.4 * u, (1, 1, 1, 1), bold=True)
        y = self.para(cr, "Scan with your phone, or open this on any computer on your Wi-Fi, and fill in the details:",
                      tx, y + 3.2 * u, tw, 1.4 * u, (0.74, 0.74, 0.74, 1)) + 0.6 * u
        addr = url.replace("http://", "")
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); cr.set_font_size(1.5 * u)
        e = cr.text_extents(addr)
        cr.move_to(tx, y); cr.text_path(addr); neon(cr, 1.0, (tx, y - e.height, tx + e.x_advance, y)); cr.fill()
        import prism_addgames as ag
        if key in ag.state.get("setup_done", []):
            self.text(cr, f"{title} is set up and on.", tx, y + 3.4 * u, 1.45 * u, (0.714, 0.827, 0.318, 1), bold=True)

    # ---- small drawing helpers ------------------------------------------------------------------------------------
    def text(self, cr, s, x, y, size, rgba, bold=False, width=None):
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(size)
        if width:
            while cr.text_extents(s).x_advance > width and len(s) > 3:
                s = s[:-2].rstrip() + "…"
        cr.set_source_rgba(*rgba); cr.move_to(x, y); cr.show_text(s)

    def para(self, cr, text, x, y, width, size, rgba):
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(size)
        cr.set_source_rgba(*rgba); line = ""
        for wd in text.split():
            t = (line + " " + wd).strip()
            if cr.text_extents(t).x_advance > width and line:
                cr.move_to(x, y); cr.show_text(line); y += size * 1.5; line = wd
            else:
                line = t
        if line:
            cr.move_to(x, y); cr.show_text(line); y += size * 1.5
        return y

    def badge(self, cr, x, cy, letter, u):
        cr.new_path(); cr.arc(x + 1.2 * u, cy, 1.2 * u, 0, 2 * math.pi); cr.set_source_rgb(0.85, 0.85, 0.85); cr.fill()
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); cr.set_font_size(1.25 * u)
        e = cr.text_extents(letter); cr.set_source_rgb(0.12, 0.12, 0.13)
        cr.move_to(x + 1.2 * u - e.x_advance / 2 - e.x_bearing / 2, cy + e.height / 2); cr.show_text(letter)

    def icon(self, cr, name, cx, cy, u):
        cr.save(); cr.translate(cx, cy); cr.scale(u / 12, u / 12)       # drawn on a 24-unit grid, ~1.55 units tall
        cr.translate(-12, -12); cr.set_source_rgb(1, 1, 1); cr.set_line_width(2.2)
        cr.set_line_cap(cairo.LINE_CAP_ROUND); cr.set_line_join(cairo.LINE_JOIN_ROUND)
        if name == "Games":
            for x, y in ((4, 4), (13, 4), (4, 13), (13, 13)):
                rounded(cr, x, y, 7, 7, 1.5)
            cr.stroke()
        elif name == "Extras":
            for a, b, c, d in ((12, 3, 12, 7), (12, 17, 12, 21), (3, 12, 7, 12), (17, 12, 21, 12), (6, 6, 8.5, 8.5),
                               (15.5, 15.5, 18, 18), (6, 18, 8.5, 15.5), (15.5, 8.5, 18, 6)):
                cr.move_to(a, b); cr.line_to(c, d)
            cr.stroke()
        elif name == "Feel":
            cr.move_to(3, 12); cr.curve_to(5, 8, 7, 8, 9, 12); cr.curve_to(11, 16, 13, 16, 15, 12)
            cr.curve_to(17, 8, 19, 8, 21, 12); cr.stroke()
        elif name == "Controllers":
            cr.move_to(7, 9); cr.line_to(17, 9); cr.curve_to(20, 9, 21.5, 12, 20.9, 13.9); cr.line_to(20.1, 17.2)
            cr.curve_to(19.7, 18.9, 17.8, 19.3, 16.6, 17.9); cr.line_to(14.8, 16); cr.line_to(9.2, 16); cr.line_to(7.4, 17.9)
            cr.curve_to(6.2, 19.3, 4.3, 18.9, 3.9, 17.2); cr.line_to(3.1, 13.9); cr.curve_to(2.5, 12, 4, 9, 7, 9)
            cr.close_path(); cr.stroke()
        else:
            cr.new_path(); cr.arc(12, 12, 8.5, 0, 2 * math.pi); cr.stroke()
            cr.move_to(12, 11); cr.line_to(12, 16); cr.move_to(12, 8); cr.line_to(12, 8.01); cr.stroke()
        cr.restore()


def sound(name):
    p = f"{prism_config.THEME_DIR}/switch/sounds/{name}.wav"
    if os.path.exists(p) and pf.feel("menu_sounds"):
        subprocess.Popen(["pw-play", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def pad_thread(app, devs):
    for d in devs:
        try:
            d.grab()
        except OSError:
            pass
    axes = {}
    while app.answer is None:
        try:
            r, _, _ = select.select(devs, [], [], 0.2)
        except (OSError, ValueError):
            break
        for d in r:
            try:
                for ev in d.read():
                    name = None
                    if ev.type == E.EV_KEY and ev.value == 1:
                        code = ev.code
                        if d.info.vendor == 0x057E and code in (E.BTN_SOUTH, E.BTN_EAST):   # Nintendo: by label
                            code = E.BTN_EAST if code == E.BTN_SOUTH else E.BTN_SOUTH
                        name = {E.BTN_SOUTH: "a", E.BTN_EAST: "b", E.BTN_DPAD_UP: "up", E.BTN_DPAD_DOWN: "down",
                                E.BTN_DPAD_LEFT: "left", E.BTN_DPAD_RIGHT: "right"}.get(code)
                    elif ev.type == E.EV_ABS and ev.code in (E.ABS_HAT0X, E.ABS_HAT0Y) and ev.value:
                        name = {(E.ABS_HAT0X, 1): "right", (E.ABS_HAT0X, -1): "left", (E.ABS_HAT0Y, 1): "down",
                                (E.ABS_HAT0Y, -1): "up"}.get((ev.code, 1 if ev.value > 0 else -1))
                    elif ev.type == E.EV_ABS and ev.code in (E.ABS_X, E.ABS_Y):
                        v = 1 if ev.value > 20000 else -1 if ev.value < -20000 else 0
                        k = (d.path, ev.code)
                        if v and v != axes.get(k, 0):
                            name = {(E.ABS_X, 1): "right", (E.ABS_X, -1): "left", (E.ABS_Y, 1): "down",
                                    (E.ABS_Y, -1): "up"}[(ev.code, v)]
                        axes[k] = v
                    if name:
                        GLib.idle_add(app.press, name)
            except OSError:
                pass
    time.sleep(0.25)
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
    why = " ".join(sys.argv[1:]) or "by hand"
    import prism_state as ps
    if ps.card_up():
        return
    app = Settings()
    app.after = None
    win = Gtk.Window(title="Prism Settings"); win.set_decorated(False); win.fullscreen()
    area = Gtk.DrawingArea(); area.connect("draw", app.draw); win.add(area)

    def tag(*_):
        appid = os.environ.get("SteamAppId")
        if not appid:
            try:
                import deck_curate as d
                pid = d.esde_pid()
                appid = d.esde_env(pid).get("SteamAppId") if pid else None
            except Exception:
                appid = None
        if appid:
            subprocess.Popen(["xprop", "-id", str(win.get_window().get_xid()), "-f", "STEAM_GAME", "32c", "-set",
                              "STEAM_GAME", appid])
    win.connect("realize", tag)
    GLib.timeout_add(33, lambda: (area.queue_draw(), True)[1])

    def test_input():          # self-tests / agents: key names (up down left right a b), one per line
        try:
            names = open(TEST_INPUT).read().split()
            os.remove(TEST_INPUT)
        except OSError:
            return True
        for i, n in enumerate(names):
            GLib.timeout_add(150 * i, lambda n=n: (app.last_act.pop(n, None), app.press(n), False)[2])
        return True
    GLib.timeout_add(100, test_input)
    GLib.timeout_add_seconds(600, lambda: (setattr(app, "answer", "CLOSE"), Gtk.main_quit(), False)[2])
    win.show_all()
    try:
        open("/tmp/prism_card_up", "w").write(str(os.getpid()))
    except OSError:
        pass
    threading.Thread(target=pad_thread, args=(app, pads()), daemon=True).start()
    log(f"open ({why})")
    Gtk.main()
    if app.view:
        app.close_view()
    win.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()
    try:
        if open("/tmp/prism_card_up").read().strip() == str(os.getpid()):
            os.remove("/tmp/prism_card_up")
    except OSError:
        pass
    if app.after:
        time.sleep(0.3)
        fn, a = app.after
        fn(*a)
    log("closed")


if __name__ == "__main__":
    main()
