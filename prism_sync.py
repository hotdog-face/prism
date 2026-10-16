#!/usr/bin/env python3
"""Prism sync: new games show up in Prism without anyone running anything (2026-10-14). prism-sync.timer, every 15 min.

Steam   installed apps (all library folders) and non-Steam game shortcuts -> roms/steam/<Name>.sh, art from Steam's
        own cache (cover, hero, logo; shortcuts: their grid images), details from the Steam store, a gamelist entry.
        Launchers whose game is uninstalled (or whose shortcut is gone) move to "_Removed games/steam" - never
        deleted, and nothing is removed while a Steam library folder (SD card) isn't there. Apps (flatpaks), Prism and
        Prism's own tools are never added; list more names to skip in ~/prism/prism_sync_skip.txt.
ROMs    new files in roms/<system>/ -> ES-DE is restarted so it lists them (the nightly scrape adds art/videos).
        Xbox 360: a dropped game folder with default.xex becomes "<Name>.xex/" + a same-named link; a dropped arcade
        folder (<titleid>/000D0000/<package>) becomes the package file "<Name>".
ES-DE is closed/restarted only while Prism sits idle in front and nobody has touched a control for 3 minutes; a
"New in Prism" pop-up names what arrived.     prism_sync.py [--dry-run] [--now]  (--now: don't wait for 3 min idle)
"""
import prism_config  # noqa: E402  (paths)
import glob, html, json, os, re, shutil, socket, struct, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
EMU = prism_config.EMU
ROMS = f"{EMU}/roms"
STEAM_ROMS = f"{ROMS}/steam"
MEDIA = f"{EMU}/tools/downloaded_media/steam"
REMOVED = f"{EMU}/_Removed games/steam"
STEAM = f"{HOME}/.local/share/Steam"
CACHE = f"{STEAM}/appcache/librarycache"
STATE = f"{HERE}/prism_sync_state.json"
STORE_CACHE = f"{HERE}/steam_store_cache.json"
SKIP_FILE = f"{HERE}/prism_sync_skip.txt"
LOG = f"{HERE}/logs/prism_sync.log"
LAST_INPUT = "/tmp/deck_last_input"
SKIP_APPS = re.compile(r"Proton|Steam Linux Runtime|Steamworks|Redistributable|SteamVR|Steam Audio", re.I)
SKIP_EXE = ("/usr/bin/flatpak", "/Emulation/tools/", f"{prism_config.PRISM}/", f"{os.path.realpath(prism_config.PRISM)}/",
            f"{prism_config.HOME}/prism/", f"{prism_config.HOME}/.local/share/prism/", f"{prism_config.HOME}/deck-mcp/",
            "es-de")                     # Prism itself, its tools and the owner's own tools are never games
ROM_IGNORE = re.compile(r"^(metadata\.txt|systeminfo\.txt|readme\.txt|_.*|\..*)$", re.I)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) prism-sync/1.0"}
DRY = "--dry-run" in sys.argv


def log(*a):
    line = time.strftime("%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    if not DRY:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")


def safe(name):
    return re.sub(r'[\\/:*?"<>|]', "", name).strip().rstrip(".")


# ---------------------------------------------------------------- Steam

def library_paths():
    t = open(f"{STEAM}/steamapps/libraryfolders.vdf", errors="replace").read()
    return [p.replace("\\\\", "/") for p in re.findall(r'"path"\s+"([^"]+)"', t)]


def installed_apps():
    """{appid: name} of fully installed apps; None when a library folder is missing (SD card out)."""
    apps, missing = {}, False
    for lib in library_paths():
        d = f"{lib}/steamapps"
        if not os.path.isdir(d):
            missing = True; continue
        for f in glob.glob(f"{d}/appmanifest_*.acf"):
            t = open(f, errors="replace").read()
            m_id, m_name = re.search(r'"appid"\s+"(\d+)"', t), re.search(r'"name"\s+"([^"]+)"', t)
            flags = re.search(r'"StateFlags"\s+"(\d+)"', t)
            if not (m_id and m_name) or SKIP_APPS.search(m_name.group(1)):
                continue
            if flags and not int(flags.group(1)) & 4:            # not fully installed yet (still downloading)
                continue
            apps[m_id.group(1)] = m_name.group(1)
    return apps, missing


