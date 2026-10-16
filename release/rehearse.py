#!/usr/bin/env python3
"""Rehearse the installer on this Deck without touching it (2026-10-14, release step 4).

    python3 ~/prism/release/rehearse.py [SRC]        SRC: an exported release (default ~/prism-release)

Everything happens in a throwaway home folder (/tmp/prism-rehearsal/home) with its own EmuDeck settings, a few test
games, a copy of Steam's shortcuts file and a link to EmuDeck's ES-DE AppImage. Stand-ins for systemctl, steam and
pgrep keep the real services and the running Steam out of it. Steps: check, install (extras: nightly scrape, Xbox
360), look at everything it made, start Prism's ES-DE off-screen from its own folder, update to a second version
(settings carried over), uninstall. Before and after, a fingerprint of the real setup must be identical.
Prints PASS / FAIL lines; exit 1 if anything failed.
"""
import glob, hashlib, json, os, re, shutil, subprocess, sys, time

REAL = os.path.expanduser("~")
SRC = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else f"{REAL}/prism-release")
T = "/tmp/prism-rehearsal"
FH = f"{T}/home"
SHIMS = f"{T}/shims"
results = []
ART = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  ({detail})" if detail and not ok else ""), flush=True)


def fingerprint():
    """What the installer could touch on the real Deck, hashed."""
    paths = sorted(glob.glob(f"{REAL}/.config/systemd/user/*") + glob.glob(f"{REAL}/.config/prism/*")
                   + glob.glob(f"{REAL}/ES-DE/settings/*.xml") + glob.glob(f"{REAL}/ES-DE/custom_systems/*")
                   + glob.glob(f"{REAL}/.local/share/Steam/userdata/*/config/shortcuts.vdf")
                   + glob.glob("/etc/udev/rules.d/*"))
    h = hashlib.sha256()
    for p in paths:
        h.update(p.encode())
        if os.path.isfile(p):
            h.update(open(p, "rb").read())
    for extra in (f"{REAL}/homebrew/plugins", f"{REAL}/prism", f"{REAL}/.local/share/prism"):
        h.update(repr(sorted(os.listdir(extra)) if os.path.isdir(extra) else None).encode())
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import prism_config                      # the real games folder (this process has no test home)
    roms = prism_config.ROMS
    if os.path.isdir(f"{roms}/steam"):
        h.update(repr(sorted(os.listdir(f"{roms}/steam"))).encode())
    return h.hexdigest()


def env():
    return {**os.environ, "HOME": FH, "PRISM_TEST_HOME": FH, "PRISM_REHEARSAL": "1",
            "PATH": f"{SHIMS}:{os.environ.get('PATH', '/usr/bin')}"}


def run(*a, **kw):
    return subprocess.run(list(a), env=env(), capture_output=True, text=True, **kw)


def setup_home():
    shutil.rmtree(T, ignore_errors=True)
    os.makedirs(SHIMS)
    for name, body in (("systemctl", f'echo "systemctl $*" >> {T}/shim.log; exit 0'),
                       ("steam", f'echo "steam $*" >> {T}/shim.log; exit 0'),
                       ("pgrep", '[ "$1 $2" = "-x steam" ] && exit 1; exec /usr/bin/pgrep "$@"')):
        p = f"{SHIMS}/{name}"
        open(p, "w").write("#!/bin/bash\n" + body + "\n")
        os.chmod(p, 0o755)
    emu = f"{FH}/Emulation"
    for system, files in (("snes", ["Test Game One (USA).sfc"]), ("gba", ["Test Game Two (USA).gba"]),
                          ("psx", ["Test Disc (USA).chd"]), ("xbox360", []), ("steam", [])):
        os.makedirs(f"{emu}/roms/{system}/roms" if system == "xbox360" else f"{emu}/roms/{system}", exist_ok=True)
        for f in files:
            open(f"{emu}/roms/{system}/{f}", "wb").write(b"\0" * 1024)
    os.makedirs(f"{emu}/tools/downloaded_media", exist_ok=True)
    os.makedirs(f"{FH}/.config/EmuDeck", exist_ok=True)
    open(f"{FH}/.config/EmuDeck/settings.sh", "w").write(f'emulationPath="{emu}"\n')
    os.makedirs(f"{FH}/Applications", exist_ok=True)
    appimage = next(p for p in sorted(glob.glob(f"{REAL}/Applications/ES-DE*.AppImage*"))
                    if open(p, "rb").read(4) == b"\x7fELF")
    os.symlink(appimage, f"{FH}/Applications/ES-DE.AppImage")
    for u in glob.glob(f"{REAL}/.local/share/Steam/userdata/*/config/shortcuts.vdf"):
        uid = u.split("/")[-3]
        os.makedirs(f"{FH}/.local/share/Steam/userdata/{uid}/config", exist_ok=True)
        shutil.copy2(u, f"{FH}/.local/share/Steam/userdata/{uid}/config/shortcuts.vdf")
    os.makedirs(f"{FH}/.local/share/Steam/steamapps", exist_ok=True)
    open(f"{FH}/.local/share/Steam/steamapps/libraryfolders.vdf", "w").write('"libraryfolders"\n{\n}\n')
    os.makedirs(f"{FH}/homebrew/plugins", exist_ok=True)          # "Decky is installed"


