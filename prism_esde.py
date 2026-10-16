#!/usr/bin/env python3
"""Close ES-DE, change its files, open it again: the one implementation (refactor, 2026-10-13).

ES-DE rewrites its gamelists and es_settings.xml when it exits, so every edit to them needs ES-DE closed. Before this,
prism_restart, the Card Test collection toggle, the gamelist repair, oneoff/pr_add_prism.py, esde_pending and the
nightly maintenance each did their own "busy flag -> boot cover -> SIGTERM ES-DE -> wait for Prism's Steam session to
end -> edit -> relaunch -> drop the flag". Now:

    from prism_esde import esde_closed, NotIdle
    with esde_closed("why"):           # raises NotIdle (nothing changed) if the flag is held or something is played
        ...edit files...               # ES-DE is closed here (gamelist.Gamelist(...).save() refuses otherwise)
    # ES-DE is back (relaunched only if it was running), /tmp/prism_busy released

Also: set_custom_collection(name, on) for es_settings.xml's CollectionSystemsCustom + front_collections.txt."""
import prism_config  # noqa: E402  (paths)
import contextlib, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prism_state as ps      # noqa: E402

BUSY = ps.BUSY
COVER = os.path.join(HERE, "prism_boot_cover.py")
SETTINGS = f"{prism_config.ESDE}/settings/es_settings.xml"
FRONT = f"{prism_config.ESDE}/settings/front_collections.txt"


class NotIdle(RuntimeError):
    """ES-DE was not touched: the busy flag is held by someone else, or something is being played."""


def log(*a):
    print("[esde-closed]", *a, flush=True)


def _take_flag(reason):
    try:
        fd = os.open(BUSY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)      # atomic: two takers can't both win
    except FileExistsError:
        raise NotIdle(f"busy flag held ({ps.busy_flag() or '?'})")
    with os.fdopen(fd, "w") as f:
        f.write(f"{reason} (pid {os.getpid()})\n")


def _drop_flag():
    try:
        if f"(pid {os.getpid()})" in open(BUSY).read():
            os.remove(BUSY)
    except OSError:
        pass


def _wait(cond, secs, step=0.5):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


def _end_leftovers():
    """ES-DE is gone but Prism's Steam session isn't (EmuDeck's es-de.sh or a helper it started still runs inside it):
    Steam would answer a launch with "Game already running". End what is left under it (deck_home does the same after
    a Steam restart)."""
    import deck_home
    log("Prism's Steam session outlived ES-DE: ending what is left in it")
    deck_home.end_leftover_session(ps.prism_reapers())


def close_esde():
    """SIGTERM every ES-DE and wait until it and Prism's Steam session are gone. True when closed."""
    for p in ps.esde_pids():
        try:
            os.kill(p, 15)
        except OSError:
            pass
    if not _wait(lambda: not ps.esde_pids(), 30):
        return False
    if not _wait(lambda: not ps.prism_reapers(), 20):
        _end_leftovers()
        _wait(lambda: not ps.prism_reapers(), 10)
    return not ps.esde_pids()


def open_esde(reason, tries=3):
    """Launch Prism through Steam and wait for ES-DE; retried while Steam is still closing the old session."""
    import deck_home
    for attempt in range(tries):
        _wait(lambda: not ps.prism_reapers(), 30, 1)     # Steam ignores a launch while the old session closes
        if ps.esde_pids():
            return True
        if ps.prism_reapers():
            log("an old Prism session is still open; not launching on top of it")
            return False
        deck_home.start_esde(reason)
        if _wait(lambda: ps.esde_pids() or ps.prism_reapers(), 30, 1):
            return True
        log(f"ES-DE did not start (attempt {attempt + 1})")
    return False


@contextlib.contextmanager
def esde_closed(reason, require_idle=True, relaunch=True, cover=True):
    """Hold /tmp/prism_busy, close ES-DE behind Prism's startup cover, run the block, start ES-DE again.

    require_idle: refuse (NotIdle) while a game / app / player / self-test runs or the Home card is up.
    relaunch: start ES-DE afterwards if it was running before; "always": also when it wasn't.
    Raises NotIdle before anything is touched, RuntimeError if ES-DE won't close (the block then doesn't run)."""
    _take_flag(reason)
    was_running = False
    try:
        if require_idle:
            why = ps.playing() or ("Home card up" if ps.card_up() else "")
            if why:
                raise NotIdle(why)
        was_running = bool(ps.esde_pids() or ps.prism_reapers())
        if was_running:
            if cover:
                try:
                    subprocess.run(["/usr/bin/python3", COVER], timeout=5, capture_output=True)
                    time.sleep(0.6)                  # cover fully up before ES-DE goes
                except Exception as e:
                    log("cover:", e)
            log(f"closing ES-DE ({reason})")
            if not close_esde():
                raise RuntimeError("ES-DE did not close")
            time.sleep(1.5)
        yield
    finally:
        try:
            if (relaunch == "always" or (relaunch and was_running)) and not ps.esde_pids():
                if not open_esde(reason):
                    log("ES-DE did not come back; deck_home will start it once the busy flag is gone")
                time.sleep(8)                        # flag held while ES-DE starts (deck_home must not launch a 2nd)
        finally:
            _drop_flag()


# ---- es_settings.xml
def setting(name):
    m = re.search(r'<\w+ name="%s" value="([^"]*)" />' % re.escape(name), open(SETTINGS).read())
    return m.group(1) if m else None


def _require_closed():
    if ps.esde_pids():
        raise RuntimeError("ES-DE is running (it rewrites its settings when it exits): use esde_closed()")


def set_setting(name, value):
    _require_closed()
    s = open(SETTINGS).read()
    m = re.search(r'(<\w+ name="%s" value=")([^"]*)(" />)' % re.escape(name), s)
    if not m:
        raise KeyError(name)
    if m.group(2) != value:
        with open(SETTINGS + ".tmp", "w") as f:
            f.write(s[:m.start(2)] + value + s[m.end(2):])
        os.replace(SETTINGS + ".tmp", SETTINGS)


def set_custom_collection(name, on):
    """Show / hide a custom collection on the home row: es_settings CollectionSystemsCustom + front_collections.txt
    (Prism's order of collections in front). Put first when switched on. ES-DE must be closed."""
    _require_closed()
    names = [n for n in (setting("CollectionSystemsCustom") or "").split(",") if n and n != name]
    set_setting("CollectionSystemsCustom", ",".join(([name] if on else []) + names))
    fr = [l.strip() for l in open(FRONT).read().splitlines() if l.strip() and l.strip() != name]
    with open(FRONT + ".tmp", "w") as f:
        f.write("\n".join(([name] if on else []) + fr) + "\n")
    os.replace(FRONT + ".tmp", FRONT)