def _vdf_read(b, i):
    d = {}
    while True:
        t = b[i]; i += 1
        if t == 8:
            return d, i
        j = b.index(0, i); k = b[i:j].decode("utf-8", "surrogateescape"); i = j + 1
        if t == 0:
            d[k], i = _vdf_read(b, i)
        elif t == 1:
            j = b.index(0, i); d[k] = b[i:j].decode("utf-8", "surrogateescape"); i = j + 1
        elif t == 2:
            d[k] = struct.unpack("<i", b[i:i + 4])[0]; i += 4
        else:
            raise ValueError(f"vdf type {t}")


def game_shortcuts():
    """{gameid: (name, shortcut appid)} of non-Steam shortcuts that are games (not apps, Prism or its tools)."""
    skip = set()
    if os.path.exists(SKIP_FILE):
        skip = {l.strip().lower() for l in open(SKIP_FILE) if l.strip() and not l.startswith("#")}
    out = {}
    for sc in glob.glob(f"{STEAM}/userdata/*/config/shortcuts.vdf"):
        try:
            top, _ = _vdf_read(open(sc, "rb").read(), 0)
        except Exception as e:
            log("shortcuts.vdf unreadable:", sc, e); continue
        for v in (top.get("shortcuts") or {}).values():
            name, exe = v.get("AppName") or "", (v.get("Exe") or "").strip('"')
            if not name or name.lower() in skip or name == "Prism" or any(s in exe for s in SKIP_EXE) \
                    or v.get("IsHidden") == 1:       # hidden shortcuts (the Game Tester) stay hidden
                continue
            aid = v.get("appid", 0) & 0xFFFFFFFF
            out[str((aid << 32) | 0x02000000)] = (name, aid, os.path.dirname(os.path.dirname(sc)))
    return out


def launchers():
    """{id: launcher filename} for every roms/steam/*.sh (by the rungameid it starts)."""
    out = {}
    for f in glob.glob(f"{STEAM_ROMS}/*.sh"):
        m = re.search(r"rungameid/(\d+)", open(f, errors="replace").read())
        if m:
            out[m.group(1)] = os.path.basename(f)
    return out


