#!/usr/bin/env python3
"""Console-style full-screen transitions over ES-DE (2026-10-06: "look more like Switch or PS5").

  deck_splash.py launch <rom path> <name> <system>
      PS5/console-style launch screen: the game's art fills the screen (fan art > screenshot > title screen > cover),
      dark gradient, big game logo bottom-left, small cyan spinner. Started by ES-DE's game-start event (ES-DE's own
      "LAUNCHING GAME" box is turned off). The emulator's window takes over the screen when it appears; this window
      then closes itself (or after 12 s).
  deck_splash.py home
      Steam-button HOME transition: the current picture shrinks and fades to dark (console-style) and stays until
      /tmp/deck_splash_done appears (deck_curate.quit_game touches it once ES-DE is back), then fades away.

Windows carry ES-DE's STEAM_GAME id so gamescope shows them over ES-DE.
"""
import prism_config  # noqa: E402  (paths)
import glob, math, os, subprocess, sys, time

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

MEDIA = f"{prism_config.TOOLS}/downloaded_media"
SHOTS = f"{prism_config.PRISM}/resume_shots"     # last frame of each game left via HOME (for "Resuming")
NO_RESUME = {"switch", "psvita", "xbox360", "n3ds", "scummvm", "steam", "media", "foryou", "ps3", "wiiu", "ps4", "model2"}
DONE = "/tmp/deck_splash_done"
READY = "/tmp/prism_ready"     # touched by ~/ES-DE/scripts/startup/prism-ready.sh (ES-DE has loaded)
CYAN = (0x27 / 255, 0xB6 / 255, 0xD5 / 255)   # palette cyan (2026-10-08)
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

FONT = "Noto Sans"


def media(system, rom, kinds):
    base = os.path.basename(rom.rstrip("/"))
    stems = [base, os.path.splitext(base)[0]]   # folder entries (Game.m3u) keep the full name
    for kind in kinds:
        for stem in stems:
            for f in glob.glob(f"{MEDIA}/{system}/{kind}/{glob.escape(stem)}.*"):
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    return f
    return None


def capture(path):
    try:
        if os.path.exists(path):      # gamescope writes the file a moment later: don't take an old one for it
            os.remove(path)
        subprocess.run(["gamescopectl", "screenshot", path], timeout=4, capture_output=True)
        for _ in range(30):
            if os.path.exists(path) and os.path.getsize(path) > 0:
                time.sleep(0.1)
                return path
            time.sleep(0.04)
    except Exception:
        pass
    return None


