#!/bin/bash
# Remove Prism (Desktop Mode). Started by "Uninstall Prism.desktop", or: bash ~/prism/install/uninstall.sh
# Takes away exactly what install recorded in ~/.config/prism/installed.json. Your games, saves, EmuDeck, Steam
# library and your normal ES-DE setup are not touched.
set -u
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
TITLE="Uninstall Prism"
LOG="/tmp/prism-uninstall.log"; : > "$LOG"
ask() { zenity --question --title="$TITLE" --width=460 --text="$1" 2>/dev/null; }

[ -f "$HOME/.config/prism/installed.json" ] || { zenity --info --title="$TITLE" --text="Prism isn't installed." 2>/dev/null; exit 0; }
ask "Remove Prism from this Deck?

This removes its services, its Steam shortcut and its ES-DE setup. Your games, saves, EmuDeck and your normal ES-DE setup stay as they are." || exit 0
KEEP=()
ask "Keep Prism's favorites, play history and settings, in case you install it again?" && KEEP=(--keep-data)

if pgrep -x steam >/dev/null; then
  zenity --info --title="$TITLE" --width=460 --text="Steam will close for a moment so the Prism shortcut can be removed." 2>/dev/null
fi
/usr/bin/python3 "$HERE/setup.py" uninstall "${KEEP[@]}" >>"$LOG" 2>&1 || {
  zenity --error --title="$TITLE" --text="Uninstall stopped partway. Details: $LOG" 2>/dev/null; exit 1; }

if [ -f /etc/udev/rules.d/90-prism.rules ] || [ -d "$HOME/homebrew/plugins/PrismBridge" ]; then
  if ask "Last step: remove Prism's touchscreen rule and Decky plugin. This needs your Deck's password."; then
    PW=$(zenity --password --title="$TITLE" 2>/dev/null) && \
      printf '%s\n' "$PW" | sudo -S -p "" bash "$HERE/root_setup.sh" uninstall "$USER" >>"$LOG" 2>&1
    unset PW
  fi
fi
zenity --info --title="$TITLE" --width=460 --text="Prism is removed.

To finish, delete the ~/prism folder (it only holds Prism itself)." 2>/dev/null
