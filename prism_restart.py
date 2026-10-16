#!/usr/bin/env python3
"""Restart Prism (ES-DE) behind its startup cover once nothing is being played (2026-10-09).

ES-DE picks its resolution and theme layout (16:10 on the Deck, 16:9 on the TV) when it starts, so after docking or
undocking prism_profile.apply() calls request(); a worker waits until Prism is idle (prism_state.not_idle(): in front,
nothing played - no game, Steam app or shortcut, movie, music or self-test -, Home card down, no busy flag), then
restarts it with prism_esde.esde_closed() (2026-10-13: was its own copy of that sequence plus a substring `ps` check)."""
import os, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_state as ps            # noqa: E402
import prism_esde                   # noqa: E402

BUSY = ps.BUSY
_pending = {"why": None, "since": 0.0}
_lock = threading.Lock()
_worker = [None]


def log(*a):
    print("[prism-restart]", *a, flush=True)


# kept for older callers
cc_state = ps.cc_state
esde_pid = ps.esde_pid


def playing():
    return bool(ps.playing())


def restart(why):
    """Restart ES-DE now (raises prism_esde.NotIdle when something is being played or the busy flag is held)."""
    with prism_esde.esde_closed(f"screen changed ({why})"):
        pass
    log("restarted for", why)


def _run():
    while True:
        time.sleep(3)
        with _lock:
            why = _pending["why"]
        if why is None:
            return
        if ps.not_idle():
            continue                                   # try again shortly (a game ends, a session releases the flag)
        with _lock:
            _pending["why"] = None
        import prism_profile
        if prism_profile.esde_matches() is True:      # e.g. ES-DE was started after the dock change
            log(f"not needed ({why}): ES-DE already runs at the {prism_profile.mode()} size")
            return
        try:
            restart(why)
        except prism_esde.NotIdle as e:                # something started in between: wait again
            log("not now:", e)
            with _lock:
                if _pending["why"] is None:
                    _pending["why"] = why
            continue
        except Exception as e:
            log("restart failed:", e)
        return


def request(why):
    with _lock:
        _pending["why"], _pending["since"] = why, time.time()
        if _worker[0] is None or not _worker[0].is_alive():
            _worker[0] = threading.Thread(target=_run, daemon=True); _worker[0].start()
    log("requested:", why)
