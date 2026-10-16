#!/usr/bin/env python3
"""Keep ES-DE calm and console-like (2026-10-06). Needs ES-DE CLOSED (it rewrites gamelists and
es_settings.xml on exit); run by esde_pending.py (idle restart before auto-sleep) and esde_maintenance.py (nightly).

  * Continue Playing = games only, last 8: Media (Plex/Kodi) and For You launches lose their "last played";
    so do games older than the 8 most recent.
  * Games whose latest boot test failed (black screen / crash / no launch, tested after the 2026-10-05 tester
    glitch) are hidden until a later test passes; only games hidden by this script are ever un-hidden.
  * Settings: no "added input device" pop-ups, hidden games stay hidden.
"""
import prism_config  # noqa: E402  (paths)
import glob, html, json, os, re, sys, time
import gamelist   # atomic writes, never drops entries (2026-10-13)

HOME = os.path.expanduser("~")
GL = f"{prism_config.ESDE}/gamelists"
SETTINGS = f"{prism_config.ESDE}/settings/es_settings.xml"
sys.path.insert(0, prism_config.PRISM)
import prism_shelf  # noqa: E402  (Needs a look)
RESULTS = f"{prism_config.PRISM}/test_results.jsonl"
HIDDEN_BY_US = f"{prism_config.PRISM}/hidden_by_tester.txt"
ARCHIVE = f"{prism_config.PRISM}/playcounts_archive.json"   # play counts taken out of gamelists (For You reads them)
NOT_GAMES = ("media", "foryou", "movies", "tv", "music")
KEEP_RECENT = 8
TESTER_GLITCH_END = time.mktime(time.strptime("2026-10-05 18:00", "%Y-%m-%d %H:%M"))
BAD = ("black", "crashed", "nolaunch")


def settings():
    t = open(SETTINGS).read()
    for name, val in (("InputDeviceNotifications", "false"), ("ShowHiddenGames", "false")):
        t = re.sub(rf'<bool name="{name}" value="[^"]*" />', f'<bool name="{name}" value="{val}" />', t)
    # Prism boot splash (deck_splash.py boot, started by EmuDeck's es-de.sh) replaces ES-DE's loading screen
    if '<bool name="SplashScreen"' in t:
        t = re.sub(r'<bool name="SplashScreen" value="[^"]*" />', '<bool name="SplashScreen" value="false" />', t)
    else:
        t = t.replace("</config>", '<bool name="SplashScreen" value="false" />\n</config>') if "</config>" in t else \
            t.rstrip("\n") + '\n<bool name="SplashScreen" value="false" />\n'
    # our own PS5/console-style launch screen (deck_splash.py, game-start event) replaces ES-DE's "LAUNCHING GAME" box
    t = re.sub(r'<string name="LaunchScreenDuration" value="[^"]*" />', '<string name="LaunchScreenDuration" value="disabled" />', t)
    open(SETTINGS, "w").write(t)


def verdicts():
    last = {}
    try:
        for l in open(RESULTS):
            try:
                r = json.loads(l)
            except ValueError:
                continue
            last[(r["system"], r["game"])] = r
    except OSError:
        pass
    return last


def path_renames():
    """Pending file swaps (e.g. a broken .rvz replaced by the working .wbfs): "system|old file|new file" lines in
    ~/prism/renames_pending_paths.txt; the entry keeps its metadata, points at the new file and is un-hidden."""
    f = f"{prism_config.PRISM}/renames_pending_paths.txt"
    try:
        lines = [l.split("|") for l in open(f).read().split("\n") if l.count("|") == 2]
    except OSError:
        return
    left = []
    for system, old, new in lines:
        gl = f"{GL}/{system}/gamelist.xml"
        if not os.path.exists(f"{prism_config.ROMS}/{system}/{new}"):
            left.append("|".join((system, old, new))); continue
        try:
            t = open(gl, encoding="utf-8").read()
        except OSError:
            continue
        o, n = html.escape(old, quote=False), html.escape(new, quote=False)
        t = t.replace(f"<path>./{o}</path>", f"<path>./{n}</path>")
        t = re.sub(r"<game>.*?</game>", lambda m: re.sub(r"\s*<hidden>true</hidden>", "", m.group(0))
                   if f"<path>./{n}</path>" in m.group(0) else m.group(0), t, flags=re.S)
        gamelist.write_text(gl, t)
        print("renamed", system, old, "->", new)
    open(f, "w").write("\n".join(left) + ("\n" if left else ""))


