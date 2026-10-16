#!/bin/bash
. "$(dirname "$(readlink -f "$0")")/../prism_env.sh"  # Prism paths (PRISM_*)
# Prism's Xenia launcher (2026-10-13). EmuDeck's tools/launchers/xenia.sh sources EmuDeck's all.sh, which pops a
# "DuckStation migration" dialog on every launch (Prism keeps the DuckStation flatpak on purpose) - the game never
# started behind it - and git-pulls EmuDeck's backend each time. This does the same Proton call and nothing else.
# Same app id as EmuDeck's launcher, so the same Proton prefix (Xenia's settings, saves, profile) is used.
T=${PRISM_EMU}/tools
EXE="\"/usr/bin/bash\" \"${PRISM_EMU}/tools/launchers/xenia.sh\""
APPID=$(/usr/bin/python3 "$T/appID.py" "$EXE" "Xenia")
exec "$T/proton-launch.sh" -p "- Experimental" -i "$APPID" -- "${PRISM_ROMS}/xbox360/xenia_canary.exe" "$@"
