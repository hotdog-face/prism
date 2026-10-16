#!/bin/bash
# For deck_curate.py: no game is selected in the system carousel; remember which system is centred (search uses it).
# Browsing means the screensaver is not running (clears a flag left behind if ES-DE closed mid-screensaver).
rm -f /tmp/esde_screensaver
rm -f /tmp/esde_selected
printf '%s' "$1" > /tmp/esde_system
echo "$(date +%T.%N) system-select $1" >> /tmp/esde_events.log
[ -p /tmp/prism_haptics ] && echo t 1<>/tmp/prism_haptics 2>/dev/null   # haptic tick (prism_haptics.py)
