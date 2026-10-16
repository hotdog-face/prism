#!/bin/bash
# For deck_curate.py: buttons that wake the screensaver must not trigger shortcuts.
# The screensaver is a dim (console-style auto-sleep): deck_autosleep.py puts the Deck to sleep a minute later.
echo "$(date +%T.%N) screensaver-start $1" >> /tmp/esde_events.log
touch /tmp/esde_screensaver
# run outside ES-DE's Steam launch (a child process would keep the "Games" launch alive after ES-DE exits)
systemd-run --user --unit=deck-autosleep --collect --quiet /usr/bin/python3 @PRISM_DIR@/deck_autosleep.py >/dev/null 2>&1 &
