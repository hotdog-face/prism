#!/bin/bash
# Steam shortcut "Game Tester": runs the command queued in ~/prism/tester_next.sh (written by game_tester.py),
# so the game shows on screen like a normal Steam launch. Hidden from the library.
cmd="$(dirname "$(readlink -f "$0")")/tester_next.sh"
[ -x "$cmd" ] && exec "$cmd"
