# Prism paths for shell scripts. Source it:   . "$(dirname "$(readlink -f "$0")")/prism_env.sh"
# Exports PRISM_DIR, PRISM_HOME, PRISM_EMU, PRISM_STORAGE, PRISM_ROMS, PRISM_STEAM, PRISM_ESDE, PRISM_THEME_DIR,
# plus PRISM_CFG_<KEY> for every plain setting in config.toml (e.g. PRISM_CFG_OVERSEERR_URL)
# (values from prism_config.py - see it for where they come from). Stops the script if they can't be read,
# so nothing ever runs against an empty path.
_prism_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]:-$0}")")"
eval "$(/usr/bin/python3 "$_prism_dir/prism_config.py" --sh 2>/dev/null)"
unset _prism_dir
if [ -z "$PRISM_EMU" ] || [ -z "$PRISM_HOME" ] || [ -z "$PRISM_DIR" ]; then
    echo "prism_env.sh: could not read Prism paths from prism_config.py" >&2
    exit 1
fi
