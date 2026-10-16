#!/usr/bin/env python3
"""Sleep timers for movies and music (2026-10-08). The Steam-button card's "Sleep timer" tile cycles
30 -> 60 -> 90 min -> end of this episode / film (movies) or this song (music) -> off. plex_play.py and
deck_music_player.py read the state: movies stop (Plex keeps your place), music fades out and pauses.
State: /tmp/sleep_<kind>.json  {"until": epoch seconds} or {"end": true}"""
import json, os, time

STEPS = [30, 60, 90, "end", None]


def path(kind):
    return f"/tmp/sleep_{kind}.json"


def get(kind):
    try:
        d = json.load(open(path(kind)))
    except (OSError, ValueError):
        return None
    if d.get("until") and time.time() > d["until"] + 3600:
        return None                               # stale (left from another session)
    return d


def label(kind):
    d = get(kind)
    if not d:
        return "Off"
    if d.get("end"):
        return "End of this " + ("song" if kind == "music" else "one")
    left = max(0, d["until"] - time.time())
    return f"{int(left // 60) + 1} min left"


def cycle(kind):
    """Next setting; returns the toast text."""
    d = get(kind)
    cur = None if not d else ("end" if d.get("end") else d.get("step"))
    nxt = STEPS[(STEPS.index(cur) + 1) % len(STEPS)] if cur in STEPS else STEPS[0]
    if nxt is None:
        try:
            os.remove(path(kind))
        except OSError:
            pass
        return "Sleep timer off"
    if nxt == "end":
        json.dump({"end": True, "step": "end"}, open(path(kind), "w"))
        return "Stops at the end of this " + ("song" if kind == "music" else "episode / film")
    json.dump({"until": time.time() + nxt * 60, "step": nxt}, open(path(kind), "w"))
    return f"Sleep in {nxt} minutes"


def due(kind, at_end=False):
    """True when playback should stop now (at_end: the current episode / song just finished)."""
    d = get(kind)
    if not d:
        return False
    if d.get("end"):
        return at_end
    return time.time() >= d["until"]


def clear(kind):
    try:
        os.remove(path(kind))
    except OSError:
        pass
