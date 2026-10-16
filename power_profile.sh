#!/bin/bash
# Per-console power limit (2026-10-06: quieter fan, longer battery). Called by ES-DE's game-start
# (with the system name) and game-end ("restore") event scripts. Older 2D consoles need a fraction of the APU.
CAP=$(ls /sys/class/hwmon/hwmon*/power1_cap 2>/dev/null | head -1)
[ -z "$CAP" ] && exit 0
case "$1" in
  nes|fds|snes|sfc|satellaview|genesis|megadrive|mastersystem|gamegear|sg-1000|segacd|sega32x|gb|gbc|gba|\
  pcengine|pcenginecd|tg16|neogeo|neogeocd|ngp|ngpc|wonderswan|wonderswancolor|virtualboy|msx|atari*|\
  colecovision|intellivision|cps1|cps2|lynx|atarilynx)  W=6 ;;
  arcade|mame|fbneo|cps3|psx|n64|saturn|dreamcast|naomi|atomiswave|nds|psp|model2|scummvm)  W=10 ;;
  *)  W=15 ;;    # GameCube, Wii, PS2, Switch, Vita, Xbox 360, PC games, and "restore"
esac
if [ -w "$CAP" ]; then echo $((W * 1000000)) > "$CAP"          # Prism udev rule: writable without sudo
else echo $((W * 1000000)) | sudo -n tee "$CAP" >/dev/null 2>&1; fi
echo "$(date +%T) power $1 -> ${W}W" >> /tmp/deck_power_profile.log
