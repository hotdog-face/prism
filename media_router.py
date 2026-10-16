#!/usr/bin/env python3
"""Music and Request Movies have no list (2026-10-08): picking the Music tile opens the YouTube-Music-style app at once, and
leaving the app goes straight back to the console row. Runs as a supervised thread in switchhome.py.

ES-DE has no "open this system as an app" option, so: when ES-DE enters the Music list (its game-select script writes
/tmp/esde_selected with system "music") the watcher presses A on the Music entry; when deck_music.py closes and ES-DE
is back on that list, it presses Back. The list itself is themed as an "Opening Music..." splash."""
import os, subprocess, time
import prism_config  # noqa: E402  (paths)

STATE = "/tmp/esde_selected"


def music_app_running():
    return subprocess.run(["pgrep", "-f", f"{prism_config.PRISM}/deck_music.py"], capture_output=True).returncode == 0


def run():
    import deck_curate as d
    phase, since, last_sel = "idle", 0.0, None
    try:                                         # whatever is on screen when this starts doesn't count as entering
        parts = open(STATE, "rb").read().decode(errors="replace").split("\0")
        last_sel = (parts[0], parts[2]) if len(parts) >= 3 else None
    except OSError:
        pass
    while True:
        time.sleep(0.1)
        sel = None
        try:
            parts = open(STATE, "rb").read().decode(errors="replace").split("\0")
            sel = (parts[0], parts[2]) if len(parts) >= 3 else None
        except OSError:
            pass
        in_music_list = bool(sel and sel[1] == "music")
        now = time.time()
        # Request Movies (system "media", one entry): open it and step ES-DE back to the console row behind it
        # Only straight from the home row (no game selected before = system view): reached by L/R tabbing from another
        # list, Music / Request Movies stay on their landing page so tabbing keeps flowing (2026-10-09)
        from_home = last_sel is None
        if phase == "idle" and sel and sel[1] == "media" and last_sel != sel and from_home:
            pid = d.esde_pid()
            if pid and d.esde_idle(pid) and str(d.focus_now()) == prism_config.PRISM_APPID:
                time.sleep(0.15)
                d.esde_keys(["Return"]); d.log("media: straight into Request Movies")
                time.sleep(1.2)
                d.esde_keys(["BackSpace"])
        if phase == "idle":
            if in_music_list and last_sel != sel and from_home and not music_app_running():
                pid = d.esde_pid()
                if pid and d.esde_idle(pid) and str(d.focus_now()) == prism_config.PRISM_APPID:
                    time.sleep(0.15)                 # let ES-DE finish the list transition
                    d.esde_keys(["Return"]); d.log("music: straight into the Music app")
                    phase, since = "launching", now
        elif phase == "launching":
            if music_app_running():
                phase = "open"
            elif now - since > 8:
                phase = "idle"                       # it didn't start: leave the list as it is
        elif phase == "open":
            if not music_app_running():
                phase, since = "closing", now
        elif phase == "closing":
            # ES-DE re-announces the selected entry once it is back on the list: wait for that, then press Back
            # (a key sent while it is still returning from the app is ignored); check it worked, retry twice
            pid = d.esde_pid()
            try:
                back_on_list = os.path.getmtime(STATE) > since
            except OSError:
                back_on_list = False
            if not in_music_list and now - since > 0.5:
                phase, run.tries = "idle", 0          # already off the list (another key, a system switch)
            elif back_on_list and pid and d.esde_idle(pid) and now - getattr(run, "last_back", 0) > 1.0:
                time.sleep(0.35)
                d.esde_keys(["BackSpace"]); run.last_back = time.time()
                run.tries = getattr(run, "tries", 0) + 1
                d.log(f"music: app closed, back to the console row (try {run.tries})")
                if run.tries >= 3:
                    phase, run.tries = "idle", 0
            elif now - since > 10:
                phase, run.tries = "idle", 0
            if phase == "closing" and not in_music_list:
                phase, run.tries = "idle", 0
        last_sel = sel
