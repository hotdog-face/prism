#!/usr/bin/env python3
"""Prism fixes itself (2026-10-14): small checks that put things back the way they should be.

    prism_doctor.py live      every 15 minutes (prism_sync.py): services, stuck Steam UI state, stray players,
                              stray files in ES-DE's hook folders, the games drive missing
    prism_doctor.py closed    nightly while ES-DE is closed (esde_maintenance.py): broken or emptied gamelists come
                              back from the daily backup, ES-DE settings Prism depends on are put back
    prism_doctor.py check     report only, change nothing

Repairs a person would notice get a short "Prism fixed something" note (prism_notice); things only a person can sort
out get one card. Technical cleanups are only logged (logs/prism_doctor.log). Nothing is deleted: anything moved goes
to backup/doctor/.
"""
import glob, os, re, shutil, subprocess, sys, tarfile, time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_config  # noqa: E402  (paths)

LOG = f"{prism_config.PRISM}/logs/prism_doctor.log"
ATTIC = f"{prism_config.PRISM}/backup/doctor"
DRY = "check" in sys.argv[1:]
SERVICES = ["switchhome.service"]
TIMERS = ["prism-sync.timer", "night-scrape.timer", "plex-sync.timer", "save-backup.timer"]
SETTINGS = f"{prism_config.ESDE}/settings/es_settings.xml"
fixed = []                 # (plain words for a note or None, log line)


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(time.strftime("%m-%d %H:%M:%S ") + ("[check] " if DRY else "") + msg + "\n")
    print(msg, flush=True)


def fix(note, line, action):
    """Run action() unless this is a report-only run; remember it for the summary note."""
    log(("would fix: " if DRY else "fixed: ") + line)
    if not DRY:
        try:
            action()
        except Exception as e:
            log(f"  failed: {e}")
            return
    fixed.append((note, line))


def sh(*a, **kw):
    return subprocess.run(list(a), capture_output=True, text=True, **kw)


def user_unit(u, what):
    return sh("systemctl", "--user", what, u).stdout.strip()


def stash(path):
    os.makedirs(ATTIC, exist_ok=True)
    shutil.move(path, f"{ATTIC}/{time.strftime('%Y%m%d-%H%M%S')}-{os.path.basename(path)}")


# ---- live checks ----------------------------------------------------------------------------------------------------
def check_services():
    unit_dir = f"{prism_config.HOME}/.config/systemd/user"
    for u in SERVICES:
        if os.path.exists(f"{unit_dir}/{u}") and user_unit(u, "is-active") not in ("active", "activating"):
            fix("Prism's background service had stopped and was restarted.", f"{u} was {user_unit(u, 'is-active')}",
                lambda u=u: sh("systemctl", "--user", "restart", u))
    for t in TIMERS:
        if os.path.exists(f"{unit_dir}/{t}") and user_unit(t, "is-enabled") == "disabled" \
                and not os.path.exists(f"{prism_config.PRISM}/party_mode"):     # party mode pauses timers on purpose
            fix(None, f"{t} was disabled", lambda t=t: sh("systemctl", "--user", "enable", "--now", t))


def check_steam_ui():
    import deck_curate as d
    import prism_state as ps
    if ps.card_up():
        return
    br = d.bridge("state")
    if br is None:
        return
    data = br.get("data") or {}
    if data.get("toastsOff"):
        fix(None, "Steam's notifications were left hidden", lambda: d.bridge("toasts_on"))
    if data.get("suppress", 0) and not d.docked() and not os.path.exists(d.STEAM_MENU_WINDOW):
        fix(None, "Steam's menus were left blocked in handheld mode", d.steam_menus_release)


def check_players():
    import prism_state as ps
    if ps.media_players():
        return
    for p in sh("pgrep", "-x", "mpv-bin").stdout.split():
        fix(None, f"orphan video player {p}", lambda p=p: sh("kill", p))


def check_esde_scripts():
    """ES-DE runs every file in scripts/<event>/: a backup copy left there runs too (it did, 2026-10-09)."""
    for f in glob.glob(f"{prism_config.ESDE}/scripts/*/*"):
        name = os.path.basename(f)
        if re.search(r"\.(bak|orig|old|tmp)\b|~$|\.bak-", name) or name.startswith("._"):
            fix(None, f"stray file in ES-DE's hook folder: {f}", lambda f=f: stash(f))


def check_games_drive():
    import prism_notice
    if not os.path.isdir(prism_config.ROMS) or not os.listdir(prism_config.ROMS):
        log(f"games folder missing: {prism_config.ROMS}")
        label = "SD card" if prism_config.STORAGE.startswith("/run/media") else "games drive"
        if not DRY:
            prism_notice.card("games-drive-missing", f"Can't find your {label}",
                              "Your games aren't showing up",
                              f"Prism can't see the {label} that holds your games. Make sure it's pushed all the way "
                              "in, then restart the Deck.", ok="OK", cancel="Later", quiet_days=1)
    elif not DRY:
        prism_notice.withdraw("games-drive-missing")


def live():
    check_services()
    check_steam_ui()
    check_players()
    check_esde_scripts()
    check_games_drive()
    summarize()


