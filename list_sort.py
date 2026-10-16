#!/usr/bin/env python3
"""View (⧉) in a game list cycles how it is sorted (2026-10-08: alphabetical isn't best for discovery).
ES-DE has the sorts but its Gamelist Options menu is otherwise unreachable on the Deck, so this drives that menu
(F1 -> "Sort games by" -> Right x n -> Back applies) and shows a small toast with the new order.
ES-DE's sort list (20 entries, wraps): 0 name asc, 1 name desc, 2/3 rating asc/desc, 4/5 release date asc/desc,
6-13 developer/publisher/genre/players, 14/15 last played asc/desc, 16/17 times played, 18/19 play time asc/desc.
Sorts last until ES-DE restarts (it resets them), so the state is kept per ES-DE process."""
import json, os, time

N = 20
GAMES = [(0, "A–Z"), (3, "Top rated"), (5, "Newest releases"), (15, "Recently played"), (19, "Most played")]
MEDIA = [(0, "Recently added"), (3, "Top rated"), (5, "Newest releases")]   # movies/tv: name sort = plex_sync order
STATE = "/tmp/switchhome_sort.json"
SKIP = {"music", "media"}


def cycle():
    import deck_curate as d
    pid = d.esde_pid()
    sel = d.selected()
    if not pid or not d.esde_idle(pid) or not sel or sel[2] in SKIP:
        return False
    system = sel[2]
    try:
        st = json.load(open(STATE))
    except (OSError, ValueError):
        st = {}
    if st.get("pid") != pid:
        st = {"pid": pid}                         # ES-DE restarted: every list is back to its default (name)
    cur = st.get(system, 0)
    steps = MEDIA if system in ("movies", "tv") else GAMES
    idx = [i for i, _ in steps]
    nxt = steps[(idx.index(cur) + 1) % len(steps)] if cur in idx else steps[0]
    d.esde_keys(["F1"])                           # Gamelist Options (waits for the menu)
    if cur in (0, 1):
        d.esde_keys(["Down"])                     # "Jump to..." is only there while sorted by name
    d.esde_keys(["Right"] * ((nxt[0] - cur) % N), gap=0.06)
    d.esde_keys(["BackSpace"])                    # Close (Apply)
    st[system] = nxt[0]
    json.dump(st, open(STATE, "w"))
    try:
        d.cc_request({"cmd": "toast", "title": "Sorted by", "sub": nxt[1], "cover": ""}, timeout=0.5)
    except OSError:
        pass
    d.log(f"sort {system}: {nxt[1]}")
    return True
