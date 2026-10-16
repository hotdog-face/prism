#!/usr/bin/env python3
"""Prism icon row (Controllers / Settings / Power): glass discs + crisp system glyphs.

Geometry is fixed by deck_icon_focus.py / deck_curate.py: a 720x200 image, disc centres at x = 120 / 360 / 600,
y = 100 (screen 85 / 167 / 249, 752 on 1280x800). SVG -> headless Chrome at 3x -> Lanczos down to 720x200.
Usage: make_iconrow.py <outdir> [variant ...]   variants: line, solid, neon
"""
import math, os, subprocess, sys, tempfile
from PIL import Image

W, H, R = 720, 200, 82
CX, CY = (120, 360, 600), 100
PAL = ["#F0436F", "#F69F3C", "#FBE01A", "#B6D351", "#27B6D5", "#29ABEB"]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SS = 3


def f(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


# ---------- glyphs (centred on 0,0) ----------
def pad_outline():
    """Modern gamepad silhouette, right half as cubic segments, mirrored."""
    half = [  # (c1, c2, end) going clockwise from top centre to bottom centre
        ((14, -24.5), (24, -26.5), (32, -26.5)),
        ((42, -26.5), (48.5, -21.5), (51.5, -11)),
        ((55.5, 3), (59.5, 20), (58, 30)),
        ((56.8, 39), (47, 43), (40.5, 36.5)),
        ((35, 31), (31, 22.5), (23, 18.5)),
        ((15.5, 15), (8, 14.5), (0, 14.5)),
    ]
    start = (0, -24)
    d = [f"M{f(start[0])},{f(start[1])}"]
    for c1, c2, e in half:
        d.append(f"C{f(c1[0])},{f(c1[1])} {f(c2[0])},{f(c2[1])} {f(e[0])},{f(e[1])}")
    # mirror back: reverse the segment list, swapping control points
    pts = [start] + [seg[2] for seg in half]
    for i in range(len(half) - 1, -1, -1):
        c1, c2, _ = half[i]
        e = pts[i]
        d.append(f"C{f(-c2[0])},{f(c2[1])} {f(-c1[0])},{f(c1[1])} {f(-e[0])},{f(e[1])}")
    return " ".join(d) + "Z"


def plus(cx, cy, arm, th, r):
    """D-pad: plus sign with rounded outer corners, as a path."""
    a, t = arm, th / 2
    return (f"M{f(cx - t)},{f(cy - a + r)} Q{f(cx - t)},{f(cy - a)} {f(cx - t + r)},{f(cy - a)} H{f(cx + t - r)} "
            f"Q{f(cx + t)},{f(cy - a)} {f(cx + t)},{f(cy - a + r)} V{f(cy - t)} H{f(cx + a - r)} "
            f"Q{f(cx + a)},{f(cy - t)} {f(cx + a)},{f(cy - t + r)} V{f(cy + t - r)} Q{f(cx + a)},{f(cy + t)} {f(cx + a - r)},{f(cy + t)} "
            f"H{f(cx + t)} V{f(cy + a - r)} Q{f(cx + t)},{f(cy + a)} {f(cx + t - r)},{f(cy + a)} H{f(cx - t + r)} "
            f"Q{f(cx - t)},{f(cy + a)} {f(cx - t)},{f(cy + a - r)} V{f(cy + t)} H{f(cx - a + r)} "
            f"Q{f(cx - a)},{f(cy + t)} {f(cx - a)},{f(cy + t - r)} V{f(cy - t + r)} Q{f(cx - a)},{f(cy - t)} {f(cx - a + r)},{f(cy - t)} "
            f"H{f(cx - t)} Z")


def pad_parts(dot_r, dpad_arm, dpad_th, stick_r):
    dl, dr = (-31, -5), (31, -5)
    parts = [plus(dl[0], dl[1], dpad_arm, dpad_th, 1.6)]
    for ox, oy in ((0, -7.2), (7.2, 0), (0, 7.2), (-7.2, 0)):
        parts.append(f"M{f(dr[0] + ox + dot_r)},{f(dr[1] + oy)} a{f(dot_r)},{f(dot_r)} 0 1,0 {f(-2 * dot_r)},0 "
                     f"a{f(dot_r)},{f(dot_r)} 0 1,0 {f(2 * dot_r)},0Z")
    sticks = [(-14.5, 7.5), (14.5, 7.5)]
    return parts, sticks, stick_r


def gear_path(n=8, ro=40, ri=31.5, top=0.30, base=0.42):
    """Gear outline: n trapezoid teeth; top/base = tooth half-width as a fraction of the pitch angle."""
    pitch = 2 * math.pi / n
    pts = []
    for i in range(n):
        a = i * pitch - math.pi / 2
        for ang, rad in ((a - base * pitch / 1, ri), (a - top * pitch / 1, ro), (a + top * pitch / 1, ro), (a + base * pitch / 1, ri)):
            pts.append((rad * math.cos(ang), rad * math.sin(ang)))
    # root arcs between teeth, straight flanks
    d = []
    for i in range(n):
        p = pts[4 * i: 4 * i + 4]
        if i == 0:
            d.append(f"M{f(p[0][0])},{f(p[0][1])}")
        d.append(f"L{f(p[1][0])},{f(p[1][1])} A{ro},{ro} 0 0,1 {f(p[2][0])},{f(p[2][1])} L{f(p[3][0])},{f(p[3][1])}")
        nxt = pts[(4 * i + 4) % len(pts)]
        d.append(f"A{ri},{ri} 0 0,1 {f(nxt[0])},{f(nxt[1])}")
    return " ".join(d) + "Z"


def glyphs(style):
    """Return SVG for the three glyphs (white), centred at 0,0, for 'line' or 'solid'."""
    out = []
    if style == "line":
        sw = 7
        parts, sticks, sr = pad_parts(3.4, 8.2, 6.2, 4.6)
        pad = (f'<path d="{pad_outline()}" fill="none" stroke="url(#ink)" stroke-width="{sw}" stroke-linejoin="round"/>'
               f'<path d="{" ".join(parts)}" fill="url(#ink)"/>'
               + "".join(f'<circle cx="{x}" cy="{y}" r="{sr}" fill="url(#ink)"/>' for x, y in sticks))
        out.append(f'<g transform="scale(0.98)">{pad}</g>')
        g = (f'<path d="{gear_path(8, 38.5, 30, 0.27, 0.40)}" fill="none" stroke="url(#ink)" stroke-width="{sw}" '
             f'stroke-linejoin="round"/><circle r="11.5" fill="none" stroke="url(#ink)" stroke-width="{sw}"/>')
        out.append(g)
    else:
        parts, sticks, sr = pad_parts(4.0, 9.5, 7.2, 5.6)
        holes = (f'<path d="{" ".join(parts)}" fill="black"/>'
                 + "".join(f'<circle cx="{x}" cy="{y}" r="{sr}" fill="black"/>' for x, y in sticks))
        pad = (f'<mask id="padm" maskUnits="userSpaceOnUse" x="-80" y="-80" width="160" height="160">'
               f'<path d="{pad_outline()}" fill="white" stroke="white" stroke-width="2" stroke-linejoin="round"/>{holes}</mask>'
               f'<rect x="-70" y="-60" width="140" height="120" fill="url(#ink)" mask="url(#padm)"/>')
        out.append(f'<g transform="scale(0.95)">{pad}</g>')
        g = (f'<mask id="gearm" maskUnits="userSpaceOnUse" x="-60" y="-60" width="120" height="120">'
             f'<path d="{gear_path(8, 39, 31, 0.26, 0.40)}" fill="white" stroke="white" stroke-width="5" stroke-linejoin="round"/>'
             f'<circle r="13.5" fill="black"/></mask>'
             f'<rect x="-50" y="-50" width="100" height="100" fill="url(#ink)" mask="url(#gearm)"/>')
        out.append(g)
    sw = 7.5 if style == "line" else 9
    r, gap = 29, 42
    a0, a1 = math.radians(-90 + gap), math.radians(-90 - gap)
    x0, y0, x1, y1 = r * math.cos(a0), r * math.sin(a0) + 4, r * math.cos(a1), r * math.sin(a1) + 4
    out.append(f'<path d="M{f(x0)},{f(y0)} A{r},{r} 0 1,1 {f(x1)},{f(y1)}" fill="none" stroke="url(#ink)" '
               f'stroke-width="{sw}" stroke-linecap="round"/>'
               f'<path d="M0,-33 V1" stroke="url(#ink)" stroke-width="{sw}" stroke-linecap="round"/>')
    return out


# ---------- discs ----------
def defs(variant):
    stops = "".join(f'<stop offset="{i / (len(PAL) - 1):.3f}" stop-color="{c}"/>' for i, c in enumerate(PAL))
    return f'''<defs>
  <linearGradient id="ink" x1="0" y1="-45" x2="0" y2="45" gradientUnits="userSpaceOnUse">
    <stop offset="0" stop-color="#FFFFFF"/><stop offset="1" stop-color="#E9E9EE"/></linearGradient>
  <linearGradient id="fill" x1="0" y1="-{R}" x2="0" y2="{R}" gradientUnits="userSpaceOnUse">
    <stop offset="0" stop-color="#2E2E31" stop-opacity="{0.62 if variant == 'neon' else 0.50}"/>
    <stop offset="1" stop-color="#202022" stop-opacity="{0.70 if variant == 'neon' else 0.58}"/></linearGradient>
  <linearGradient id="sheen" x1="0" y1="-{R}" x2="0" y2="{R}" gradientUnits="userSpaceOnUse">
    <stop offset="0" stop-color="#fff" stop-opacity="0.13"/><stop offset="0.24" stop-color="#fff" stop-opacity="0.035"/>
    <stop offset="0.5" stop-color="#fff" stop-opacity="0"/></linearGradient>
  <linearGradient id="rim" x1="0" y1="-{R}" x2="0" y2="{R}" gradientUnits="userSpaceOnUse">
    <stop offset="0" stop-color="#fff" stop-opacity="0.42"/><stop offset="0.5" stop-color="#fff" stop-opacity="0.16"/>
    <stop offset="1" stop-color="#fff" stop-opacity="0.12"/></linearGradient>
  <linearGradient id="spec" x1="-{R * .6:.1f}" y1="{R * .6:.1f}" x2="{R * .6:.1f}" y2="-{R * .6:.1f}" gradientUnits="userSpaceOnUse">{stops}</linearGradient>
  <linearGradient id="fadeup" x1="0" y1="-{R}" x2="0" y2="{R}" gradientUnits="userSpaceOnUse">
    <stop offset="0.35" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#fff" stop-opacity="1"/></linearGradient>
  <mask id="lower" maskUnits="userSpaceOnUse" x="-100" y="-100" width="200" height="200">
    <rect x="-100" y="-100" width="200" height="200" fill="url(#fadeup)"/></mask>
  <filter id="shadow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="5"/></filter>
  <filter id="gshadow" x="-50%" y="-50%" width="200%" height="200%">
    <feDropShadow dx="0" dy="1.5" stdDeviation="2" flood-color="#000" flood-opacity="0.45"/></filter>
  <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="4"/></filter>
</defs>'''


def disc(variant):
    s = [f'<circle cy="3" r="{R}" fill="#000" opacity="0.38" filter="url(#shadow)"/>',
         f'<circle r="{R}" fill="url(#fill)"/>',
         f'<circle r="{R}" fill="url(#sheen)"/>',
         f'<circle r="{R - 1.4}" fill="none" stroke="url(#rim)" stroke-width="2.8"/>']
    if variant == "solid":   # neon hairline on the lower rim only
        s.append(f'<circle r="{R - 1.4}" fill="none" stroke="url(#spec)" stroke-width="2.8" opacity="0.9" mask="url(#lower)"/>')
    if variant == "neon":    # full spectrum rim like the selection frame, with a soft bloom
        s.append(f'<circle r="{R - 2}" fill="none" stroke="url(#spec)" stroke-width="6" opacity="0.55" filter="url(#glow)"/>')
        s.append(f'<circle r="{R - 2}" fill="none" stroke="url(#spec)" stroke-width="4"/>')
    return "".join(s)


def svg(variant, w=720):
    cxs = [c * w / 720 for c in CX]
    style = "solid" if variant == "solid" else "line"
    gl = glyphs(style)
    body = []
    for cx, g in zip(cxs, gl):
        body.append(f'<g transform="translate({cx},{CY})">{disc(variant)}<g filter="url(#gshadow)">{g}</g></g>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{H}" viewBox="0 0 {w} {H}">'
            f'{defs(variant)}{"".join(body)}</svg>')


def render(variant, out, w=720):
    with tempfile.TemporaryDirectory() as td:
        html = os.path.join(td, "i.html")
        with open(html, "w") as fh:
            fh.write(f'<html><body style="margin:0;background:transparent;overflow:hidden">{svg(variant, w)}</body></html>')
        big = os.path.join(td, "big.png")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--default-background-color=00000000",
                        f"--force-device-scale-factor={SS}", f"--window-size={w},{H}", f"--screenshot={big}",
                        "file://" + html], check=True, capture_output=True)
        im = Image.open(big).convert("RGBA").crop((0, 0, w * SS, H * SS))
        im.convert("RGBa").resize((w, H), Image.LANCZOS).convert("RGBA").save(out)  # premultiplied downsample
    return out


if __name__ == "__main__":
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    for v in sys.argv[2:] or ["line", "solid", "neon"]:
        print(render(v, os.path.join(outdir, f"iconrow_{v}.png")))
        print(render(v, os.path.join(outdir, f"iconrow_{v}_169.png"), 800))
