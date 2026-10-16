#!/usr/bin/env python3
"""console-style auto-sleep (2026-10-06): no gameplay-video screensaver; ES-DE dims the screen after
4 min without input (ScreensaverType=dim, ScreensaverTimer=240000) and this puts the Deck to sleep 1 min later.

Started by ~/ES-DE/scripts/screensaver-start/curate.sh. Any input ends ES-DE's screensaver (the flag file goes away)
and cancels the sleep. Waits instead of sleeping while background work runs (nightly scrape, For You refresh,
save sync, a boot test in progress). After waking, if nobody touches the Deck, it sleeps again a minute later.
"""
import prism_config  # noqa: E402  (paths)
import fcntl, os, subprocess, sys, time

FLAG = "/tmp/esde_screensaver"
LAST_INPUT = "/tmp/deck_last_input"
DELAY = 60            # seconds of dimmed screen before sleeping
MIN_IDLE = 4          # minutes without any input (guards against a stale flag)
BUSY_UNITS = ["night-scrape.service", "foryou-fetch.service", "switch-save-sync.service",
              "game-tester-now.service", "game-tester-now2.service", "game-tester-now3.service",
              "game-tester-now4.service", "game-tester-night.service"]
LOG = "/tmp/deck_autosleep.log"


def log(msg):
    with open(LOG, "a") as f:
        f.write(time.strftime("%H:%M:%S ") + msg + "\n")


def idle_minutes():
    try:
        last = os.path.getmtime(LAST_INPUT)
    except OSError:
        last = time.time() - float(open("/proc/uptime").read().split()[0])
    return (time.time() - last) / 60


def agent_working():
    """An agent (Claude over SSH / the steamdeck MCP server) used the Deck in the last 10 minutes, or an SSH command
    other than the idle MCP server is running right now."""
    # an already-running MCP server (older code) doesn't touch the file: count its CPU time going up as activity
    import json, glob
    try:
        seen = json.load(open("/tmp/deck_mcp_cpu.json"))
    except (OSError, ValueError):
        seen = {}
    now_cpu = {}
    for p in subprocess.run(["pgrep", "-f", "deck_mcp.py"], capture_output=True, text=True).stdout.split():
        try:
            f = open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()
            now_cpu[p] = int(f[11]) + int(f[12])
        except (OSError, IndexError, ValueError):
            pass
    kids = [k for p in now_cpu for k in subprocess.run(["pgrep", "-P", p], capture_output=True, text=True).stdout.split()]
    if kids or any(seen.get(p) is not None and seen[p] != c for p, c in now_cpu.items()):
        open("/tmp/deck_agent_active", "w").write("mcp cpu")
    json.dump(now_cpu, open("/tmp/deck_mcp_cpu.json", "w"))
    try:
        if time.time() - os.path.getmtime("/tmp/deck_agent_active") < 600:
            return True
    except OSError:
        pass
    # background jobs an agent started with systemd-run (transient user units), e.g. a batch test
    out = subprocess.run(["systemctl", "--user", "list-units", "--type=service", "--state=running", "--no-legend",
                          "--plain"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        unit = line.split()[0] if line.split() else ""
        if unit.startswith("deck-autosleep"):
            continue   # this watcher itself
        if unit.startswith("run-") or unit in ("hidden-check.service",):
            return True
        frag = subprocess.run(["systemctl", "--user", "show", "-p", "FragmentPath", "--value", unit],
                              capture_output=True, text=True).stdout.strip()
        if "/transient/" in frag:
            return True
    sshd = subprocess.run(["pgrep", "-f", "sshd-session: deck"], capture_output=True, text=True).stdout.split()
    for p in sshd:
        for k in subprocess.run(["pgrep", "-P", p], capture_output=True, text=True).stdout.split():
            try:
                cmd = open(f"/proc/{k}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
            except OSError:
                continue
            if "deck_mcp.py" not in cmd and "sshd" not in cmd:
                return True
    return False


def busy():
    if os.path.exists(f"{prism_config.PRISM}/party_mode"):
        return "party mode"
    if agent_working():
        return "an agent is working on the Deck"
    for u in BUSY_UNITS:
        if subprocess.run(["systemctl", "--user", "is-active", "-q", u]).returncode == 0:
            return u
    out = subprocess.run(["pgrep", "-af", f"SteamLaunch AppId={prism_config.TESTER_APPID}"], capture_output=True, text=True).stdout
    if any("pgrep" not in l for l in out.splitlines()):
        return "boot test"
    return None


def main():
    lock = open("/tmp/deck_autosleep.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return   # already watching
    t0 = time.time(); said = None
    while os.path.exists(FLAG):
        if time.time() - t0 >= DELAY and idle_minutes() >= MIN_IDLE:
            b = busy()
            if b:
                if b != said:
                    log(f"waiting: {b}"); said = b
            else:
                pend = f"{prism_config.PRISM}/esde_restart_pending"
                if os.path.exists(pend):   # ES-DE changes that need a restart: do it now, nobody is looking
                    log("applying pending ES-DE changes")
                    subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/esde_pending.py"])
                    time.sleep(25)
                    open(FLAG, "a").close()   # the restarted ES-DE is not in screensaver; keep this loop going
                log("sleeping")
                subprocess.run(["systemctl", "suspend"])
                time.sleep(20)          # resume takes a moment
                log("woke up")
                # woken but not touched: give whoever pressed power 5 minutes before sleeping again (the 01:29 wake
                # for the nightly run keeps it awake through its own busy check)
                t0 = time.time() + 240; said = None
        time.sleep(5)


if __name__ == "__main__":
    main()
