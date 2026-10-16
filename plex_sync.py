#!/usr/bin/env python3
"""Movies and TV Shows from the owner's Plex server as browsable ES-DE systems (2026-10-08: "movies are browsable like
plex but straight in the menu").

  plex_sync.py            fetch: stub files (roms/movies/*.plex, roms/tv/<Show>/<Season>/*.plex), posters/backdrops/
                          episode thumbnails into downloaded_media, gamelists staged in ~/prism/plex/staged/
  plex_sync.py --apply    copy the staged gamelists into ~/ES-DE/gamelists (ES-DE must be CLOSED: esde_pending runs it)

A stub holds {"key": ratingKey, "type": movie|episode}; plex_play.py plays it with mpv and reports progress to Plex.
The info line (the theme's "players" slot) shows "PG-13 · 2h 8m"; watched items get <completed>.
"""
import prism_config  # noqa: E402  (paths)
import concurrent.futures, glob, time, html, json, os, re, shutil, sys, urllib.parse, urllib.request

HOME = os.path.expanduser("~")
CFG = json.load(open(f"{prism_config.PRISM}/plex.json"))
ROMS = prism_config.ROMS
MEDIA = f"{prism_config.TOOLS}/downloaded_media"
STAGE = f"{prism_config.PRISM}/plex/staged"
SECTIONS = {"movies": "4", "tv": "2"}


def api(path, **params):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{CFG['url']}{path}{'?' + q if q else ''}",
                                 headers={"X-Plex-Token": CFG["token"], "Accept": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))["MediaContainer"]


def clean(name):
    name = re.sub(r'[\\/:*?"<>|]', "", name).strip().rstrip(".")
    return re.sub(r"\s+", " ", name)[:120] or "Untitled"


def esc(t):
    return html.escape(str(t or ""), quote=False)


def runtime(ms):
    if not ms:
        return ""
    m = int(ms) // 60000
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"


def info_line(it):
    year = str(it.get("year") or "") if it.get("type") in ("movie", "show") else ""
    return " · ".join(x for x in (year, it.get("contentRating", ""), runtime(it.get("duration")), it.get("_mark")) if x)


NEW_DAYS = 21


def recent_key(it):
    """Plex 'recently added' order: newest first (shows: when an episode was last added)."""
    return 9999999999 - int(max(it.get("addedAt") or 0, it.get("updatedAt") or 0 if it.get("type") == "show" else 0))


def movie_order(it):
    """Unwatched first, each group newest-added first (how the owner browses Plex: recently added, not watched).
    Returns (sortname, mark for the info line)."""
    watched = bool(it.get("viewCount"))
    new = not watched and time.time() - int(it.get("addedAt") or 0) < NEW_DAYS * 86400
    return f"{1 if watched else 0}-{recent_key(it):010d}", ("Watched" if watched else "New" if new else "")


def show_order(it):
    left = int(it.get("leafCount") or 0) - int(it.get("viewedLeafCount") or 0)
    mark = "Watched" if left <= 0 and it.get("leafCount") else (f"{left} unwatched" if left else "")
    return f"{1 if left <= 0 else 0}-{recent_key(it):010d}", mark


def date(it):
    d = it.get("originallyAvailableAt") or (f"{it['year']}-01-01" if it.get("year") else "")
    return d.replace("-", "") + "T000000" if d else ""


def genres(it):
    return ", ".join(g["tag"] for g in it.get("Genre", [])[:3])


jobs = []   # (plex image path, local file, width)
copies = []   # (local source, local copy) done after the downloads


def image(thumb, dest, width):
    if thumb and not any(os.path.exists(os.path.splitext(dest)[0] + e) for e in (".jpg", ".png")):
        jobs.append((thumb, dest, width))


