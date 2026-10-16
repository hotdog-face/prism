#!/usr/bin/env python3
"""Storage guard (2026-10-14): a card before space runs out, and a reserve Prism's own files can always use.

    prism_storage.py check      run by prism_sync.py every 15 minutes
    prism_storage.py biggest    the card's "See the biggest games": build the "Biggest games" collection and open it
    prism_storage.py status

Below 25 GB free on a drive (the SD card with the Emulation folder, or the internal drive) the person gets one card;
below 10 GB another, and the low_space flag pauses Prism's own copies (Add games uploads, imports) so saves and
gamelists always have room - an empty gamelist after a full disk is what lost the Switch list on 2026-10-10.
Space coming back (30 GB) withdraws waiting cards and clears the flag. Nothing is ever deleted: "Biggest games" is a
normal collection, and games leave it with the usual L4 remove (to "Removed games", restorable).
"""
import json, os, re, shutil, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_config  # noqa: E402  (paths)

GB = 1e9
WARN, CRITICAL, CLEAR = 25, 10, 30
FLAG = f"{prism_config.PRISM}/low_space"
STATE = f"{prism_config.PRISM}/storage_state.json"
COLLECTION = "Biggest games"
BIG_N = 40


def drives():
    """(key, label, path): the drive holding the Emulation folder, and the internal one if it's a different drive."""
    out = [("games", "SD card" if prism_config.STORAGE.startswith("/run/media") else "Game drive", prism_config.STORAGE)]
    if os.stat(prism_config.HOME).st_dev != os.stat(prism_config.STORAGE).st_dev:
        out.append(("internal", "Internal storage", prism_config.HOME))
    return out


def size_label(n):
    return f"{n / 1e12:.1f} TB" if n >= 1e12 else f"{n / GB:.0f} GB"


def low_space():
    """True while Prism's own copies should wait."""
    return os.path.exists(FLAG)


def check():
    import prism_notice
    try:
        state = json.load(open(STATE))
    except (OSError, ValueError):
        state = {}
    critical = False
    for key, label, path in drives():
        try:
            du = shutil.disk_usage(path)
        except OSError:
            continue
        free = du.free / GB
        level = 2 if free < CRITICAL else 1 if free < WARN else 0
        was = state.get(key, 0)
        bar = (round(du.used / du.total, 3), f"{label} · {size_label(du.total)}", f"{free:.0f} GB free")
        if level > was:
            prism_notice.card(
                f"storage-{key}-{level}", "Storage is getting low",
                f"{free:.0f} GB left on the " + (label if label == "SD card" else label.lower()),
                f"Below {CRITICAL} GB Prism pauses new copies so your saves and game lists always have room."
                if level == 1 else "Prism has paused new copies so your saves and game lists keep room. "
                                   "Move a few big games out of the way to make space.",
                ok="See the biggest games", cancel="Later", action="run:prism_storage.py biggest", bar=bar,
                quiet_days=0)
            state[key] = level
        elif free >= CLEAR and was:
            for lv in (1, 2):
                prism_notice.withdraw(f"storage-{key}-{lv}")
            state[key] = 0
        critical |= free < CRITICAL
    if critical and not os.path.exists(FLAG):
        open(FLAG, "w").write(time.strftime("%Y-%m-%d %H:%M\n"))
    elif not critical and os.path.exists(FLAG):
        os.remove(FLAG)
    json.dump(state, open(STATE, "w"))
    return state


def _size(p):
    if os.path.isfile(p):
        return os.path.getsize(p)
    total = 0
    for dirpath, _, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def steam_sizes():
    """roms/steam/<Name>.sh -> SizeOnDisk of the game it launches."""
    out = {}
    libs = [prism_config.STEAM] + [m for m in re.findall(r'"path"\s+"([^"]+)"', open(
        f"{prism_config.STEAM}/steamapps/libraryfolders.vdf").read()) if os.path.isdir(m)]
    sizes = {}
    for lib in set(libs):
        for acf in os.listdir(f"{lib}/steamapps") if os.path.isdir(f"{lib}/steamapps") else []:
            if acf.startswith("appmanifest_"):
                t = open(f"{lib}/steamapps/{acf}", errors="replace").read()
                aid = re.search(r'"appid"\s+"(\d+)"', t); sz = re.search(r'"SizeOnDisk"\s+"(\d+)"', t)
                if aid and sz:
                    sizes[aid.group(1)] = int(sz.group(1))
    for sh in os.listdir(f"{prism_config.ROMS}/steam") if os.path.isdir(f"{prism_config.ROMS}/steam") else []:
        try:
            m = re.search(r"rungameid/(\d+)", open(f"{prism_config.ROMS}/steam/{sh}").read())
        except OSError:
            continue
        if m and m.group(1) in sizes:
            out[f"{prism_config.ROMS}/steam/{sh}"] = sizes[m.group(1)]
    return out


def biggest():
    """Write the Biggest games collection (largest first in the file) and open it in Prism."""
    games = steam_sizes()
    for system in os.listdir(prism_config.ROMS):
        d = f"{prism_config.ROMS}/{system}"
        if system in ("steam", "movies", "tv", "music", "media") or not os.path.isdir(d) or os.path.islink(d):
            continue
        for name in os.listdir(d):
            if name.startswith((".", "_")) or name.endswith((".txt", ".xml", ".cfg", ".json")):
                continue
            games[f"{d}/{name}"] = _size(f"{d}/{name}")
    top = sorted(games.items(), key=lambda kv: -kv[1])[:BIG_N]
    path = f"{prism_config.ESDE}/collections/custom-{COLLECTION}.cfg"
    import prism_esde
    with prism_esde.esde_closed("storage: Biggest games", require_idle=False):
        with open(path, "w") as f:
            f.write("".join(p + "\n" for p, _ in top))
        prism_esde.set_custom_collection(COLLECTION, True)
    time.sleep(6)
    import subprocess
    subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/esde_nav.py", COLLECTION, "open"], timeout=120)
    return top


if __name__ == "__main__":
    cmd = (sys.argv[1:] or ["status"])[0]
    if cmd == "check":
        print(check())
    elif cmd == "biggest":
        for p, n in biggest()[:10]:
            print(f"{n / GB:7.1f} GB  {p}")
    else:
        for key, label, path in drives():
            du = shutil.disk_usage(path)
            print(f"{label:18} {du.free / GB:6.0f} GB free of {size_label(du.total)}")
        print("copies paused (low_space)" if low_space() else "copies allowed")
