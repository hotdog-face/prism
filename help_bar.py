#!/usr/bin/env python3
"""Tap support for Prism's bottom hint bar (2026-10-08): tapping "B Back" acts like pressing B, like the
Switch's own touchable hints. The hints change per screen, so on a tap the bar strip is captured and its badges
are found and recognised against the theme's own badge artwork (help_templates.json, rendered from
themes/switch-home/assets/help/*.png). deck_curate.Touch calls button_at(x)."""
import json, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
Y0, Y1 = 726, 778                   # the hint strip (screen px), below the divider line at ~708
W = 1280
_T = None


def templates():
    global _T
    if _T is None:
        _T = {k: {int(h): v for h, v in d.items()} for k, d in json.load(open(f"{HERE}/help_templates.json")).items()}
    return _T


def strip(png):
    """Grey pixels of the hint strip, via ffmpeg (no image library on the Deck)."""
    h = Y1 - Y0
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", png, "-vf", f"crop={W}:{h}:0:{Y0}", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-"], capture_output=True, timeout=5)
    return r.stdout if len(r.stdout) == W * h else None


def blobs(px, h, thr=170):
    """Connected bright regions -> (x0, y0, x1, y1, area)."""
    seen = bytearray(len(px)); out = []
    for i in range(len(px)):
        if px[i] < thr or seen[i]:
            continue
        stack = [i]; seen[i] = 1
        x0 = x1 = i % W; y0 = y1 = i // W; area = 0
        while stack:
            j = stack.pop(); area += 1
            x, y = j % W, j // W
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for k in (j - 1, j + 1, j - W, j + W):
                if 0 <= k < len(px) and not seen[k] and px[k] >= thr and abs(k % W - x) <= 1:
                    seen[k] = 1; stack.append(k)
        out.append((x0, y0, x1, y1, area))
    return out


def classify(px, b):
    x0, y0, x1, y1, area = b
    bh, bw = y1 - y0 + 1, x1 - x0 + 1
    best = (1e9, None)
    for name, sizes in templates().items():
        for hh in (bh, bh + 1, bh - 1):
            if hh not in sizes:
                continue
            tw, t = sizes[hh]
            if abs(tw - bw) > 2:
                continue
            tot = n = 0
            for yy in range(hh):
                row = (y0 + yy) * W
                for xx in range(tw):
                    k = row + x0 + xx
                    if k < len(px) and x0 + xx < W:
                        tot += abs(px[k] - t[yy * tw + xx]); n += 1
            if n and tot / n < best[0]:
                best = (tot / n, name)
    return best


def badges(png):
    px = strip(png)
    if px is None:
        return []
    h = Y1 - Y0
    found = []
    for b in blobs(px, h):
        x0, y0, x1, y1, area = b
        bh, bw = y1 - y0 + 1, x1 - x0 + 1
        if not (14 <= bh <= 33 and 0.7 * bh <= bw <= 1.45 * bh and area >= 0.35 * bh * bw):
            continue                       # text strokes, specks
        score, name = classify(px, b)
        if name and score < 45:
            found.append((x0, x1, name, round(score)))
    return sorted(found)


def button_at(x, png):
    """The button whose hint (badge + label) is under screen x, or None."""
    bs = badges(png)
    for i, (x0, x1, name, _) in enumerate(bs):
        if i + 1 < len(bs):
            nx = bs[i + 1][0]
            right = (x1 + nx) // 2 if nx - x1 < 20 else nx - 8     # L|R pills touch: split between them
        else:
            right = W
        if x0 - 10 <= x < right:
            return None if name == "DPAD" else name
    return None


if __name__ == "__main__":
    import sys
    print(badges(sys.argv[1]))
