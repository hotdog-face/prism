#!/usr/bin/env python3
"""Where things live on this Deck - the one place Prism code gets its paths from.

    import prism_config
    roms = f"{prism_config.ROMS}/snes"

Shell scripts source prism_env.sh, which exports the same values as PRISM_* variables
(python3 prism_config.py --sh prints them).

Each value comes from the first of:
  1. ~/.config/prism/config.toml        e.g.  emulation = "/run/media/mmcblk0p1/Emulation"
  2. EmuDeck's settings (~/.config/EmuDeck/settings.sh, emulationPath)
  3. the default below
HOME is the home of the user who owns this folder, so scripts run as root (sudo, system units)
still find the deck user's files.
"""
import os
import pwd
import re
import sys

PRISM = os.path.dirname(os.path.abspath(__file__))          # this folder (~/prism)
_link = os.path.join(pwd.getpwuid(os.stat(PRISM).st_uid).pw_dir if os.path.exists(PRISM) else "", "prism")
if os.path.islink(_link) and os.path.realpath(_link) == os.path.realpath(PRISM):
    PRISM = _link             # an installed Prism always calls itself ~/prism, whichever way it was started
try:
    HOME = pwd.getpwuid(os.stat(PRISM).st_uid).pw_dir
except (KeyError, OSError):
    HOME = os.path.expanduser("~")
HOME = os.environ.get("PRISM_TEST_HOME") or HOME          # release/rehearse.py: a throwaway home folder
CONFIG_FILE = f"{HOME}/.config/prism/config.toml"


def _config():
    try:
        import tomllib
        with open(CONFIG_FILE, "rb") as f:
            return tomllib.load(f)
    except (OSError, ValueError, ImportError):
        return {}


def _emudeck_emulation():
    """emulationPath from EmuDeck's settings.sh, e.g.  emulationPath="/run/media/deck/MYCARD"/Emulation"""
    try:
        with open(f"{HOME}/.config/EmuDeck/settings.sh") as f:
            for line in f:
                m = re.match(r"\s*(?:export\s+)?emulationPath=(.+)", line)
                if m:
                    value = m.group(1).split("#")[0].strip().replace('"', "").replace("'", "")
                    value = value.replace("$HOME", HOME).replace("${HOME}", HOME)
                    return value.rstrip("/") or None
    except OSError:
        pass
    return None


_cfg = _config()
EMU = (_cfg.get("emulation") or _emudeck_emulation() or f"{HOME}/Emulation").rstrip("/")  # EmuDeck's Emulation folder
STORAGE = (_cfg.get("storage") or os.path.dirname(EMU)).rstrip("/")   # the drive that holds it (SD card root)
ROMS = f"{EMU}/roms"
TOOLS = f"{EMU}/tools"
SAVES = f"{EMU}/saves"
BIOS = f"{EMU}/bios"
STEAM = (_cfg.get("steam") or f"{HOME}/.local/share/Steam").rstrip("/")
ESDE = (_cfg.get("esde") or f"{HOME}/ES-DE").rstrip("/")          # ES-DE's data folder (gamelists, themes)
THEME = _cfg.get("theme") or "prism"                                  # ES-DE theme folder name
PRISM_APPID = str(_cfg.get("steam_appid") or "")                       # Prism's Steam shortcut (the installer adds it)
PRISM_RUNGAMEID = str((int(PRISM_APPID) << 32) | 0x02000000) if PRISM_APPID.isdigit() else ""
TESTER_APPID = str(_cfg.get("tester_appid") or "")                     # the hidden Game Tester shortcut
ESDE_APP = (_cfg.get("esde_app") or next((p for p in (f"{HOME}/.local/share/prism/es-de-app",
                                                    f"{HOME}/Applications/ES-DE-switchhome") if os.path.isdir(p)), ""))
ESDE_RESOURCES = next((p for p in (f"{ESDE_APP}/usr/share/es-de/resources/systems/linux",
                                   f"{PRISM}/esde_x/squashfs-root/usr/share/es-de/resources/systems/linux")
                       if os.path.isdir(p)), "")                     # ES-DE's own es_systems.xml / es_find_rules.xml
THEME_DIR = f"{ESDE}/themes/{THEME}"


def get(key, default=None):
    """Any other setting from config.toml, e.g. prism_config.get("overseerr_url")."""
    return _cfg.get(key, default)


def _toml(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    import json
    return json.dumps(str(v))


def set(key, value):                     # noqa: A001  (prism_config.set("haptics", False))
    """Save one setting in config.toml (other lines and comments stay as they are)."""
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    try:
        lines = open(CONFIG_FILE).read().split("\n")
    except OSError:
        lines = ["# Prism settings (safe to edit)."]
    line = f"{key} = {_toml(value)}"
    for i, l in enumerate(lines):
        if re.match(rf"\s*{re.escape(key)}\s*=", l):
            lines[i] = line
            break
    else:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append(line)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")
    os.replace(tmp, CONFIG_FILE)
    _cfg[key] = value


SH_VARS = {"PRISM_DIR": PRISM, "PRISM_HOME": HOME, "PRISM_EMU": EMU, "PRISM_STORAGE": STORAGE,
           "PRISM_ROMS": ROMS, "PRISM_STEAM": STEAM, "PRISM_ESDE": ESDE, "PRISM_THEME_DIR": THEME_DIR}

if __name__ == "__main__":
    if "--sh" in sys.argv:
        import shlex
        for k, v in SH_VARS.items():
            print(f"export {k}={shlex.quote(v)}")
        for k, v in _cfg.items():          # other plain settings, e.g. overseerr_url -> PRISM_CFG_OVERSEERR_URL
            if isinstance(v, (str, int, float)) and re.fullmatch(r"\w+", k):
                print(f"export PRISM_CFG_{k.upper()}={shlex.quote(str(v))}")
    else:
        for k, v in SH_VARS.items():
            print(f"{k:14} {v}")
        print(f"{'config file':14} {CONFIG_FILE}" + ("" if os.path.exists(CONFIG_FILE) else "  (none - using EmuDeck / defaults)"))
