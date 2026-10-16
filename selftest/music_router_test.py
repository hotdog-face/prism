import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Prism root
import prism_config  # noqa: E402  (paths)
import os, signal, subprocess, sys, time
import deck_curate as d
def app_pids():
    out = []
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                c = open(f"/proc/{p}/cmdline", "rb").read()
            except OSError:
                continue
            if f"{prism_config.PRISM}/deck_music.py".encode() in c and b"music_router_test" not in c:
                out.append(int(p))
    return out
for p in app_pids():
    os.kill(p, signal.SIGTERM)
time.sleep(1.5)
for _ in range(3):
    if d.selected() is None: break
    d.esde_keys(["BackSpace"]); time.sleep(0.8)
subprocess.run(["python3", f"{prism_config.PRISM}/esde_nav.py", "music"], capture_output=True)

t0 = time.time(); d.esde_keys(["Return"])
for _ in range(80):
    time.sleep(0.1)
    if app_pids(): break
ok = bool(app_pids()); print(("PASS" if ok else "FAIL") + f"  Music tile opens the Music app directly ({time.time() - t0:.1f}s)")
time.sleep(3)
for p in app_pids():
    os.kill(p, signal.SIGTERM)               # as if B was pressed in the app
t1 = time.time()
for _ in range(60):
    time.sleep(0.1)
    if d.selected() is None: break
print(("PASS" if d.selected() is None else "FAIL") + f"  leaving the Music app returns to the console row ({time.time() - t1:.1f}s)")
