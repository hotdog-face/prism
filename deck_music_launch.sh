#!/bin/bash
. "$(dirname "$(readlink -f "$0")")/prism_env.sh"  # Prism paths (PRISM_*)
# Prism Music row: open deck_music.py on the page named in the stub (ES-DE passes the path shell-escaped)
f=$(printf '%b' "$*" | sed 's/\\\(.\)/\1/g')
page=$(head -1 "$f" 2>/dev/null)
exec /usr/bin/python3 "${PRISM_DIR}/deck_music.py" "${page:-home}"