def fetch_image(job):
    thumb, dest, width = job
    if thumb.startswith("tmdb:"):                    # Discover artwork straight from TMDB's image CDN
        size, path = thumb[5:].split("/", 1)
        try:
            data = urllib.request.urlopen(f"https://image.tmdb.org/t/p/{size}/{path}", timeout=30).read()
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            open(dest, "wb").write(data)
            return True
        except Exception:
            return False
    url = (f"{CFG['url']}/photo/:/transcode?" +
           urllib.parse.urlencode({"width": width, "height": width * 2, "minSize": 1, "upscale": 0, "url": thumb,
                                   "X-Plex-Token": CFG["token"]}))
    try:
        data = urllib.request.urlopen(url, timeout=60).read()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").write(data)
        return True
    except Exception:
        return False


def stub(path, key, kind):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = json.dumps({"key": str(key), "type": kind}) + "\n"
    if not os.path.exists(path) or open(path).read() != body:
        open(path, "w").write(body)


def entry(tag, path, it, extra=""):
    rating = it.get("audienceRating") or it.get("rating")
    parts = [f"\t<{tag}>", f"\t\t<path>./{esc(path)}</path>", f"\t\t<name>{esc(it.get('_name') or it.get('title'))}</name>",
             f"\t\t<sortname>{esc(it['_sort'])}</sortname>" if it.get("_sort") else "",
             f"\t\t<desc>{esc(it.get('summary'))}</desc>"]
    if rating:
        parts.append(f"\t\t<rating>{min(1.0, float(rating) / 10):.2f}</rating>")
    if date(it):
        parts.append(f"\t\t<releasedate>{date(it)}</releasedate>")
    parts.append(f"\t\t<developer>{esc(it.get('_display') or it.get('title'))}</developer>")   # shown title (movies/tv.xml)
    if genres(it):
        parts.append(f"\t\t<genre>{esc(genres(it))}</genre>")
    if info_line(it):
        parts.append(f"\t\t<players>{esc(info_line(it))}</players>")
    if it.get("viewCount"):
        parts.append("\t\t<completed>true</completed>")
    parts.append(extra + f"\t</{tag}>")
    return "\n".join(p for p in parts if p)


CW = "Continue Watching"
LIB = {"movies": {}, "tv": {}}   # plex guid id -> (ratingKey, item), filled by sync_movies / sync_tv


def sync_ondeck(system):
    """'Continue Watching' folder at the top of Movies / TV: Plex's On Deck (started movies, next episodes)."""
    sec = SECTIONS[system]
    try:
        items = api(f"/library/sections/{sec}/onDeck").get("Metadata", [])[:30]
    except Exception:
        items = []
    d = f"{ROMS}/{system}/{CW}"
    os.makedirs(d, exist_ok=True)
    keep, out = set(), []
    for it in items:
        if not it.get("Media"):
            continue
        if it.get("type") == "episode":
            name = (f"{it.get('grandparentTitle', '')} - S{int(it.get('parentIndex') or 0):02d}"
                    f"E{int(it.get('index') or 0):02d} - {it.get('title', '')}")
            kind, poster, backdrop = "episode", it.get("grandparentThumb"), it.get("thumb")
        else:
            name = f"{it['title']} ({it['year']})" if it.get("year") else it["title"]
            kind, poster, backdrop = "movie", it.get("thumb"), it.get("art")
        stem = clean(name)
        keep.add(stem + ".plex")
        stub(f"{d}/{stem}.plex", it["ratingKey"], kind)
        image(poster, f"{MEDIA}/{system}/covers/{CW}/{stem}.jpg", 500)
        image(backdrop, f"{MEDIA}/{system}/screenshots/{CW}/{stem}.jpg", 1280)
        dur, off = int(it.get("duration") or 0), int(it.get("viewOffset") or 0)
        left = f"{runtime(dur - off)} left" if off and dur > off else "Up next"
        desc = f"{left}.\n\n{it.get('summary', '')}"
        out.append(entry("game", f"{CW}/{stem}.plex", {**it, "title": name, "summary": desc,
                                                          "contentRating": left, "duration": None,
                                                          "_display": name if system == "tv" else None}))
    for f in os.listdir(d):                      # no longer on deck: stub moved aside (never deleted)
        if f.endswith(".plex") and f not in keep:
            os.makedirs(f"{prism_config.PRISM}/plex/removed", exist_ok=True)
            shutil.move(f"{d}/{f}", f"{prism_config.PRISM}/plex/removed/{system}-{f}")
    cw_name = f"000 {CW}" if system == "tv" else CW        # TV folders sort by name only
    cw_dev = f"\t\t<developer>{CW}</developer>\n"
    folder = (f"\t<folder>\n\t\t<path>./{CW}</path>\n\t\t<name>{cw_name}</name>\n\t\t<sortname>!</sortname>\n{cw_dev}\t\t<genre>In progress</genre>\n\t\t<players>{len(out)} titles</players>\n"
              f"\t\t<desc>Pick up where you left off ({len(out)} in progress), from Plex.</desc>\n\t</folder>")
    if items and items[0].get("thumb" if system == "movies" else "grandparentThumb"):
        image(items[0].get("thumb") if system == "movies" else items[0].get("grandparentThumb"),
              f"{MEDIA}/{system}/covers/{CW}.jpg", 500)
    return [folder] + out


