#!/usr/bin/env python3
"""Put back a game removed with the L4 curation button.

Usage: curation_restore.py list            removals, newest last
       curation_restore.py <text>          restore the newest removal whose name contains <text>
ES-DE must be closed (it rewrites gamelists when it exits). Files move back out of _Removed games.
"""
import prism_config  # noqa: E402  (paths)
import json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gamelist  # noqa: E402  (atomic writes, 2026-10-13)

HOME = os.path.expanduser("~")
LOG = f"{prism_config.PRISM}/curation_removed.log"
MEDIA = f"{prism_config.TOOLS}/downloaded_media"
GAMELISTS = f"{prism_config.ESDE}/gamelists"


def put(src, dst):
    """Move src back to dst. If dst is already the same file (a hard link), just drop the extra link."""
    if not os.path.exists(src):
        return
    if os.path.exists(dst):
        if os.path.samefile(src, dst):
            os.unlink(src)
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.rename(src, dst)
    except OSError:
        subprocess.run(["mv", src, dst], check=True)


def main():
    recs = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
    if len(sys.argv) < 2 or sys.argv[1] == "list":
        for r in recs:
            state = ("  (restored)" if r.get("restored") else "" if r.get("saved") else
                     "  (can't auto-restore)" if r.get("deleted") else "  (not removed)")   # L4 deletes + 10-09 moves: no "saved" record
            print(f"{r['time']}  {r['system']:<12} {r['name']}{state}")
        return
    want = sys.argv[1].lower()
    match = [r for r in recs if want in r["name"].lower() and not r.get("restored") and r.get("saved")]
    if not match:
        sys.exit(f"no removal matching {sys.argv[1]!r}")
    if subprocess.run(["pgrep", "-x", "es-de"], capture_output=True).returncode == 0:
        sys.exit("close ES-DE first")
    r = match[-1]
    put(r["saved"]["rom"][0], r["path"])
    for orig, saved in r["saved"]["moved"]:
        put(saved, orig)
    for m in r["saved"]["media"]:
        kind = os.path.basename(os.path.dirname(m))
        stem = os.path.splitext(os.path.basename(r["path"]))[0]
        whole = os.path.basename(r["path"])   # folder games (Game.xex/, Game.m3u/) keep the extension in media names
        if os.path.basename(m).startswith(whole + "."):
            stem = whole
        put(m, f"{MEDIA}/{r['system']}/{kind}/{stem}{os.path.splitext(m)[1]}")
    stem = os.path.splitext(os.path.basename(r["path"]))[0]
    entry_file = os.path.join(os.path.dirname(r["saved"]["rom"][0]), f"{stem}.gamelist.xml")
    gl = f"{GAMELISTS}/{r['system']}/gamelist.xml"
    if os.path.exists(entry_file) and os.path.exists(gl):
        entry = open(entry_file).read().strip()
        text = open(gl).read()
        path_tag = re.search(r"<path>.*?</path>", entry).group(0)
        if path_tag not in text:
            gamelist.write_text(gl, text.replace("</gameList>", f"\t{entry}\n</gameList>"))
    if os.path.exists(entry_file):
        os.rename(entry_file, entry_file + ".restored")
    with open(LOG, "w") as f:
        for x in recs:
            if x is r:
                x["restored"] = True
            f.write(json.dumps(x) + "\n")
    print(f"restored {r['system']}/{r['name']}")


if __name__ == "__main__":
    main()
