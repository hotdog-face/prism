#!/usr/bin/env python3
"""Home-row tiles (400x400) + header icons (160x160) for Movies / TV Shows / Music / Media, and covers for the
Music row entries - same look as the existing Prism tiles (dark tile, fanned cards, title + subtitle)."""
import glob, hashlib, json, math, os, random, subprocess
import prism_config  # noqa: E402  (paths)
import cairo
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deck_power_menu import neon  # noqa: E402
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

H = os.path.expanduser("~")
THEME = f"{prism_config.THEME_DIR}/switch"
MEDIA = f"{prism_config.TOOLS}/downloaded_media"
CACHE = f"{H}/.cache/deck-music"
FONT = "Noto Sans"
CYAN = (0x27 / 255, 0xB6 / 255, 0xD5 / 255)   # palette cyan; badges use neon() (2026-10-08)


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0); cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi); cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def pix(path, w, h):
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, w, h, False)
    except Exception:
        return None


def paint_img(cr, pb, x, y, w, h, r):
    cr.save(); rounded(cr, x, y, w, h, r); cr.clip()
    sc = max(w / pb.get_width(), h / pb.get_height())
    cr.translate(x + (w - pb.get_width() * sc) / 2, y + (h - pb.get_height() * sc) / 2); cr.scale(sc, sc)
    Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0); cr.paint(); cr.restore()


def text(cr, t, x, y, size, rgb, bold=False):
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size); e = cr.text_extents(t)
    cr.set_source_rgb(*rgb); cr.move_to(x - e.x_advance / 2, y); cr.show_text(t)


def save(surface, out):
    png = "/tmp/_tile.png"; surface.write_to_png(png)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", png, "-quality", "92", out], check=True)
    print("wrote", out)


def background(cr, W):
    g = cairo.LinearGradient(0, 0, 0, W)
    g.add_color_stop_rgb(0, 0.17, 0.17, 0.18); g.add_color_stop_rgb(1, 0.11, 0.11, 0.12)
    cr.set_source(g); cr.paint()


def play_badge(cr, cx, cy, r):
    cr.new_path(); cr.arc(cx, cy, r, 0, 2 * math.pi); neon(cr); cr.fill()
    cr.set_source_rgb(1, 1, 1)
    cr.move_to(cx - r * 0.3, cy - r * 0.42); cr.line_to(cx + r * 0.45, cy); cr.line_to(cx - r * 0.3, cy + r * 0.42)
    cr.close_path(); cr.fill()


def glass_background(cr, W):
    """Glass home tile (2026-10-08): translucent tinted card, soft top sheen, thin light rim (same look as
    glassify_tiles.py gives the console tiles), so the home background shows through."""
    cr.set_source_rgba(64 / 255, 64 / 255, 67 / 255, 0.40); cr.paint()
    g = cairo.LinearGradient(0, 0, 0, W)
    g.add_color_stop_rgba(0, 1, 1, 1, 0.10); g.add_color_stop_rgba(0.25, 1, 1, 1, 0.025); g.add_color_stop_rgba(0.5, 1, 1, 1, 0)
    cr.set_source(g); cr.paint()
    rim = cairo.LinearGradient(0, 0, 0, W)
    rim.add_color_stop_rgba(0, 1, 1, 1, 0.30); rim.add_color_stop_rgba(1, 1, 1, 1, 0.16)
    cr.set_source(rim); cr.set_line_width(1.6); cr.rectangle(0.8, 0.8, W - 1.6, W - 1.6); cr.stroke()


