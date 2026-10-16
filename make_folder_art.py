#!/usr/bin/env python3
"""Prism-style cards for the Movies / TV folders (2026-10-09: the old cyan cards looked underwhelming):
Continue Watching, Watch Later, Discover and its shelves. Each card shows the folder's own posters fanned on a glass
card with a neon badge, a title and a neon line (Continue Watching's line reads as a progress bar). Rebuilt by
plex_sync.folder_art() whenever Plex syncs, so the posters follow what's actually in the folder.

  make_folder_art.py            build all cards and write them over covers/<folder>.jpg
  make_folder_art.py preview    also save a contact sheet to /tmp/folder_art_preview.png
"""
import prism_config  # noqa: E402  (paths)
import glob, math, os, subprocess, sys
import cairo
import gi
gi.require_version("GdkPixbuf", "2.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deck_power_menu import neon  # noqa: E402

MEDIA = f"{prism_config.TOOLS}/downloaded_media"
W, H = 600, 900
FONT = "Noto Sans"
CARDS = {   # folder (relative) -> title, subtitle, glyph
    "Continue Watching": ("Continue Watching", "Pick up where you left off", "resume"),
    "Watch Later": ("Watch Later", "Saved for later", "bookmark"),
    "Discover": ("Discover", "Find something new to request", "compass"),
    "Discover/Trending now": ("Trending now", "What everyone's watching", "trend"),
    "Discover/New & popular": ("New & popular", "Fresh and loved", "flame"),
    "Discover/Just released": ("Just released", "Out this week", "spark"),
    "Discover/Coming soon": ("Coming soon", "On the way", "calendar"),
    "Discover/All-time popular": ("All-time popular", "The classics", "star"),
    "Discover/Search and request": ("Search & request", "Find any title", "search"),
}


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0); cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi); cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def posters(system, rel):
    base = f"{MEDIA}/{system}/covers/{rel}"
    if rel == "Discover":                  # the top folder holds the shelf cards: borrow real posters from the shelves
        files = []
        for shelf in ("Trending now", "New & popular", "Just released"):
            files += sorted(glob.glob(f"{base}/{shelf}/*.jpg"), key=lambda f: -os.path.getmtime(f))[:1]
    else:
        files = [f for f in glob.glob(f"{base}/*") if f.lower().endswith((".jpg", ".png", ".webp")) and os.path.isfile(f)]
        files.sort(key=lambda f: -os.path.getmtime(f))
    out = []
    for f in files:
        try:
            out.append(GdkPixbuf.Pixbuf.new_from_file_at_scale(f, 400, 600, False))
        except Exception:
            continue
        if len(out) == 3:
            break
    return out


def paint_poster(cr, pb, x, y, w, h, r):
    cr.save(); rounded(cr, x, y, w, h, r); cr.clip()
    sc = max(w / pb.get_width(), h / pb.get_height())
    cr.translate(x + (w - pb.get_width() * sc) / 2, y + (h - pb.get_height() * sc) / 2); cr.scale(sc, sc)
    Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0); cr.paint(); cr.restore()


