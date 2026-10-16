import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Prism root
import prism_config  # noqa: E402  (paths)
import subprocess, sys, time
SOCK = "/run/user/1000/app/io.mpv.Mpv/hudtest.sock"
m = subprocess.Popen(["flatpak", "run", "io.mpv.Mpv", "--vo=null", "--ao=null", "--keep-open=yes", "--hr-seek=yes", "--volume-max=130", "--volume=60",
                      f"--input-ipc-server={SOCK}", f"{prism_config.HOME}/.var/app/io.mpv.Mpv/cache/test600.mka"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)
import plex_play as pp, plex_hud, plex_touch
pp.SOCK = SOCK; plex_hud.SOCK = SOCK
t = plex_touch.Touch(pp.mpv_cmd, pp.mpv_get); t.hud.sock_path = SOCK
pos = lambda: pp.mpv_get("time-pos") or 0
print("start pos", round(pos()), "dur", pp.mpv_get("duration"))
t.down(640, 400); t.up(640, 400); time.sleep(0.5)
print(("PASS" if t.hud.bar_visible() else "FAIL") + "  a tap shows the info bar")
t.down(400, 730)
for x in range(400, 1001, 10):
    t.move(x, 730); time.sleep(0.005)
t.up(1000, 730); time.sleep(0.6)
v = pos(); print(("PASS" if abs(v - 480) < 8 else "FAIL") + f"  timeline drag lands where the finger is ({v:.0f}s vs 480s)")
t.down(100, 735); t.up(100, 735); time.sleep(0.6)
v = pos(); print(("PASS" if abs(v - 30) < 6 else "FAIL") + f"  timeline tap seeks ({v:.0f}s vs 30s)")
time.sleep(3.2)
print(("PASS" if not t.hud.bar_visible() else "FAIL") + "  info bar hides by itself")
p0 = pos()
t.down(640, 730); t.up(640, 730); time.sleep(0.5)
print(("PASS" if abs(pos() - p0) < 3 and t.hud.bar_visible() else "FAIL") + "  a tap with the bar hidden only shows it (no seek)")
v0 = pp.mpv_get("volume"); t.down(1000, 600)
for y in range(600, 299, -4):
    t.move(1000, y); time.sleep(0.008)
t.up(1000, 300); time.sleep(0.15); v1 = pp.mpv_get("volume"); time.sleep(1); v2 = pp.mpv_get("volume")
print(("PASS" if v1 and v1 > (v0 or 0) and v1 == v2 else "FAIL") + f"  volume swipe changes the player volume and settles at once ({v0}->{v1}->{v2})")
pp.mpv_cmd(["quit"]); m.wait(5)