def tile(images, title, sub, out, card=(118, 176)):
    W = 400
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, W); cr = cairo.Context(s)
    glass_background(cr, W)
    cw, ch = card
    for i, (pb, ang) in sorted(enumerate(zip(images, (-9, 0, 9))), key=lambda e: e[0] == 1):   # centre card on top
        cr.save(); cr.translate(W / 2 + (i - 1) * 92, 72 + ch / 2 + abs(i - 1) * 10); cr.rotate(math.radians(ang))
        cr.set_source_rgba(0, 0, 0, 0.45); rounded(cr, -cw / 2 + 4, -ch / 2 + 8, cw, ch, 14); cr.fill()
        if pb:
            paint_img(cr, pb, -cw / 2, -ch / 2, cw, ch, 14)
        else:
            rounded(cr, -cw / 2, -ch / 2, cw, ch, 14); cr.set_source_rgb(0.3, 0.3, 0.32); cr.fill()
        cr.restore()
    play_badge(cr, W / 2 + 128, 72 + ch + 6, 26)
    text(cr, title, W / 2, 330, 40, (1, 1, 1), bold=False)
    text(cr, sub, W / 2, 364, 19, (0.72, 0.72, 0.74))
    save(s, out)


def header(glyph, out):
    W = 160
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, W); cr = cairo.Context(s)
    cr.new_path(); cr.arc(80, 80, 74, 0, 2 * math.pi); cr.set_source_rgb(0.08, 0.08, 0.08); cr.fill()
    cr.new_path(); cr.arc(80, 80, 70, 0, 2 * math.pi); neon(cr); cr.fill()
    cr.set_source_rgb(1, 1, 1); cr.set_line_width(7); cr.set_line_cap(cairo.LINE_CAP_ROUND); cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if glyph == "film":
        rounded(cr, 44, 54, 72, 52, 8); cr.fill()
        cr.set_source_rgb(*CYAN)
        for i in range(4):
            cr.rectangle(50 + i * 17, 58, 8, 6); cr.rectangle(50 + i * 17, 96, 8, 6)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
    elif glyph == "tv":
        rounded(cr, 40, 50, 80, 54, 8); cr.stroke()
        cr.move_to(64, 118); cr.line_to(96, 118); cr.stroke()
    elif glyph == "note":
        cr.move_to(70, 104); cr.line_to(70, 50); cr.line_to(104, 42); cr.line_to(104, 96); cr.stroke()
        cr.new_path(); cr.arc(60, 106, 12, 0, 2 * math.pi); cr.fill()
        cr.new_path(); cr.arc(94, 98, 12, 0, 2 * math.pi); cr.fill()
    elif glyph == "play":
        cr.move_to(66, 52); cr.line_to(110, 80); cr.line_to(66, 108); cr.close_path(); cr.fill()
    save(s, out)


def heart(cr, cx, cy, r):
    cr.new_path(); cr.move_to(cx, cy + r * 0.9)
    cr.curve_to(cx - r * 1.6, cy - r * 0.2, cx - r * 0.7, cy - r * 1.4, cx, cy - r * 0.45)
    cr.curve_to(cx + r * 0.7, cy - r * 1.4, cx + r * 1.6, cy - r * 0.2, cx, cy + r * 0.9)
    cr.close_path(); cr.fill()


def music_art():
    lib = json.load(open(f"{prism_config.PRISM}/music/library.json"))
    def art(thumb, size=300):
        p = f"{CACHE}/{hashlib.md5((str(thumb) + str(size)).encode()).hexdigest()}.jpg"
        return pix(p, 300, 300) if thumb and os.path.exists(p) and os.path.getsize(p) > 1000 else None
    albums = [art(a["thumb"]) for a in lib["albums"] if a["sec"] != "11"]
    albums = [a for a in albums if a]
    artists = [art(a["thumb"]) for a in lib["artists"] if a["sec"] != "11"]
    artists = [a for a in artists if a]
    return albums, artists