def shortcuts():
    sys.path.insert(0, f"{SRC}/install")
    import steam_shortcuts as ss
    out = {}
    for u in glob.glob(f"{FH}/.local/share/Steam/userdata/*/config/shortcuts.vdf"):
        for v in ss.load(u).values():
            out[v.get("AppName")] = v
    return out


def inspect(features):
    data = f"{FH}/.local/share/prism"
    link = f"{FH}/prism"
    ver = open(f"{SRC}/VERSION").read().split()[0]
    check("~/prism links to the installed version", os.path.realpath(link) == f"{data}/versions/{ver}",
          os.path.realpath(link))
    cfg = open(f"{FH}/.config/prism/config.toml").read()
    for key in ("emulation", "esde", "theme", "features", "esde_app", "steam_appid", "tester_appid"):
        check(f"config.toml has {key}", re.search(rf"^{key} = ", cfg, re.M))
    esde = f"{data}/ES-DE"
    check("theme installed into Prism's ES-DE", os.path.isfile(f"{esde}/themes/prism/theme.xml")
          and not os.path.islink(f"{esde}/themes/prism"))
    scripts = glob.glob(f"{esde}/scripts/*/*")
    check(f"ES-DE hooks installed ({len(scripts)})", len(scripts) >= 6)
    check("no placeholders left in hooks", not any("@" in re.sub(r"\$@|\"\$@\"", "", open(f).read()) for f in scripts))
    xml = open(f"{esde}/custom_systems/es_systems.xml").read()
    check("Movies/TV/Music rows left out (Plex not chosen)", not re.search(r"<name>(movies|tv|music)</name>", xml))
    check("Xbox 360 row present", "<name>xbox360</name>" in xml)
    check("Needs a look row present", "<name>needsalook</name>" in xml)
    check("home row order installed", os.path.exists(f"{esde}/custom_systems/es_systems_sorting.xml"))
    st = open(f"{esde}/settings/es_settings.xml").read()
    check("ES-DE settings point at the test games", f'value="{FH}/Emulation/roms"' in st)
    check("ES-DE settings: Prism theme, no accounts", 'name="Theme" value="prism"' in st
          and 'ScraperPasswordScreenScraper" value=""' in st)
    units = sorted(os.path.basename(u) for u in glob.glob(f"{FH}/.config/systemd/user/*"))
    want = {"switchhome.service", "prism-sync.service", "prism-sync.timer", "night-scrape.service", "night-scrape.timer"}
    check("services for core + chosen extras", want <= set(units) and "plex-sync.timer" not in units, units)
    check("services start Prism from ~/prism", all(f"{FH}/prism/" in open(f"{FH}/.config/systemd/user/{u}").read()
                                                   for u in units if u.endswith(".service")))
    log = open(f"{T}/shim.log").read() if os.path.exists(f"{T}/shim.log") else ""
    check("services enabled (through the stand-in)", "enable switchhome.service" in log and "enable prism-sync.timer" in log)
    sc = shortcuts()
    check("Steam shortcut \"Prism\" added", "Prism" in sc and sc["Prism"].get("Exe", "").strip('"') == f"{data}/prism.sh")
    check("hidden Game Tester shortcut added", "Game Tester" in sc and sc["Game Tester"].get("IsHidden") == 1)
    aid = sc.get("Prism", {}).get("appid", 0)
    ART.append(aid)
    check("Prism's library art in place", len(glob.glob(f"{FH}/.local/share/Steam/userdata/*/config/grid/{aid}*")) >= 4)
    check("ES-DE build swapped in", open(f"{data}/es-de-app/usr/bin/es-de", "rb").read() == open(f"{SRC}/esde/bin/es-de", "rb").read())
    check("launcher starts ES-DE with Prism's own folder", f'ESDE_APPDATA_DIR="{data}/ES-DE"' in open(f"{data}/prism.sh").read())
    r = run(sys.executable, "-c", "import prism_config as c, prism_features as f; print(c.ESDE, c.PRISM_APPID, "
            "[k for k in f.EXTRAS if f.enabled(k)])", cwd=link)
    check("installed Prism reads its own settings", FH in r.stdout and str(aid) in r.stdout, r.stdout + r.stderr[-200:])


