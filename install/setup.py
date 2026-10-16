#!/usr/bin/env python3
"""Prism install / uninstall steps (run by install.sh and uninstall.sh; no dialogs here).

    setup.py check     --src DIR
    setup.py install   --src DIR [--dest ~/prism] [--root-ok 0|1] [--features plex scrape ...]
                       [--plex-url U --plex-token T] [--overseerr-url U] [--backup-ssh S --backup-dir D]
    setup.py uninstall [--keep-data]
    setup.py status

Everything install creates is listed in ~/.config/prism/installed.json; uninstall removes exactly that, so a Deck
without Prism looks like it did before. Running install again updates in place (code, theme, units, ES-DE hooks) and
keeps settings, gamelists and favorites.
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, tempfile, time

HOME = os.path.expanduser("~")
CONF_DIR = f"{HOME}/.config/prism"
CONF = f"{CONF_DIR}/config.toml"
RECORD = f"{CONF_DIR}/installed.json"
DATA = f"{HOME}/.local/share/prism"           # ES-DE's home for Prism (ES-DE adds /ES-DE) + the ES-DE app
ESDE_HOME = DATA
ESDE = f"{DATA}/ES-DE"
APP = f"{DATA}/es-de-app"
LAUNCHER = f"{DATA}/prism.sh"
VERSIONS = f"{DATA}/versions"                 # one folder per installed version; ~/prism links to the current one
LINK = f"{HOME}/prism"
UNIT_DIR = f"{HOME}/.config/systemd/user"
STEAM = f"{HOME}/.local/share/Steam"
ESDE_VERSION = "3.5.0"

FEATURE_UNITS = {None: ["switchhome.service", "prism-sync.service", "prism-sync.timer"],
                 "plex": ["plex-sync.service", "plex-sync.timer"],
                 "scrape": ["night-scrape.service", "night-scrape.timer"],
                 "backup": ["save-backup.service", "save-backup.timer"]}
ENABLE = {"switchhome.service", "prism-sync.timer", "plex-sync.timer", "night-scrape.timer", "save-backup.timer"}
FEATURE_SYSTEMS = {"plex": {"movies", "tv", "media", "music"}}   # ES-DE systems that need a feature (Music plays Plex music)


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def sh(*a, check=True, **kw):
    return subprocess.run(list(a), capture_output=True, text=True, check=check, **kw)


# ---- small TOML writer (flat keys: str / int / bool / list of str) ----------------------------------------------
def read_conf():
    try:
        import tomllib
        with open(CONF, "rb") as f:
            return tomllib.load(f)
    except (OSError, ValueError):
        return {}


def write_conf(cfg):
    def val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, list):
            return "[" + ", ".join(val(x) for x in v) + "]"
        return json.dumps(str(v))
    os.makedirs(CONF_DIR, exist_ok=True)
    lines = ["# Prism settings (written by the installer; safe to edit). See ~/prism/README.md."]
    lines += [f"{k} = {val(v)}" for k, v in cfg.items() if not isinstance(v, dict)]
    tmp = CONF + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, CONF)


def record_load():
    try:
        return json.load(open(RECORD))
    except (OSError, ValueError):
        return {}


def record_save(r):
    os.makedirs(CONF_DIR, exist_ok=True)
    json.dump(r, open(RECORD + ".tmp", "w"), indent=1)
    os.replace(RECORD + ".tmp", RECORD)


# ---- detection ------------------------------------------------------------------------------------------------------
def emudeck_emulation():
    p = f"{HOME}/.config/EmuDeck/settings.sh"
    try:
        for line in open(p):
            m = re.match(r"\s*(?:export\s+)?emulationPath=(.+)", line)
            if m:
                v = m.group(1).split("#")[0].strip().replace('"', "").replace("'", "")
                return v.replace("$HOME", HOME).rstrip("/")
    except OSError:
        pass
    return None


def esde_appimage():
    """EmuDeck's ES-DE AppImage (the real one, not a wrapper script)."""
    for p in [f"{HOME}/Applications/ES-DE.AppImage"] + sorted(glob.glob(f"{HOME}/Applications/ES-DE*.AppImage*")):
        if os.path.isfile(p):
            with open(p, "rb") as f:
                head = f.read(4)
            if head == b"\x7fELF":
                return p
    return None


