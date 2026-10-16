#!/bin/bash
@PRISM_DIR@/power_profile.sh restore &
[ "$3" != needsalook ] && systemd-run --user --collect --quiet /usr/bin/python3 @PRISM_DIR@/prism_screens.py after-game "$3" >/dev/null 2>&1 &   # Continue screen (prism_screens.py)
exit 0
