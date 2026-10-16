#!/bin/bash
# Prism (ES-DE) has finished loading: tells the startup screen (deck_splash.py boot) to fade away
touch /tmp/prism_ready
echo "$(date +%T.%N) startup" >> /tmp/esde_events.log
systemd-run --user --collect --quiet /usr/bin/python3 @PRISM_DIR@/prism_screens.py after-boot >/dev/null 2>&1 &   # Continue screen on a real start (prism_screens.py)