WL = "Watch Later"


def sync_watchlist(system, library):
    """'Watch Later' folder next to Continue Watching: the account's Plex watchlist, titles that are on this server.
    library: {plex guid id: (ratingKey, item)}."""
    try:
        import plex_watch
        ids = plex_watch.watchlist_ids()
    except Exception as e:
        print(f"watchlist unavailable: {e}"); return []
    d = f"{ROMS}/{system}/{WL}"
    os.makedirs(d, exist_ok=True)
    keep, out = set(), []
    for gid in ids:
        if gid not in library:
            continue
        k, it = library[gid]
        title = it["title"]
        stem = clean(f"{title} ({it['year']})" if it.get("year") else title)
        if system == "movies":
            keep.add(stem + ".plex")
            stub(f"{d}/{stem}.plex", k, "movie")
            copies.append((f"{MEDIA}/movies/covers/{stem}.jpg", f"{MEDIA}/movies/covers/{WL}/{stem}.jpg"))
            copies.append((f"{MEDIA}/movies/screenshots/{stem}.jpg", f"{MEDIA}/movies/screenshots/{WL}/{stem}.jpg"))
            sort, mark = movie_order(it)
            out.append(entry("game", f"{WL}/{stem}.plex", {**it, "_sort": sort, "_mark": mark}))
        else:   # a show: its first unwatched episode starts it (plex_play takes a show key and picks the episode)
            keep.add(stem + ".plex")
            stub(f"{d}/{stem}.plex", k, "show")
            show_dir = clean(title)
            copies.append((f"{MEDIA}/tv/covers/{show_dir}.jpg", f"{MEDIA}/tv/covers/{WL}/{stem}.jpg"))
            copies.append((f"{MEDIA}/tv/screenshots/{show_dir}.jpg", f"{MEDIA}/tv/screenshots/{WL}/{stem}.jpg"))
            sort, mark = show_order(it)
            out.append(entry("game", f"{WL}/{stem}.plex", {**it, "_sort": sort, "_mark": mark, "_display": title,
                                                           "_name": title}))
    for f in os.listdir(d):
        if f.endswith(".plex") and f not in keep:
            os.makedirs(f"{prism_config.PRISM}/plex/removed", exist_ok=True)
            shutil.move(f"{d}/{f}", f"{prism_config.PRISM}/plex/removed/{system}-wl-{f}")
    covers = sorted(glob.glob(f"{MEDIA}/{system}/covers/{WL}/*.jpg"), key=os.path.getmtime, reverse=True)
    if covers:                                   # folder poster: the newest title saved for later
        dst = f"{MEDIA}/{system}/covers/{WL}.jpg"
        try:
            os.remove(dst)
        except OSError:
            pass
        shutil.copy(covers[0], dst)
    name = f"000 {WL}" if system == "tv" else WL
    dev = f"\t\t<developer>{WL}</developer>\n"
    folder = (f"\t<folder>\n\t\t<path>./{WL}</path>\n\t\t<name>{name}</name>\n{dev}\t\t<genre>Your Plex watchlist</genre>\n"
              f"\t\t<players>{len(out)} titles</players>\n\t\t<desc>Saved for later on Plex (R4 adds or removes the selected"
              f" title).</desc>\n\t</folder>")
    return [folder] + out


