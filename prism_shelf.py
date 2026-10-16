#!/usr/bin/env python3
"""Only games that work on the shelf (2026-10-14): games that fail their boot test move to "Needs a look".

"Needs a look" is its own row on the home screen (ES-DE system `needsalook`, roms/needsalook/<game>, one flat list;
look_index.json remembers each game's home), so the games keep their art and details and can be seen, but don't
clutter the systems they came from. Picking one there
asks "Put back in <system>?"; yes moves it home and remembers that choice (look_kept.txt), so the nightly test never
moves it again. Nothing is ever deleted.

    prism_shelf.py move SYSTEM RELPATH     (used by esde_tidy.py, ES-DE closed)
    prism_shelf.py restore ROM             (ES-DE's launch command for the needsalook system)
    prism_shelf.py list
"""
import glob, html, json, os, re, shutil, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_config  # noqa: E402  (paths)

LOOK = "needsalook"
LOOK_DIR = f"{prism_config.ROMS}/{LOOK}"
MEDIA = f"{prism_config.TOOLS}/downloaded_media"
KEPT = f"{prism_config.PRISM}/look_kept.txt"
INDEX = f"{prism_config.PRISM}/look_index.json"      # name in the Needs a look folder -> "<system>/<path in system>"
LOG = f"{prism_config.PRISM}/logs/prism_shelf.log"


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")
    print(msg, flush=True)


def kept():
    try:
        return set(open(KEPT).read().split("\n")) - {""}
    except OSError:
        return set()


def index():
    try:
        return json.load(open(INDEX))
    except (OSError, ValueError):
        return {}


def _save_index(ix):
    tmp = INDEX + ".tmp"
    json.dump(ix, open(tmp, "w"), indent=1)
    os.replace(tmp, INDEX)


def home_of(rom):
    """(system, path in system) for a file in the Needs a look folder."""
    name = os.path.relpath(rom, LOOK_DIR)
    ix = index()
    if name in ix:
        return tuple(ix[name].split("/", 1))
    return tuple(name.split("/", 1))           # the first, folder-per-system layout


def fullname(system):
    """The system's display name from ES-DE's systems files (custom first)."""
    for f in [f"{prism_config.ESDE}/custom_systems/es_systems.xml",
              f"{prism_config.ESDE_RESOURCES}/es_systems.xml"]:
        try:
            m = re.search(rf"<name>{re.escape(system)}</name>\s*<fullname>([^<]+)</fullname>", open(f).read())
            if m:
                return html.unescape(m.group(1))
        except OSError:
            pass
    return system


def _media(system, rel, name):
    """(source file, link under the needsalook media folder) for every image/video ES-DE has for this game."""
    stem = os.path.splitext(rel)[0] if not os.path.isdir(f"{prism_config.ROMS}/{system}/{rel}") else rel
    stem = re.sub(r"\.(m3u|xex)$", "", stem)
    out = []
    for typedir in glob.glob(f"{MEDIA}/{system}/*/"):
        kind = os.path.basename(typedir.rstrip("/"))
        for f in glob.glob(glob.escape(f"{typedir}{stem}") + ".*"):
            out.append((f, f"{MEDIA}/{LOOK}/{kind}/{_stem(name)}{os.path.splitext(f)[1]}"))
    return out


def _stem(name):
    return re.sub(r"\.(m3u|xex)$", "", os.path.splitext(name)[0] if "." in name else name)


