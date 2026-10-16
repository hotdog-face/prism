#!/bin/bash
. "$(dirname "$(readlink -f "$0")")/prism_env.sh"  # Prism paths (PRISM_*)
# "Request Movies" tile: Overseerr on the NAS in a full-screen Chrome app window. Uses its own Chrome profile
# so no extensions, sign-in prompts or other tabs get in front of it. The URL is overseerr_url in config.toml;
# the Steam shortcut points here, so no Steam restart is needed.
# Pages on the NAS go through deck_kbd_proxy.py (127.0.0.1:5056), which pops up the Steam keyboard when a
# text box is tapped; everything else goes direct.
URL="${PRISM_CFG_OVERSEERR_URL:?set overseerr_url in ~/.config/prism/config.toml}"
HOST=$(printf %s "$URL" | sed -E "s#^[a-z]+://([^/:]+).*#\\1#")
PROFILE="$HOME/.var/app/com.google.Chrome/data/overseerr-profile"
PAC="function FindProxyForURL(url, host) { return host == \"$HOST\" ? \"PROXY 127.0.0.1:5056\" : \"DIRECT\"; }"
mkdir -p "$PROFILE"
systemctl --user is-active -q switchhome || systemctl --user start deck-kbd-proxy 2>/dev/null   # inside switchhome now
exec /usr/bin/flatpak run com.google.Chrome --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check \
  --disable-sync --proxy-pac-url="data:application/x-ns-proxy-autoconfig;base64,$(printf %s "$PAC" | base64 -w0)" \
  --app="$URL" --start-fullscreen