def check(src):
    problems, warns = [], []
    osr = open("/etc/os-release").read() if os.path.exists("/etc/os-release") else ""
    if "ID=steamos" not in osr:
        warns.append("This doesn't look like SteamOS; Prism is only tested on SteamOS 3.7.")
    if subprocess.run(["systemctl", "--user", "is-active", "-q", "gamescope-session.service"]).returncode == 0 \
            and not os.environ.get("PRISM_REHEARSAL") and not shortcuts_in_place(record_load()):
        problems.append("Run the installer from Desktop Mode (Steam restarts by itself in Game Mode, which would "
                        "undo the Prism shortcut).")
    emu = emudeck_emulation()
    if not emu or not os.path.isdir(f"{emu}/roms"):
        problems.append("EmuDeck isn't set up (no Emulation folder found). Install EmuDeck and run it once first.")
    if not esde_appimage():
        problems.append("ES-DE isn't installed. Install it from EmuDeck (Manage Emulators → ES-DE) first.")
    if not os.path.exists(f"{src}/esde/bin/es-de"):
        problems.append("This Prism download is missing its ES-DE build (esde/bin/es-de).")
    if not os.path.isdir(f"{HOME}/homebrew/plugins"):
        warns.append("Decky Loader isn't installed: Prism works, but its Steam-menu helper (sleep from the Power menu, "
                     "Steam-button card over Steam's own menus) stays off until you install Decky and run this again.")
    if not users():
        problems.append("No Steam account has signed in on this Deck yet.")
    free = shutil.disk_usage(HOME).free / 1e9
    if free < 1.0:
        problems.append(f"Only {free:.1f} GB free in your home folder; Prism needs about 1 GB.")
    if os.path.exists(RECORD):
        warns.append("Prism is already installed; this updates it and keeps your settings.")
    for w in warns:
        print("warn: " + w)
    for p in problems:
        print(p)
    if not problems:
        print("ok")
    return not problems


def users():
    return [d for d in glob.glob(f"{STEAM}/userdata/*") if os.path.basename(d).isdigit() and os.path.basename(d) != "0"]


# ---- steps ------------------------------------------------------------------------------------------------------------
def fill(text, values):
    for k, v in values.items():
        text = text.replace(f"@{k}@", v)
    left = re.findall(r"@[A-Z_]+@", text)
    if left:
        raise RuntimeError(f"unfilled placeholders: {sorted(set(left))}")
    return text


def release_version(src):
    try:
        return open(f"{src}/VERSION").read().split()[0]
    except (OSError, IndexError):
        return time.strftime("%Y%m%d-%H%M")


CARRY_INSIDE = ["emu/dolphin_states.txt", "emu/duckstation_serials.txt", "emu/pcsx2_states.txt", "selftest/reports"]


