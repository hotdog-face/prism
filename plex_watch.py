#!/usr/bin/env python3
"""Watched / unwatched and Watch Later (Plex watchlist) for Prism (2026-10-08).
Watched state lives on the Plex server; Watch Later is the account's Plex watchlist (discover.provider.plex.tv), so it
matches the Plex apps. Used by deck_curate (R4 / L4 in Movies and TV, Steam-button card while watching)."""
import prism_config  # noqa: E402  (paths)
import json, os, time, urllib.parse, urllib.request

HOME = os.path.expanduser("~")
CFG = json.load(open(f"{prism_config.PRISM}/plex.json"))
FOLDERS = f"{prism_config.PRISM}/plex/folders.json"       # TV folder (relative path) -> Plex ratingKey, from plex_sync
NOW = "/tmp/plex_now.json"                           # what plex_play is playing
DISCOVER = "https://discover.provider.plex.tv"
HDR = {"X-Plex-Token": CFG["token"], "Accept": "application/json", "X-Plex-Client-Identifier": "switchhome-deck",
       "X-Plex-Product": "Prism"}
ROMS = prism_config.ROMS


def req(url, method="GET"):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=HDR, method=method), timeout=15)
    body = r.read()
    return json.loads(body) if body.strip().startswith(b"{") else {}


def server(path, method="GET"):
    return req(CFG["url"] + path, method)


def meta(key):
    return server(f"/library/metadata/{key}")["MediaContainer"]["Metadata"][0]


def key_for(path):
    """Plex ratingKey for an ES-DE entry: a .plex stub, or a TV show / season folder."""
    if path.endswith(".plex"):
        try:
            return json.load(open(path))["key"]
        except (OSError, ValueError, KeyError):
            return None
    try:
        rel = os.path.relpath(path.rstrip("/"), f"{ROMS}/tv")
        return json.load(open(FOLDERS)).get(rel)
    except (OSError, ValueError):
        return None


def watched(m):
    if m.get("leafCount"):
        return int(m.get("viewedLeafCount") or 0) >= int(m["leafCount"])
    return bool(m.get("viewCount"))


def set_watched(key, on):
    q = urllib.parse.urlencode({"key": key, "identifier": "com.plexapp.plugins.library"})
    server(f"/:/{'scrobble' if on else 'unscrobble'}?{q}")


def toggle_watched(key):
    m = meta(key)
    on = not watched(m)
    set_watched(key, on)
    return on, m.get("title", "")


def watch_target(m):
    """Episodes and seasons go on the watchlist as their show; movies and shows as themselves."""
    if m.get("type") in ("episode", "season"):
        m = meta(m.get("grandparentRatingKey") or m.get("parentRatingKey"))
    guid = m.get("guid", "")
    return (guid.rsplit("/", 1)[-1] if guid.startswith("plex://") else None), m.get("title", "")


def watchlist_ids():
    ids, start = set(), 0
    while True:
        d = req(f"{DISCOVER}/library/sections/watchlist/all?includeFields=guid&X-Plex-Container-Start={start}"
                f"&X-Plex-Container-Size=100").get("MediaContainer", {})
        items = d.get("Metadata", [])
        ids |= {i.get("guid", "").rsplit("/", 1)[-1] for i in items}
        start += len(items)
        if not items or start >= int(d.get("totalSize") or 0):
            return ids


def set_watch_later(key, on):
    pid, title = watch_target(meta(key))
    if not pid:
        raise RuntimeError("no Plex id for this title")
    req(f"{DISCOVER}/actions/{'addToWatchlist' if on else 'removeFromWatchlist'}?ratingKey={pid}", "PUT")
    return title


def toggle_watch_later(key):
    pid, title = watch_target(meta(key))
    if not pid:
        raise RuntimeError("no Plex id for this title")
    on = pid not in watchlist_ids()
    req(f"{DISCOVER}/actions/{'addToWatchlist' if on else 'removeFromWatchlist'}?ratingKey={pid}", "PUT")
    return on, title


def plex_id_for_tmdb(media, tmdb):
    """Plex's own id for a TMDB title that isn't in the library (Discover), via Plex's metadata service."""
    d = req(f"https://metadata.provider.plex.tv/library/metadata/matches?type={1 if media == 'movie' else 2}"
            f"&guid=tmdb://{int(tmdb)}").get("MediaContainer", {})
    m = (d.get("Metadata") or [None])[0]
    return (m.get("ratingKey"), m.get("title", "")) if m else (None, "")


def toggle_watch_later_tmdb(media, tmdb):
    pid, title = plex_id_for_tmdb(media, tmdb)
    if not pid:
        raise RuntimeError("Plex doesn't know this title")
    on = pid not in watchlist_ids()
    req(f"{DISCOVER}/actions/{'addToWatchlist' if on else 'removeFromWatchlist'}?ratingKey={pid}", "PUT")
    return on, title


def now_playing():
    try:
        return json.load(open(NOW))
    except (OSError, ValueError):
        return {}