def music_covers(albums, artists):
    out = f"{MEDIA}/music/covers"; os.makedirs(out, exist_ok=True)
    W = 600
    def surf():
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, W); return s, cairo.Context(s)
    # 0 Music: red ring + play
    s, cr = surf(); cr.set_source_rgb(0.06, 0.06, 0.06); cr.paint()
    cr.new_path(); cr.arc(300, 300, 190, 0, 2 * math.pi); cr.set_source_rgb(1, 0, 0.2); cr.fill()
    cr.new_path(); cr.arc(300, 300, 112, 0, 2 * math.pi); cr.set_source_rgb(1, 1, 1); cr.set_line_width(18); cr.stroke()
    cr.move_to(270, 245); cr.line_to(355, 300); cr.line_to(270, 355); cr.close_path(); cr.fill()
    s.write_to_png(f"{out}/0 Music.png")
    # 1 Liked Songs
    s, cr = surf(); g = cairo.LinearGradient(0, 0, W, W)
    g.add_color_stop_rgb(0, 0.55, 0.2, 0.9); g.add_color_stop_rgb(1, 0.15, 0.55, 0.95); cr.set_source(g); cr.paint()
    cr.set_source_rgb(1, 1, 1); heart(cr, 300, 320, 150)
    s.write_to_png(f"{out}/1 Liked Songs.png")
    # 2 Albums: 2x2 collage
    s, cr = surf(); cr.set_source_rgb(0.06, 0.06, 0.06); cr.paint()
    for i, pb in enumerate(random.sample(albums, min(4, len(albums)))):
        paint_img(cr, pb, (i % 2) * 300, (i // 2) * 300, 300, 300, 1)
    s.write_to_png(f"{out}/2 Albums.png")
    # 3 Artists: overlapping circles
    s, cr = surf(); cr.set_source_rgb(0.08, 0.08, 0.08); cr.paint()
    for i, pb in enumerate(random.sample(artists, min(3, len(artists)))):
        cx = 170 + i * 130; cy = 300
        cr.save(); cr.new_path(); cr.arc(cx, cy, 150, 0, 2 * math.pi); cr.clip()
        sc = 300 / min(pb.get_width(), pb.get_height())
        cr.translate(cx - pb.get_width() * sc / 2, cy - pb.get_height() * sc / 2); cr.scale(sc, sc)
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0); cr.paint(); cr.restore()
        cr.new_path(); cr.arc(cx, cy, 150, 0, 2 * math.pi); cr.set_source_rgb(0.08, 0.08, 0.08); cr.set_line_width(10); cr.stroke()
    s.write_to_png(f"{out}/3 Artists.png")
    # 4 YouTube Playlist
    s, cr = surf(); g = cairo.LinearGradient(0, 0, W, W)
    g.add_color_stop_rgb(0, 1, 0.1, 0.2); g.add_color_stop_rgb(1, 0.55, 0, 0.1); cr.set_source(g); cr.paint()
    rounded(cr, 130, 180, 340, 240, 60); cr.set_source_rgb(1, 1, 1); cr.fill()
    cr.move_to(265, 240); cr.line_to(355, 300); cr.line_to(265, 360); cr.close_path(); cr.set_source_rgb(1, 0.1, 0.2); cr.fill()
    s.write_to_png(f"{out}/4 YouTube Playlist.png")
    # 5 Now Playing: equalizer
    s, cr = surf(); cr.set_source_rgb(0.06, 0.06, 0.06); cr.paint()
    for i, h in enumerate((140, 260, 200, 320, 170, 240)):
        rounded(cr, 120 + i * 62, 450 - h, 40, h, 14); cr.set_source_rgb(1, 0, 0.2); cr.fill()
    s.write_to_png(f"{out}/5 Now Playing.png")
    print("music covers written")


# Music tile: hand-picked covers with bold art (2026-10-09: no audiobooks / author portraits; Daft Punk plus
# other cool ones). Order = left, centre (front), right.
MUSIC_TILE_PICKS = [("Tame Impala", "Innerspeaker"), ("Daft Punk", "Random Access Memories"), ("Gorillaz", "Plastic Beach")]
NOT_MUSIC = ("Unabridged", "Top 100 Sci-Fi Books", "Cal Newport", "Grant Cardone", "Jen Sincero", "Joe Haldeman", "Kurt Vonnegut",
             "Michael Gerber", "Robert A. Heinlein", "[Unknown Album]")


def music_tile_art():
    lib = json.load(open(f"{prism_config.PRISM}/music/library.json"))
    def art(a):
        p = f"{CACHE}/{hashlib.md5((str(a['thumb']) + '300').encode()).hexdigest()}.jpg"
        return pix(p, 300, 300) if a.get("thumb") and os.path.exists(p) and os.path.getsize(p) > 1000 else None
    picks = []
    for artist, title in MUSIC_TILE_PICKS:
        a = next((x for x in lib["albums"] if x["artist"] == artist and x["title"] == title), None)
        if a and art(a):
            picks.append(art(a))
    if len(picks) < 3:                       # fallback: random real albums, never audiobooks
        pool = [a for a in lib["albums"] if a["sec"] != "11" and not any(k in (a["artist"] + a["title"]) for k in NOT_MUSIC)]
        random.shuffle(pool)
        picks += [p for p in (art(a) for a in pool) if p][: 3 - len(picks)]
    return picks


def main():
    if "music-tile" in _sys.argv:
        tile(music_tile_art(), "Music", "Liked songs, albums & artists", f"{THEME}/tiles/music.webp", card=(150, 150))
        return
    random.seed(7)
    mv = sorted(glob.glob(f"{MEDIA}/movies/covers/*.jpg"))
    picks = [p for p in mv if any(k in p for k in ("Inception", "Interstellar", "Star Wars", "Spirited", "Jurassic",
                                                    "Matrix", "Back to the Future", "Dune", "Toy Story"))][:3] or mv[:3]
    tile([pix(p, 236, 352) for p in picks], "Movies", f"{len(glob.glob(f'{prism_config.ROMS}/movies/*.plex'))} movies from Plex",
         f"{THEME}/tiles/movies.webp")
    tvs = sorted(p for p in glob.glob(f"{MEDIA}/tv/covers/*.jpg"))
    tile([pix(p, 236, 352) for p in tvs[:40:13][:3]], "TV Shows", f"{len([d for d in os.listdir(f'{prism_config.ROMS}/tv')])} shows from Plex",
         f"{THEME}/tiles/tv.webp")
    albums, artists = music_art()
    tile(music_tile_art(), "Music", "Liked songs, albums & artists", f"{THEME}/tiles/music.webp", card=(150, 150))
    music_covers(albums, artists)
    for name, g in (("movies", "film"), ("tv", "tv"), ("music", "note")):
        header(g, f"{THEME}/header/{name}.webp")
    # Media: what's left in it now
    def flat(rgb1, rgb2, label, glyph):
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, 236, 352); cr = cairo.Context(s)
        g = cairo.LinearGradient(0, 0, 0, 352); g.add_color_stop_rgb(0, *rgb1); g.add_color_stop_rgb(1, *rgb2)
        cr.set_source(g); cr.paint(); cr.set_source_rgb(1, 1, 1); cr.set_line_width(10)
        if glyph == "plus":
            rounded(cr, 58, 110, 120, 90, 14); cr.stroke(); cr.move_to(118, 128); cr.line_to(118, 182); cr.move_to(91, 155)
            cr.line_to(145, 155); cr.stroke()
        else:
            cr.move_to(70, 120); cr.line_to(90, 120); cr.line_to(108, 190); cr.line_to(170, 190); cr.line_to(182, 140)
            cr.line_to(98, 140); cr.stroke(); cr.new_path(); cr.arc(114, 212, 10, 0, 7); cr.arc(162, 212, 10, 0, 7); cr.fill()
        cr.select_font_face(FONT, 0, 1); cr.set_font_size(26 if len(label) < 10 else 21); e = cr.text_extents(label)
        cr.move_to(118 - e.x_advance / 2, 270); cr.show_text(label)
        p = f"/tmp/_media_{glyph}.png"; s.write_to_png(p); return pix(p, 236, 352)
    req = flat((0.24, 0.2, 0.55), (0.12, 0.1, 0.3), "Requests", "plus")
    store = flat((0.1, 0.36, 0.62), (0.04, 0.14, 0.3), "Steam Store", "cart")
    tile([req, store, req], "Media", "Requests & Steam Store", f"{THEME}/tiles/media.webp")


if __name__ == "__main__":
    main()
