#!/usr/bin/env python3
"""Intro / recap / credits times for the movie player's Skip button (2026-10-08).

Plex only makes these markers with Plex Pass (this server has none), so they come from introdb.app, a free
community database looked up by IMDb id (+ season / episode). Plex markers are used first if a title has any.
Only the show/movie IMDb id and episode numbers are sent. Cached in ~/prism/plex/markers.json.
  segments(rating_key) -> [{"kind": "intro"|"recap"|"credits", "start": s, "end": s or None}]"""
import prism_config  # noqa: E402  (paths)
import json, os, time, urllib.parse, urllib.request

HOME = os.path.expanduser("~")
CACHE = f"{prism_config.PRISM}/plex/markers.json"
CFG = json.load(open(f"{prism_config.PRISM}/plex.json"))
TTL_HIT, TTL_MISS = 30 * 86400, 5 * 86400


def plex(path):
    sep = "&" if "?" in path else "?"
    r = urllib.request.urlopen(urllib.request.Request(f"{CFG['url']}{path}{sep}X-Plex-Token={CFG['token']}",
                                                      headers={"Accept": "application/json"}), timeout=15)
    return json.load(r)["MediaContainer"]


def imdb_of(meta):
    for g in meta.get("Guid", []):
        if g.get("id", "").startswith("imdb://"):
            return g["id"][7:]
    return None


def introdb(params):
    url = "https://api.introdb.app/segments?" + urllib.parse.urlencode(params)
    r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "SwitchHome/1.0"}), timeout=10)
    return json.load(r)


def segments(key):
    key = str(key)
    try:
        cache = json.load(open(CACHE))
    except (OSError, ValueError):
        cache = {}
    hit = cache.get(key)
    if hit and time.time() - hit["t"] < (TTL_HIT if hit["segs"] else TTL_MISS):
        return hit["segs"]
    segs = []
    try:
        m = plex(f"/library/metadata/{key}?includeMarkers=1&includeGuids=1")["Metadata"][0]
        for mk in m.get("Marker", []):               # Plex's own markers win when present
            kind = {"intro": "intro", "credits": "credits"}.get(mk.get("type"))
            if kind:
                segs.append({"kind": kind, "start": mk["startTimeOffset"] / 1000,
                             "end": mk["endTimeOffset"] / 1000 if mk.get("final") is not True else None})
        if not segs:
            if m.get("type") == "episode":
                show = plex(f"/library/metadata/{m['grandparentRatingKey']}?includeGuids=1")["Metadata"][0]
                imdb = imdb_of(show)
                q = {"imdb_id": imdb, "season": m.get("parentIndex"), "episode": m.get("index")} if imdb else None
            else:
                imdb = imdb_of(m)
                q = {"imdb_id": imdb} if imdb else None
            if q:
                d = introdb(q)
                for name, kind in (("recap", "recap"), ("intro", "intro"), ("outro", "credits")):
                    s = d.get(name)
                    if s and s.get("end_sec", 0) > s.get("start_sec", 0):
                        segs.append({"kind": kind, "start": float(s["start_sec"]), "end": float(s["end_sec"])})
    except Exception:
        if hit:
            return hit["segs"]
        return []
    cache[key] = {"t": time.time(), "segs": segs}
    tmp = CACHE + ".tmp"
    json.dump(cache, open(tmp, "w")); os.replace(tmp, CACHE)
    return segs


LABEL = {"intro": "Skip Intro", "recap": "Skip Recap", "credits": "Skip Credits"}


def active(segs, pos, duration):
    """The segment the player is in (with a little slack at its end), or None."""
    for s in segs:
        end = s["end"] if s["end"] else duration
        if end and s["start"] - 0.5 <= pos < end - 1.5:
            return s
    return None


if __name__ == "__main__":
    import sys
    print(segments(sys.argv[1]))