def glyph(cr, kind, cx, cy, u):
    cr.set_source_rgb(1, 1, 1); cr.set_line_width(5.5); cr.set_line_cap(cairo.LINE_CAP_ROUND); cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if kind == "resume":
        cr.new_path(); cr.arc(cx, cy, u * 0.62, math.radians(-60), math.radians(250)); cr.stroke()
        cr.move_to(cx - u * 0.18, cy - u * 0.30); cr.line_to(cx + u * 0.32, cy); cr.line_to(cx - u * 0.18, cy + u * 0.30); cr.close_path(); cr.fill()
    elif kind == "bookmark":
        cr.move_to(cx - u * 0.35, cy - u * 0.55); cr.line_to(cx + u * 0.35, cy - u * 0.55); cr.line_to(cx + u * 0.35, cy + u * 0.55)
        cr.line_to(cx, cy + u * 0.28); cr.line_to(cx - u * 0.35, cy + u * 0.55); cr.close_path(); cr.fill()
    elif kind == "compass":
        cr.new_path(); cr.arc(cx, cy, u * 0.62, 0, 2 * math.pi); cr.stroke()
        cr.move_to(cx + u * 0.30, cy - u * 0.30); cr.line_to(cx + u * 0.09, cy + u * 0.09); cr.line_to(cx - u * 0.09, cy - u * 0.09); cr.close_path(); cr.fill()
        cr.move_to(cx - u * 0.30, cy + u * 0.30); cr.line_to(cx - u * 0.09, cy - u * 0.09); cr.line_to(cx + u * 0.09, cy + u * 0.09); cr.close_path()
        cr.set_source_rgba(1, 1, 1, 0.55); cr.fill()
    elif kind == "trend":
        cr.move_to(cx - u * 0.6, cy + u * 0.35); cr.line_to(cx - u * 0.15, cy - u * 0.05); cr.line_to(cx + u * 0.1, cy + u * 0.18)
        cr.line_to(cx + u * 0.55, cy - u * 0.35); cr.stroke()
        cr.move_to(cx + u * 0.2, cy - u * 0.38); cr.line_to(cx + u * 0.58, cy - u * 0.38); cr.line_to(cx + u * 0.58, cy); cr.stroke()
    elif kind == "flame":
        cr.move_to(cx, cy - u * 0.65)
        cr.curve_to(cx + u * 0.55, cy - u * 0.15, cx + u * 0.55, cy + u * 0.55, cx, cy + u * 0.6)
        cr.curve_to(cx - u * 0.55, cy + u * 0.55, cx - u * 0.5, cy, cx - u * 0.1, cy - u * 0.2)
        cr.curve_to(cx - u * 0.05, cy + u * 0.05, cx + u * 0.1, cy - u * 0.2, cx, cy - u * 0.65); cr.close_path(); cr.fill()
    elif kind == "spark":
        for ang, ln in ((0, 0.65), (90, 0.65), (45, 0.35), (135, 0.35)):
            a = math.radians(ang)
            cr.move_to(cx - math.cos(a) * u * ln, cy - math.sin(a) * u * ln); cr.line_to(cx + math.cos(a) * u * ln, cy + math.sin(a) * u * ln); cr.stroke()
    elif kind == "calendar":
        rounded(cr, cx - u * 0.55, cy - u * 0.45, u * 1.1, u * 1.0, u * 0.15); cr.stroke()
        cr.move_to(cx - u * 0.55, cy - u * 0.15); cr.line_to(cx + u * 0.55, cy - u * 0.15); cr.stroke()
        for dx in (-0.25, 0.25):
            cr.move_to(cx + u * dx, cy - u * 0.6); cr.line_to(cx + u * dx, cy - u * 0.32); cr.stroke()
        cr.new_path(); cr.arc(cx + u * 0.18, cy + u * 0.2, u * 0.12, 0, 2 * math.pi); cr.fill()
    elif kind == "star":
        cr.new_path()
        for k in range(10):
            a = -math.pi / 2 + k * math.pi / 5; rr = u * (0.62 if k % 2 == 0 else 0.27)
            (cr.move_to if k == 0 else cr.line_to)(cx + math.cos(a) * rr, cy + math.sin(a) * rr)
        cr.close_path(); cr.fill()
    elif kind == "search":
        cr.new_path(); cr.arc(cx - u * 0.1, cy - u * 0.1, u * 0.38, 0, 2 * math.pi); cr.stroke()
        cr.move_to(cx + u * 0.18, cy + u * 0.18); cr.line_to(cx + u * 0.55, cy + u * 0.55); cr.stroke()


def text(cr, t, cx, y, size, rgba, bold=False):
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size); e = cr.text_extents(t)
    if e.x_advance > W - 60:                                   # long titles: shrink to fit
        cr.set_font_size(size * (W - 60) / e.x_advance); e = cr.text_extents(t)
    cr.set_source_rgba(*rgba); cr.move_to(cx - e.x_advance / 2, y); cr.show_text(t)