def arcade_emulators():
    """Arcade sets that only run on an older core (found by game_tester.py, arcade_cores.json) get that core as their
    per-game emulator (<altemulator>), so ES-DE launches them with it."""
    try:
        cores = json.load(open(f"{prism_config.PRISM}/arcade_cores.json"))
    except (OSError, ValueError):
        return
    gl = f"{GL}/arcade/gamelist.xml"
    try:
        t = open(gl, encoding="utf-8").read()
    except OSError:
        return
    n = 0

    def fix(m):
        nonlocal n
        g = m.group(0); p = re.search(r"<path>\./(.*?)</path>", g)
        lab = cores.get(html.unescape(p.group(1))) if p else None
        if not lab:
            return g
        g2 = re.sub(r"\s*<altemulator>.*?</altemulator>", "", g)
        g2 = g2.replace("</game>", f"\t<altemulator>{html.escape(lab, quote=False)}</altemulator>\n\t</game>")
        n += g2 != g
        return g2
    new = re.sub(r"<game>.*?</game>", fix, t, flags=re.S)
    have = set(re.findall(r"<path>\./(.*?)</path>", new))
    for rom, lab in cores.items():   # sets without an entry yet
        if lab and html.escape(rom, quote=False) not in have and os.path.exists(f"{prism_config.ROMS}/arcade/{rom}"):
            new = new.replace("</gameList>", f"\t<game>\n\t\t<path>./{html.escape(rom, quote=False)}</path>\n"
                                            f"\t\t<altemulator>{html.escape(lab, quote=False)}</altemulator>\n\t</game>\n</gameList>")
            n += 1
    if new != t:
        gamelist.write_text(gl, new)
    print(f"arcade: {n} games set to an older core")


def game_emulators():
    """Per-game emulator overrides: game_emulators.json (chosen by hand, "system/file" -> ES-DE command label, e.g.
    2025 Switch games on Eden) plus the tester's MAME/CPS findings in arcade_cores.json ("system/file" keys)."""
    try:
        emu = json.load(open(f"{prism_config.PRISM}/game_emulators.json"))
    except (OSError, ValueError):
        emu = {}
    try:
        emu.update({k: v for k, v in json.load(open(f"{prism_config.PRISM}/arcade_cores.json")).items() if "/" in k and v})
    except (OSError, ValueError):
        pass
    if not emu:
        return
    by_sys = {}
    for k, lab in emu.items():
        s, f = k.split("/", 1); by_sys.setdefault(s, {})[f] = lab
    for system, files in by_sys.items():
        gl = f"{GL}/{system}/gamelist.xml"
        try:
            t = open(gl, encoding="utf-8").read()
        except OSError:
            continue

        def fix(m):
            g = m.group(0); p = re.search(r"<path>\./(.*?)</path>", g)
            lab = files.get(html.unescape(p.group(1))) if p else None
            if not lab:
                return g
            g = re.sub(r"\s*<altemulator>.*?</altemulator>", "", g)
            return g.replace("</game>", f"\t<altemulator>{html.escape(lab, quote=False)}</altemulator>\n\t</game>")
        new = re.sub(r"<game>.*?</game>", fix, t, flags=re.S)
        if new != t:
            gamelist.write_text(gl, new)
    print(f"per-game emulators: {len(emu)}")


