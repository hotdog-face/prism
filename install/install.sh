#!/bin/bash
# Prism installer (Desktop Mode). Started by "Install Prism.desktop" next to it, or: bash install/install.sh
# The dialogs only collect choices; install/setup.py does the work and records everything it creates in
# ~/.config/prism/installed.json, which install/uninstall.sh uses to put the Deck back the way it was.
set -u
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SRC="$(dirname "$HERE")"                  # the unpacked Prism folder
DEST="$HOME/prism"
TITLE="Install Prism"
LOG="/tmp/prism-install.log"
: > "$LOG"

ask()  { zenity --question --title="$TITLE" --width=460 --text="$1" 2>/dev/null; }
info() { zenity --info --title="$TITLE" --width=460 --text="$1" 2>/dev/null; }
fail() { zenity --error --title="$TITLE" --width=460 --text="$1\n\nDetails: $LOG" 2>/dev/null; exit 1; }
entry() { zenity --entry --title="$TITLE" --width=460 --text="$1" --entry-text="${2:-}" 2>/dev/null; }

command -v zenity >/dev/null || { echo "zenity is missing; run this from SteamOS Desktop Mode." >&2; exit 1; }

# ---- checks ---------------------------------------------------------------------------------------------------------
CHECK=$(/usr/bin/python3 "$HERE/setup.py" check --src "$SRC" 2>>"$LOG") || fail "Prism can't be installed on this Deck yet:\n\n$CHECK"
# check prints "ok" lines and "warn: ..." lines
WARN=$(printf '%s\n' "$CHECK" | sed -n 's/^warn: /• /p')

ask "Prism turns this Deck into a console-style home screen for your Steam and emulated games.

It installs into ~/prism, runs ES-DE with its own settings (your normal ES-DE setup is not touched), adds a \"Prism\" shortcut to Steam and starts in Game Mode.
${WARN:+
Notes:
$WARN
}
Install now?" || exit 0

# ---- optional features ---------------------------------------------------------------------------------------------
CHOICES=$(zenity --list --checklist --title="$TITLE" --width=620 --height=380 \
  --text="Choose the extras you want. You can change these later in Prism Settings." \
  --column="" --column="Feature" --column="What it needs" --separator=" " --print-column=2 \
  FALSE "Movies & TV"    "A Plex server" \
  FALSE "Request Movies" "An Overseerr server (with Movies & TV)" \
  TRUE  "Nightly scrape" "A free ScreenScraper account, set in ES-DE's scraper menu" \
  TRUE  "Xbox 360"       "EmuDeck's Xenia" \
  FALSE "Save backups"   "Another computer that accepts SSH" 2>/dev/null) || exit 0

ARGS=()
case "$CHOICES" in *"Movies"*)
  URL=$(entry "Plex server address (for example http://192.168.x.x:32400):") || exit 0
  TOKEN=$(entry "Plex token for that server.\n\nIn Plex Web, open any movie, choose ··· → Get Info → View XML; the token is the X-Plex-Token=… part of the address.") || exit 0
  ARGS+=(--plex-url "$URL" --plex-token "$TOKEN") ;;
esac
case "$CHOICES" in *"Request Movies"*)
  OURL=$(entry "Overseerr address (for example http://192.168.x.x:5055):") || exit 0
  ARGS+=(--overseerr-url "$OURL") ;;
esac
case "$CHOICES" in *"Save backups"*)
  BSSH=$(entry "Back up saves to (SSH user@host; this Deck's SSH key must already be accepted there):") || exit 0
  BDIR=$(entry "Folder on that computer for the backups:" "deck-saves") || exit 0
  ARGS+=(--backup-ssh "$BSSH" --backup-dir "$BDIR") ;;
esac
FEATURES=()
for f in "Movies & TV:plex" "Request Movies:overseerr" "Nightly scrape:scrape" "Xbox 360:xbox360" "Save backups:backup"; do
  case "$CHOICES" in *"${f%%:*}"*) FEATURES+=("${f##*:}") ;; esac
done

# ---- the one step that needs the Deck's password --------------------------------------------------------------------
ROOT_OK=0
if ask "One part of Prism needs your Deck's password once: it lets Prism read the touchscreen (taps on the home screen and in the movie player), set the per-console power limit, and installs Prism's Decky plugin.

Without it, Prism still works, but taps on Prism's own screens and the power limit stay off.

Enter the password now?"; then
  for try in 1 2 3; do
    PW=$(zenity --password --title="$TITLE" 2>/dev/null) || break
    if printf '%s\n' "$PW" | sudo -S -p "" true 2>>"$LOG"; then
      printf '%s\n' "$PW" | sudo -S -p "" bash "$HERE/root_setup.sh" install "$SRC" "$USER" >>"$LOG" 2>&1 && ROOT_OK=1
      break
    fi
    zenity --warning --title="$TITLE" --text="That password didn't work. (If you never set one, run passwd in Konsole first.)" 2>/dev/null
  done
  unset PW
fi

# ---- install ---------------------------------------------------------------------------------------------------------
if pgrep -x steam >/dev/null; then
  info "Steam will close for a moment so the Prism shortcut can be added. It opens again when you return to Game Mode." || true
fi
(
  /usr/bin/python3 "$HERE/setup.py" install --src "$SRC" --dest "$DEST" --root-ok "$ROOT_OK" \
      --features "${FEATURES[@]:-}" "${ARGS[@]}" >>"$LOG" 2>&1
  echo "EXIT=$?" >>"$LOG"
) | zenity --progress --pulsate --auto-close --no-cancel --title="$TITLE" --text="Installing Prism..." 2>/dev/null
grep -q "^EXIT=0$" "$LOG" || fail "The install stopped partway. Nothing outside ~/prism and Prism's own folders was changed; run Uninstall Prism to clean up."

info "Prism is installed.

Return to Game Mode (the \"Return to Gaming Mode\" icon on the desktop) and Prism starts by itself. Hold L5 + R5 at any time to get back to it.

To remove it later, run Uninstall Prism in ~/prism."
