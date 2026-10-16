#!/bin/bash
# The only part of Prism that runs as root, once, from the installer (after the user typed their password):
#   root_setup.sh install   <prism source folder> <user>
#   root_setup.sh uninstall <user>
# It adds a udev rule (touchscreen readable and the GPU power limit writable by that user, so Prism never needs sudo
# afterwards) and copies Prism's Decky plugin. Everything lives under /etc and ~/homebrew, which SteamOS updates keep.
set -eu
RULE=/etc/udev/rules.d/90-prism.rules
PLUGIN=PrismBridge
OLD_PLUGIN=SwitchHomeBridge

reload() {
  udevadm control --reload-rules
  udevadm trigger --subsystem-match=input --action=change || true
  udevadm trigger --subsystem-match=hwmon --action=add || true
}

case "${1:-}" in
install)
  SRC="$2"; U="$3"
  G="$(id -gn "$U")"; H="$(getent passwd "$U" | cut -d: -f6)"
  cat > "$RULE" <<EOF
# Prism (installed by ~/prism/install): the signed-in user reads the touchscreen and sets the GPU power limit
# without sudo. Remove with: ~/prism/install/uninstall.sh
SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_TOUCHSCREEN}=="1", TAG+="uaccess", GROUP="$G", MODE="0660"
SUBSYSTEM=="hwmon", ATTR{name}=="amdgpu", ACTION=="add", RUN+="/bin/chgrp $G /sys%p/power1_cap", RUN+="/bin/chmod g+w /sys%p/power1_cap"
EOF
  reload
  if [ -d "$H/homebrew/plugins" ]; then
    rm -rf "$H/homebrew/plugins/$PLUGIN" "$H/homebrew/plugins/$OLD_PLUGIN"   # old name: same socket, never both
    cp -r "$SRC/decky/$PLUGIN" "$H/homebrew/plugins/$PLUGIN"
    systemctl restart plugin_loader.service 2>/dev/null || true
    echo "decky plugin installed"
  else
    echo "decky not installed: plugin skipped"
  fi
  echo "root setup done"
  ;;
uninstall)
  U="$2"; H="$(getent passwd "$U" | cut -d: -f6)"
  rm -f "$RULE"; reload
  rm -rf "$H/homebrew/plugins/$PLUGIN"
  systemctl restart plugin_loader.service 2>/dev/null || true
  echo "root parts removed"
  ;;
*)
  echo "usage: root_setup.sh install <src> <user> | uninstall <user>" >&2; exit 2 ;;
esac