def fetch(url, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def store_details(appid, cache):
    if appid in cache:
        return cache[appid]
    try:
        d = json.loads(fetch(f"https://store.steampowered.com/api/appdetails?appids={appid}&l=english"))
        data = d[appid]["data"] if d[appid]["success"] else {}
    except Exception as e:
        log("  store details unavailable:", appid, e); return {}
    cache[appid] = {k: data.get(k) for k in ("short_description", "genres", "release_date", "developers",
                                              "publishers", "screenshots", "header_image")}
    return cache[appid]


def copy_art(src, kind, stem):
    if src and os.path.exists(src):
        ext = os.path.splitext(src)[1].lower() or ".jpg"
        os.makedirs(f"{MEDIA}/{kind}", exist_ok=True)
        if not DRY:
            shutil.copyfile(src, f"{MEDIA}/{kind}/{stem}{ext}")
        return True
    return False


def cache_art(appid, fname):
    hits = glob.glob(f"{CACHE}/{appid}/{fname}") + glob.glob(f"{CACHE}/{appid}/*/{fname}")
    return max(hits, key=os.path.getsize) if hits else None


def grid_art(userdir, aid, suffix):
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        p = f"{userdir}/config/grid/{aid}{suffix}{ext}"
        if os.path.exists(p):
            return p
    return None


def add_launcher(gid, name, kind, extra, store_cache):
    """Write roms/steam/<Name>.sh + art; return the gamelist fields for it."""
    stem = safe(name)
    path = f"{STEAM_ROMS}/{stem}.sh"
    n = 2
    while os.path.exists(path):                                   # a different game with the same name
        path = f"{STEAM_ROMS}/{stem} ({n}).sh"; n += 1
    stem = os.path.basename(path)[:-3]
    what = f"Steam app {gid}" if kind == "app" else f"non-Steam shortcut {extra[0]}"
    if not DRY:
        os.makedirs(STEAM_ROMS, exist_ok=True)
        with open(path, "w") as f:
            f.write(f"#!/bin/bash\n# {name} ({what}; added by prism_sync.py)\nunset LD_LIBRARY_PATH\n"
                    f"exec /usr/bin/steam -ifrunning steam://rungameid/{gid}\n")
        os.chmod(path, 0o755)
    fields = {"path": f"./{stem}.sh", "name": name}
    if kind == "app":
        copy_art(cache_art(gid, "library_600x900.jpg") or cache_art(gid, "library_600x900_2x.jpg"), "covers", stem)
        copy_art(cache_art(gid, "logo.png"), "marquees", stem)
        copy_art(cache_art(gid, "library_hero.jpg"), "fanart", stem)
        data = store_details(gid, store_cache)
        if data.get("screenshots") and not DRY:
            try:
                os.makedirs(f"{MEDIA}/screenshots", exist_ok=True)
                open(f"{MEDIA}/screenshots/{stem}.jpg", "wb").write(fetch(data["screenshots"][0]["path_full"]))
            except Exception as e:
                log("  screenshot:", e)
        if data.get("short_description"):
            fields["desc"] = html.unescape(data["short_description"])
        m = re.search(r"(\d{4})", (data.get("release_date") or {}).get("date", "") or "")
        if m:
            fields["releasedate"] = f"{m.group(1)}0101T000000"
        if data.get("developers"):
            fields["developer"] = ", ".join(data["developers"])
        if data.get("publishers"):
            fields["publisher"] = ", ".join(data["publishers"])
        if data.get("genres"):
            fields["genre"] = ", ".join(g["description"] for g in data["genres"][:2])
    else:
        aid, userdir = extra
        copy_art(grid_art(userdir, aid, "p"), "covers", stem)
        copy_art(grid_art(userdir, aid, "_logo"), "marquees", stem)
        copy_art(grid_art(userdir, aid, "_hero"), "fanart", stem)
        copy_art(grid_art(userdir, aid, ""), "screenshots", stem)
    log(f"  + {name} -> roms/steam/{stem}.sh ({what})")
    return fields


def retire_launcher(fname):
    """Move a launcher and its art to _Removed games/steam (kept, never deleted)."""
    stem = fname[:-3]
    if DRY:
        log(f"  - would move {fname} to _Removed games/steam"); return
    os.makedirs(REMOVED, exist_ok=True)
    shutil.move(f"{STEAM_ROMS}/{fname}", f"{REMOVED}/{fname}")
    for f in glob.glob(f"{MEDIA}/*/{glob.escape(stem)}.*"):
        dst = f"{REMOVED}/media/{os.path.basename(os.path.dirname(f))}"
        os.makedirs(dst, exist_ok=True)
        shutil.move(f, f"{dst}/{os.path.basename(f)}")
    log(f"  - {fname} moved to _Removed games/steam (game uninstalled / shortcut removed)")


def sync_steam(state):
    apps, missing = installed_apps()
    shortcuts = game_shortcuts()
    have = launchers()
    store_cache = json.load(open(STORE_CACHE)) if os.path.exists(STORE_CACHE) else {}
    added = []
    for gid, name in sorted(apps.items(), key=lambda kv: kv[1].lower()):
        if gid not in have:
            state["pending_gamelist"].append(add_launcher(gid, name, "app", None, store_cache)); added.append(name)
    for gid, (name, aid, userdir) in sorted(shortcuts.items(), key=lambda kv: kv[1][0].lower()):
        if gid not in have:
            state["pending_gamelist"].append(add_launcher(gid, name, "shortcut", (aid, userdir), store_cache))
            added.append(name)
    if not DRY:
        json.dump(store_cache, open(STORE_CACHE, "w"))
    removed = []
    if missing:
        log("a Steam library folder is missing (SD card?) - not removing anything this time")
    elif apps:
        for gid, fname in have.items():
            is_app = int(gid) < (1 << 32)
            gone = (gid not in apps) if is_app else (gid not in shortcuts)
            if gone:
                retire_launcher(fname); removed.append(fname[:-3])
    return added, removed


# ---------------------------------------------------------------- emulators

def wrap_xbox360():
    """Dropped Xbox 360 folders -> the layout Prism launches (see the module docstring)."""
    root, out = f"{ROMS}/xbox360/roms", []
    if not os.path.isdir(root):
        return out
    for e in sorted(os.listdir(root)):
        p = f"{root}/{e}"
        if not os.path.isdir(p) or e.startswith((".", "_")) or e.lower() == "xbla" or e.lower().endswith(".xex"):
            continue
        xex = next((f for f in os.listdir(p) if f.lower() == "default.xex"), None)
        name = re.sub(r"\.", "", e).strip()                        # no dots: ES-DE reads them as an extension
        if xex:
            dest = f"{root}/{name}.xex"
            if not os.path.exists(dest) and not DRY:
                os.rename(p, dest); os.symlink(xex, f"{dest}/{name}.xex")
            out.append(name); log(f"  xbox360: wrapped {e}/ as {name}.xex/")
            continue
        pk = [f for f in glob.glob(f"{p}/*/000D0000/*") if os.path.isfile(f) and not os.path.basename(f).startswith("._")]
        target = f"{root}/{name}"
        if len(pk) == 1 and (target == p or not os.path.exists(target)):   # the folder itself may carry the name
            if not DRY:
                tmp = f"{root}/.{name}.pkg"
                shutil.move(pk[0], tmp)
                kept = f"{EMU}/_Removed games/xbox360-emptied"
                os.makedirs(kept, exist_ok=True)
                shutil.move(p, f"{kept}/{e}-{int(time.time())}")             # the now-empty folder, kept
                os.rename(tmp, target)
            out.append(name); log(f"  xbox360: arcade package from {e}/ as {name}")
    return out


def rom_snapshot():
    snap = {}
    for d in sorted(glob.glob(f"{ROMS}/*/")):
        system = os.path.basename(d.rstrip("/"))
        if system == "steam":
            continue
        base = f"{d}roms" if system == "xbox360" else d.rstrip("/")
        try:
            snap[system] = sorted(e for e in os.listdir(base) if not ROM_IGNORE.match(e))
        except OSError:
            pass
    return snap


# ---------------------------------------------------------------- apply

def minutes_since_input():
    try:
        return (time.time() - os.path.getmtime(LAST_INPUT)) / 60
    except OSError:
        return 999


def toast(title, sub):
    for attempt in (1, 2):
        try:
            c = socket.socket(socket.AF_UNIX); c.settimeout(2); c.connect("/tmp/deck_cc.sock")
            c.sendall((json.dumps({"cmd": "toast", "title": title, "sub": sub, "cover": ""}) + "\n").encode())
            c.recv(64); c.close()
            return
        except OSError:
            if attempt == 2:
                return
            try:                      # the card overlay isn't running: start it (it shows the pop-up)
                import deck_curate
                deck_curate.cc_daemon()
            except Exception:
                return


def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    state.setdefault("pending_gamelist", []); state.setdefault("pending_names", [])
    first_run = "roms" not in state
    added, removed = sync_steam(state)
    import prism_features
    wrapped = wrap_xbox360() if prism_features.enabled("xbox360") else []
    snap = rom_snapshot()
    new_roms = []
    if not first_run:
        for system, files in snap.items():
            old = set(state["roms"].get(system, []))
            new_roms += [f"{f} ({system})" for f in files if f not in old]
    state["pending_names"] += added + [f"{n} (xbox360)" for n in wrapped] + new_roms
    need = bool(state["pending_gamelist"] or state["pending_names"] or removed or state.get("pending_restart"))
    if removed:
        state["pending_restart"] = True
    if first_run:
        log("first run: recorded", sum(len(v) for v in snap.values()), "ROM entries; nothing to restart for")
    state["roms"] = snap
    if not need:
        if not DRY:
            json.dump(state, open(STATE, "w"), indent=1)
        return
    import prism_state, prism_esde
    why = prism_state.not_idle()
    quiet = minutes_since_input()
    if why or (quiet < 3 and "--now" not in sys.argv):
        log(f"changes waiting for an idle Prism ({why or f'last input {quiet:.1f} min ago'}): "
            f"{len(state['pending_names'])} new, {len(state['pending_gamelist'])} gamelist entries")
        if not DRY:
            json.dump(state, open(STATE, "w"), indent=1)
        return
    if DRY:
        log("dry run: would restart ES-DE for", state["pending_names"] or "removals"); return
    try:
        with prism_esde.esde_closed("new games (prism_sync)"):
            if state["pending_gamelist"]:
                import gamelist
                gl = gamelist.Gamelist("steam")
                for fields in state["pending_gamelist"]:
                    gl.merge(fields)
                gl.save()
                log("gamelist: added", len(state["pending_gamelist"]), "Steam entries")
    except prism_esde.NotIdle as e:
        log("not idle after all:", e); json.dump(state, open(STATE, "w"), indent=1); return
    names = state["pending_names"]
    log("ES-DE restarted; new:", ", ".join(names) or "-", "| removed:", len(removed))
    if names:
        time.sleep(4)
        toast("New in Prism", ", ".join(n.split(" (")[0] for n in names[:4]) + (f" +{len(names) - 4} more" if len(names) > 4 else ""))
    state["pending_gamelist"], state["pending_names"], state["pending_restart"] = [], [], False
    json.dump(state, open(STATE, "w"), indent=1)


if __name__ == "__main__":
    main()
    try:                      # storage guard rides along every 15 minutes (cards via prism_notice)
        import prism_storage
        prism_storage.check()
    except Exception as e:
        log("storage check failed:", e)
    try:                      # Prism fixes itself: services, stuck Steam UI state, stray files, games drive
        import prism_doctor
        prism_doctor.live()
    except Exception as e:
        log("doctor failed:", e)
