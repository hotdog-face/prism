#!/usr/bin/env python3
"""'New on Deck' (2026-10-08): an ES-DE custom collection of the games most recently added to the SD card,
newest first by when the file arrived (ctime). Only games visible in their lists (not hidden by failed boot
tests). Written to ~/ES-DE/collections/custom-New on Deck.cfg; ES-DE must be closed (esde_pending runs this).
  new_on_deck.py [--count=40] [--dry]"""
import prism_config  # noqa: E402  (paths)
import re, glob, os, subprocess, sys, xml.etree.ElementTree as ET

HOME = os.path.expanduser("~")
ROMS = prism_config.ROMS
GL = f"{prism_config.ESDE}/gamelists"
OUT = f"{prism_config.ESDE}/collections/custom-New on Deck.cfg"
SETTINGS = f"{prism_config.ESDE}/settings/es_settings.xml"
NOT_GAMES = {"movies", "tv", "music", "media", "foryou", "desktop", "emulators", "steam"}
NAME = "New on Deck"


def parse_gamelist(path):
    """ES-DE gamelists can hold more than one top element (<alternativeEmulator> before <gameList>), which strict
    XML parsing rejects: wrap the file in one root first."""
    txt = re.sub(r"^\s*<\?xml[^>]*\?>", "", open(path, encoding="utf-8", errors="replace").read())
    return ET.fromstring(f"<root>{txt}</root>")


def visible_games():
    out = set()
    for g in glob.glob(f"{GL}/*/gamelist.xml"):
        system = os.path.basename(os.path.dirname(g))
        if system in NOT_GAMES:
            continue
        try:
            root = parse_gamelist(g)
        except ET.ParseError:
            continue
        for e in root.iter("game"):
            if (e.findtext("hidden") or "").lower() == "true" or not e.findtext("path"):
                continue
            p = os.path.normpath(os.path.join(ROMS, system, e.findtext("path")))
            if os.path.isfile(p):
                out.add(p)
    return out


def main():
    count = int(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--count=")), 40))
    games = visible_games()
    r = subprocess.run(["find", ROMS, "-type", "f", "-printf", "%C@ %T@ %p\n"], capture_output=True, text=True)
    born = {}
    for line in r.stdout.splitlines():
        b, m, p = line.split(" ", 2)
        if p in games:
            born[p] = float(b)          # status change = when it was copied in (this card keeps no birth time)
    newest = sorted(born, key=born.get, reverse=True)[:count]
    lines = [p.replace(ROMS, "%ROMPATH%", 1) for p in newest]
    if "--dry" in sys.argv:
        print("\n".join(lines[:10])); return
    open(OUT, "w").write("\n".join(lines) + "\n")
    s = open(SETTINGS).read()
    import re
    m = re.search(r'<string name="CollectionSystemsCustom" value="([^"]*)" />', s)
    if m and NAME not in m.group(1).split(","):
        s = s.replace(m.group(0), f'<string name="CollectionSystemsCustom" value="{NAME},{m.group(1)}" />')
        open(SETTINGS, "w").write(s)
    print(f"New on Deck: {len(lines)} games")


if __name__ == "__main__":
    main()
