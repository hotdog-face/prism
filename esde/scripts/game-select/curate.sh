#!/bin/bash
# For deck_curate.py: remember the selected game (path, name, system, system full name).
# Browsing means the screensaver is not running (clears a flag left behind if ES-DE closed mid-screensaver).
rm -f /tmp/esde_screensaver
printf '%s\0%s\0%s\0%s' "$1" "$2" "$3" "$4" > /tmp/esde_selected.$$ && mv -f /tmp/esde_selected.$$ /tmp/esde_selected
echo "$(date +%T.%N) game-select $3 $2" >> /tmp/esde_events.log
[ -p /tmp/prism_haptics ] && echo t 1<>/tmp/prism_haptics 2>/dev/null   # haptic tick (prism_haptics.py)