def install_code(src, record):
    """Copy this release into versions/<version>, carry over what Prism keeps next to its code (settings such as
    plex.json, logs, notices) from the version in use, then point ~/prism at the new one in one step (prism_guard
    can point it back)."""
    ver = release_version(src)
    new = f"{VERSIONS}/{ver}"
    old = os.path.realpath(LINK) if os.path.islink(LINK) else None
    log(f"code -> {new}")
    os.makedirs(VERSIONS, exist_ok=True)
    # Build next to the target and swap it in: an update with the same version name as the one in use (an export of
    # uncommitted changes keeps the HEAD hash) used to delete the live folder and re-copy it with nothing carried
    # over - Plex sign-in, For You, the removal log... were lost that way on 2026-10-14.
    if os.path.realpath(src) != os.path.realpath(new):
        build = f"{new}.building"
        if os.path.exists(build):
            shutil.rmtree(build)
        shutil.copytree(src, build, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        if old and os.path.isdir(old):
            shipped = set(os.listdir(src))
            for name in os.listdir(old):
                if name not in shipped and name not in (".git", "__pycache__"):
                    s_, d_ = f"{old}/{name}", f"{build}/{name}"
                    shutil.copytree(s_, d_, symlinks=True) if os.path.isdir(s_) else shutil.copy2(s_, d_)
            for rel in CARRY_INSIDE:                     # state kept inside shipped folders (emu/, selftest/)
                s_, d_ = f"{old}/{rel}", f"{build}/{rel}"
                if os.path.exists(s_) and not os.path.exists(d_):
                    os.makedirs(os.path.dirname(d_), exist_ok=True)
                    shutil.copytree(s_, d_, symlinks=True) if os.path.isdir(s_) else shutil.copy2(s_, d_)
        aside = None
        if os.path.exists(new):
            aside = f"{new}.replaced-{time.strftime('%Y%m%d-%H%M%S')}"
            os.rename(new, aside)
        os.rename(build, new)
        if aside:
            shutil.rmtree(aside, ignore_errors=True)
    if os.path.lexists(LINK) and not os.path.islink(LINK):          # a folder of its own there: keep it, aside
        os.rename(LINK, f"{LINK}.before-prism-{time.strftime('%Y%m%d-%H%M%S')}")
    tmp = LINK + ".new"
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(new, tmp)
    os.replace(tmp, LINK)
    vers = [v for v in record.get("versions", []) if v != ver] + [ver]
    keep = {ver, os.path.basename(old) if old else None}
    try:
        keep.add(json.load(open(f"{DATA}/guard.json")).get("last_good"))
    except (OSError, ValueError):
        pass
    for v in vers[:]:
        if v not in keep and os.path.isdir(f"{VERSIONS}/{v}"):
            shutil.rmtree(f"{VERSIONS}/{v}"); vers.remove(v)
    record["versions"], record["version"] = vers, ver
    return LINK


def install_app(src):
    """ES-DE from EmuDeck's AppImage, with Prism's ES-DE binary swapped in."""
    appimage = esde_appimage()
    log(f"ES-DE app from {appimage}")
    tmp = tempfile.mkdtemp(dir=DATA)
    sh(appimage, "--appimage-extract", cwd=tmp)
    new = f"{tmp}/squashfs-root"
    shutil.copy2(f"{src}/esde/bin/es-de", f"{new}/usr/bin/es-de")
    os.chmod(f"{new}/usr/bin/es-de", 0o755)
    if os.path.exists(APP):
        shutil.rmtree(APP)
    os.rename(new, APP)
    shutil.rmtree(tmp, ignore_errors=True)


def render_esde(dest, values, features, record):
    # the theme as a real folder (refreshed on every install): a running ES-DE keeps the paths it resolved at start,
    # so a link into versions/<v> broke its videos once an update removed the old version
    os.makedirs(f"{ESDE}/themes", exist_ok=True)
    theme = f"{ESDE}/themes/prism"
    if os.path.islink(theme):
        os.remove(theme)
    shutil.copytree(f"{dest}/theme/prism", theme, dirs_exist_ok=True)
    # ES-DE runs every file in scripts/<event>/: replace the folder wholesale so no stale copies stay behind
    if os.path.isdir(f"{ESDE}/scripts"):
        shutil.rmtree(f"{ESDE}/scripts")
    for f in glob.glob(f"{dest}/esde/scripts/*/*"):
        out = f"{ESDE}/scripts/{os.path.relpath(f, dest + '/esde/scripts')}"
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "w").write(fill(open(f).read(), values))
        os.chmod(out, 0o755)
    xml = fill(open(f"{dest}/esde/custom_systems/es_systems.xml").read(), values)
    drop = set()
    for feat, systems in FEATURE_SYSTEMS.items():
        if feat not in features:
            drop |= systems
    def keep(m):
        name = re.search(r"<name>\s*([^<]+?)\s*</name>", m.group(0))
        return "" if name and name.group(1) in drop else m.group(0)
    xml = re.sub(r"[ \t]*<system>.*?</system>\s*\n?", keep, xml, flags=re.S)
    os.makedirs(f"{ESDE}/custom_systems", exist_ok=True)
    open(f"{ESDE}/custom_systems/es_systems.xml", "w").write(xml)
    rules = f"{HOME}/ES-DE/custom_systems/es_find_rules.xml"           # EmuDeck's emulator locations, if any
    if os.path.exists(rules) and not os.path.exists(f"{ESDE}/custom_systems/es_find_rules.xml"):
        shutil.copy2(rules, f"{ESDE}/custom_systems/es_find_rules.xml")
    sort = f"{dest}/esde/custom_systems/es_systems_sorting.xml"      # the order of the home row
    if os.path.exists(sort):
        shutil.copy2(sort, f"{ESDE}/custom_systems/es_systems_sorting.xml")
    # settings: start from Prism's template the first time; later installs keep the user's changes
    st = f"{ESDE}/settings/es_settings.xml"
    if not os.path.exists(st):
        os.makedirs(os.path.dirname(st), exist_ok=True)
        open(st, "w").write(fill(open(f"{dest}/install/es_settings.xml.in").read(), values))
    # favorites, play counts and collections from the user's normal ES-DE, once
    old = f"{HOME}/ES-DE"
    for sub in ("gamelists", "collections"):
        if os.path.isdir(f"{old}/{sub}") and not os.path.exists(f"{ESDE}/{sub}"):
            shutil.copytree(f"{old}/{sub}", f"{ESDE}/{sub}")
            log(f"copied {sub} from {old}")
            record["copied_from_esde"] = True