def move(system, rel):
    """roms/<system>/<rel> -> roms/needsalook/<system>/<rel>, with its gamelist entry and art. ES-DE must be closed."""
    import gamelist
    src = f"{prism_config.ROMS}/{system}/{rel}"
    name = os.path.basename(rel)
    if os.path.lexists(f"{LOOK_DIR}/{name}"):                # same file name from another system
        base, ext = os.path.splitext(name)
        name = f"{base} [{system}]{ext}"
    dst = f"{LOOK_DIR}/{name}"
    if f"{system}/{rel}" in kept() or not os.path.exists(src) or os.path.lexists(dst):
        return False
    os.makedirs(LOOK_DIR, exist_ok=True)
    shutil.move(src, dst)
    ix = index(); ix[name] = f"{system}/{rel}"; _save_index(ix)
    gl, look = gamelist.Gamelist(system), gamelist.Gamelist(LOOK)
    g = gl.find(f"./{rel}")
    fields = {"path": f"./{name}"}
    if g is not None:
        for e in g:
            if e.tag != "path" and e.text and e.tag != "hidden":
                fields[e.tag] = e.text
    fields.setdefault("name", os.path.splitext(os.path.basename(rel))[0])
    fields["desc"] = (f"Didn't start in Prism's nightly test (from {fullname(system)}). Press A to put it back. "
                      + (fields.get("desc") or ""))[:2000]
    look.merge(fields, overwrite=True)
    os.makedirs(os.path.dirname(look.path), exist_ok=True)
    look.save(backup=False)
    for f, link in _media(system, rel, name):
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if not os.path.lexists(link):
            os.symlink(f, link)
    log(f"moved {system}/{rel} to Needs a look")
    return True


def restore_now(rom):
    """Move a needsalook game back home (ES-DE closed by the caller)."""
    import gamelist
    rel_look = os.path.relpath(rom, LOOK_DIR)
    system, rel = home_of(rom)
    dst = f"{prism_config.ROMS}/{system}/{rel}"
    if os.path.exists(dst):
        raise RuntimeError(f"{dst} already exists")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    for link in glob.glob(f"{MEDIA}/{LOOK}/*/" + glob.escape(_stem(os.path.basename(rel_look))) + ".*") + \
            glob.glob(f"{MEDIA}/{LOOK}/*/{system}/" + glob.escape(_stem(os.path.basename(rel))) + ".*"):
        if os.path.islink(link):
            os.remove(link)
    shutil.move(rom, dst)
    look = gamelist.Gamelist(LOOK)
    g = look.find(f"./{rel_look}")
    if g is not None:
        home = gamelist.Gamelist(system)          # its entry normally never left; put it back if ES-DE dropped it
        fields = {e.tag: e.text for e in g if e.text and e.tag != "path"}
        fields["desc"] = re.sub(r"^Didn't start in Prism's nightly test \(from [^)]*\)\. Press A to put it back\. ",
                                "", fields.get("desc", ""))
        if not fields["desc"]:
            del fields["desc"]
        home.merge({"path": f"./{rel}", **fields})
        home.save()
        look.root.remove(g)
        look.save(backup=False, allow_shrink=True)
    ix = index(); ix.pop(rel_look, None); _save_index(ix)
    with open(KEPT, "a") as f:
        f.write(f"{system}/{rel}\n")
    log(f"put back {system}/{rel} (kept)")
    return system, rel


def restore(rom):
    """ES-DE launched a needsalook entry: ask, then hand the move to a worker outside ES-DE's process tree (moving
    the file and fixing the gamelists needs ES-DE closed, which would end this process)."""
    system = home_of(rom)[0]
    import gamelist
    entry = gamelist.Gamelist(LOOK).find(f"./{os.path.relpath(rom, LOOK_DIR)}")
    name = (entry.findtext("name") if entry is not None else "") or _stem(os.path.basename(rom))
    shots = [f for k in ("screenshots", "titlescreens", "covers")
             for f in glob.glob(f"{MEDIA}/{LOOK}/{k}/" + glob.escape(_stem(os.path.basename(rom))) + ".*")]
    r = subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/deck_confirm.py", f"Put back in {fullname(system)}?",
                        name, "It didn't start in the nightly test. Put it back if it works for you, or if you want "
                        "to try it yourself.", "Put back", "-"], capture_output=True, text=True,
                       env={**os.environ, "DECK_CONFIRM_CANCEL": "Leave it", "DECK_CONFIRM_BG": shots[0] if shots else ""})
    if "CONFIRMED" not in r.stdout:
        return
    subprocess.run(["systemd-run", "--user", "--collect", "--quiet", "--unit", f"prism-restore-{int(time.time())}",
                    "/usr/bin/python3", os.path.abspath(__file__), "restore-worker", rom])


def restore_worker(rom):
    import prism_esde
    time.sleep(1.5)                       # let ES-DE finish its "game ended" step
    with prism_esde.esde_closed("put back from Needs a look", require_idle=False):
        system, rel = restore_now(rom)
    try:
        import prism_notice
        prism_notice.toast(f"restored-{system}/{rel}", "Put back", f"{os.path.splitext(os.path.basename(rel))[0]} "
                           f"is back in {fullname(system)}.")
    except Exception:
        pass


