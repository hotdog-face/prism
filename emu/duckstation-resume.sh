#!/bin/bash
. "$(dirname "$(readlink -f "$0")")/../prism_env.sh"  # Prism paths (PRISM_*)
# ES-DE's DuckStation command with resume (2026-10-06: L4+R4 = HOME, coming back resumes).
# DuckStation saves <serial>_resume.sav on a clean exit (L4+R4 sends SIGTERM). "-resume" with no such file shows an
# error, so we only pass it when we know this disc's serial (learned after its first run) and its resume file exists.
# Boot-test launches (DECK_TESTER=1) always start fresh. Used via ~/ES-DE/custom_systems/es_find_rules.xml.
STATES=${PRISM_EMU}/saves/duckstation/states
MAP=${PRISM_DIR}/emu/duckstation_serials.txt   # "<rom path>\t<serial>" lines
ROM="${!#}"
if [ -n "$DECK_TESTER" ]; then exec /usr/bin/flatpak run org.duckstation.DuckStation "$@"; fi
SERIAL=$(grep -F "$ROM	" "$MAP" 2>/dev/null | tail -1 | cut -f2)
ARGS=()
[ -n "$SERIAL" ] && [ -s "$STATES/${SERIAL}_resume.sav" ] && ARGS=(-resume)
touch /tmp/duckstation_launch.$$
/usr/bin/flatpak run org.duckstation.DuckStation "${ARGS[@]}" "$@"
rc=$?
NEW=$(find "$STATES" -maxdepth 1 -name '*_resume.sav' -newer /tmp/duckstation_launch.$$ -printf '%f\n' 2>/dev/null | head -1)
rm -f /tmp/duckstation_launch.$$
if [ -n "$NEW" ] && [ -z "$SERIAL" ]; then printf '%s\t%s\n' "$ROM" "${NEW%_resume.sav}" >> "$MAP"; fi
exit $rc
