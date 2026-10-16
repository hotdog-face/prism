#!/usr/bin/env python3
"""Build the clean Prism release repo from this Deck (2026-10-14, step 2 of the release plan).

    python3 ~/deck-mcp/release/export.py [OUT]        default OUT: ~/prism-release

~/deck-mcp stays the working copy; this copies what release/manifest.txt lists, plus the theme, the ES-DE patch,
the Decky bridge plugin, the systemd units and the ES-DE hooks, into OUT and commits it there. On the way it:
  * renames the theme folder to "prism" and drops theme backups and unused media,
  * turns this Deck's paths in units / ES-DE files into @PLACEHOLDERS@ for the installer,
  * takes names and "Switch Home" wording out of comments and text,
  * refuses to commit if anything personal is left (addresses, accounts, tokens, this Deck's paths).
Run it again after changing deck-mcp; the release repo gets a new commit with the difference.
"""
import fnmatch, glob, json, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
DM = os.path.dirname(HERE)
sys.path.insert(0, DM)
import prism_config  # noqa: E402

HOME = prism_config.HOME
OUT = os.path.abspath(os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/prism-release"))
SRC_ESDE = f"{HOME}/ES-DE"          # the sources: theme, hooks, custom systems (the installed copies are made from these)
THEME_SRC = f"{SRC_ESDE}/themes/switch-home"
ESDE_SRC = f"{HOME}/build/esde"
DECKY_SRC = f"{HOME}/homebrew/plugins/SwitchHomeBridge"
UNITS = ["switchhome.service", "prism-sync.service", "prism-sync.timer", "night-scrape.service", "night-scrape.timer",
         "plex-sync.service", "plex-sync.timer", "save-backup.service", "save-backup.timer"]
THEME_SKIP = [".git", "backdrops", "sounds_old_*", "*.bak*", "*.pre-ui", "backup-screens-*", "*_unused*", "*_2026-10-0*", "card test",
              "*.orig", "__pycache__"]

# ---- wording: names and the old product name out of comments / text --------------------------------------------
WORDING = [
    (r"\bSwitch Home\b", "Prism"),
    (r"\bSwitch-style\b", "console-style"),
    (r"\bPS5-style\b", "console-style"),
    (r"~/deck-mcp\b", "~/prism"),                  # the release installs into ~/prism
    (r"\bdeck-mcp/(?=\w+\.py)", ""),
]
TEXT_EXT = (".py", ".sh", ".md", ".xml", ".service", ".timer", ".json", ".txt", ".desktop", ".patch", ".toml", ".js",
            ".ts", ".tsx")

# ---- this Deck's specifics -> installer placeholders (units, ES-DE hooks, launcher) ------------------------------
PLACEHOLDERS = [
    (prism_config.ROMS, "@ROMS@"), (prism_config.EMU, "@EMULATION@"), (prism_config.PRISM, "@PRISM_DIR@"),
    (f"{HOME}/deck-mcp", "@PRISM_DIR@"), ("~/deck-mcp", "@PRISM_DIR@"), (SRC_ESDE, "@ESDE@"),
    (HOME, "@HOME@"),
]

# ---- anything matching these in the output stops the commit ------------------------------------------------------
PERSONAL = [
    (r"192\.168\.\d+\.\d+", "LAN address"),
    (r"[\w.+-]+@(?!anthropic\.com|deck\b|localhost)[\w-]+\.(?:com|me|net|org|io)\b", "e-mail address"),
    (r"/home/deck\b", "this Deck's home path"),
    (r"(?i)x-plex-token[=:]\s*['\"]?[A-Za-z0-9_-]{16,}", "Plex token"),
    (r"(?i)(?:api[_-]?key|password|passwd|secret)\s*[=:]\s*['\"][^'\"\s]{6,}['\"]", "credential"),
]
# names, account ids, the SD card's label: kept out of this (public) file, in ~/.config/prism/export_private.json
# {"wording": [[regex, replacement], ...], "personal": [[regex, label], ...]}
try:
    _PRIV = json.load(open(os.path.expanduser("~/.config/prism/export_private.json")))
except (OSError, ValueError):
    _PRIV = {}
WORDING[:0] = [tuple(x) for x in _PRIV.get("wording", [])]
PERSONAL += [tuple(x) for x in _PRIV.get("personal", [])]
PERSONAL_ALLOW = {   # file -> patterns allowed there (documented examples, not data)
    "prism_config.py": {"this Deck's SD card name"},
    "selftest/fixtures.py": set(),
    "release/export.py": {p for _, p in PERSONAL},
}


def esde_settings_template(src, out):
    """ES-DE's settings for a new install, from this Deck's: no accounts or passwords, paths as placeholders, the
    Prism theme, no personal collections, ES-DE's own update check off (Prism ships its own ES-DE build)."""
    t = open(src, encoding="utf-8").read()
    def setv(name, value):
        nonlocal t
        t = re.sub(rf'(<(?:string|bool|int) name="{name}" value=")[^"]*(" />)', lambda m: m.group(1) + value + m.group(2), t)
    for k in ("ScraperUsernameScreenScraper", "ScraperPasswordScreenScraper", "ApplicationUpdaterLastCheck",
              "ScreensaverSlideshowCustomDir", "UserThemeDirectory"):
        setv(k, "")
    setv("ScraperUseAccountScreenScraper", "false")
    setv("ROMDirectory", "@ROMS@"); setv("MediaDirectory", "@MEDIA@")
    setv("Theme", "prism"); setv("ThemeSet", "prism")
    setv("CollectionSystemsCustom", "")
    setv("ApplicationUpdaterFrequency", "never")
    t = t.replace(prism_config.HOME, "@HOME@")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write(t)


def sh(*a, cwd=None):
    return subprocess.run(list(a), cwd=cwd, capture_output=True, text=True, check=True).stdout


def manifest():
    groups, cur = {}, None
    for line in open(f"{HERE}/manifest.txt"):
        line = line.split("#")[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"\[([\w-]+)\]", line)
        if m:
            cur = groups.setdefault(m.group(1), [])
        else:
            cur.append(line)
    return groups


def skipped(rel):
    return any(fnmatch.fnmatch(part, pat) for part in rel.split("/") for pat in THEME_SKIP)


def copy(src, dst):
    if os.path.basename(src).startswith("._"):     # macOS metadata files, never content
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def reword(text):
    for pat, rep in WORDING:
        text = re.sub(pat, rep, text)
    return text


def placeholders(text):
    for real, ph in PLACEHOLDERS:
        text = text.replace(real, ph)
    return text


def theme_unused(root):
    """Media files in the theme's top-level asset folder that nothing mentions (theme XML or Prism code)."""
    corpus = ""
    for f in glob.glob(f"{root}/**/*.xml", recursive=True) + glob.glob(f"{OUT}/**/*.py", recursive=True):
        corpus += open(f, errors="replace").read()
    unused = []
    for f in glob.glob(f"{root}/switch/*"):
        if os.path.isfile(f) and f.endswith((".mp4", ".webm", ".png", ".jpg", ".webp")) \
                and os.path.basename(f) not in corpus:
            unused.append(f)
    return unused


def main():
    os.makedirs(OUT, exist_ok=True)
    if not os.path.isdir(f"{OUT}/.git"):
        sh("git", "init", "-q", "-b", "main", cwd=OUT)
    # start from an empty tree each time (keep .git) so files dropped from the manifest disappear
    for name in os.listdir(OUT):
        if name != ".git":
            p = f"{OUT}/{name}"
            shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)

    tracked = set(sh("git", "ls-files", cwd=DM).splitlines())
    groups = manifest()
    problems = []
    for group, files in groups.items():
        for rel in files:
            if rel not in tracked:
                problems.append(f"manifest [{group}]: {rel} is not a tracked file in deck-mcp")
                continue
            copy(f"{DM}/{rel}", f"{OUT}/{rel}")
    copy(f"{HERE}/manifest.txt", f"{OUT}/release/manifest.txt")
    copy(__file__, f"{OUT}/release/export.py")
    copy(f"{HERE}/rehearse.py", f"{OUT}/release/rehearse.py")

    # every local import of an exported module must be exported too
    exported = {r for fs in groups.values() for r in fs}
    local_mods = {os.path.basename(f)[:-3]: f for f in tracked if f.endswith(".py") and "/" not in f}
    for rel in sorted(exported):
        if not rel.endswith(".py"):
            continue
        for m in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", open(f"{DM}/{rel}").read(), re.M):
            mod = m.group(1)
            if mod in local_mods and local_mods[mod] not in exported:
                problems.append(f"{rel} imports {mod}, which is not in the manifest")

    # theme -> theme/prism
    tdst = f"{OUT}/theme/prism"
    for dirpath, dirnames, filenames in os.walk(THEME_SRC):
        rel_dir = os.path.relpath(dirpath, THEME_SRC)
        dirnames[:] = [d for d in dirnames if not skipped(os.path.normpath(f"{rel_dir}/{d}"))]
        for fn in filenames:
            rel = os.path.normpath(f"{rel_dir}/{fn}")
            if not skipped(rel):
                copy(f"{dirpath}/{fn}", f"{tdst}/{rel}")
    unused = theme_unused(tdst)
    for f in unused:
        os.remove(f)
    linear = f"{SRC_ESDE}/themes/linear-video-first"
    copy(f"{linear}/LICENSE", f"{tdst}/LICENSE-linear")
    copy(f"{linear}/CREDITS.md", f"{tdst}/CREDITS-linear.md")

    # ES-DE patch (everything changed in the 3.5.0 source tree, new files included)
    src = f"{ESDE_SRC}/src"
    patch = sh("git", "diff", "HEAD", cwd=src)
    base = sh("git", "log", "-1", "--format=%H %s", cwd=src).strip()
    os.makedirs(f"{OUT}/esde", exist_ok=True)
    with open(f"{OUT}/esde/prism-es-de.patch", "w") as f:
        f.write(patch)
    with open(f"{OUT}/esde/BASE", "w") as f:
        f.write(base + "\n")

    # Decky bridge plugin, ES-DE hooks + custom systems + launcher, systemd units
    for dirpath, dirnames, filenames in os.walk(DECKY_SRC):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "node_modules")]
        for fn in filenames:
            if ".bak" not in fn:
                rel = os.path.relpath(f"{dirpath}/{fn}", DECKY_SRC)
                copy(f"{dirpath}/{fn}", f"{OUT}/decky/PrismBridge/{rel}")
    pj = f"{OUT}/decky/PrismBridge/plugin.json"
    if os.path.exists(pj):                   # the release name; the socket it serves stays the same
        meta = json.load(open(pj))
        meta.update(name="Prism Bridge", author="Prism")
        meta.setdefault("publish", {}).update(tags=["prism"], description="Lets Prism drive Steam's UI: hide Steam's "
                                              "menu under the Steam-button card, close side menus, pause toasts.")
        json.dump(meta, open(pj, "w"), indent=4)
    for f in glob.glob(f"{SRC_ESDE}/scripts/*/*"):
        if ".bak" not in f:
            copy(f, f"{OUT}/esde/scripts/{os.path.relpath(f, SRC_ESDE + '/scripts')}")
    copy(f"{SRC_ESDE}/custom_systems/es_systems.xml", f"{OUT}/esde/custom_systems/es_systems.xml")
    copy(f"{SRC_ESDE}/custom_systems/es_systems_sorting.xml", f"{OUT}/esde/custom_systems/es_systems_sorting.xml")
    esde_settings_template(f"{SRC_ESDE}/settings/es_settings.xml", f"{OUT}/install/es_settings.xml.in")
    os.makedirs(f"{OUT}/esde/bin", exist_ok=True)       # Prism's ES-DE build (the installer swaps it into ES-DE)
    shutil.copy2(f"{prism_config.ESDE_APP}/usr/bin/es-de", f"{OUT}/esde/bin/es-de")
    with open(f"{OUT}/VERSION", "w") as f:
        f.write(time.strftime("%Y.%m.%d") + "-" + sh("git", "rev-parse", "--short", "HEAD", cwd=DM).strip() + "\n")
    for u in UNITS:
        p = f"{HOME}/.config/systemd/user/{u}"
        if os.path.exists(p):
            copy(p, f"{OUT}/systemd/{u}")
        else:
            problems.append(f"unit {u} not found")

    # README, CREDITS, .gitignore, esde/README.md ... : release/overlay/ is copied over the top as-is
    overlay = f"{HERE}/overlay"
    for dirpath, dirnames, filenames in os.walk(overlay):
        for fn in filenames:
            rel = os.path.relpath(f"{dirpath}/{fn}", overlay)
            copy(f"{dirpath}/{fn}", f"{OUT}/{rel.replace('dot.gitignore', '.gitignore')}")
    copy(f"{ESDE_SRC}/src/LICENSE", f"{OUT}/esde/LICENSE-es-de")


    # wording everywhere; placeholders in the installer-template files
    for dirpath, dirnames, filenames in os.walk(OUT):
        if "/.git" in dirpath + "/":
            continue
        for fn in filenames:
            p = f"{dirpath}/{fn}"
            rel = os.path.relpath(p, OUT)
            if not fn.endswith(TEXT_EXT) or rel == "release/export.py":
                continue
            text = open(p, errors="surrogateescape").read()
            new = text
            if rel.startswith(("systemd/", "esde/scripts/", "esde/custom_systems/")):
                new = placeholders(new)        # before the wording, which would shorten deck-mcp/x.py paths
            new = reword(new)
            if new != text:
                with open(p, "w", errors="surrogateescape") as f:
                    f.write(new)

    # personal-data check over the whole output
    for dirpath, dirnames, filenames in os.walk(OUT):
        if "/.git" in dirpath + "/":
            continue
        for fn in filenames:
            p = f"{dirpath}/{fn}"
            rel = os.path.relpath(p, OUT)
            try:
                text = open(p, errors="strict").read()
            except (UnicodeDecodeError, OSError):
                continue   # binary
            allowed = PERSONAL_ALLOW.get(rel, set())
            for pat, what in PERSONAL:
                if what in allowed:
                    continue
                for m in re.finditer(pat, text):
                    line = text.count("\n", 0, m.start()) + 1
                    problems.append(f"{what}: {rel}:{line}: {m.group(0)[:60]}")

    size = sh("du", "-sh", "--exclude=.git", OUT).split()[0]
    print(f"exported {sum(len(v) for v in groups.values())} files from deck-mcp + theme/ESDE/decky/systemd -> {OUT} ({size})")
    if unused:
        print(f"theme: left out {len(unused)} unused media files: " + ", ".join(os.path.basename(u) for u in unused))
    if problems:
        print(f"\nNOT COMMITTED - {len(problems)} problem(s):")
        for p in problems:
            print("  " + p)
        sys.exit(1)
    head = sh("git", "rev-parse", "--short", "HEAD", cwd=DM).strip()
    sh("git", "add", "-A", cwd=OUT)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=OUT).returncode == 0:
        print("no changes since the last export")
        return
    sh("git", "-c", "user.name=Prism", "-c", "user.email=prism@deck", "commit", "-q", "-m",
       f"Export from deck-mcp {head}", cwd=OUT)
    print("committed", sh("git", "log", "--oneline", "-1", cwd=OUT).strip())


if __name__ == "__main__":
    main()
