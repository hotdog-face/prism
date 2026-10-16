#!/usr/bin/env python3
"""Prism's extras, on and off (2026-10-14; Prism Settings > Extras and > Feel).

    import prism_features
    prism_features.enabled("plex")          configured and switched on
    prism_features.set_enabled("plex", False)

An extra switched off: its timers are disabled, its home-screen rows leave ES-DE's custom systems file (kept in
hidden_systems.json and put back when it's switched on again), and Prism refreshes its home screen the next time
nobody is playing (esde_pending). Nothing about the extra's data is removed. Feel settings (haptics, Continue screen,
menu sounds, Who's playing on connect) are plain settings in config.toml; menu sounds is ES-DE's own setting, applied
at that same refresh.
    prism_features.py status
"""
import json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_config  # noqa: E402  (paths)

EXTRAS = {   # key: (title, what it does, timers, ES-DE systems it adds)
    "plex": ("Movies & TV", "Your Plex library as rows on the home screen", ["plex-sync.timer"], ["movies", "tv", "music"]),
    "overseerr": ("Request Movies", "Ask for a movie or show from the Movies row (Overseerr)", [], []),
    "scrape": ("Nightly scrape", "Box art and videos for new games, fetched overnight", ["night-scrape.timer"], []),
    "xbox360": ("Xbox 360", "Xbox 360 games through Xenia", [], ["xbox360"]),
    "backup": ("Save backups", "A copy of every save on another computer each night", ["save-backup.timer"], []),
}
FEEL = {     # config key: (title, what it does, default)
    "haptics": ("Haptics", "A light tick from the trackpads as you move through menus (handheld only)", True),
    "continue_screen": ("Continue screen", "Your last game, ready to pick up, when you come back to the home screen", True),
    "menu_sounds": ("Menu sounds", "The Glass sounds when moving and selecting", True),
    "join_on_connect": ("Who's playing? when a controller connects",
                        "Show the player slots when a second player picks up a controller", True),
}
HIDDEN = f"{prism_config.PRISM}/hidden_systems.json"
SYSTEMS_FILE = f"{prism_config.ESDE}/custom_systems/es_systems.xml"
PENDING = f"{prism_config.PRISM}/esde_settings_pending.json"


def configured(key):
    if key == "plex":
        return os.path.exists(f"{prism_config.PRISM}/plex.json")
    if key == "overseerr":
        return bool(prism_config.get("overseerr_url")) and configured("plex")
    if key == "backup":
        return bool(prism_config.get("backup_ssh") and prism_config.get("backup_dir"))
    return True


def _chosen():
    f = prism_config.get("features")
    return None if f is None else set(f)


def enabled(key):
    if not configured(key):
        return False
    chosen = _chosen()
    return True if chosen is None else key in chosen          # never chosen: everything that's set up is on


def feel(key):
    return bool(prism_config.get(key, FEEL[key][2]))


def _units(names, on):
    unit_dir = f"{prism_config.HOME}/.config/systemd/user"
    for u in names:
        if os.path.exists(f"{unit_dir}/{u}"):
            subprocess.run(["systemctl", "--user", "enable" if on else "disable", "--now", u], capture_output=True)


def _systems(names, on):
    """Move <system> blocks out of / back into ES-DE's custom systems file. ES-DE reads it when it starts."""
    try:
        hidden = json.load(open(HIDDEN))
    except (OSError, ValueError):
        hidden = {}
    xml = open(SYSTEMS_FILE).read()
    changed = False
    for name in names:
        pat = re.compile(r"[ \t]*<system>\s*<name>" + re.escape(name) + r"</name>.*?</system>\s*\n?", re.S)
        if not on:
            m = pat.search(xml)
            if m:
                hidden[name] = m.group(0)
                xml = xml[:m.start()] + xml[m.end():]
                changed = True
        elif name in hidden and not pat.search(xml):
            xml = xml.replace("</systemList>", hidden.pop(name) + "</systemList>")
            changed = True
    if changed:
        tmp = SYSTEMS_FILE + ".tmp"
        open(tmp, "w").write(xml); os.replace(tmp, SYSTEMS_FILE)
        json.dump(hidden, open(HIDDEN, "w"), indent=1)
    return changed


def refresh_home_later():
    """Prism refreshes its home screen the next time nobody is playing (deck_autosleep -> esde_pending)."""
    open(f"{prism_config.PRISM}/esde_restart_pending", "w").close()


def set_enabled(key, on):
    chosen = _chosen()
    if chosen is None:
        chosen = {k for k in EXTRAS if configured(k)}
    chosen = (chosen | {key}) if on else (chosen - {key})
    prism_config.set("features", sorted(chosen))
    _, _, timers, systems = EXTRAS[key]
    _units(timers, on)
    if _systems(systems, on):
        refresh_home_later()
    if key == "plex" and not on and enabled("overseerr"):
        pass                                  # Request Movies needs the Movies row; it just has nothing to show


def set_feel(key, on):
    prism_config.set(key, bool(on))
    if key == "menu_sounds":
        try:
            pend = json.load(open(PENDING))
        except (OSError, ValueError):
            pend = {}
        pend["NavigationSounds"] = "true" if on else "false"
        json.dump(pend, open(PENDING, "w"))
        refresh_home_later()


def apply_pending_esde_settings():
    """Called by esde_pending.py with ES-DE closed."""
    try:
        pend = json.load(open(PENDING))
    except (OSError, ValueError):
        return
    import prism_esde
    for name, value in pend.items():
        try:
            prism_esde.set_setting(name, value)
        except KeyError:
            pass
    os.remove(PENDING)


if __name__ == "__main__":
    if sys.argv[1:] == ["status"]:
        for k, (t, *_r) in EXTRAS.items():
            print(f"{t:18} {'on' if enabled(k) else 'off'}{'' if configured(k) else ' (not set up)'}")
        for k, (t, *_r) in FEEL.items():
            print(f"{t:18} {'on' if feel(k) else 'off'}")
    else:
        sys.exit(__doc__)