def sound(name):
    p = os.path.expanduser(f"{prism_config.THEME_DIR}/switch/sounds/{name}.wav")
    if os.path.exists(p):
        subprocess.Popen(["pw-play", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load(path, w=None):
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
        if w and pb.get_width() > w:
            pb = pb.scale_simple(w, int(pb.get_height() * w / pb.get_width()), GdkPixbuf.InterpType.BILINEAR)
        return pb
    except Exception:
        return None


class Splash(Gtk.Window):
    def __init__(self, mode, rom="", name="", system=""):
        super().__init__(title="Splash")
        self.mode, self.name = mode, name
        self.resuming = False; self.saved_at = None
        self.t0 = time.monotonic(); self.closing = None
        self.set_decorated(False)
        self.fullscreen()
        area = Gtk.DrawingArea(); area.connect("draw", self.draw); self.add(area); self.area = area
        self.connect("realize", self.tag)
        if mode == "launch":
            shot = f"{SHOTS}/{system}/{os.path.basename(rom.rstrip('/'))}.png"
            if (system not in NO_RESUME or system in ("switch", "psvita")) and os.path.exists(shot):
                self.bg = load(shot, 1280); self.resuming = self.bg is not None
                self.resume_label = "Resuming" if system not in NO_RESUME else "Last played"
            else:
                self.bg = load(media(system, rom, ("fanart", "screenshots", "titlescreens", "covers")) or "", 1280)
            self.logo = load(media(system, rom, ("marquees",)) or "", 900)
            self.cover = None if self.bg else load(media(system, rom, ("covers",)) or "", 600)
        elif mode == "boot":   # Prism startup (2026-10-09): carries on from the boot video - home wave + wordmark
            self.bg = load(f"{prism_config.THEME_DIR}/switch/ambient_still.png", 1280)
            try:
                self.logo = GdkPixbuf.Pixbuf.new_from_file(f"{prism_config.PRISM}/prism_wordmark.png")
            except Exception:
                self.logo = None
            self.cover = None
        else:
            given = os.environ.get("DECK_SPLASH_SHOT", "")
            shot = given if given and os.path.exists(given) else capture("/tmp/deck_splash_home.png")
            self.bg = load(shot) if shot else None
            self.logo = self.cover = None
            if shot and system and rom and system not in NO_RESUME:   # remember where you left off
                os.makedirs(f"{SHOTS}/{system}", exist_ok=True)
                subprocess.run(["cp", shot, f"{SHOTS}/{system}/{os.path.basename(rom.rstrip('/'))}.png"])
        self.wall0 = time.time()
        GLib.timeout_add(16, self.tick)

    def tag(self, *_):
        appid = os.environ.get("SteamAppId")
        if appid:
            subprocess.Popen(["xprop", "-id", str(self.get_window().get_xid()), "-f", "STEAM_GAME", "32c", "-set",
                              "STEAM_GAME", appid])
        if self.mode == "boot":   # above Prism's own (black while loading) window until Prism says it is ready
            subprocess.Popen(["xprop", "-id", str(self.get_window().get_xid()), "-f", "STEAM_OVERLAY", "32c", "-set",
                              "STEAM_OVERLAY", "1"])

    def close_soon(self):
        if self.closing is None:
            self.closing = time.monotonic()

    def tick(self):
        self.area.queue_draw()
        now = time.monotonic()
        if self.closing is None:
            if self.mode == "home" and os.path.exists(DONE):
                if self.saved_at is None:
                    self.saved_at = now          # show "Saved" for a moment before lifting the cover
                    sound("cc_choose")
                elif now - self.saved_at > 0.8:
                    self.close_soon()
            if self.mode == "boot":   # ES-DE's startup event script touches READY once Prism has loaded
                try:
                    ready = os.path.getmtime(READY) > self.wall0
                except OSError:
                    ready = False
                if ready and getattr(self, "ready_at", None) is None:
                    self.ready_at = now
                if getattr(self, "ready_at", None) is not None and now - self.ready_at > 0.6:
                    self.close_soon()
            if now - self.t0 > {"launch": 12, "boot": 25}.get(self.mode, 30):   # home: closing can take a while
                self.close_soon()
        elif now - self.closing > (0.5 if self.mode == "boot" else 0.18):
            Gtk.main_quit(); return False
        return True

    def paint_cover(self, cr, pb, W, H, scale=1.0, alpha=1.0):
        s = max(W / pb.get_width(), H / pb.get_height()) * scale
        w, h = pb.get_width() * s, pb.get_height() * s
        cr.save(); cr.translate((W - w) / 2, (H - h) / 2); cr.scale(s, s)
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0); cr.paint_with_alpha(alpha); cr.restore()

    def draw(self, w, cr):
        W, H = w.get_allocated_width(), w.get_allocated_height(); s = H / 800
        t = time.monotonic() - self.t0
        fade_in = min(1.0, t / 0.2)
        fade_out = 1.0 - min(1.0, (time.monotonic() - self.closing) / (0.5 if self.mode == "boot" else 0.18)) if self.closing else 1.0
        cr.set_source_rgb(0, 0, 0); cr.paint()
        if self.mode == "boot":   # Prism startup: the home wave, the PRISM wordmark and a thin spectrum line (no spinner)
            a = fade_in * fade_out
            if self.bg:
                cr.save(); cr.scale(W / self.bg.get_width(), H / self.bg.get_height())
                Gdk.cairo_set_source_pixbuf(cr, self.bg, 0, 0); cr.paint_with_alpha(a); cr.restore()
            if self.logo:
                lw, lh = self.logo.get_width() * s, self.logo.get_height() * s
                cr.save(); cr.translate(W / 2 - lw / 2, 300 * s); cr.scale(s, s)
                Gdk.cairo_set_source_pixbuf(cr, self.logo, 0, 0); cr.paint_with_alpha(a); cr.restore()
                bar = lw * 0.62; by = 300 * s + lh + 30 * s
                cr.set_source_rgba(1, 1, 1, 0.12 * a); cr.rectangle(W / 2 - bar / 2, by, bar, 2.5 * s); cr.fill()
                frac = 1 - math.exp(-t / 2.5)                  # creeps towards full while Prism loads
                neon(cr, a); cr.rectangle(W / 2 - bar / 2, by, bar * frac, 2.5 * s); cr.fill()
            return False
        if self.mode == "home":
            k = min(1.0, t / 0.28); ease = 1 - (1 - k) ** 3
            if self.bg:
                sc = 1.0 - 0.12 * ease
                w2, h2 = W * sc, H * sc
                cr.save(); cr.translate((W - w2) / 2, (H - h2) / 2)
                r = 26 * s * ease
                cr.new_sub_path()
                cr.arc(w2 - r, r, r, -math.pi / 2, 0); cr.arc(w2 - r, h2 - r, r, 0, math.pi / 2)
                cr.arc(r, h2 - r, r, math.pi / 2, math.pi); cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
                cr.close_path(); cr.clip()
                cr.scale(sc * W / self.bg.get_width(), sc * H / self.bg.get_height())
                Gdk.cairo_set_source_pixbuf(cr, self.bg, 0, 0); cr.paint_with_alpha((1 - 0.85 * ease) * fade_out)
                cr.restore()
            if self.saved_at is None and t > 0.2:   # "Saving…": spinner, label and a progress bar while it closes
                k3 = min(1.0, (t - 0.2) / 0.25)
                ang = t * 5.5; cr.set_line_width(5 * s); cr.set_line_cap(cairo.LINE_CAP_ROUND)
                neon(cr, k3); cr.new_path(); cr.arc(W / 2, H / 2 - 10 * s, 26 * s, ang, ang + 4.4); cr.stroke()
                cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(24 * s)
                txt = "Saving your game" + "." * (1 + int(t * 2) % 3)
                e = cr.text_extents("Saving your game..."); cr.set_source_rgba(1, 1, 1, k3)
                cr.move_to(W / 2 - e.x_advance / 2, H / 2 + 52 * s); cr.show_text(txt)
                bw = 260 * s; bx = W / 2 - bw / 2; by = H / 2 + 74 * s
                frac = 1 - math.exp(-(t - 0.2) / 3.0)          # creeps toward full; jumps to done on "Saved"
                cr.set_source_rgba(1, 1, 1, 0.15 * k3); cr.rectangle(bx, by, bw, 4 * s); cr.fill()
                neon(cr, k3); cr.rectangle(bx, by, bw * frac, 4 * s); cr.fill()
            if self.saved_at is not None:   # "Saved" check
                k2 = min(1.0, (time.monotonic() - self.saved_at) / 0.2) * fade_out
                neon(cr, k2); cr.new_path(); cr.arc(W / 2, H / 2 - 10 * s, 30 * s, 0, 2 * math.pi); cr.fill()
                cr.set_source_rgba(1, 1, 1, k2); cr.set_line_width(5 * s); cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.move_to(W / 2 - 13 * s, H / 2 - 10 * s); cr.line_to(W / 2 - 3 * s, H / 2); cr.line_to(W / 2 + 14 * s, H / 2 - 22 * s); cr.stroke()
                cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(24 * s)
                e = cr.text_extents("Saved"); cr.move_to(W / 2 - e.x_advance / 2, H / 2 + 52 * s); cr.show_text("Saved")
            return False
        alpha = fade_in * fade_out
        if self.bg and self.resuming:   # the frame you left on, gently dimmed
            self.paint_cover(cr, self.bg, W, H, 1.0, alpha * 0.85)
        elif self.bg:
            self.paint_cover(cr, self.bg, W, H, 1.0 + 0.03 * min(1, t / 6), alpha)   # slow Ken Burns push
        elif self.cover:
            ch = H * 0.62; cw = self.cover.get_width() * ch / self.cover.get_height()
            cr.save(); cr.translate(W / 2 - cw / 2, H * 0.14); cr.scale(ch / self.cover.get_height(), ch / self.cover.get_height())
            Gdk.cairo_set_source_pixbuf(cr, self.cover, 0, 0); cr.paint_with_alpha(alpha); cr.restore()
        # bottom-left gradient for the logo
        g = cairo.LinearGradient(0, H * 0.35, 0, H)
        g.add_color_stop_rgba(0, 0, 0, 0, 0); g.add_color_stop_rgba(1, 0, 0, 0, 0.88 * alpha)
        cr.set_source(g); cr.rectangle(0, 0, W, H); cr.fill()
        x0, y0 = 70 * s, H - 70 * s
        if self.logo:
            lw = min(self.logo.get_width(), 520 * s); lh = self.logo.get_height() * lw / self.logo.get_width()
            if lh > 200 * s:
                lh = 200 * s; lw = self.logo.get_width() * lh / self.logo.get_height()
            cr.save(); cr.translate(x0, y0 - lh); cr.scale(lw / self.logo.get_width(), lh / self.logo.get_height())
            Gdk.cairo_set_source_pixbuf(cr, self.logo, 0, 0); cr.paint_with_alpha(alpha); cr.restore()
        else:
            cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(46 * s); cr.set_source_rgba(1, 1, 1, alpha)
            cr.move_to(x0, y0); cr.show_text(self.name)
        if self.resuming:   # "Resuming" above the logo
            cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(20 * s); neon(cr, alpha)
            ly = y0 - (min(self.logo.get_height() * min(self.logo.get_width(), 520 * s) / self.logo.get_width(), 200 * s) if self.logo else 46 * s)
            cr.move_to(x0, ly - 16 * s); cr.show_text(getattr(self, "resume_label", "Resuming"))
        # spinner bottom-right
        a = t * 5
        cr.set_line_width(4 * s); cr.set_line_cap(cairo.LINE_CAP_ROUND)
        neon(cr, alpha)
        cr.new_path(); cr.arc(W - 70 * s, H - 70 * s, 16 * s, a, a + 4.4); cr.stroke()
        return False


def emulator_window_up(t0):
    """True once a game window (another X window of ES-DE's display, newer than us) is showing."""
    try:
        out = subprocess.run(["xprop", "-root", "GAMESCOPE_FOCUSED_WINDOW"], env={**os.environ, "DISPLAY": ":0"},
                             capture_output=True, text=True, timeout=2).stdout
        return out.strip().split("=")[-1].strip()
    except Exception:
        return None


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "home"
    if mode == "home":
        try:
            os.remove(DONE)
        except OSError:
            pass
        win = Splash("home", sys.argv[3] if len(sys.argv) > 3 else "", "", sys.argv[2] if len(sys.argv) > 2 else "")
    elif mode == "boot":
        win = Splash("boot")
    else:
        rom, name, system = (sys.argv[2:5] + ["", "", ""])[:3]
        import re
        rom = re.sub(r"\\(.)", r"\1", rom)   # ES-DE passes the path shell-escaped ("\ " for spaces, "\(" ...)
        if not os.path.exists(rom) and rom.startswith('"') and rom.endswith('"'):
            rom = rom[1:-1]
        win = Splash("launch", rom, name, system)
    win.show_all()
    if mode in ("launch", "boot"):
        # once the emulator's (or ES-DE's) window has taken focus (it shows on top), finish quietly behind it
        first = {"v": None}

        def watch():
            f = emulator_window_up(None)
            if first["v"] is None and time.monotonic() - win.t0 > 0.6:
                first["v"] = f
            elif f and first["v"] and f != first["v"]:
                GLib.timeout_add(1500, lambda: win.close_soon() or False)
                return False
            return win.closing is None
        GLib.timeout_add(250, watch)
    Gtk.main()


if __name__ == "__main__":
    main()