def refresh_art():
    """Home-row tile (box art of the games in it) and header icon, in the theme's tile style (make_media_tiles)."""
    import math, cairo
    import make_media_tiles as mt
    from deck_power_menu import neon
    covers = sorted(glob.glob(f"{MEDIA}/{LOOK}/covers/*"))[:3]
    n = len(listing())
    pics = [mt.pix(c, 236, 352) for c in covers]
    pics = (pics * 3)[:3] if pics else [None] * 3                    # one or two games: repeat them across the fan
    if len(covers) == 2:
        pics = [pics[0], pics[1], pics[0]]
    mt.tile(pics, "Needs a look",
            f"{n} game{'s' if n != 1 else ''} to check", f"{mt.THEME}/tiles/{LOOK}.webp")
    W = 160
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, W); cr = cairo.Context(surf)
    cr.new_path(); cr.arc(80, 80, 74, 0, 2 * math.pi); cr.set_source_rgb(0.08, 0.08, 0.08); cr.fill()
    cr.new_path(); cr.arc(80, 80, 70, 0, 2 * math.pi); neon(cr); cr.fill()
    cr.set_source_rgb(1, 1, 1); cr.set_line_width(9); cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.new_path(); cr.arc(72, 72, 22, 0, 2 * math.pi); cr.stroke()           # magnifier
    cr.move_to(88, 88); cr.line_to(108, 108); cr.stroke()
    png = "/tmp/_look_header.png"; surf.write_to_png(png)          # still webp (ffmpeg picks the animated encoder)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", png, "-c:v", "libwebp", "-quality", "92",
                    f"{mt.THEME}/header/{LOOK}.webp"], check=True)


def system_xml():
    """ES-DE <system> for the Needs a look row: every extension any system uses, launch = put back."""
    exts = set()
    for f in [f"{prism_config.ESDE}/custom_systems/es_systems.xml",
              f"{prism_config.ESDE_RESOURCES}/es_systems.xml"]:
        try:
            for m in re.finditer(r"<extension>([^<]*)</extension>", open(f).read()):
                exts |= {e for e in m.group(1).split() if len(e) > 1}
        except OSError:
            pass
    return ("  <system>\n    <name>needsalook</name>\n    <fullname>Needs a look</fullname>\n"
            "    <path>%ROMPATH%/needsalook</path>\n"
            f"    <extension>{' '.join(sorted(exts))}</extension>\n"
            f"    <command label=\"Put back\">/usr/bin/python3 {prism_config.PRISM}/prism_shelf.py restore %ROM%</command>\n"
            "    <platform>ignore</platform>\n    <theme>needsalook</theme>\n  </system>\n")


def install_system():
    """Add (or refresh) the needsalook system in ES-DE's custom systems file. ES-DE reads it at start."""
    p = f"{prism_config.ESDE}/custom_systems/es_systems.xml"
    xml = open(p).read()
    block = system_xml()
    new = re.sub(r"  <system>\s*<name>needsalook</name>.*?</system>\n", lambda m: block, xml, flags=re.S)
    if new == xml and "<name>needsalook</name>" not in xml:
        new = xml.replace("</systemList>", "  <!-- Needs a look (prism_shelf.py): games that didn't start in the nightly test -->\n"
                          + block + "</systemList>")
    if new != xml:
        tmp = p + ".tmp"; open(tmp, "w").write(new); os.replace(tmp, p)
        return True
    return False


def listing():
    return sorted(f"{home} (as {name})" for name, home in index().items() if os.path.lexists(f"{LOOK_DIR}/{name}"))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["move"] and len(a) == 3:
        print(move(a[1], a[2]))
    elif a[:1] == ["restore"] and len(a) == 2:
        restore(a[1])
    elif a[:1] == ["restore-worker"] and len(a) == 2:
        restore_worker(a[1])
    elif a[:1] == ["install-system"]:
        print(install_system())
    elif a[:1] == ["art"]:
        refresh_art()
    elif a[:1] == ["list"]:
        print("\n".join(listing()) or "(empty)")
    else:
        sys.exit(__doc__)
