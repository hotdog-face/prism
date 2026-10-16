#!/usr/bin/env python3
"""Updates that undo themselves (2026-10-14).

An installed Prism lives in ~/.local/share/prism/versions/<version>/ with ~/prism a link to the current one; the
installer keeps the version before it. switchhome.py calls on_start() each time it starts:
  - every start is counted, and the count is cleared as soon as Prism (ES-DE) reaches its home screen
    (/tmp/prism_ready, written by ES-DE's startup hook) - that version is then remembered as the last good one;
  - two starts in a row that never got there (a broken update, or a SteamOS update Prism's new code doesn't work
    with) switch ~/prism back to the last good version, restart Prism's services, and leave a "Prism fixed
    something" note.
A Prism that wasn't installed by the installer (no ~/.config/prism/installed.json, or ~/prism isn't a link) is left
alone.
    prism_guard.py status
"""
import json, os, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_config  # noqa: E402  (paths)

HOME = prism_config.HOME
LINK = f"{HOME}/prism"
VERSIONS = f"{HOME}/.local/share/prism/versions"
STATE = f"{HOME}/.local/share/prism/guard.json"
READY = "/tmp/prism_ready"
READY_WITHIN = 300             # seconds after the supervisor starts
FAILS_TO_ROLL_BACK = 2


def log(msg):
    print("[guard]", msg, flush=True)


def _load():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def _save(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


def current():
    return os.path.basename(os.path.realpath(LINK)) if os.path.islink(LINK) else None


def managed():
    return os.path.islink(LINK) and os.path.isdir(VERSIONS) and os.path.exists(f"{HOME}/.config/prism/installed.json")


def roll_back(st, to):
    """Point ~/prism at version `to` and restart Prism's services from it."""
    tmp = LINK + ".guard"
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(f"{VERSIONS}/{to}", tmp)
    os.replace(tmp, LINK)                    # atomic switch
    bad = st.get("version")
    st.update(version=to, fails=0, rolled_back_from=bad, rolled_back_at=time.time())
    _save(st)
    log(f"rolled back from {bad} to {to}")
    try:
        import prism_notice
        prism_notice.remember_fix(f"Went back to version {to} after an update didn't start properly.")
        prism_notice.toast(f"guard-rollback-{int(time.time())}", "Prism fixed something",
                           "Prism didn't start properly after an update, so it went back to the version that worked.")
    except Exception:
        pass
    subprocess.Popen(["systemctl", "--user", "daemon-reload"])
    subprocess.Popen(["systemd-run", "--user", "--collect", "--quiet", "--on-active=2",
                      "systemctl", "--user", "restart", "switchhome.service"])


def on_start():
    if not managed():
        return
    st = _load()
    ver = current()
    if st.get("version") != ver:
        st = {"version": ver, "fails": 0, "last_good": st.get("last_good"), "history": st.get("history", [])}
    st["fails"] = st.get("fails", 0) + 1                     # cleared below once Prism reaches its home screen
    st["started_at"] = time.time()
    _save(st)
    good = st.get("last_good")
    if st["fails"] > FAILS_TO_ROLL_BACK and good and good != ver and os.path.isdir(f"{VERSIONS}/{good}"):
        roll_back(st, good)
        return
    threading.Thread(target=_watch, args=(st["started_at"],), daemon=True).start()


def _watch(started):
    end = started + READY_WITHIN
    while time.time() < end:
        try:
            fresh = os.path.getmtime(READY) >= started - 5
        except OSError:
            fresh = False
        try:
            import prism_state
            running = bool(prism_state.esde_pid()) and time.time() - started > 20   # restarted while ES-DE was up
        except Exception:
            running = False
        try:
            if fresh or running:
                st = _load()
                st.update(fails=0, last_good=st.get("version"), good_at=time.time())
                _save(st)
                log(f"{st.get('version')} reached the home screen: remembered as good")
                return
        except OSError:
            pass
        time.sleep(5)
    log("Prism didn't reach its home screen in time; counted as a failed start")


if __name__ == "__main__":
    if sys.argv[1:] == ["status"]:
        print(json.dumps({"managed": managed(), "current": current(), **_load()}, indent=1))
    else:
        sys.exit(__doc__)