DISC = "Discover"


def tmdb_image(path, dest, size):
    if path and not os.path.exists(dest):
        jobs.append(("tmdb:" + size + path, dest, 0))


def discover_shelves(system):
    import datetime as dt
    t = dt.date.today()
    d = lambda n: str(t - dt.timedelta(days=n))
    if system == "movies":
        return [("Trending now", "/discover/trending", {}, "movie"),
                ("New & popular", "/discover/movies", {"primaryReleaseDateGte": d(120), "sortBy": "popularity.desc"}, "movie"),
                ("Just released", "/discover/movies", {"primaryReleaseDateGte": d(45), "primaryReleaseDateLte": str(t),
                                                       "sortBy": "primary_release_date.desc"}, "movie"),
                ("Coming soon", "/discover/movies/upcoming", {}, "movie"),
                ("All-time popular", "/discover/movies", {"sortBy": "vote_count.desc"}, "movie")]
    return [("Trending now", "/discover/trending", {}, "tv"),
            ("New & popular", "/discover/tv", {"firstAirDateGte": d(120), "sortBy": "popularity.desc"}, "tv"),
            ("Coming soon", "/discover/tv/upcoming", {}, "tv"),
            ("All-time popular", "/discover/tv", {"sortBy": "vote_count.desc"}, "tv")]


