#!/usr/bin/env python3
"""Non-Steam shortcuts for the Prism installer: read and write Steam's binary shortcuts.vdf.

    python3 steam_shortcuts.py list
    python3 steam_shortcuts.py add  NAME EXE [--start-dir D] [--options "..."] [--icon PNG] [--tag T]
    python3 steam_shortcuts.py remove NAME

Steam rewrites shortcuts.vdf when it exits, so Steam must not be running while this writes (the installer shuts it
down first). Every write keeps the previous file as shortcuts.vdf.prism-bak. The app id is the one Steam itself uses
for a non-Steam shortcut: crc32(exe + name) | 0x80000000; `steam://rungameid/` takes (appid << 32) | 0x02000000.
Art for a shortcut goes to userdata/<user>/config/grid/<appid>p.png (portrait), <appid>.png (wide), <appid>_hero.png,
<appid>_logo.png - see set_art().
"""
import glob, os, shutil, struct, sys, zlib

STEAM = os.path.expanduser("~/.local/share/Steam")


# ---- binary VDF -----------------------------------------------------------------------------------------------
def _read_cstr(b, i):
    j = b.index(b"\x00", i)
    return b[i:j].decode("utf-8", "replace"), j + 1


def _parse(b, i=0):
    out = {}
    while True:
        t = b[i]; i += 1
        if t == 0x08:
            return out, i
        key, i = _read_cstr(b, i)
        if t == 0x00:
            out[key], i = _parse(b, i)
        elif t == 0x01:
            out[key], i = _read_cstr(b, i)
        elif t == 0x02:
            out[key] = struct.unpack_from("<I", b, i)[0]; i += 4
        else:
            raise ValueError(f"shortcuts.vdf: unknown field type {t:#x} at {i - 1}")


def load(path):
    if not os.path.exists(path):
        return {}
    b = open(path, "rb").read()
    if not b:
        return {}
    data, _ = _parse(b + b"\x08")
    return data.get("shortcuts", {})


def _dump(d):
    out = bytearray()
    for k, v in d.items():
        kb = k.encode() + b"\x00"
        if isinstance(v, dict):
            out += b"\x00" + kb + _dump(v)
        elif isinstance(v, int):
            out += b"\x02" + kb + struct.pack("<I", v & 0xFFFFFFFF)
        else:
            out += b"\x01" + kb + str(v).encode() + b"\x00"
    return bytes(out) + b"\x08"


def save(path, shortcuts):
    items = {str(i): v for i, v in enumerate(shortcuts.values())}   # Steam wants keys "0", "1", ...
    data = b"\x00shortcuts\x00" + _dump(items) + b"\x08"
    if os.path.exists(path):
        shutil.copy2(path, path + ".prism-bak")
    tmp = path + ".prism-tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


# ---- users / ids ------------------------------------------------------------------------------------------------
def users():
    """Steam user folders that have signed in on this Deck, most recently used first."""
    dirs = [d for d in glob.glob(f"{STEAM}/userdata/*") if os.path.basename(d).isdigit() and os.path.basename(d) != "0"]
    return sorted(dirs, key=lambda d: os.path.getmtime(f"{d}/config") if os.path.isdir(f"{d}/config") else 0,
                  reverse=True)


def appid(exe, name):
    return zlib.crc32((exe + name).encode()) | 0x80000000


def rungameid(aid):
    return (aid << 32) | 0x02000000


def steam_running():
    import subprocess
    return subprocess.run(["pgrep", "-x", "steam"], capture_output=True).returncode == 0


# ---- operations -------------------------------------------------------------------------------------------------
def add(name, exe, start_dir="", options="", icon="", tags=(), user_dir=None, hidden=False):
    """Add (or update, matched by name) a shortcut for every signed-in user. Returns the app id."""
    if steam_running():
        raise RuntimeError("Steam is running; close it first (it overwrites shortcuts.vdf on exit)")
    q = lambda s: s if s.startswith('"') else f'"{s}"'
    aid = appid(q(exe), name)
    for u in [user_dir] if user_dir else users():
        path = f"{u}/config/shortcuts.vdf"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sc = load(path)
        sc = {k: v for k, v in sc.items() if v.get("AppName", v.get("appname")) != name}
        sc["new"] = {"appid": aid, "AppName": name, "Exe": q(exe), "StartDir": q(start_dir or os.path.dirname(exe)),
                     "icon": icon, "ShortcutPath": "", "LaunchOptions": options, "IsHidden": 1 if hidden else 0,
                     "AllowDesktopConfig": 1, "AllowOverlay": 1, "OpenVR": 0, "Devkit": 0, "DevkitGameID": "",
                     "DevkitOverrideAppID": 0, "LastPlayTime": 0, "FlatpakAppID": "",
                     "tags": {str(i): t for i, t in enumerate(tags)}}
        save(path, sc)
    return aid


def remove(name):
    if steam_running():
        raise RuntimeError("Steam is running; close it first")
    n = 0
    for u in users():
        path = f"{u}/config/shortcuts.vdf"
        sc = load(path)
        keep = {k: v for k, v in sc.items() if v.get("AppName", v.get("appname")) != name}
        if len(keep) != len(sc):
            save(path, keep); n += len(sc) - len(keep)
    return n


def set_art(aid, portrait=None, wide=None, hero=None, logo=None):
    """Copy art into every user's grid folder (missing ones are skipped)."""
    for u in users():
        grid = f"{u}/config/grid"
        os.makedirs(grid, exist_ok=True)
        for src, suffix in ((portrait, "p"), (wide, ""), (hero, "_hero"), (logo, "_logo")):
            if src and os.path.exists(src):
                shutil.copy2(src, f"{grid}/{aid}{suffix}{os.path.splitext(src)[1]}")


def remove_art(aid):
    for u in users():
        for f in glob.glob(f"{u}/config/grid/{aid}*"):
            os.remove(f)


def main():
    a = sys.argv[1:]
    if not a or a[0] == "list":
        for u in users():
            for v in load(f"{u}/config/shortcuts.vdf").values():
                print(f"{os.path.basename(u)}\t{v.get('appid', 0)}\t{v.get('AppName', v.get('appname'))}\t{v.get('Exe', v.get('exe'))}")
    elif a[0] == "add":
        opts = {"--start-dir": "", "--options": "", "--icon": "", "--tag": []}
        rest = []
        it = iter(a[1:])
        for x in it:
            if x in opts:
                v = next(it)
                opts[x] = opts[x] + [v] if isinstance(opts[x], list) else v
            else:
                rest.append(x)
        aid = add(rest[0], rest[1], opts["--start-dir"], opts["--options"], opts["--icon"], opts["--tag"])
        print(aid, rungameid(aid))
    elif a[0] == "remove":
        print(remove(a[1]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
