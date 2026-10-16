#!/usr/bin/env python3
"""Gamelist upkeep that needs ES-DE closed. Run by esde_night_scrape.py between closing and reopening ES-DE.
 1. apply file renames queued in renames_pending.json (gamelist paths, media already renamed, collections)
 2. drop gamelist entries whose files are gone (kept in <gamelist>.stale-removed.xml)
 3. give art-less games with scene/tagged file names a clean <name>, so the scraper searches the right title"""
import prism_config  # noqa: E402  (paths)
import glob, html, json, os, re
import gamelist   # atomic writes (2026-10-13)
from xml.sax.saxutils import escape
EMU = prism_config.EMU; R = EMU + "/roms"; M = EMU + "/tools/downloaded_media"
GL = f"{prism_config.ESDE}/gamelists"; COLL = f"{prism_config.ESDE}/collections"
IMG = ("screenshots", "titlescreens", "miximages", "covers", "3dboxes")
pend = f"{prism_config.PRISM}/renames_pending.json"
if os.path.exists(pend):
    for system, old, new in json.load(open(pend)):
        gl = f"{GL}/{system}/gamelist.xml"
        if os.path.exists(gl):
            t = open(gl).read().replace(f"<path>./{escape(old)}</path>", f"<path>./{escape(new)}</path>")
            gamelist.write_text(gl, t)
        for c in glob.glob(f"{COLL}/*.cfg"):
            t = open(c).read()
            if f"/{system}/{old}\n" in t:
                open(c, "w").write(t.replace(f"/{system}/{old}\n", f"/{system}/{new}\n"))
    os.rename(pend, pend + ".done")
    print("renames applied")


def clean(stem):
    s = stem.replace("_", " ")
    if " " not in s:
        s = s.replace(".", " ")
    s = re.sub(r"-(RDC|NBS|DCRES|XIAOPANG|YZB|DCCM)\b", "", s, flags=re.I)
    s = re.split(r"\s*[\(\[]|\s+T[+-]Eng|\s+v\d", s, maxsplit=1)[0]
    return re.sub(r"\s+", " ", s).strip(" -")


named = 0
for gl in glob.glob(f"{GL}/*/gamelist.xml"):
    system = os.path.basename(os.path.dirname(gl))
    if system in ("steam", "media", "foryou"):
        continue
    t = open(gl, errors="replace").read()
    gone = []
    def keep(m):
        p = re.search(r"<path>\./(.*?)</path>", m.group(0))
        if p and not os.path.exists(f"{R}/{system}/{html.unescape(p.group(1))}"):
            gone.append(m.group(0)); return ""
        return m.group(0)
    t = re.sub(r"[ \t]*<game>.*?</game>\n?", keep, t, flags=re.S)
    if gone and not os.path.isdir(f"{R}/{system}") or (len(gone) > 5 and len(gone) * 2 > gamelist.entry_count(t) + len(gone)):
        # SD card not mounted, or a ROM folder moved: not a few stale entries but the whole list (2026-10-13 guard)
        print(f"{system}: {len(gone)} entries without files, left alone (ROM folder missing?)")
        continue
    if gone:
        open(gl + ".stale-removed.xml", "a").write("\n".join(gone) + "\n")
    have = {html.unescape(p) for p in re.findall(r"<path>\./(.*?)</path>", t)}
    add = []
    rdir = f"{R}/{system}"
    for f in (os.listdir(rdir) if os.path.isdir(rdir) else []):
        if f.startswith(".") or f.endswith((".txt", ".cfg", ".xml", ".json", ".toml")) or f in have:
            continue
        stem = f if os.path.isdir(f"{rdir}/{f}") else os.path.splitext(f)[0]
        if any(glob.glob(f"{M}/{system}/{k}/{glob.escape(stem)}.*") for k in IMG):
            continue
        nice = clean(stem)
        if nice and nice != stem:
            add.append(f"\t<game>\n\t\t<path>./{escape(f)}</path>\n\t\t<name>{escape(nice)}</name>\n\t</game>")
    if add:
        t = t.replace("</gameList>", "\n".join(add) + "\n</gameList>")
        named += len(add)
    gamelist.write_text(gl, t, allow_shrink=True)
print("clean names added:", named)

# For You: install this week's staged picks (foryou.py fetch stages them; ES-DE is closed right now)
import subprocess
subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/foryou/foryou.py", "apply"])

# Console-style tidy-up (Continue Playing = last 8 games, hide games that fail boot tests, calm settings)
subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/esde_tidy.py"])
try:                          # broken or emptied gamelists back from the newest healthy backup, key ES-DE settings
    import prism_doctor
    prism_doctor.closed()
except Exception as e:
    print("doctor:", e)