def sync_discover(system):
    """'Discover' folder (2026-10-08): Overseerr's shelves of titles NOT in the library yet; picking one asks
    'Request …?' (discover_request.py via plex_play.py) and Overseerr downloads it into Plex like Request Movies."""
    import datetime as dt
    try:
        import overseerr
    except Exception as e:
        print(f"discover unavailable: {e}"); return []
    base = f"{ROMS}/{system}/{DISC}"
    new = base + ".new"
    shutil.rmtree(new, ignore_errors=True); os.makedirs(new)
    out = []
    for n, (name, path, params, mt) in enumerate(discover_shelves(system), 1):
        try:
            items = [i for i in overseerr.discover(path, 2, **params) if i.get("mediaType") == mt]
        except Exception as e:
            print(f"discover {name}: {e}"); continue
        items = [i for i in items if (i.get("mediaInfo") or {}).get("status") != 5][:30]   # not already in Plex
        if not items:
            continue
        os.makedirs(f"{new}/{name}")
        first_cover = None
        for rank, i in enumerate(items, 1):
            title = i.get("title") or i.get("name") or "?"
            date = i.get("releaseDate") or i.get("firstAirDate") or ""
            year = date[:4]
            stem = clean(f"{title} ({year})" if year else title)
            rel = f"{DISC}/{name}/{stem}"
            json.dump({"type": "discover", "media": mt, "tmdb": i["id"], "title": title}, open(f"{new}/{name}/{stem}.plex", "w"))
            tmdb_image(i.get("posterPath"), f"{MEDIA}/{system}/covers/{rel}.jpg", "w500")
            tmdb_image(i.get("backdropPath"), f"{MEDIA}/{system}/screenshots/{rel}.jpg", "w1280")
            first_cover = first_cover or f"{MEDIA}/{system}/covers/{rel}.jpg"
            status = overseerr.STATUS.get((i.get("mediaInfo") or {}).get("status"), "")
            when = ""
            try:
                dd = dt.date.fromisoformat(date)
                when = f"Coming {dd.strftime('%b %-d')}" if dd > dt.date.today() else ""
            except Exception:
                pass
            vote = f"★ {i['voteAverage']:.1f}" if i.get("voteAverage") else ""
            info = " · ".join(x for x in (year, vote, when, status or "A: request it") if x)
            out.append(f"\t<game>\n\t\t<path>./{esc(rel)}.plex</path>\n\t\t<name>{esc(title)}</name>\n"
                       f"\t\t<sortname>{rank:03d}</sortname>\n\t\t<developer>{esc(title)}</developer>\n"
                       f"\t\t<desc>{esc(i.get('overview') or '')}</desc>\n"
                       + (f"\t\t<rating>{min(1.0, i['voteAverage'] / 10):.2f}</rating>\n" if i.get("voteAverage") else "")
                       + (f"\t\t<releasedate>{date.replace('-', '')}T000000</releasedate>\n" if len(date) == 10 else "")
                       + f"\t\t<genre>{esc(name)}</genre>\n\t\t<players>{esc(info)}</players>\n\t</game>")
        if first_cover:
            copies.append((first_cover, f"{MEDIA}/{system}/covers/{DISC}/{name}.jpg"))
        out.append(f"\t<folder>\n\t\t<path>./{DISC}/{esc(name)}</path>\n\t\t<name>{n} {esc(name)}</name>\n"
                   f"\t\t<developer>{esc(name)}</developer>\n\t\t<genre>Discover</genre>\n\t\t<players>{len(items)} titles</players>\n"
                   f"\t\t<desc>Not in your library yet. Pick one to request it: it downloads to Plex.</desc>\n\t</folder>")
    # Search & request: the full Overseerr app for anything not on the shelves
    json.dump({"type": "overseerr"}, open(f"{new}/Search and request.plex", "w"))
    out.append(f"\t<game>\n\t\t<path>./{DISC}/Search and request.plex</path>\n\t\t<name>Search &amp; request…</name>\n"
               f"\t\t<sortname>999</sortname>\n\t\t<developer>Search &amp; request…</developer>\n"
               f"\t\t<desc>Find any movie or show and request it (opens Overseerr).</desc>\n\t\t<genre>Discover</genre>\n\t</game>")
    old = f"{prism_config.PRISM}/plex/removed/{system}-discover-previous"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(base):
        os.makedirs(os.path.dirname(old), exist_ok=True); shutil.move(base, old)   # last night's shelves, kept once
    os.rename(new, base)
    name = f"000 {DISC}" if system == "tv" else DISC
    shelf_covers = sorted(glob.glob(f"{MEDIA}/{system}/covers/{DISC}/*.jpg"))
    if shelf_covers:
        shutil.copy(shelf_covers[0], f"{MEDIA}/{system}/covers/{DISC}.jpg")
    folder = (f"\t<folder>\n\t\t<path>./{DISC}</path>\n\t\t<name>{name}</name>\n\t\t<developer>{DISC}</developer>\n"
              f"\t\t<genre>New to request</genre>\n\t\t<players>Trending · New · Coming soon</players>\n"
              f"\t\t<desc>Top new {'movies' if system == 'movies' else 'shows'}, just released, coming soon. Pick one to request"
              f" it through Overseerr: it downloads to Plex.</desc>\n\t</folder>")
    return [folder] + out


def guid_id(it):
    g = it.get("guid", "")
    return g.rsplit("/", 1)[-1] if g.startswith("plex://") else None


def sync_movies():
    items = api(f"/library/sections/{SECTIONS['movies']}/all").get("Metadata", [])
    out, keep = [], set()
    for it in items:
        if not it.get("Media"):
            continue
        stem = clean(f"{it['title']} ({it['year']})" if it.get("year") else it["title"])
        keep.add(stem + ".plex")
        stub(f"{ROMS}/movies/{stem}.plex", it["ratingKey"], "movie")
        image(it.get("thumb"), f"{MEDIA}/movies/covers/{stem}.jpg", 500)
        image(it.get("art"), f"{MEDIA}/movies/screenshots/{stem}.jpg", 1280)
        sort, mark = movie_order(it)
        if guid_id(it):
            LIB["movies"][guid_id(it)] = (it["ratingKey"], it)
        out.append(entry("game", stem + ".plex", {**it, "_sort": sort, "_mark": mark}))
    for f in os.listdir(f"{ROMS}/movies"):          # gone from Plex: stub moved aside (never deleted; folders skipped)
        if f.endswith(".plex") and f not in keep:
            os.makedirs(f"{prism_config.PRISM}/plex/removed", exist_ok=True)
            shutil.move(f"{ROMS}/movies/{f}", f"{prism_config.PRISM}/plex/removed/{f}")
    return out, len(items)


