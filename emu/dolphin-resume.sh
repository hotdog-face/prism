#!/bin/bash
. "$(dirname "$(readlink -f "$0")")/../prism_env.sh"  # Prism paths (PRISM_*)
# ES-DE's Dolphin command (GameCube/Wii) with resume: the Steam button / L4+R4 press Shift+F1 (Save State Slot 1)
# before quitting; we remember which <GameID>.s01 belongs to which disc and load it next launch with -s.
# Boot-test launches (DECK_TESTER=1) start fresh. Used via ~/ES-DE/custom_systems/es_find_rules.xml (DOLPHIN).
APP=${PRISM_HOME}/.local/share/flatpak/exports/bin/org.DolphinEmu.dolphin-emu
STATES=${PRISM_HOME}/.var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/StateSaves
MAP=${PRISM_DIR}/emu/dolphin_states.txt   # "<rom path>\t<state file name>" lines
ROM="${!#}"
if [ -n "$DECK_TESTER" ]; then exec "$APP" "$@"; fi
STATE=$(grep -F "$ROM	" "$MAP" 2>/dev/null | tail -1 | cut -f2)
ARGS=()
[ -n "$STATE" ] && [ -s "$STATES/$STATE" ] && ARGS=(-s "$STATES/$STATE")
touch /tmp/dolphin_launch.$$
"$APP" "${ARGS[@]}" "$@"
rc=$?
NEW=$(find "$STATES" -maxdepth 1 -name '*.s01' -newer /tmp/dolphin_launch.$$ -printf '%f\n' 2>/dev/null | head -1)
rm -f /tmp/dolphin_launch.$$
if [ -n "$NEW" ] && [ "$NEW" != "$STATE" ]; then printf '%s\t%s\n' "$ROM" "$NEW" >> "$MAP"; fi
exit $rc