def card(system, rel, title, sub, kind):
    s = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H); cr = cairo.Context(s)
    g = cairo.LinearGradient(0, 0, 0, H)                      # charcoal, a breath lighter at the top
    g.add_color_stop_rgb(0, 0.15, 0.15, 0.14); g.add_color_stop_rgb(1, 0.09, 0.09, 0.085); cr.set_source(g); cr.paint()
    cr.save()                                                 # soft spectrum glow behind the posters
    rg = cairo.RadialGradient(W / 2, 330, 20, W / 2, 330, 420)
    rg.add_color_stop_rgba(0, 0.16, 0.71, 0.84, 0.20); rg.add_color_stop_rgba(0.55, 0.94, 0.26, 0.44, 0.07); rg.add_color_stop_rgba(1, 0, 0, 0, 0)
    cr.set_source(rg); cr.paint(); cr.restore()
    pbs = posters(system, rel)
    pw, ph = 230, 345
    if pbs:
        slots = [(-1, -10, 0.88), (1, 10, 0.88), (0, 0, 1.0)][:len(pbs)] if len(pbs) > 1 else [(0, 0, 1.0)]
        order = sorted(range(len(slots)), key=lambda i: slots[i][0] == 0)        # centre poster drawn last
        for i in order:
            dx, ang, sc = slots[i]; pb = pbs[i]
            w, h = pw * sc, ph * sc
            cr.save(); cr.translate(W / 2 + dx * 150, 330 + abs(dx) * 14); cr.rotate(math.radians(ang))
            for k in range(6):                                # layered soft shadow
                cr.set_source_rgba(0, 0, 0, 0.07); rounded(cr, -w / 2 - k * 2 + 6, -h / 2 - k * 2 + 14, w + k * 4, h + k * 4, 16 + k); cr.fill()
            paint_poster(cr, pb, -w / 2, -h / 2, w, h, 14)
            cr.set_source_rgba(1, 1, 1, 0.18); cr.set_line_width(1.5); rounded(cr, -w / 2, -h / 2, w, h, 14); cr.stroke()
            cr.restore()
        bx, by, br = W / 2 + 118, 330 + ph / 2 - 6, 46
    else:
        bx, by, br = W / 2, 330, 96
    cr.new_path(); cr.arc(bx, by + 4, br + 6, 0, 2 * math.pi); cr.set_source_rgba(0, 0, 0, 0.35); cr.fill()
    cr.new_path(); cr.arc(bx, by, br, 0, 2 * math.pi); neon(cr); cr.fill()
    cr.save(); cr.translate(bx, by); cr.scale(br / 46, br / 46); glyph(cr, kind, 0, 0, 46); cr.restore()
    text(cr, title, W / 2, 685, 50, (1, 1, 1, 1))
    text(cr, sub, W / 2, 735, 25, (0.70, 0.70, 0.73, 1))
    bw = 220; bx0 = W / 2 - bw / 2                             # neon line (Continue Watching: a progress bar)
    cr.set_source_rgba(1, 1, 1, 0.14); rounded(cr, bx0, 785, bw, 6, 3); cr.fill()
    fill = bw * (0.62 if kind == "resume" else 1.0)
    rounded(cr, bx0, 785, fill, 6, 3); neon(cr, 1.0, (bx0, 785, bx0 + bw, 791)); cr.fill()
    cr.set_source_rgba(1, 1, 1, 0.16); cr.set_line_width(2); rounded(cr, 1, 1, W - 2, H - 2, 26); cr.stroke()
    return s


def build(preview=False):
    done, sheet = 0, []
    for system in ("movies", "tv"):
        for rel, (title, sub, kind) in CARDS.items():
            if not os.path.isdir(f"{MEDIA}/{system}/covers/{os.path.dirname(rel) or '.'}"):
                continue
            surf = card(system, rel, title, sub, kind)
            png = f"/tmp/_fa_{system}_{done}.png"; surf.write_to_png(png)
            dst = f"{MEDIA}/{system}/covers/{rel}.jpg"
            for ext in (".jpg", ".png", ".webp"):             # never write through a hard link to a real poster
                try:
                    os.remove(f"{MEDIA}/{system}/covers/{rel}{ext}")
                except OSError:
                    pass
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", png, "-q:v", "3", dst], check=True)
            sheet.append(png); done += 1
    if preview:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y"] + sum([["-i", p] for p in sheet[:9]], []) +
                       ["-filter_complex", "".join(f"[{i}]scale=200:300[s{i}];" for i in range(min(9, len(sheet)))) +
                        "".join(f"[s{i}]" for i in range(min(9, len(sheet)))) + f"hstack=inputs={min(9, len(sheet))}",
                        "/tmp/folder_art_preview.png"], check=True)
    print(f"folder cards: {done}")
    return done


if __name__ == "__main__":
    build("preview" in sys.argv)
