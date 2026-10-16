#!/usr/bin/env python3
"""ES-DE gamelists: load, merge an entry, write (refactor, 2026-10-13). The one implementation for scripts that edit
~/ES-DE/gamelists/<system>/gamelist.xml.

    gl = gamelist.Gamelist("steam")                 # or a path
    gl.merge({"path": "./x.sh", "name": "X"})        # -> "added" / "updated" / "unchanged"
    gl.save()                                        # ES-DE must be closed (prism_esde.esde_closed)

save() keeps ES-DE's own format (<?xml version="1.0"?>, an optional <alternativeEmulator> block before <gameList>,
tab indentation), copies the old file to gamelist.xml.bak-YYYYmmdd-HHMMSS first, writes atomically, and refuses to
drop entries unless allow_shrink=True. write_text(path, text) is the same write for scripts that edit the XML text
with regexes (esde_tidy, esde_maintenance, ...), without the .bak copy.

Why (2026-10-10, 95 of 98 Switch entries lost): during the party a crashing Ryujinx's core dump filled /home; ES-DE's
save of the Switch gamelist at that moment (16:43:17, a game launch) truncated the file and failed to write it, and
ES-DE's next save treated the empty file as new and wrote only the games changed since. A write must never truncate
the old file first: temp file + fsync + rename (ES-DE's own save: patch in ~/build/esde/src GamelistFileParser.cpp).

daily_backup(): ~/prism/backup/gamelists/gamelists-YYYYMMDD.tar.gz of every gamelist, the last 14 days kept (run by
the nightly maintenance, esde_night_scrape.py). `python3 gamelist.py backup` does it by hand; `check` parses every
gamelist and confirms writing it back would be byte-identical.

guard(fix): entries in those archives whose ROM is still on the SD card but that a game system's gamelist no longer
has (ES-DE never drops them, curation moves the ROM away), and gamelists that don't parse. Run nightly: reported
before the backup, restored from the newest archive copy while ES-DE is closed. `python3 gamelist.py guard [--fix]`;
findings go to logs/gamelist_guard.log."""
import prism_config  # noqa: E402  (paths)
import copy, glob, os, re, shutil, sys, tarfile, time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
GAMELISTS = f"{prism_config.ESDE}/gamelists"
BACKUPS = os.path.join(HERE, "backup", "gamelists")
ROMS = prism_config.ROMS
NOT_GAMES = ("media", "foryou", "movies", "tv", "music")     # rebuilt by plex_sync / foryou, not guarded
GUARD_LOG = os.path.join(HERE, "logs", "gamelist_guard.log")
CONTENT = ("desc", "releasedate", "developer", "publisher", "genre", "players", "rating")   # scraped, refilled
HEADER = '<?xml version="1.0"?>\n'
KEEP_DAYS = 14


def path_for(system):
    return system if "/" in system else f"{GAMELISTS}/{system}/gamelist.xml"


def esde_check():
    import prism_state
    if prism_state.esde_pids():
        raise RuntimeError("ES-DE is running (it rewrites gamelists when it exits): use prism_esde.esde_closed()")


