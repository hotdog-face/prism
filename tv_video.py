#!/usr/bin/env python3
"""Game-start video setup for the TV vs the Deck's screen (2026-10-09, the owner: N64 on the TV had uneven pixels).

RetroArch's Prism bezel (overlays/switchhome) was drawn for the Deck's 1280x800 screen; docked (16:9, 1080p) a 16:9
version is swapped in so the frame isn't stretched. The N64 core renders 1440x1080 on the TV (an exact 4:3 fit, no
uneven 1.125x scaling) and 1280x960 on the Deck."""
import glob, os, re, shutil, sys

RA = os.path.expanduser("~/.var/app/org.libretro.RetroArch/config/retroarch")
OV = f"{RA}/overlays/switchhome"
OPT = f"{RA}/config/Mupen64Plus-Next/Mupen64Plus-Next.opt"


def docked():
    return any(open(f).read().strip() == "connected" for f in glob.glob("/sys/class/drm/card*-DP-*/status"))


def make_169():
    """1280x800 bezel -> 1920x1080: scaled by 1.35 (its 4:3 window becomes exactly 1440 px wide), edges extended."""
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    src = GdkPixbuf.Pixbuf.new_from_file(f"{OV}/bezel_1610.png")
    mid = src.scale_simple(1728, 1080, GdkPixbuf.InterpType.BILINEAR)
    out = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1920, 1080)
    left = mid.new_subpixbuf(0, 0, 8, 1080).scale_simple(96, 1080, GdkPixbuf.InterpType.BILINEAR)
    right = mid.new_subpixbuf(1720, 0, 8, 1080).scale_simple(96, 1080, GdkPixbuf.InterpType.BILINEAR)
    left.copy_area(0, 0, 96, 1080, out, 0, 0)
    mid.copy_area(0, 0, 1728, 1080, out, 96, 0)
    right.copy_area(0, 0, 96, 1080, out, 1824, 0)
    out.savev(f"{OV}/bezel_169.png", "png", [], [])


def main():
    if not os.path.exists(f"{OV}/bezel_1610.png"):
        shutil.copy(f"{OV}/switchhome-bezel.png", f"{OV}/bezel_1610.png")      # the original, kept
    if not os.path.exists(f"{OV}/bezel_169.png"):
        make_169()
    tv = docked()
    want = f"{OV}/bezel_169.png" if tv else f"{OV}/bezel_1610.png"
    cur = f"{OV}/switchhome-bezel.png"
    if open(want, "rb").read() != open(cur, "rb").read():
        shutil.copy(want, cur)
    if os.path.exists(OPT):
        t = open(OPT).read()
        size = "1440x1080" if tv else "1280x960"
        n = re.sub(r'mupen64plus-43screensize = "[^"]*"', f'mupen64plus-43screensize = "{size}"', t)
        if n != t:
            open(OPT, "w").write(n)
    print(f"tv_video: {'TV' if tv else 'Deck'} - bezel {os.path.basename(want)}, N64 {'1440x1080' if tv else '1280x960'}")


if __name__ == "__main__":
    main()
