#!/bin/bash
# $1 rom path, $2 name, $3 system. PS5/console-style launch screen (ES-DE launch box is off), power profile per
# console; media / For You launches must not linger in Continue Playing (see @PRISM_DIR@/esde_tidy.py).
echo "$(date +%T) game-start [$1] [$2] [$3]" >> /tmp/esde_events.log
case "$1" in   # Discover titles / Search & request open a request card, not playback: no splash, no profile, music keeps going
  */movies/Discover/*|*/tv/Discover/*) exit 0 ;;
esac
[ "$3" = needsalook ] && exit 0   # Needs a look: picking a game only asks "Put back?" (prism_shelf.py)
[ -p /tmp/prism_haptics ] && echo T 1<>/tmp/prism_haptics 2>/dev/null   # haptic tap (prism_haptics.py)
case "$3" in   # Switch: buttons by label, docked mode on the TV (before Ryujinx reads its config)
  switch) /usr/bin/python3 @PRISM_DIR@/ryujinx_mode.py >> /tmp/esde_events.log 2>&1 ;;
  wii) /usr/bin/python3 @PRISM_DIR@/dolphin_mode.py >> /tmp/esde_events.log 2>&1 ;;
esac
/usr/bin/python3 @PRISM_DIR@/tv_video.py >> /tmp/esde_events.log 2>&1   # TV vs Deck: bezel shape, N64 render size
case "$3" in   # local multiplayer: docked, players 1-4 = the external pads in Steam's order (@PRISM_DIR@/party_input.py)
  steam|media|foryou|music|movies|tv) ;;
  *) /usr/bin/python3 @PRISM_DIR@/party_input.py "$1" "$3" >> /tmp/esde_events.log 2>&1 ;;
esac
case "$3" in
  steam|media|foryou|music) ;;
  movies|tv) setsid /usr/bin/python3 @PRISM_DIR@/deck_splash.py launch "$1" "$2" "$3" >/dev/null 2>&1 < /dev/null & ;;
  *) # games: the console-style hub (switchhome game-hub thread); the old launch splash only when the hub can't run
     if systemctl --user is-active -q switchhome && [ ! -e /tmp/game_hub_off ] && [ ! -e @PRISM_DIR@/party_mode ]; then   # party: straight into the game
       /usr/bin/python3 @PRISM_DIR@/hub_event.py "$1" "$2" "$3"
     else
       setsid /usr/bin/python3 @PRISM_DIR@/deck_splash.py launch "$1" "$2" "$3" >/dev/null 2>&1 < /dev/null &
     fi ;;
esac
@PRISM_DIR@/power_profile.sh "$3" &
case "$3" in media|foryou) touch @PRISM_DIR@/esde_restart_pending ;; esac
if [ "$3" != music ]; then   # a game or video starts: pause Prism Music (deck_music_player.py)
  python3 -c 'import socket,os; s=socket.socket(socket.AF_UNIX); s.settimeout(0.5); s.connect(f"/run/user/{os.getuid()}/app/io.mpv.Mpv/music.sock"); s.sendall(b"{\"command\": [\"set_property\", \"pause\", true]}\n")' 2>/dev/null &
fi
exit 0
