#!/usr/bin/env python3
"""Fill missing ES-DE screenshots, title screens and covers from the libretro thumbnail archive.

Only writes files that don't exist yet (never replaces scraped media). Matching: exact ROM name first, then the
title with region/flag tags removed. Usage: libretro_fill.py [system ...]   (default: all mapped systems)
"""
import prism_config  # noqa: E402  (paths)
import html, os, re, sys, time, urllib.parse, urllib.request

EMU = prism_config.EMU
ROMS, MEDIA = f"{EMU}/roms", f"{EMU}/tools/downloaded_media"
BASE = "https://thumbnails.libretro.com"
UA = {"User-Agent": "deck-esde-media/1.0"}
SYSTEMS = {
    "dreamcast": "Sega - Dreamcast", "gba": "Nintendo - Game Boy Advance", "gbc": "Nintendo - Game Boy Color",
    "genesis": "Sega - Mega Drive - Genesis", "n64": "Nintendo - Nintendo 64", "nds": "Nintendo - Nintendo DS",
    "nes": "Nintendo - Nintendo Entertainment System", "ngpc": "SNK - Neo Geo Pocket Color",
    "pcengine": "NEC - PC Engine - TurboGrafx 16", "ps2": "Sony - PlayStation 2", "psvita": "Sony - PlayStation Vita",
    "psx": "Sony - PlayStation", "satellaview": "Nintendo - Satellaview", "saturn": "Sega - Saturn",
    "sega32x": "Sega - 32X", "segacd": "Sega - Mega-CD - Sega CD", "snes": "Nintendo - Super Nintendo Entertainment System",
    "virtualboy": "Nintendo - Virtual Boy", "wii": "Nintendo - Wii", "gc": "Nintendo - GameCube",
    "xbox360": "Microsoft - Xbox 360", "neogeocd": "SNK - Neo Geo CD", "psp": "Sony - PlayStation Portable",
    "neogeo": "SNK - Neo Geo", "mame": "MAME", "arcade": "MAME", "msx": "Microsoft - MSX", "model2": "MAME", "wiiu": "Nintendo - Wii U", "ps4": "Sony - PlayStation 4",
}
KINDS = [("Named_Snaps", "screenshots"), ("Named_Titles", "titlescreens"), ("Named_Boxarts", "covers")]
ROM_EXT = re.compile(r"\.(zip|7z|chd|cue|iso|gdi|cdi|m3u|rvz|wbfs|wua|nsp|xci|bin|sfc|smc|nes|gba|gbc|gb|n64|z64|v64|"
                     r"nds|md|gen|smd|32x|pce|ngc|ngp|vb|psvita|vpk|pbp|cso|elf|xex|iso|rom|mx1|mx2|dsk|sms)$", re.I)


def get(url, timeout=40):
    for i in range(3):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(3 * (i + 1))
        except Exception:
            time.sleep(3 * (i + 1))
    return None


def norm(s):
    s = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", s)          # tags
    s = re.sub(r"^(.*), (The|A|An)\b", r"\2 \1", s)          # "Legend of Zelda, The" -> "The Legend of Zelda"
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def base_title(t):
    """Title without region/translation/version tags: "Star Ocean (J) T+Eng v1.0 DeJap" -> "Star Ocean"."""
    t = t.replace("_", " ")
    return re.split(r"\s*[\(\[]|\s+T[+-]Eng|\s+v\d", t, maxsplit=1)[0].strip()


def listing(playlist, kind):
    raw = get(f"{BASE}/{urllib.parse.quote(playlist)}/{kind}/", timeout=90)
    if not raw:
        return {}
    names = [html.unescape(urllib.parse.unquote(m)) for m in re.findall(r'href="([^"?/][^"]*\.png)"', raw.decode("utf-8", "replace"))]
    exact = {n[:-4]: n for n in names}
    loose = {}
    for n in names:
        k = norm(n[:-4])
        # prefer USA / World / Europe versions for the loose match
        if k not in loose or re.search(r"\((USA|World)", n):
            loose[k] = n
    return exact, loose


def fill(system):
    playlist = SYSTEMS[system]
    rdir = f"{ROMS}/{system}"
    # (media file stem, title used for matching). ES-DE names a folder entry's media after the FULL folder name
    # (e.g. "Game.m3u.png"), so folders keep their extension in the media stem.
    stems = sorted({(f, ROM_EXT.sub("", f)) if os.path.isdir(f"{rdir}/{f}") else (ROM_EXT.sub("", f), ROM_EXT.sub("", f))
                    for f in os.listdir(rdir)
                    if not f.startswith(".") and (os.path.isdir(f"{rdir}/{f}") or
                                                  (not f.endswith((".txt", ".cfg")) and ROM_EXT.search(f)))})
    got = 0
    for kind, folder in KINDS:
        out = f"{MEDIA}/{system}/{folder}"
        os.makedirs(out, exist_ok=True)
        have = {os.path.splitext(f)[0] for f in os.listdir(out)}
        todo = [(m, t) for m, t in stems if m not in have]
        if not todo:
            continue
        idx = listing(playlist, kind)
        if not idx:
            continue
        exact, loose = idx
        for s, title in todo:
            safe = re.sub(r'[&*/:`<>?\\|"]', "_", title)
            name = exact.get(safe) or loose.get(norm(title)) or loose.get(norm(base_title(title)))
            if not name:
                continue
            data = get(f"{BASE}/{urllib.parse.quote(playlist)}/{kind}/{urllib.parse.quote(name)}")
            if data and data[:8] == b"\x89PNG\r\n\x1a\n":
                with open(f"{out}/{s}.png", "wb") as f:
                    f.write(data)
                got += 1
    print(f"{system:14} {len(stems):5} games, {got} images added", flush=True)


def main():
    for s in sys.argv[1:] or SYSTEMS:
        if s in SYSTEMS and os.path.isdir(f"{ROMS}/{s}"):
            try:
                fill(s)
            except Exception as e:
                print(s, "failed:", e, flush=True)


if __name__ == "__main__":
    main()