def install_units(dest, values, features):
    os.makedirs(UNIT_DIR, exist_ok=True)
    units = []
    for feat, names in FEATURE_UNITS.items():
        if feat is None or feat in features:
            units += names
    for u in units:
        open(f"{UNIT_DIR}/{u}", "w").write(fill(open(f"{dest}/systemd/{u}").read(), values))
    sh("systemctl", "--user", "daemon-reload")
    for u in units:
        if u in ENABLE:
            sh("systemctl", "--user", "enable", u)
    # units of features turned off since the last install
    for feat, names in FEATURE_UNITS.items():
        if feat and feat not in features:
            for u in names:
                if os.path.exists(f"{UNIT_DIR}/{u}"):
                    sh("systemctl", "--user", "disable", "--now", u, check=False)
                    os.remove(f"{UNIT_DIR}/{u}")
    return units


def stop_steam():
    if sh("pgrep", "-x", "steam", check=False).returncode:
        return
    log("closing Steam")
    subprocess.run(["steam", "-shutdown"], capture_output=True)
    for _ in range(60):
        if sh("pgrep", "-x", "steam", check=False).returncode:
            return
        time.sleep(1)
    raise RuntimeError("Steam did not close")


def shortcuts_in_place(record):
    """An update: the shortcuts this install made are still in Steam, pointing where they should (no need to
    close Steam, so updates also work in Game Mode)."""
    want = (record or {}).get("shortcuts") or {}
    if "Prism" not in want:
        return False
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    import steam_shortcuts as ss
    for u in ss.users():
        for v in ss.load(f"{u}/config/shortcuts.vdf").values():
            if v.get("appid") == want["Prism"] and v.get("Exe", "").strip('"') == LAUNCHER:
                return True
    return False


def install_shortcuts(dest, features, cfg):
    sys.path.insert(0, f"{dest}/install")
    import steam_shortcuts as ss
    stop_steam()
    made = {}
    aid = ss.add("Prism", LAUNCHER, start_dir=DATA, tags=["Prism"])
    tid = ss.add("Game Tester", f"{dest}/game_tester_launch.sh", hidden=True)   # the nightly game check launches here
    made["Game Tester"] = tid
    cfg["tester_appid"] = tid
    art = f"{dest}/install/art"
    ss.set_art(aid, portrait=f"{art}/portrait.png", wide=f"{art}/wide.png", hero=f"{art}/hero.png",
               logo=f"{art}/logo.png")
    made["Prism"] = aid
    cfg["steam_appid"] = aid
    if "overseerr" in features:
        rid = ss.add("Request Movies", f"{dest}/request_movies.sh", tags=["Prism"])
        made["Request Movies"] = rid
        cfg["request_movies_appid"] = rid
    elif "Request Movies" in (record_load().get("shortcuts") or {}):
        ss.remove("Request Movies")
    return made