def esde_offscreen():
    """Start Prism's ES-DE in an invisible gamescope from its own folder; it must load the theme and the systems.
    Hooks are switched off for this run so the running Prism isn't confused by a second ES-DE's events."""
    data = f"{FH}/.local/share/prism"
    st = f"{data}/ES-DE/settings/es_settings.xml"
    s = open(st).read()
    open(st, "w").write(s.replace('name="CustomEventScripts" value="true"', 'name="CustomEventScripts" value="false"'))
    logf = f"{data}/ES-DE/logs/es_log.txt"
    # a copy under another name: the running Prism watches for processes called es-de and would close the older one
    exe = f"{data}/es-de-app/usr/bin/es-de-rehearse"
    shutil.copy2(f"{data}/es-de-app/usr/bin/es-de", exe)
    p = subprocess.Popen(["gamescope", "--backend", "headless", "-w", "1280", "-h", "800", "--", exe, "--no-splash"],
                         env={**env(), "ESDE_APPDATA_DIR": f"{data}/ES-DE"}, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    ok = False
    for _ in range(60):
        time.sleep(1)
        if os.path.exists(logf) and re.search(r"Application startup time", open(logf, errors="replace").read()):
            time.sleep(4); ok = True; break
    os.killpg(p.pid, 15)
    time.sleep(2)
    try:
        os.killpg(p.pid, 9)
    except OSError:
        pass
    open(st, "w").write(s)
    text = open(logf, errors="replace").read() if os.path.exists(logf) else ""
    check("Prism's ES-DE starts from its own folder", ok, text[-300:])
    check("ES-DE loaded the Prism theme", "prism" in text.lower() and "Couldn't find any themes" not in text)
    errs = [l for l in text.splitlines() if "Error:" in l and "Couldn't find" not in l]
    check("no ES-DE errors", not errs, "; ".join(errs[:3]))


def update():
    src2 = f"{T}/src-v2"
    shutil.copytree(SRC, src2, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    open(f"{src2}/VERSION", "w").write("9999.12.31-rehearsal\n")
    old = os.path.realpath(f"{FH}/prism")
    open(f"{FH}/prism/rehearsal_state.txt", "w").write("kept\n")         # something Prism keeps next to its code
    r = run(sys.executable, f"{src2}/install/setup.py", "install", "--src", src2, "--root-ok", "0",
            "--features", "scrape", "xbox360")
    check("update installs", r.returncode == 0, r.stderr[-400:])
    new = os.path.realpath(f"{FH}/prism")
    check("~/prism now links to the new version", new.endswith("/versions/9999.12.31-rehearsal"), new)
    check("previous version kept for rollback", os.path.isdir(old))
    check("settings carried over to the new version", open(f"{new}/rehearsal_state.txt").read() == "kept\n")
    r = run(sys.executable, f"{new}/prism_guard.py", "status")
    check("rollback guard sees an installed Prism", '"managed": true' in r.stdout, r.stdout[-200:])


def uninstall():
    r = run(sys.executable, f"{FH}/prism/install/setup.py", "uninstall")
    check("uninstall runs", r.returncode == 0, r.stderr[-400:])
    check("Prism's folders gone", not os.path.lexists(f"{FH}/prism") and not os.path.exists(f"{FH}/.local/share/prism"))
    check("Prism's services gone", not glob.glob(f"{FH}/.config/systemd/user/*"))
    check("Prism's settings gone", not os.path.exists(f"{FH}/.config/prism/config.toml"))
    sc = shortcuts()
    check("Steam shortcuts removed", "Prism" not in sc and "Game Tester" not in sc)
    check("no Prism art left", ART and not glob.glob(f"{FH}/.local/share/Steam/userdata/*/config/grid/{ART[0]}*"))
    check("test games untouched", os.path.exists(f"{FH}/Emulation/roms/snes/Test Game One (USA).sfc"))


def real_prism():
    for p in os.listdir("/proc"):
        try:
            if p.isdigit() and open(f"/proc/{p}/comm").read().strip() == "es-de":
                return p
        except OSError:
            pass
    return None


def main():
    before = fingerprint()
    prism_before = real_prism()
    setup_home()
    r = run(sys.executable, f"{SRC}/install/setup.py", "check", "--src", SRC)
    check("pre-flight check", r.returncode == 0, r.stdout + r.stderr[-300:])
    r = run(sys.executable, f"{SRC}/install/setup.py", "install", "--src", SRC, "--root-ok", "0",
            "--features", "scrape", "xbox360")
    check("install runs", r.returncode == 0, (r.stdout + r.stderr)[-600:])
    if r.returncode == 0:
        inspect(["scrape", "xbox360"])
        esde_offscreen()
        update()
        uninstall()
    check("the real Deck is unchanged", fingerprint() == before)
    check("the running Prism was left alone", real_prism() == prism_before, f"{prism_before} -> {real_prism()}")
    bad = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} passed" + (f"; failed: {', '.join(bad)}" if bad else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
