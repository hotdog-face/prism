#!/usr/bin/env python3
"""Prism supervisor (2026-10-08): ONE user service instead of four.

  main thread   deck_curate     buttons + touch (one hidraw reader), Steam-button card, rescue chord (L5+R5)
  thread        screensaver     screen off when idle - input comes from deck_curate, no second controller reader
  thread        home            boot into Prism (after Decky), bring it back, sleep during idle games
  thread        kbd-proxy       Steam keyboard for text boxes in Request Movies (127.0.0.1:5056)
  thread        media-router    Music tile opens the Music app directly; leaving it returns to the console row

Each thread is supervised: if one crashes it is logged and restarted after a short back-off without touching the
others. If the main thread (buttons) dies the process exits and systemd restarts everything (Restart=always).
Still separate on purpose: deck_cc.py (the overlay window, its own GTK process), deck_autosleep.py (started by
ES-DE only while its dim screensaver is up), plex_play / deck_music players, timers (nightly jobs).
Rollback to the four old services: ~/prism/switchhome_rollback.sh
"""
import os, sys, threading, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOG = "/tmp/switchhome.log"


def log(msg):
    line = time.strftime("%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def supervised(name, target):
    def run():
        backoff = 5
        while True:
            t0 = time.time()
            try:
                log(f"{name}: starting")
                target()
                log(f"{name}: returned, restarting")
            except BaseException:
                log(f"{name}: crashed\n" + traceback.format_exc(limit=8))
            if time.time() - t0 > 300:
                backoff = 5                       # it ran a good while: a fresh failure, not a crash loop
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
    th = threading.Thread(target=run, name=name, daemon=True)
    th.start()
    return th


def main():
    import deck_curate
    import deck_screensaver
    import deck_home
    import deck_kbd_proxy
    import http.server

    def kbd():
        http.server.ThreadingHTTPServer.daemon_threads = True
        http.server.ThreadingHTTPServer.allow_reuse_address = True
        http.server.ThreadingHTTPServer(deck_kbd_proxy.LISTEN, deck_kbd_proxy.Proxy).serve_forever()

    supervised("screensaver", lambda: deck_screensaver.main(shared_input=lambda: deck_curate._input_at[0]))
    supervised("home", deck_home.main)
    supervised("kbd-proxy", kbd)
    import media_router
    supervised("media-router", media_router.run)
    import game_hub
    supervised("game-hub", game_hub.run)
    import controller_bar
    supervised("controller-bar", controller_bar.run)
    import prism_guard
    prism_guard.on_start()                            # an installed Prism goes back to the last good version if needed
    import prism_save
    supervised("save-guard", prism_save.run)          # save the game before sleep and at 5 % battery
    import prism_notice
    supervised("notices", prism_notice.run)           # cards and notes from background jobs, when someone is here
    import prism_haptics
    supervised("haptics", prism_haptics.run)          # trackpad ticks with the menu sounds (handheld)
    log("buttons: starting (main thread)")
    deck_curate.main()


if __name__ == "__main__":
    main()