def write_atomic(path, text):
    """Temp file, fsync, rename: a failed write (disk full) leaves the old file as it was."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def entry_count(text):
    return len(re.findall(r"<game>", text))


def write_text(path, text, allow_shrink=False):
    """Write a gamelist edited as text (regex scripts): ES-DE closed, atomic, and no <game> entries dropped unless
    allow_shrink. No .bak copy (nightly callers would pile them up; the daily archive has them). Returns the path, or
    None when nothing changed."""
    esde_check()
    path = path_for(path)
    old = open(path, encoding="utf-8", errors="replace").read() if os.path.exists(path) else None
    if old == text:
        return None
    if old is not None and not allow_shrink and entry_count(text) < entry_count(old):
        raise RuntimeError(f"{path}: would drop {entry_count(old) - entry_count(text)} of {entry_count(old)} entries "
                           "(allow_shrink=True)")
    write_atomic(path, text)
    return path


class Gamelist:
    def __init__(self, system, text=None):
        """text: parse this instead of the file (an archived copy)."""
        self.path = path_for(system)
        self.system = os.path.basename(os.path.dirname(self.path))
        if text is None and os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                text = f.read()
        if text is not None:
            body = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
            top = ET.fromstring("<prism-top>" + body + "</prism-top>")       # ES-DE files can have two top elements
        else:
            top = ET.fromstring("<prism-top><gameList /></prism-top>")
        self.extra = [e for e in top if e.tag != "gameList"]       # <alternativeEmulator>, written before <gameList>
        self.root = top.find("gameList")
        if self.root is None:
            self.root = ET.Element("gameList")
        self.loaded = len(self.root.findall("game"))

    def games(self):
        return {g.findtext("path"): g for g in self.root.findall("game")}

    def find(self, path):
        return next((g for g in self.root.findall("game") if g.findtext("path") == path), None)

    def merge(self, fields, overwrite=False):
        """Add the game {"path": "./...", tag: text, ...}, or fill in the fields it lacks (all of them when overwrite).
        Returns "added", "updated" or "unchanged"."""
        g = self.find(fields["path"])
        if g is None:
            g = ET.SubElement(self.root, "game")
            for tag, val in fields.items():
                ET.SubElement(g, tag).text = str(val)
            return "added"
        changed = False
        for tag, val in fields.items():
            e = g.find(tag)
            if e is None:
                ET.SubElement(g, tag).text = str(val); changed = True
            elif overwrite and e.text != str(val):
                e.text = str(val); changed = True
        return "updated" if changed else "unchanged"

    def render(self):
        parts = []
        for e in self.extra + [self.root]:
            ET.indent(e, space="\t")
            e.tail = None
            parts.append(ET.tostring(e, encoding="unicode"))
        return HEADER + "\n".join(parts) + "\n"

    def save(self, backup=True, allow_shrink=False):
        esde_check()
        n = len(self.root.findall("game"))
        if n < self.loaded and not allow_shrink:
            raise RuntimeError(f"{self.path}: would drop {self.loaded - n} of {self.loaded} entries (allow_shrink=True)")
        text = self.render()
        if os.path.exists(self.path):
            if open(self.path, encoding="utf-8", errors="replace").read() == text:
                return None
            if backup:
                shutil.copy2(self.path, self.path + time.strftime(".bak-%Y%m%d-%H%M%S"))
        write_atomic(self.path, text)
        self.loaded = n
        return self.path


def all_gamelists():
    return sorted(glob.glob(f"{GAMELISTS}/*/gamelist.xml"))


def daily_backup(keep_days=KEEP_DAYS):
    """Today's archive of every gamelist (once a day), and archives older than keep_days removed. Returns its path, or
    None when today's already exists."""
    os.makedirs(BACKUPS, exist_ok=True)
    today = time.strftime("%Y%m%d")
    out = f"{BACKUPS}/gamelists-{today}.tar.gz"
    made = None
    if not os.path.exists(out):
        with tarfile.open(out + ".tmp", "w:gz") as t:
            for p in all_gamelists():
                t.add(p, arcname=os.path.relpath(p, GAMELISTS))
        os.replace(out + ".tmp", out)
        made = out
    cutoff = time.strftime("%Y%m%d", time.localtime(time.time() - keep_days * 86400))
    for old in glob.glob(f"{BACKUPS}/gamelists-[0-9]*.tar.gz"):
        m = re.search(r"gamelists-(\d{8})\.tar\.gz$", old)
        if m and m.group(1) < cutoff:
            os.remove(old)              # retention: 14 days of our own archives (nothing else is ever removed)
    return made


def archives():
    """The daily archives, newest first."""
    return sorted(glob.glob(f"{BACKUPS}/gamelists-[0-9]*.tar.gz"), reverse=True)


def rom_exists(system, path):
    return os.path.exists(f"{ROMS}/{system}/{path[2:] if path.startswith('./') else path}")


def guard_log(line):
    os.makedirs(os.path.dirname(GUARD_LOG), exist_ok=True)
    with open(GUARD_LOG, "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")


def default_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def thinned(game, old):
    """Scraped fields the archived <game> had and this one lacks ({tag: text}): CONTENT tags missing here, and then
    also the name if this one has ES-DE's default (the file name; on its own that can be a deliberate rename, e.g. a
    ROM hack named after its file). Stats, flags and <altemulator> are never filled back (removing them can be on
    purpose: esde_tidy, unfavouriting, choosing the default emulator)."""
    fill = {}
    for e in old:
        if e.tag in CONTENT and (e.text or "").strip() and not (game.findtext(e.tag) or "").strip():
            fill[e.tag] = e.text
    name, old_name = game.findtext("name"), old.findtext("name")
    if fill and old_name and name == default_name(game.findtext("path") or "") and old_name != name:
        fill["name"] = old_name
    return fill