def install(a):
    src = os.path.abspath(a.src)
    features = sorted(f for f in (a.features or []) if f)
    os.makedirs(DATA, exist_ok=True)
    record = record_load()
    record.setdefault("created", time.strftime("%Y-%m-%d %H:%M"))
    record.update({"dest": LINK, "data": DATA, "features": features, "root_ok": bool(int(a.root_ok)),
                   "updated": time.strftime("%Y-%m-%d %H:%M")})
    dest = install_code(src, record)

    cfg = read_conf()
    emu = cfg.get("emulation") or emudeck_emulation()
    cfg.update({"emulation": emu, "esde": ESDE, "theme": "prism", "features": features, "esde_app": APP})
    for key, v in (("overseerr_url", a.overseerr_url), ("backup_ssh", a.backup_ssh), ("backup_dir", a.backup_dir)):
        if v:
            cfg[key] = v
    write_conf(cfg)
    if a.plex_url and a.plex_token:
        p = f"{dest}/plex.json"
        json.dump({"url": a.plex_url.rstrip("/"), "token": a.plex_token}, open(p, "w"))
        os.chmod(p, 0o600)

    install_app(src)
    values = {"HOME": HOME, "PRISM_DIR": LINK, "ROMS": f"{emu}/roms", "EMULATION": emu, "ESDE": ESDE,
              "ESDE_HOME": ESDE_HOME, "APP": APP, "MEDIA": f"{emu}/tools/downloaded_media"}
    open(LAUNCHER, "w").write(fill(open(f"{dest}/install/prism.sh.in").read(), values))
    os.chmod(LAUNCHER, 0o755)
    render_esde(dest, values, features, record)
    record["units"] = install_units(dest, values, features)
    if shortcuts_in_place(record):
        log("update: Steam shortcuts already in place")
        cfg.setdefault("steam_appid", record["shortcuts"]["Prism"])
    else:
        record["shortcuts"] = install_shortcuts(dest, features, cfg)
    write_conf(cfg)                                  # now with the Steam app ids
    record_save(record)

    # ES-DE's own controller / locale tweaks, then the Steam row (ES-DE is closed during install)
    sh(sys.executable, f"{dest}/prism_shelf.py", "install-system", check=False)   # the Needs a look row
    if os.environ.get("PRISM_REHEARSAL"):     # release/rehearse.py: never touch the ES-DE that's running for real
        log("rehearsal: skipped apply_ui and the first prism_sync")
    else:
        sh(sys.executable, f"{dest}/apply_ui.py", check=False)
        r = sh(sys.executable, f"{dest}/prism_sync.py", "--now", check=False)
        log("prism_sync: " + (r.stdout.strip().splitlines() or ["done"])[-1])
    log("installed")


def uninstall(a):
    record = record_load()
    if not record:
        print("Prism is not installed (no installed.json).")
        return
    dest = record.get("dest", f"{HOME}/prism")
    for u in record.get("units", []):
        sh("systemctl", "--user", "disable", "--now", u, check=False)
        if os.path.exists(f"{UNIT_DIR}/{u}"):
            os.remove(f"{UNIT_DIR}/{u}")
    sh("systemctl", "--user", "daemon-reload", check=False)
    if record.get("shortcuts"):
        sys.path.insert(0, f"{dest}/install")
        import steam_shortcuts as ss
        stop_steam()
        for name, aid in record["shortcuts"].items():
            ss.remove(name)
            ss.remove_art(aid)
    shutil.rmtree(APP, ignore_errors=True)
    shutil.rmtree(VERSIONS, ignore_errors=True)        # Prism itself (every installed version)
    for f in (LAUNCHER, f"{DATA}/guard.json"):
        if os.path.exists(f):
            os.remove(f)
    if os.path.islink(LINK):
        os.remove(LINK)
    if not a.keep_data:
        shutil.rmtree(DATA, ignore_errors=True)        # Prism's ES-DE settings, gamelists, favorites
        if os.path.exists(CONF):
            os.remove(CONF)
    os.remove(RECORD)
    log("removed Prism, its services, shortcuts and ES-DE setup" + (" (kept its ES-DE settings and gamelists)"
                                                                     if a.keep_data else ""))
    log("left in place: the Steam launchers Prism made in roms/steam (they open the games in Steam)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "install", "uninstall", "status"])
    ap.add_argument("--src", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--dest", default=f"{HOME}/prism")
    ap.add_argument("--root-ok", default="0")
    ap.add_argument("--features", nargs="*")
    ap.add_argument("--plex-url"); ap.add_argument("--plex-token")
    ap.add_argument("--overseerr-url"); ap.add_argument("--backup-ssh"); ap.add_argument("--backup-dir")
    ap.add_argument("--keep-data", action="store_true")
    a = ap.parse_args()
    if a.cmd == "check":
        sys.exit(0 if check(a.src) else 1)
    if a.cmd == "install":
        install(a)
    elif a.cmd == "uninstall":
        uninstall(a)
    else:
        print(json.dumps(record_load(), indent=1) or "not installed")


if __name__ == "__main__":
    main()
