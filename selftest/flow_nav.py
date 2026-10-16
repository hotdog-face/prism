"""Walking the Movies / TV cover flow for the self-test (2026-10-09). The lists are a horizontal carousel (theme
switch/screens/_coverflow.xml): Left/Right move, Down does nothing. Folders come first (Continue Watching, Discover,
Watch Later) and ES-DE remembers the cursor per list, so walk until the wanted kind of entry instead of counting.
Every helper checks what is selected; callers press Return only on an entry a check accepted."""
import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Prism root
import prism_config  # noqa: E402  (paths)
import json, os, time
import deck_curate as d

ROMS = prism_config.ROMS
SPECIAL = ("Continue Watching", "Discover", "Watch Later")


def walk(ok, key="Right", steps=130, gap=0.22):
    """Step through the open list until ok(selected) is true. None if not in a list, or after a full lap."""
    first, seen = None, set()
    for _ in range(steps):
        s = d.selected()
        if s is None:
            return None
        if ok(s):
            return s
        if first is None:
            first = s[0]
        elif s[0] == first and len(seen) >= 3:
            return None                              # wrapped round: nothing like that in this list
        seen.add(s[0])
        d.esde_keys([key]); time.sleep(gap)
    return None


def plex_type(path):
    try:
        return json.load(open(path)).get("type")
    except (OSError, ValueError):
        return None


def is_movie(s):
    """A real library movie (top level of Movies), not a Discover / request entry or a folder."""
    return (s[2] == "movies" and s[0].endswith(".plex") and os.path.dirname(s[0]) == f"{ROMS}/movies"
            and plex_type(s[0]) == "movie")


def is_show(s):
    return (s[2] == "tv" and os.path.isdir(s[0]) and os.path.dirname(s[0]) == f"{ROMS}/tv"
            and os.path.basename(s[0]) not in SPECIAL)


def is_season(s):
    return s[2] == "tv" and os.path.isdir(s[0]) and os.path.basename(s[0]).startswith("Season")


def is_episode(s):
    return (s[2] == "tv" and s[0].endswith(".plex") and not any(f"/{x}/" in s[0] for x in SPECIAL)
            and plex_type(s[0]) == "episode")