def guard(fix=False):
    """Game systems whose gamelist lost entries (an entry in a daily archive, ROM still on the SD card, not in the
    gamelist now), lost scraped fields of an entry (thinned()), or doesn't parse. Returns {system: {"lost": {path:
    <game> from the newest archive having it}, "thin": {path: {tag: text}}, "broken": parse error or None,
    "restored": count}}. fix (ES-DE closed): a broken file is moved aside to gamelist.xml.broken-<time> and replaced by
    its newest archive copy, then the lost entries and fields go back (save() keeps a .bak copy). Systems whose ROM
    folder is missing (SD card not mounted) are skipped."""
    current, found, base = {}, {}, {}
    for p in all_gamelists():
        system = os.path.basename(os.path.dirname(p))
        if system in NOT_GAMES or not os.path.isdir(f"{ROMS}/{system}"):
            continue
        try:
            current[system] = Gamelist(system).games()
        except ET.ParseError as e:
            current[system] = {}
            found[system] = {"lost": {}, "thin": {}, "broken": str(e), "restored": 0}
    for a in archives():
        try:
            tar = tarfile.open(a)
            members = [m for m in tar if m.name.endswith("/gamelist.xml") and m.name.split("/")[0] in current]
            texts = {m.name.split("/")[0]: tar.extractfile(m).read().decode("utf-8") for m in members}
            tar.close()
        except (OSError, tarfile.TarError, UnicodeDecodeError):
            continue
        for system, text in texts.items():
            try:
                old = Gamelist(system, text=text)
            except ET.ParseError:
                continue
            f = found.setdefault(system, {"lost": {}, "thin": {}, "broken": None, "restored": 0})
            if f["broken"]:
                base.setdefault(system, text)
            for path, g in old.games().items():
                if not path:
                    continue
                if path in current[system]:
                    for tag, val in thinned(current[system][path], g).items():
                        f["thin"].setdefault(path, {}).setdefault(tag, val)
                elif path not in f["lost"] and rom_exists(system, path):
                    f["lost"][path] = g
    found = {s: f for s, f in sorted(found.items()) if f["lost"] or f["thin"] or f["broken"]}
    for system, f in found.items():
        guard_log(f"{system}: " + (f"gamelist does not parse ({f['broken']}); " if f["broken"] else "") +
                  f"{len(f['lost'])} entries lost (ROMs still there, {len(current[system])} entries now), "
                  f"{len(f['thin'])} lost scraped fields: " +
                  ", ".join(sorted(f["lost"] or f["thin"])[:5]) + (" ..." if len(f["lost"] or f["thin"]) > 5 else ""))
    if fix and found:
        esde_check()
        for system, f in found.items():
            path = path_for(system)
            if f["broken"]:
                if system not in base:
                    guard_log(f"{system}: no archive copy to replace the broken gamelist with, left as it is")
                    continue
                os.replace(path, path + time.strftime(".broken-%Y%m%d-%H%M%S"))
                write_atomic(path, base[system])
            gl = Gamelist(system)
            have = gl.games()
            for p, g in f["lost"].items():
                if p not in have:
                    gl.root.append(copy.deepcopy(g)); f["restored"] += 1
            filled = 0
            for p, fields in f["thin"].items():
                g = have.get(p)
                if g is None:
                    continue
                for tag, val in fields.items():
                    e = g.find(tag)
                    if e is None:
                        e = ET.SubElement(g, tag)
                    e.text = val
                filled += 1
            gl.save()
            guard_log(f"{system}: restored {f['restored']} entries, scraped fields of {filled}" +
                      (" (broken file replaced)" if f["broken"] else ""))
    return found


def check():
    """Every gamelist parses and would be written back unchanged (format check of this module)."""
    bad = []
    for p in all_gamelists():
        try:
            gl = Gamelist(p)
            if gl.render() != open(p, encoding="utf-8").read():
                bad.append((p, "differs"))
        except ET.ParseError as e:
            bad.append((p, str(e)))
    return bad


if __name__ == "__main__":
    if sys.argv[1:2] == ["backup"]:
        print(daily_backup() or "today's backup already exists")
    elif sys.argv[1:2] == ["check"]:
        b = check()
        print(f"{len(all_gamelists())} gamelists, {len(b)} would change")
        for p, why in b:
            print(" ", p, why)
    elif sys.argv[1:2] == ["guard"]:
        r = guard(fix="--fix" in sys.argv)
        for system, f in r.items():
            print(f"{system}: {len(f['lost'])} lost, {len(f['thin'])} thinned" +
                  (f", broken: {f['broken']}" if f["broken"] else "") +
                  (f", restored {f['restored']}" if f["restored"] else ""))
        print("gamelists OK" if not r else f"{len(r)} gamelists with losses (see {GUARD_LOG})")
    else:
        print(__doc__)