def sync_tv():
    sec = SECTIONS["tv"]
    shows = {s["ratingKey"]: s for s in api(f"/library/sections/{sec}/all").get("Metadata", [])}
    seasons = {s["ratingKey"]: s for s in api(f"/library/sections/{sec}/all", type=3).get("Metadata", [])}
    episodes = api(f"/library/sections/{sec}/all", type=4).get("Metadata", [])
    out, n_ep = [], 0
    show_dir = {k: clean(s["title"]) for k, s in shows.items()}
    newest = {}
    for e in episodes:
        k = e.get("grandparentRatingKey")
        newest[k] = max(newest.get(k, 0), int(e.get("addedAt") or 0))
    left_of = lambda s: int(s.get("leafCount") or 0) - int(s.get("viewedLeafCount") or 0)
    order = sorted(shows, key=lambda k: (left_of(shows[k]) <= 0, -newest.get(k, int(shows[k].get("addedAt") or 0))))
    rank = {k: i + 1 for i, k in enumerate(order)}
    folder_keys = {}
    for k, s in shows.items():
        d = show_dir[k]
        image(s.get("thumb"), f"{MEDIA}/tv/covers/{d}.jpg", 500)
        image(s.get("art"), f"{MEDIA}/tv/screenshots/{d}.jpg", 1280)
        os.makedirs(f"{ROMS}/tv/{d}", exist_ok=True)
        sort, mark = show_order(s)
        folder_keys[d] = k
        if guid_id(s):
            LIB["tv"][guid_id(s)] = (k, s)
        out.append(entry("folder", d, {**s, "_sort": sort, "_mark": mark, "_name": f"{rank[k]:03d} {s['title']}",
                                       "_display": s["title"]}))
    for k, se in seasons.items():
        if se.get("parentRatingKey") not in show_dir:
            continue
        d = f"{show_dir[se['parentRatingKey']]}/{clean(se.get('title') or 'Season ' + str(se.get('index', '')))}"
        os.makedirs(f"{ROMS}/tv/{d}", exist_ok=True)
        folder_keys[d] = k
        image(se.get("thumb") or shows[se["parentRatingKey"]].get("thumb"), f"{MEDIA}/tv/covers/{d}.jpg", 500)
        show = shows[se["parentRatingKey"]]
        left = int(se.get("leafCount") or 0) - int(se.get("viewedLeafCount") or 0)
        out.append(entry("folder", d, {**show, "title": se.get("title"), "summary": se.get("summary") or show.get("summary"),
                                       "viewCount": se.get("viewedLeafCount") == se.get("leafCount") and se.get("leafCount"),
                                       "_mark": "Watched" if left <= 0 else f"{left} unwatched",
                                       "_name": f"{int(se.get('index') or 0):03d} {se.get('title') or ''}",
                                       "_display": se.get("title") or f"Season {se.get('index', '')}"}))
    for e in episodes:
        sk, sek = e.get("grandparentRatingKey"), e.get("parentRatingKey")
        if sk not in show_dir or not e.get("Media"):
            continue
        season = seasons.get(sek, {})
        sdir = clean(season.get("title") or f"Season {e.get('parentIndex', '')}")
        name = f"S{int(e.get('parentIndex') or 0):02d}E{int(e.get('index') or 0):02d} - {e.get('title', '')}"
        stem = clean(name)
        rel = f"{show_dir[sk]}/{sdir}/{stem}"
        stub(f"{ROMS}/tv/{rel}.plex", e["ratingKey"], "episode")
        image(e.get("thumb"), f"{MEDIA}/tv/screenshots/{rel}.jpg", 960)
        copies.append((f"{MEDIA}/tv/covers/{show_dir[sk]}.jpg", f"{MEDIA}/tv/covers/{rel}.jpg"))   # show poster, no re-download
        out.append(entry("game", rel + ".plex", {**e, "title": name, "studio": shows[sk].get("studio"),
                                                 "Genre": shows[sk].get("Genre", []),
                                                 "contentRating": shows[sk].get("contentRating", ""),
                                                 "_mark": "Watched" if e.get("viewCount") else "",
                                                 "_display": name}))
        n_ep += 1
    json.dump(folder_keys, open(f"{prism_config.PRISM}/plex/folders.json", "w"))
    return out, f"{len(shows)} shows, {n_ep} episodes"


