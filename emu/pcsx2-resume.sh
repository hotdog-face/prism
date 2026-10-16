#!/bin/bash
. "$(dirname "$(readlink -f "$0")")/../prism_env.sh"  # Prism paths (PRISM_*)
# ES-DE's PCSX2 command with resume (L4+R4 = HOME). PCSX2 writes "<serial> (<crc>).resume.p2s" on a clean shutdown
# (SaveStateOnShutdown = true). We remember which file belongs to which disc (learned after its first run) and pass
# it back with -statefile. Boot-test launches (DECK_TESTER=1) start fresh. Used via es_find_rules.xml (PCSX2).
APP=${PRISM_HOME}/Applications/pcsx2-Qt.AppImage
STATES=${PRISM_EMU}/saves/pcsx2/states
MAP=${PRISM_DIR}/emu/pcsx2_states.txt   # "<rom path>\t<state file name>" lines
ROM="${!#}"
if [ -n "$DECK_TESTER" ]; then exec "$APP" "$@"; fi
STATE=$(grep -F "$ROM	" "$MAP" 2>/dev/null | tail -1 | cut -f2)
ARGS=()
[ -n "$STATE" ] && [ -s "$STATES/$STATE" ] && ARGS=(-statefile "$STATES/$STATE")
touch /tmp/pcsx2_launch.$$
"$APP" "${ARGS[@]}" "$@"
rc=$?
NEW=$(find "$STATES" -maxdepth 1 -name '*.p2s' -newer /tmp/pcsx2_launch.$$ -printf '%f\n' 2>/dev/null | head -1)
rm -f /tmp/pcsx2_launch.$$
if [ -n "$NEW" ] && [ "$NEW" != "$STATE" ]; then printf '%s\t%s\n' "$ROM" "$NEW" >> "$MAP"; fi
exit $rc