def tidy():
    path_renames()
    arcade_emulators()
    game_emulators()
    settings()
    last = verdicts()
    try:
        ours = set(open(HIDDEN_BY_US).read().split("\n")) - {""}
    except OSError:
        ours = set()
    recent = []   # (lastplayed, system, path)
    files = {}
    for gl in glob.glob(f"{GL}/*/gamelist.xml"):
        system = gl.split("/")[-2]
        files[system] = open(gl, encoding="utf-8", errors="replace").read()
        for m in re.finditer(r"<game>.*?</game>", files[system], re.S):
            g = m.group(0)
            p = re.search(r"<path>\./(.*?)</path>", g); lp = re.search(r"<lastplayed>(.*?)</lastplayed>", g)
            if p and lp and system not in NOT_GAMES:
                recent.append((lp.group(1), system, p.group(1)))
    keep = {(s, p) for _, s, p in sorted(recent, reverse=True)[:KEEP_RECENT]}
    # ES-DE puts a game in Last Played when its PLAY COUNT is above 0 (sorted by lastplayed), so the play count is
    # what has to go for Media / For You entries and for games older than the newest KEEP_RECENT. The counts are
    # archived in playcounts_archive.json so For You still knows what you played.
    try:
        archive = json.load(open(ARCHIVE))
    except (OSError, ValueError):
        archive = {}
    hidden_now, changed = set(), 0
    to_move, kept = [], prism_shelf.kept()
    for system, t in files.items():
        def fix(m):
            g = m.group(0)
            p = re.search(r"<path>\./(.*?)</path>", g)
            if not p:
                return g
            path = p.group(1); key = f"{system}/{html.unescape(path)}"
            # Continue Playing: games only, newest 8 (membership = play count > 0)
            pc = re.search(r"<playcount>(\d+)</playcount>", g)
            if pc and (system in NOT_GAMES or (system, path) not in keep):
                if system not in NOT_GAMES:
                    archive[key] = archive.get(key, 0) + int(pc.group(1))
                g = re.sub(r"\s*<playcount>\d+</playcount>", "", g)
            if system in NOT_GAMES and "<lastplayed>" in g:
                g = re.sub(r"\s*<lastplayed>.*?</lastplayed>", "", g)
            # games that fail their boot test move to the Needs a look row (prism_shelf, 2026-10-14) instead of being
            # hidden in place; the ones hidden the old way are un-hidden and moved too. Put-back games stay (kept).
            r = last.get((system, html.unescape(path).split("/")[-1]))
            bad = r is not None and r.get("verdict") in BAD and r.get("time", 0) > TESTER_GLITCH_END
            if key in ours and "<hidden>true</hidden>" in g:
                g = re.sub(r"\s*<hidden>true</hidden>", "", g)
            if bad and system != prism_shelf.LOOK and key not in kept:
                to_move.append((system, html.unescape(path)))
            return g
        new = re.sub(r"<game>.*?</game>", fix, t, flags=re.S)
        if new != t:
            gamelist.write_text(f"{GL}/{system}/gamelist.xml", new); changed += 1
    open(HIDDEN_BY_US, "w").write("\n".join(sorted(hidden_now)) + "\n")
    moved = [f"{sy}/{p}" for sy, p in to_move if prism_shelf.move(sy, p)]
    if moved or prism_shelf.listing():
        prism_shelf.install_system()
        prism_shelf.refresh_art()
    if moved:
        import prism_notice
        n = len(moved)
        pron = "They're" if n != 1 else "It's"
        prism_notice.card(f"needs-a-look-{time.strftime('%Y%m%d')}", "Needs a look",
                          f"{n} game{'s' if n != 1 else ''} didn't start",
                          f"Last night's check found {n} game{'s' if n != 1 else ''} that "
                          f"{'show' if n != 1 else 'shows'} a black screen or close. {pron} in Needs a "
                          "look, so the rest of your shelf only has games that work.",
                          ok="Show me", cancel="Later", action="system:needsalook", quiet_days=0)
    json.dump(archive, open(ARCHIVE, "w"), indent=1)
    print(f"tidy: {changed} gamelists updated, {len(moved)} games moved to Needs a look (failed boot tests), "
          f"Continue Playing = {len(keep)} games")


if __name__ == "__main__":
    tidy()