# ---- with ES-DE closed ----------------------------------------------------------------------------------------------
def fullname(system):
    try:
        import prism_shelf
        return prism_shelf.fullname(system)
    except Exception:
        return system


def backups():
    """{system: [(day, text), ...] newest first} from the daily gamelist archives (14 days)."""
    import gamelist
    out = {}
    for arc in sorted(glob.glob(f"{gamelist.BACKUPS}/gamelists-*.tar.gz"), reverse=True):
        day = re.search(r"(\d{8})", arc).group(1)
        try:
            with tarfile.open(arc) as t:
                for m in t.getmembers():
                    if m.name.endswith("/gamelist.xml"):
                        out.setdefault(m.name.split("/")[0], []).append(
                            (day, t.extractfile(m).read().decode("utf-8", "replace")))
        except (OSError, tarfile.TarError):
            continue
    return out


def check_gamelists():
    """A gamelist that doesn't parse, or has less than half the games of its best recent backup while the games are
    still on the drive, comes back from the newest healthy backup (tonight's backup may already hold the broken list,
    so "healthy" = at least 90 % of the most entries any of the last 14 days had)."""
    import gamelist
    if not os.path.isdir(prism_config.ROMS):
        return
    for system, copies in backups().items():
        best = max(gamelist.entry_count(t) for _, t in copies)
        if best < 10 or not os.path.isdir(f"{prism_config.ROMS}/{system}"):
            continue
        live_path = f"{gamelist.GAMELISTS}/{system}/gamelist.xml"
        try:
            cur = open(live_path, encoding="utf-8", errors="replace").read()
            ET.fromstring("<t>" + re.sub(r"^\s*<\?xml[^>]*\?>", "", cur) + "</t>")
            now, broken = gamelist.entry_count(cur), False
        except (OSError, ET.ParseError):
            now, broken = 0, True
        if not broken and now * 2 >= best:
            continue
        files = len([f for f in os.listdir(f"{prism_config.ROMS}/{system}") if not f.startswith(".")])
        if files * 2 < best:
            continue                        # the games themselves went away (moved, removed): not a broken list
        day, good = next((d_, t) for d_, t in copies if gamelist.entry_count(t) * 10 >= best * 9)
        when = "this morning's" if day == time.strftime("%Y%m%d") else \
            "yesterday's" if day == time.strftime("%Y%m%d", time.localtime(time.time() - 86400)) else "a recent"
        note = f"Your {fullname(system)} list was restored from {when} backup."

        def restore(live_path=live_path, good=good):
            if os.path.exists(live_path):
                shutil.copy2(live_path, live_path + time.strftime(".broken-%Y%m%d-%H%M%S"))
            os.makedirs(os.path.dirname(live_path), exist_ok=True)
            gamelist.write_atomic(live_path, good)
        fix(note, f"{system} gamelist {'unreadable' if broken else f'{now} of {best} entries'}: restored from the "
            f"{day} backup", restore)


def check_settings():
    """The few ES-DE settings Prism can't work without."""
    want = {"Theme": prism_config.THEME, "ROMDirectory": prism_config.ROMS,
            "MediaDirectory": f"{prism_config.TOOLS}/downloaded_media"}
    try:
        text = open(SETTINGS).read()
    except OSError:
        return
    new = text
    for key, val in want.items():
        m = re.search(rf'<string name="{key}" value="([^"]*)" />', new)
        if m and m.group(1) != val and (key != "Theme" or os.path.isdir(prism_config.THEME_DIR)):
            log(f"es_settings {key}: {m.group(1)!r} -> {val!r}")
            new = new.replace(m.group(0), f'<string name="{key}" value="{val}" />')
    if new != text:
        def write():
            shutil.copy2(SETTINGS, SETTINGS + time.strftime(".doctor-%Y%m%d-%H%M%S"))
            tmp = SETTINGS + ".tmp"; open(tmp, "w").write(new); os.replace(tmp, SETTINGS)
        fix("Prism's look and game folders were set back in ES-DE's settings.", "ES-DE settings restored", write)


def closed():
    import gamelist
    gamelist.esde_check()                      # refuses while ES-DE runs
    check_gamelists()
    check_settings()
    summarize()


def summarize():
    if DRY:
        return
    notes = [n for n, _ in fixed if n]
    if notes:
        import prism_notice
        for n in notes:
            prism_notice.remember_fix(n)
        prism_notice.toast(f"doctor-{time.strftime('%Y%m%d-%H%M')}", "Prism fixed something",
                           notes[0] if len(notes) == 1 else f"{notes[0]} (and {len(notes) - 1} more)")


if __name__ == "__main__":
    cmd = (sys.argv[1:] or ["check"])[0]
    if cmd == "live":
        live()
    elif cmd == "closed":
        closed()
    elif cmd == "check":
        live()
        try:
            check_gamelists(); check_settings()
        except Exception as e:
            log(f"closed checks skipped: {e}")
        print("nothing to fix" if not fixed else f"{len(fixed)} thing(s) would be fixed")
    else:
        sys.exit(__doc__)
