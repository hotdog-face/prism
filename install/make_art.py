#!/usr/bin/env python3
"""Steam library art for the "Prism" shortcut (install/art/): portrait 600x900, wide 920x430, hero 1920x620 and a
transparent logo, from the theme's wave background and Prism's wordmark. Run once when the art changes:
    python3 install/make_art.py"""
import os, sys
import cairo
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import prism_config  # noqa: E402

BG = next(p for p in (f"{ROOT}/theme/prism/switch/ambient_still.png",
                      f"{prism_config.THEME_DIR}/switch/ambient_still.png") if os.path.exists(p))
WORD = f"{ROOT}/prism_wordmark.png"
NEON = ((0xF0, 0x43, 0x6F), (0xF6, 0x9F, 0x3C), (0xFB, 0xE0, 0x1A), (0xB6, 0xD3, 0x51), (0x27, 0xB6, 0xD5), (0x29, 0xAB, 0xEB))


def cover(cr, pb, W, H, focus_y=0.62):
    k = max(W / pb.get_width(), H / pb.get_height())
    cr.save()
    cr.translate((W - pb.get_width() * k) / 2, (H - pb.get_height() * k) * focus_y)
    cr.scale(k, k); Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0); cr.paint(); cr.restore()


def line(cr, x, y, w, h):
    g = cairo.LinearGradient(x, y, x + w, y)
    for i, c in enumerate(NEON):
        g.add_color_stop_rgb(i / (len(NEON) - 1), c[0] / 255, c[1] / 255, c[2] / 255)
    cr.set_source(g); cr.rectangle(x, y, w, h); cr.fill()


def word(cr, W, cy, width):
    pb = GdkPixbuf.Pixbuf.new_from_file(WORD)
    k = width / pb.get_width()
    h = pb.get_height() * k
    cr.save(); cr.translate(W / 2 - width / 2, cy - h / 2); cr.scale(k, k)
    Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0); cr.paint(); cr.restore()
    return h


def make(name, W, H, with_bg=True, word_w=None, word_y=None):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H); cr = cairo.Context(s)
    if with_bg:
        cr.set_source_rgb(0.067, 0.067, 0.062); cr.paint()
        cover(cr, GdkPixbuf.Pixbuf.new_from_file(BG), W, H)
    if word_w:
        h = word(cr, W, word_y, word_w)
        line(cr, W / 2 - word_w * 0.31, word_y + h / 2 + H * 0.025, word_w * 0.62, max(3, H * 0.006))
    os.makedirs(f"{HERE}/art", exist_ok=True)
    s.write_to_png(f"{HERE}/art/{name}.png")
    print("wrote", name)


make("portrait", 600, 900, word_w=420, word_y=330)
make("wide", 920, 430, word_w=520, word_y=170)
make("hero", 1920, 620)
make("logo", 1280, 400, with_bg=False, word_w=1100, word_y=180)