def write_stage(system, entries):
    os.makedirs(f"{STAGE}/{system}", exist_ok=True)
    open(f"{STAGE}/{system}/gamelist.xml", "w", encoding="utf-8").write(
        '<?xml version="1.0"?>\n<gameList>\n' + "\n".join(entries) + "\n</gameList>\n")


def apply():
    for system in SECTIONS:
        src = f"{STAGE}/{system}/gamelist.xml"
        if os.path.exists(src):
            os.makedirs(f"{prism_config.ESDE}/gamelists/{system}", exist_ok=True)
            shutil.copy(src, f"{prism_config.ESDE}/gamelists/{system}/gamelist.xml")
            print("applied", system)


def main():
    if "--apply" in sys.argv:
        return apply()
    os.makedirs(f"{ROMS}/movies", exist_ok=True); os.makedirs(f"{ROMS}/tv", exist_ok=True)
    m, nm = sync_movies(); m = sync_ondeck("movies") + sync_watchlist("movies", LIB["movies"]) + sync_discover("movies") + m
    write_stage("movies", m); print(f"movies: {nm}")
    t, nt = sync_tv(); t = sync_ondeck("tv") + sync_watchlist("tv", LIB["tv"]) + sync_discover("tv") + t
    write_stage("tv", t); print(f"tv: {nt}")
    if "--ondeck" in sys.argv:
        print("continue watching refreshed")
    print(f"downloading {len(jobs)} images")
    with concurrent.futures.ThreadPoolExecutor(6) as ex:
        ok = sum(ex.map(fetch_image, jobs))
    print(f"images: {ok}/{len(jobs)}")
    n = 0
    for src, dst in copies:
        if os.path.exists(src) and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy(src, dst)
            n += 1
    print(f"local copies: {n}")
    # cover-flow backgrounds: blurred ambient versions of the backdrops in fanart/ (plex_ambient.py, 2026-10-08)
    import plex_ambient
    plex_ambient.main()
    folder_art()


FOLDER_ART = f"{prism_config.PRISM}/plex/folder_art"


def folder_art():
    """Prism cards built from each folder's own posters (make_folder_art.py, 2026-10-09); the older static cards are
    the fallback if that fails."""
    try:
        import make_folder_art
        if make_folder_art.build():
            return
    except Exception as e:
        print(f"folder art (Prism cards) failed: {e}")
    folder_art_static()


def folder_art_static():
    """Continue Watching, Watch Later, Discover and its shelves get their own 'button' artwork (plex/folder_art/*.jpg)
    instead of a borrowed movie poster, so they don't look like films (2026-10-08)."""
    n = 0
    for system in ("movies", "tv"):
        cov = f"{MEDIA}/{system}/covers"
        targets = {CW: CW, WL: WL, DISC: DISC}
        for shelf, *_ in discover_shelves(system):
            targets[f"{DISC}/{shelf}"] = shelf
        targets[f"{DISC}/Search and request"] = "Search and request"
        for rel, art in targets.items():
            src = f"{FOLDER_ART}/{art}.jpg"
            if not os.path.exists(src):
                continue
            dst = f"{cov}/{rel}.jpg"
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            for ext in (".jpg", ".png", ".webp"):          # a hard link to a real poster must not be written through
                try:
                    os.remove(f"{cov}/{rel}{ext}")
                except OSError:
                    pass
            shutil.copy(src, dst); n += 1
    print(f"folder art: {n}")


if __name__ == "__main__":
    main()
