#!/usr/bin/env python3
"""Merge "(Disc N)" files of a system into one ES-DE entry: roms/<sys>/<Game>.m3u/ holding the discs and a
<Game>.m3u playlist (ES-DE shows the folder as one game and launches the playlist). Disc 1's media and gamelist
entry become the game's; the other discs' media and entries are set aside in _Removed games/<sys>/disc-dupes/.
Run with ES-DE closed.  Usage: merge_discs.py <system> [--dry]"""
import prism_config  # noqa: E402  (paths)
import glob, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gamelist  # noqa: E402  (atomic writes, 2026-10-13)
from xml.sax.saxutils import escape
EMU = prism_config.EMU
system, dry = sys.argv[1], "--dry" in sys.argv
roms, media = f"{EMU}/roms/{system}", f"{EMU}/tools/downloaded_media/{system}"
aside = f"{EMU}/_Removed games/{system}/disc-dupes"
gl_path = os.path.expanduser(f"{prism_config.ESDE}/gamelists/{system}/gamelist.xml")
DISC = re.compile(r"^(.*?)\s*\(Disc (\d+)\)(.*)$", re.I)
groups = {}
for f in sorted(os.listdir(roms)):
    full = f"{roms}/{f}"
    stem, ext = os.path.splitext(f)
    m = DISC.match(stem)
    if os.path.isfile(full) and m:
        base = (m.group(1) + m.group(3)).strip()
        groups.setdefault(base, []).append((int(m.group(2)), f))
gl = open(gl_path).read() if os.path.exists(gl_path) else ""
for base, discs in groups.items():
    if len(discs) < 2:
        continue
    discs.sort()
    print(f"{base}: {[d[1] for d in discs]}")
    if dry:
        continue
    folder = f"{roms}/{base}.m3u"
    os.makedirs(folder, exist_ok=True)
    for _, f in discs:
        os.rename(f"{roms}/{f}", f"{folder}/{f}")
    with open(f"{folder}/{base}.m3u", "w") as fh:
        fh.write("\n".join(f for _, f in discs) + "\n")
    first = os.path.splitext(discs[0][1])[0]
    for kind_dir in glob.glob(f"{media}/*"):
        kind = os.path.basename(kind_dir)
        for m in glob.glob(f"{kind_dir}/{glob.escape(first)}.*"):
            dst = f"{kind_dir}/{base}.m3u{os.path.splitext(m)[1]}"  # ES-DE names folder media after the full folder name
            if not os.path.exists(dst):
                os.rename(m, dst)
        for _, f in discs[1:]:
            for m in glob.glob(f"{kind_dir}/{glob.escape(os.path.splitext(f)[0])}.*"):
                os.makedirs(f"{aside}/{kind}", exist_ok=True)
                os.rename(m, f"{aside}/{kind}/{os.path.basename(m)}")
    # gamelist: Disc 1 entry -> the playlist folder, other disc entries set aside
    for i, (_, f) in enumerate(discs):
        pat = re.compile(r"\s*<game>\s*<path>\./" + re.escape(escape(f)) + r"</path>.*?</game>", re.S)
        mm = pat.search(gl)
        if not mm:
            continue
        if i == 0:
            entry = mm.group(0).replace(f"./{escape(f)}", f"./{escape(base)}.m3u")
            entry = re.sub(r"(<name>.*?)\s*\(Disc 1\)(.*?</name>)", r"\1\2", entry)
            gl = gl[:mm.start()] + entry + gl[mm.end():]
        else:
            os.makedirs(aside, exist_ok=True)
            open(f"{aside}/{os.path.splitext(f)[0]}.gamelist.xml", "w").write(mm.group(0).strip() + "\n")
            gl = gl[:mm.start()] + gl[mm.end():]
if not dry and gl:
    gamelist.write_text(gl_path, gl, allow_shrink=True)
