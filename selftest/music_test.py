import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Prism root
import prism_config  # noqa: E402  (paths)
import subprocess, sys, time
import deck_curate as d
LOG = f"{prism_config.PRISM}/media_card_test.log"
say = lambda m: open(LOG, "a").write(time.strftime("%T ") + m + "\n")
d.log = lambda m: say("[curate] " + m)
H = prism_config.HOME
subprocess.run(["systemctl", "--user", "reset-failed", "deck-music-player"], capture_output=True)
subprocess.run(["systemd-run", "--user", "--unit=deck-music-player", "--collect", "/usr/bin/python3",
                f"{prism_config.PRISM}/deck_music_player.py"], capture_output=True)
for _ in range(50):
    time.sleep(0.1)
    if d.mpv_ipc(d.MUSIC_SOCK, ["get_property", "idle-active"]) is not None: break
d.mpv_ipc(d.MUSIC_SOCK, ["loadlist", prism_config.PRISM + "/music/queue.m3u", "replace"]); time.sleep(0.15)
d.mpv_ipc(d.MUSIC_SOCK, ["set_property", "playlist-pos", 0]); d.mpv_ipc(d.MUSIC_SOCK, ["set_property", "pause", False])
get = lambda prop: d.mpv_ipc(d.MUSIC_SOCK, ["get_property", prop])
for _ in range(30):
    time.sleep(0.5)
    if get("time-pos"): break
time.sleep(3)
t1, p1 = get("media-title"), get("time-pos")
say(f"playing: {t1} at {p1}")
d.music_button(hold=False); time.sleep(1); paused = get("pause")
d.music_button(hold=False); time.sleep(1); resumed = get("pause")
d.music_button(hold=True); time.sleep(2); t2 = get("media-title")
say(f"R5 tap -> pause={paused}; tap again -> pause={resumed}; hold -> {t2}")
d._focus_cache[0] = 0
res = [("music plays", p1 is not None and p1 > 0),
       ("R5 tap pauses, tap again resumes", paused is True and resumed is False),
       ("R5 hold skips to the next song", bool(t2 and t2 != t1)),
       ("focus stays on Prism", str(d.focus_now()) == prism_config.PRISM_APPID)]
subprocess.run(["systemctl", "--user", "stop", "deck-music-player"], capture_output=True); time.sleep(1.5)
res.append(("player stops cleanly", subprocess.run(["systemctl", "--user", "is-active", "deck-music-player"],
                                                   capture_output=True, text=True).stdout.strip() != "active"))
say("=== MUSIC RESULTS")
for n, ok in res: say(f"{'PASS' if ok else 'FAIL'}  {n}")
