#!/usr/bin/env python3
"""Party mode (2026-10-10): nothing automatic touches the Deck while friends play.

  party_mode.py on [until]   e.g. on 2026-10-11T10:00 (default: tomorrow 10:00)
  party_mode.py off
  party_mode.py status

On: pauses the jobs that drive or restart Prism (nightly scrape + self-test, Plex sync, For You fetch, game tester,
the 23:19 wake-from-sleep), and writes ~/prism/party_mode, which deck_screensaver (no dimming / screen off),
deck_autosleep (no sleep, no idle ES-DE restart) and esde_pending check. A persistent user timer turns it off again
at the end time, also across reboots. Save backups and the Switch save sync keep running (light, and they protect saves).
"""
import prism_config  # noqa: E402  (paths)
import datetime, os, subprocess, sys

FLAG = f"{prism_config.PRISM}/party_mode"
UNIT_DIR = os.path.expanduser("~/.config/systemd/user")
USER_TIMERS = ["night-scrape.timer", "plex-sync.timer", "foryou-fetch.timer", "game-tester.timer"]
SYSTEM_TIMERS = ["deck-nightly-wake.timer"]


def sh(*a, root=False):
    return subprocess.run((["sudo", "-n"] if root else []) + list(a), capture_output=True, text=True)


def on(until):
    paused = []          # disabled (not just stopped) so a reboot can't bring them back; re-enabled by off()
    for t in USER_TIMERS:
        if sh("systemctl", "--user", "is-enabled", t).stdout.strip() == "enabled":
            sh("systemctl", "--user", "disable", "--now", t); paused.append("user:" + t)
    for t in SYSTEM_TIMERS:
        if sh("systemctl", "is-enabled", t).stdout.strip() == "enabled":
            sh("systemctl", "disable", "--now", t, root=True); paused.append("system:" + t)
    old = open(FLAG).read().split()[1:] if os.path.exists(FLAG) else []
    open(FLAG, "w").write(" ".join([until.isoformat(timespec="minutes")] + sorted(set(old + paused))) + "\n")
    with open(f"{UNIT_DIR}/party-end.service", "w") as f:
        f.write("[Unit]\nDescription=Turn Prism party mode off\n\n[Service]\nType=oneshot\n"
                f"ExecStart=/usr/bin/python3 {os.path.abspath(__file__)} off\n")
    with open(f"{UNIT_DIR}/party-end.timer", "w") as f:
        f.write("[Unit]\nDescription=End Prism party mode\n\n[Timer]\n"
                f"OnCalendar={until.strftime('%Y-%m-%d %H:%M:00')}\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")
    sh("systemctl", "--user", "daemon-reload")
    sh("systemctl", "--user", "enable", "--now", "party-end.timer")
    print(f"party mode ON until {until:%a %H:%M}")


def off():
    paused = open(FLAG).read().split()[1:] if os.path.exists(FLAG) else []
    for e in paused:
        kind, t = e.split(":", 1)
        if kind == "user":
            sh("systemctl", "--user", "enable", "--now", t)
        else:
            sh("systemctl", "enable", "--now", t, root=True)
    try:
        os.remove(FLAG)
    except OSError:
        pass
    sh("systemctl", "--user", "disable", "--now", "party-end.timer")
    print("party mode OFF (timers back on)")


def status():
    if os.path.exists(FLAG):
        print("ON until", open(FLAG).read().split()[0], "| paused:", " ".join(open(FLAG).read().split()[1:]))
    else:
        print("OFF")
    for t in USER_TIMERS:
        print(" ", t, sh("systemctl", "--user", "is-active", t).stdout.strip())
    for t in SYSTEM_TIMERS:
        print(" ", t, sh("systemctl", "is-active", t).stdout.strip())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "on":
        until = (datetime.datetime.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else
                 datetime.datetime.combine(datetime.date.today() + datetime.timedelta(days=1), datetime.time(10)))
        on(until)
    elif cmd == "off":
        off()
    else:
        status()
