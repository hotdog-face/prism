#!/usr/bin/env python3
"""Never lose progress (2026-10-14): save a state for the emulator game that's running, whenever the Deck is about
to sleep (any way: power button, Steam's menu, Prism's Power menu, auto-sleep) and when the battery reaches 5 %.
Supervised thread in switchhome.py ("save-guard"); `prism_save.py now` saves by hand.

Sleep: logind gives a "delay" inhibitor up to 5 s (InhibitDelayMaxUSec) between announcing PrepareForSleep and
suspending; the save runs inside that window. RetroArch saves into its auto slot (-1), the one it loads when the game
starts (savestate_auto_load), so a Deck that runs flat while asleep still resumes at the moment it went to sleep.
PCSX2, Dolphin and PPSSPP use the same save paths as quitting from Prism (deck_curate.pre_quit_save).
Steam/PC games are left alone: they have their own saves.
"""
import glob, os, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_config  # noqa: E402  (paths)

LOG = "/tmp/prism_save.log"
BAT = "/sys/class/power_supply/BAT1"
LOW = 5                       # % battery
RA_STATES = f"{prism_config.SAVES}/retroarch/states"
_lock = threading.Lock()


def log(msg):
    line = time.strftime("%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _cmdline(p):
    try:
        return open(f"/proc/{p}/cmdline", "rb").read()
    except OSError:
        return b""


def _newest(pattern, since):
    for f in glob.glob(pattern):
        try:
            if os.path.getmtime(f) >= since:
                return f
        except OSError:
            pass
    return None


def save_running(reason, budget=4.0):
    """Save the running emulator game. Returns a short description, or None when there was nothing to save."""
    import deck_curate as d
    with _lock:
        pid = d.esde_pid()
        if not pid or not d.game_running(pid):
            return None
        procs = [k for k in d.esde_game_procs(pid) if k != pid and not d.protected(k)]
        cmds = {k: _cmdline(k) for k in procs}
        frozen = any(_state(k) == "T" for k in procs)
        if frozen and os.path.exists("/tmp/game_hub_ready"):
            log(f"{reason}: game still on its hub page (not started), nothing new to save")
            return None
        if frozen:                       # paused by the Steam-button card: let it run just long enough to save
            d.signal_game(pid, __import__("signal").SIGCONT); time.sleep(0.3)
        try:
            return _save(d, cmds, reason, budget)
        finally:
            if frozen:
                d.signal_game(pid, __import__("signal").SIGSTOP)


def _state(p):
    try:
        return open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return ""


def _save(d, cmds, reason, budget):
    t0 = time.time()
    for k, c in cmds.items():
        low = c.lower()
        if b"retroarch" in low:
            d.ra_goto_slot(k, -1)                       # the auto slot: loaded when the game starts
            d.retroarch_cmd("SAVE_STATE")
            while time.time() - t0 < budget:
                if _newest(f"{RA_STATES}/*.state.auto", t0 - 0.5):
                    time.sleep(0.3)
                    log(f"{reason}: RetroArch auto state saved ({time.time() - t0:.1f}s)")
                    return "RetroArch"
                time.sleep(0.1)
            log(f"{reason}: RetroArch did not write a state in {budget:.0f}s")
            return None
        if b"pcsx2" in low:
            ok = d.pre_quit_save(k, "pine:1", f"{prism_config.SAVES}/pcsx2/states", ".p2s", wait=budget)
            log(f"{reason}: PCSX2 state {'saved' if ok else 'not saved'}")
            return "PCSX2" if ok else None
        if b"dolphin-emu" in low:
            ok = d.pre_quit_save(k, "uinput:f6", f"{prism_config.HOME}/.var/app/org.DolphinEmu.dolphin-emu/data/"
                                 "dolphin-emu/StateSaves", ".s01", wait=budget)
            log(f"{reason}: Dolphin state {'saved' if ok else 'not saved'}")
            return "Dolphin" if ok else None
        if b"ppsspp" in low:
            ok = d.pre_quit_save(k, "uinput:f2", f"{prism_config.HOME}/.var/app/org.ppsspp.PPSSPP/config/ppsspp/"
                                 "PSP/PPSSPP_STATE", ".ppst", wait=budget)
            log(f"{reason}: PPSSPP state {'saved' if ok else 'not saved'}")
            return "PPSSPP" if ok else None
    log(f"{reason}: no save-state support for this emulator")
    return None


# ---- before sleep -----------------------------------------------------------------------------------------------
def sleep_guard():
    from gi.repository import Gio, GLib
    ctx = GLib.MainContext.new()
    ctx.push_thread_default()
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    held = {"fd": None}

    def take():
        if held["fd"] is not None:
            return
        try:
            r, fds = bus.call_with_unix_fd_list_sync(
                "org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager", "Inhibit",
                GLib.Variant("(ssss)", ("sleep", "Prism", "Saving your game before sleep", "delay")),
                GLib.VariantType.new("(h)"), Gio.DBusCallFlags.NONE, -1, None, None)
            held["fd"] = fds.get(r.unpack()[0])
        except Exception as e:
            log(f"sleep guard: no delay lock ({e})")

    def release():
        if held["fd"] is not None:
            os.close(held["fd"]); held["fd"] = None

    def on_signal(_c, _s, _p, _i, _n, params):
        going = params.unpack()[0]
        if going:
            try:
                save_running("before sleep", budget=3.5)
            except Exception as e:
                log(f"before sleep: save failed ({e})")
            release()                      # let the Deck sleep
        else:
            take()                         # awake again: hold the next one
            subprocess.Popen(["systemd-run", "--user", "--collect", "--quiet", "/usr/bin/python3",
                              f"{prism_config.PRISM}/prism_screens.py", "after-wake"])   # Continue screen

    bus.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
                         "/org/freedesktop/login1", None, Gio.DBusSignalFlags.NONE, on_signal)
    take()
    log("sleep guard: ready" if held["fd"] is not None else "sleep guard: running without a delay lock")
    GLib.MainLoop.new(ctx, False).run()


# ---- low battery --------------------------------------------------------------------------------------------------
def battery_guard():
    saved_at = None
    while True:
        try:
            cap = int(open(f"{BAT}/capacity").read())
            status = open(f"{BAT}/status").read().strip()
        except (OSError, ValueError):
            return                         # no battery to watch
        if status == "Discharging" and cap <= LOW:
            if saved_at is None:
                what = save_running(f"battery {cap}%")
                saved_at = cap
                if what:
                    import deck_curate as d
                    d.toast("Battery low", "Your game is saved")
        elif status != "Discharging" or cap > LOW + 2:
            saved_at = None
        time.sleep(30)


def run():
    """switchhome thread: both guards (the sleep one needs its own GLib loop)."""
    t = threading.Thread(target=battery_guard, name="battery-guard", daemon=True)
    t.start()
    sleep_guard()


if __name__ == "__main__":
    if sys.argv[1:] == ["now"]:
        print(save_running("by hand") or "nothing to save")
    else:
        sys.exit(__doc__)
