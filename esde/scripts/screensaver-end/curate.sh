#!/bin/bash
# For deck_curate.py: buttons that wake the screensaver must not trigger shortcuts.
echo "$(date +%T.%N) screensaver-end $1" >> /tmp/esde_events.log
[ "screensaver-end" = screensaver-start ] && touch /tmp/esde_screensaver || { rm -f /tmp/esde_screensaver; date +%s.%N > /tmp/esde_screensaver_end; }
