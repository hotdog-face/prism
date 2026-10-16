#!/usr/bin/env python3
"""Prism Music: a YouTube Music-style player for the owner's Plex music (2026-10-08: "made to look and feel like
youtube music with a way to like songs and hear the songs you previously liked as a playlist").

  deck_music.py [home|liked|albums|artists|youtube|nowplaying]     (launched from the Music row in Prism)

Tabs: Home (shelves) · Liked · Albums · Artists · YouTube.  Likes are Plex ratings (5 stars), so they sync with Plex.
Playback lives in deck_music_player.py (background unit), so music keeps going after this app closes.
Controls: d-pad move · A play/open · B back (exit at the top) · Y like · X shuffle · L/R tabs · LT/RT prev/next ·
Start Now Playing.
"""
import prism_config  # noqa: E402  (paths)
import re, hashlib, json, math, os, random, select, socket, subprocess, sys, threading, time, urllib.parse, urllib.request

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402
import evdev  # noqa: E402
from evdev import ecodes as E  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deck_power_menu import FONT, neon, rounded  # noqa: E402

HOME = os.path.expanduser("~")
DIR = f"{prism_config.PRISM}/music"
CACHE = f"{HOME}/.cache/deck-music"
LIB = f"{DIR}/library.json"
QUEUE, QUEUE_M3U, HISTORY = f"{DIR}/queue.json", f"{DIR}/queue.m3u", f"{DIR}/history.json"
SOCK = f"/run/user/{os.getuid()}/app/io.mpv.Mpv/music.sock"
CFG = json.load(open(f"{prism_config.PRISM}/plex.json"))
SECTIONS = {"8": "Music Library", "11": "YouTube Playlist"}
YOUTUBE = "11"
TABS = [("home", "Home"), ("liked", "Liked"), ("albums", "Albums"), ("artists", "Artists"), ("youtube", "YouTube")]
BG, CARD, TXT, SUB, RED = (0.11, 0.11, 0.10), (0.17, 0.17, 0.16), (1, 1, 1), (0.68, 0.68, 0.66), (0xF0 / 255, 0x43 / 255, 0x6F / 255)   # Redraw palette (2026-10-08)
SOUNDS = f"{prism_config.THEME_DIR}/switch/sounds"


def sound(n):
    p = f"{SOUNDS}/{n}.wav"
    if os.path.exists(p):
        subprocess.Popen(["pw-play", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- Plex library
def api(path, method="GET", **params):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{CFG['url']}{path}{'?' + q if q else ''}", method=method,
                                 headers={"X-Plex-Token": CFG["token"], "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    return json.loads(body)["MediaContainer"] if body.strip().startswith(b"{") else None


def fetch_library():
    lib = {"tracks": [], "albums": [], "artists": [], "time": int(time.time())}
    for sec in SECTIONS:
        for a in api(f"/library/sections/{sec}/all", type=8).get("Metadata", []):
            lib["artists"].append({"key": a["ratingKey"], "title": a.get("title", ""), "thumb": a.get("thumb"), "sec": sec})
        for a in api(f"/library/sections/{sec}/all", type=9).get("Metadata", []):
            lib["albums"].append({"key": a["ratingKey"], "title": a.get("title", ""), "artist": a.get("parentTitle", ""),
                                  "artistKey": a.get("parentRatingKey"), "thumb": a.get("thumb"), "year": a.get("year"),
                                  "added": a.get("addedAt", 0), "sec": sec})
        start = 0
        while True:
            m = api(f"/library/sections/{sec}/all", type=10,
                    **{"X-Plex-Container-Start": start, "X-Plex-Container-Size": 1000})
            items = m.get("Metadata", [])
            for t in items:
                if not t.get("Media"):
                    continue
                lib["tracks"].append({
                    "key": t["ratingKey"], "title": t.get("title", ""),
                    "artist": t.get("originalTitle") or t.get("grandparentTitle", ""), "album": t.get("parentTitle", ""),
                    "albumKey": t.get("parentRatingKey"), "artistKey": t.get("grandparentRatingKey"),
                    "index": t.get("index") or 0, "disc": t.get("parentIndex") or 1, "duration": t.get("duration") or 0,
                    "part": t["Media"][0]["Part"][0]["key"], "thumb": t.get("parentThumb") or t.get("grandparentThumb"),
                    "liked": (t.get("userRating") or 0) >= 10, "plays": t.get("viewCount") or 0, "sec": sec})
            start += len(items)
            if not items or start >= (m.get("totalSize") or 0):
                break
    os.makedirs(DIR, exist_ok=True)
    json.dump(lib, open(LIB, "w"))
    return lib


def load_library():
    try:
        return json.load(open(LIB))
    except (OSError, ValueError):
        return fetch_library()


def set_like(track, liked):
    track["liked"] = liked
    try:
        api("/:/rate", method="PUT", key=track["key"], identifier="com.plexapp.plugins.library", rating=10 if liked else -1)
    except Exception:
        pass


def stream_url(t):
    return f"{CFG['url']}{t['part']}?X-Plex-Token={CFG['token']}"


# ---------------------------------------------------------------- album art cache
class Art:
    def __init__(self, redraw):
        self.mem, self.busy, self.redraw = {}, set(), redraw
        os.makedirs(CACHE, exist_ok=True)

    def get(self, thumb, size):
        if not thumb:
            return None
        k = (thumb, size)
        if k in self.mem:
            return self.mem[k]
        path = f"{CACHE}/{hashlib.md5(f'{thumb}{size}'.encode()).hexdigest()}.jpg"
        if os.path.exists(path) and os.path.getsize(path) > 0:
            try:
                self.mem[k] = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
            except Exception:
                self.mem[k] = None
            return self.mem[k]
        if k not in self.busy and len(self.busy) < 8:
            self.busy.add(k)
            threading.Thread(target=self.fetch, args=(thumb, size, path, k), daemon=True).start()
        return None

    def fetch(self, thumb, size, path, k):
        try:
            url = f"{CFG['url']}/photo/:/transcode?" + urllib.parse.urlencode(
                {"width": size, "height": size, "minSize": 1, "url": thumb, "X-Plex-Token": CFG["token"]})
            data = urllib.request.urlopen(url, timeout=30).read()
            open(path, "wb").write(data)
        except Exception:
            pass                         # not cached: tried again next time
        self.busy.discard(k)
        GLib.idle_add(self.redraw)


# ---------------------------------------------------------------- player (deck_music_player.py over mpv IPC)
def ipc(cmd, timeout=1.0):
    try:
        s = socket.socket(socket.AF_UNIX); s.settimeout(timeout); s.connect(SOCK)
        s.sendall((json.dumps({"command": cmd}) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        s.close()
        for line in data.decode().splitlines():
            r = json.loads(line)
            if "error" in r:
                return r.get("data")
    except (OSError, ValueError):
        pass
    return None


def player_alive():
    return ipc(["get_property", "idle-active"]) is not None


def ensure_player():
    if player_alive():
        return True
    subprocess.run(["systemctl", "--user", "reset-failed", "deck-music-player"], capture_output=True)
    subprocess.run(["systemd-run", "--user", "--unit=deck-music-player", "--collect", "/usr/bin/python3",
                    f"{prism_config.PRISM}/deck_music_player.py"], capture_output=True)
    for _ in range(50):
        time.sleep(0.1)
        if player_alive():
            return True
    return False


def play_queue(tracks, start=0, shuffle=False):
    tracks = list(tracks)
    if not tracks:
        return
    if shuffle:
        first = tracks.pop(start) if start else None
        random.shuffle(tracks)
        if first:
            tracks.insert(0, first)
        start = 0
    os.makedirs(DIR, exist_ok=True)
    json.dump(tracks, open(QUEUE, "w"))
    fd = os.open(QUEUE_M3U, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # holds the Plex token: owner only
    with os.fdopen(fd, "w") as f:
        f.write("#EXTM3U\n" + "".join(f"#EXTINF:{t['duration'] // 1000},{t['artist']} - {t['title']}\n{stream_url(t)}\n"
                                      for t in tracks))
    if ensure_player():
        ipc(["loadlist", QUEUE_M3U, "replace"])
        time.sleep(0.15)
        ipc(["set_property", "playlist-pos", start])
        ipc(["set_property", "pause", False])


def player_status():
    if not os.path.exists(SOCK):
        return None
    idx = ipc(["get_property", "playlist-pos"], 0.3)
    if not isinstance(idx, int) or idx < 0:
        return None
    try:
        q = json.load(open(QUEUE))
    except (OSError, ValueError):
        return None
    if idx >= len(q):
        return None
    return {"track": q[idx], "pos": ipc(["get_property", "time-pos"], 0.3) or 0,
            "dur": ipc(["get_property", "duration"], 0.3) or q[idx]["duration"] / 1000,
            "paused": ipc(["get_property", "pause"], 0.3) is True, "next": q[idx + 1:idx + 6]}


# ---------------------------------------------------------------- lyrics (LRCLIB, free; only artist / title / length sent)
LYR_DIR = os.path.expanduser("~/.cache/deck-music/lyrics")


def parse_lrc(txt):
    out = []
    for line in (txt or "").splitlines():
        m = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)", line.strip())
        if m:
            out.append((int(m.group(1)) * 60 + float(m.group(2)), m.group(3).strip()))
    return out


def fetch_lyrics(t):
    """{"synced": [(sec, line)], "plain": [line]} or {} (none / instrumental). Cached per track."""
    os.makedirs(LYR_DIR, exist_ok=True)
    f = f"{LYR_DIR}/{t['key']}.json"
    try:
        return json.load(open(f))
    except (OSError, ValueError):
        pass
    res = {}
    try:
        q = urllib.parse.urlencode({"artist_name": t.get("artist", ""), "track_name": t.get("title", ""),
                                    "album_name": t.get("album", ""), "duration": int((t.get("duration") or 0) / 1000)})
        r = urllib.request.urlopen(urllib.request.Request(f"https://lrclib.net/api/get?{q}",
                                                          headers={"User-Agent": "SwitchHome Music (Steam Deck)"}), timeout=8)
        d = json.load(r)
        if not d.get("instrumental"):
            res = {"synced": parse_lrc(d.get("syncedLyrics")), "plain": (d.get("plainLyrics") or "").splitlines()}
    except Exception:
        res = {}
    json.dump(res, open(f, "w"))
    return res


def queue_now():
    """(tracks, index of the one playing) from the player's queue."""
    try:
        q = json.load(open(QUEUE))
    except (OSError, ValueError):
        return [], -1
    idx = ipc(["get_property", "playlist-pos"], 0.3)
    return q, idx if isinstance(idx, int) else -1


# ---------------------------------------------------------------- drawing helpers
def text(cr, t, x, y, size, rgb, bold=False, maxw=None, align="left", alpha=1.0):
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    t = str(t or "")
    if maxw:
        while t and cr.text_extents(t).x_advance > maxw:
            t = t[:-2] + "…" if len(t) > 2 else ""
    e = cr.text_extents(t)
    dx = {"left": 0, "center": -e.x_advance / 2, "right": -e.x_advance}[align]
    cr.set_source_rgba(*rgb, alpha); cr.move_to(x + dx, y); cr.show_text(t)
    return e.x_advance


def placeholder(cr, x, y, w, h, label, r, circle=False):
    hue = (sum(map(ord, label or "?")) % 360) / 360
    c1 = [0.5 + 0.4 * math.cos(2 * math.pi * (hue + k / 3)) for k in range(3)]
    g = cairo.LinearGradient(x, y, x + w, y + h)
    g.add_color_stop_rgb(0, *[v * 0.9 for v in c1]); g.add_color_stop_rgb(1, *[v * 0.35 for v in c1])
    if circle:
        cr.new_path(); cr.arc(x + w / 2, y + h / 2, w / 2, 0, 2 * math.pi)
    else:
        rounded(cr, x, y, w, h, r)
    cr.set_source(g); cr.fill()
    text(cr, (label or "?")[:1].upper(), x + w / 2, y + h / 2 + h * 0.14, h * 0.4, (1, 1, 1), bold=True, align="center",
         alpha=0.85)


def art(cr, pb, x, y, w, h, r, label="", circle=False):
    if pb is None:
        return placeholder(cr, x, y, w, h, label, r, circle)
    cr.save()
    if circle:
        cr.new_path(); cr.arc(x + w / 2, y + h / 2, w / 2, 0, 2 * math.pi)
    else:
        rounded(cr, x, y, w, h, r)
    cr.clip()
    sc = max(w / pb.get_width(), h / pb.get_height())
    cr.translate(x + (w - pb.get_width() * sc) / 2, y + (h - pb.get_height() * sc) / 2); cr.scale(sc, sc)
    Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0); cr.paint(); cr.restore()


def heart(cr, cx, cy, r, rgb, filled=True):
    cr.new_path()
    cr.move_to(cx, cy + r * 0.9)
    cr.curve_to(cx - r * 1.6, cy - r * 0.2, cx - r * 0.7, cy - r * 1.4, cx, cy - r * 0.45)
    cr.curve_to(cx + r * 0.7, cy - r * 1.4, cx + r * 1.6, cy - r * 0.2, cx, cy + r * 0.9)
    cr.close_path(); cr.set_source_rgb(*rgb)
    if filled:
        cr.fill()
    else:
        cr.set_line_width(max(1.5, r * 0.18)); cr.stroke()


def fmt(sec):
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


# ---------------------------------------------------------------- the app
class Music(Gtk.Window):
    def __init__(self, start_tab):
        super().__init__(title="Music")
        self.set_decorated(False); self.fullscreen()
        self.area = Gtk.DrawingArea(); self.area.connect("draw", self.draw); self.add(self.area)
        self.connect("realize", self.tag)
        self.art = Art(self.area.queue_draw)
        self.lib = load_library()
        self.index()
        self.tab = [k for k, _ in TABS].index(start_tab) if start_tab in dict(TABS) else 0
        self.stack = []              # pushed pages (album / artist / now playing) above the tab page
        self.focus = {}              # page id -> focus state
        self.status, self.t0 = None, time.monotonic()
        self.toast, self.toast_t = "", 0
        self.np_lyrics = False       # Now Playing: lyrics instead of Up Next (X)
        self.lyrics = {}             # track key -> lyrics dict / "loading"
        if start_tab == "nowplaying":
            self.stack.append({"type": "nowplaying"})
        GLib.timeout_add(500, self.poll_player)
        GLib.timeout_add(33, self.tick)
        threading.Thread(target=self.refresh_library, daemon=True).start()

    def tag(self, *_):
        if os.environ.get("SteamAppId"):
            subprocess.Popen(["xprop", "-id", str(self.get_window().get_xid()), "-f", "STEAM_GAME", "32c", "-set",
                              "STEAM_GAME", os.environ["SteamAppId"]])

    # ---- data
    def index(self):
        L = self.lib
        self.albums = [a for a in L["albums"] if a["sec"] != YOUTUBE]
        self.albums.sort(key=lambda a: (a["artist"].lower(), a.get("year") or 0))
        self.artists = sorted([a for a in L["artists"] if a["sec"] != YOUTUBE], key=lambda a: a["title"].lower())
        self.by_album, self.by_artist = {}, {}
        for t in L["tracks"]:
            self.by_album.setdefault(t["albumKey"], []).append(t)
            self.by_artist.setdefault(t["artistKey"], []).append(t)
        for v in self.by_album.values():
            v.sort(key=lambda t: (t["disc"], t["index"]))
        for v in self.by_artist.values():
            v.sort(key=lambda t: (t["album"].lower(), t["disc"], t["index"]))
        self.youtube = sorted([t for t in L["tracks"] if t["sec"] == YOUTUBE], key=lambda t: t["index"])
        self.tracks_by_key = {t["key"]: t for t in L["tracks"]}

    def liked(self):
        return [t for t in self.lib["tracks"] if t["liked"]]

    def recent(self):
        try:
            hist = json.load(open(HISTORY))
        except (OSError, ValueError):
            hist = []
        return [self.tracks_by_key[h["key"]] for h in hist if h["key"] in self.tracks_by_key][:20]

    def refresh_library(self):
        if time.time() - self.lib.get("time", 0) < 6 * 3600:
            return
        try:
            lib = fetch_library()
        except Exception:
            return
        def swap():
            self.lib = lib; self.index(); self.area.queue_draw()
        GLib.idle_add(swap)

    # ---- pages
    def page(self):
        if self.stack:
            return self.stack[-1]
        kind = TABS[self.tab][0]
        if kind == "home":
            return {"type": "home", "id": "home"}
        if kind == "liked":
            return {"type": "tracks", "id": "liked", "title": "Liked songs", "sub": "Your likes, newest at the top",
                    "tracks": self.liked(), "heart": True}
        if kind == "youtube":
            return {"type": "tracks", "id": "youtube", "title": "YouTube Playlist", "sub": f"{len(self.youtube)} songs",
                    "tracks": self.youtube}
        if kind == "albums":
            return {"type": "grid", "id": "albums", "items": self.albums, "circle": False}
        return {"type": "grid", "id": "artists", "items": self.artists, "circle": True}

    def shelves(self):
        out = []
        liked = self.liked()
        out.append(("Liked songs", "tracks", liked[:20]) if liked else ("Liked songs", "hint", []))
        rec = self.recent()
        if rec:
            out.append(("Recently played", "tracks", rec))
        newest = sorted(self.albums, key=lambda a: -(a.get("added") or 0))[:20]
        out.append(("Recently added albums", "albums", newest))
        most = sorted(self.lib["tracks"], key=lambda t: -t["plays"])[:20]
        if most and most[0]["plays"]:
            out.append(("Your most played", "tracks", most))
        out.append(("Artists", "artists", self.artists[:24]))
        if self.youtube:
            out.append(("From your YouTube playlist", "tracks", self.youtube[:20]))
        return out

    def fstate(self, pid, **default):
        return self.focus.setdefault(pid, dict(default))

    # ---- input
    def nav(self, dx, dy):
        p = self.page()
        if p["type"] == "nowplaying" and dy > 0:
            q, idx = queue_now()
            if q:
                self.stack.append({"type": "queue", "id": "queue"})
                self.fstate("queue", i=0)["i"] = max(0, idx + 1 if idx + 1 < len(q) else idx)
            sound("cc_move"); return
        if p["type"] == "queue":
            q, idx = queue_now()
            st = self.fstate("queue", i=0)
            st["i"] = max(0, min(len(q) - 1, st["i"] + dy + dx * 8)); sound("cc_move"); return
        if p["type"] == "home":
            st = self.fstate("home", row=0, col=0)
            sh = self.shelves()
            if dy:
                st["row"] = max(0, min(len(sh) - 1, st["row"] + dy))
                st["col"] = min(st["col"], max(0, len(sh[st["row"]][2]) - 1))
            if dx:
                st["col"] = max(0, min(max(0, len(sh[st["row"]][2]) - 1), st["col"] + dx))
        elif p["type"] == "tracks":
            st = self.fstate(p["id"], i=0)
            st["i"] = max(0, min(max(0, len(p["tracks"]) - 1), st["i"] + dy * (1 if not dx else 0) + dx * 8))
        elif p["type"] == "grid":
            st = self.fstate(p["id"], i=0)
            st["i"] = max(0, min(len(p["items"]) - 1, st["i"] + dx + dy * 6))
        sound("cc_move")

    def activate(self, shuffle=False):
        p = self.page()
        if p["type"] == "home":
            st = self.fstate("home", row=0, col=0)
            sh = self.shelves()
            title, kind, items = sh[st["row"]]
            if kind == "tracks" and items:
                play_queue(items, st["col"], shuffle); self.say("Playing")
            elif kind == "albums" and items:
                self.open_album(items[st["col"]], shuffle)
            elif kind == "artists" and items:
                self.open_artist(items[st["col"]], shuffle)
        elif p["type"] == "tracks":
            st = self.fstate(p["id"], i=0)
            if p["tracks"]:
                play_queue(p["tracks"], 0 if shuffle else st["i"], shuffle); self.say("Shuffling" if shuffle else "Playing")
        elif p["type"] == "grid":
            st = self.fstate(p["id"], i=0)
            it = p["items"][st["i"]] if p["items"] else None
            if it:
                (self.open_artist if p["circle"] else self.open_album)(it, shuffle)
        elif p["type"] == "nowplaying":
            ipc(["cycle", "pause"])
        elif p["type"] == "queue":                     # jump to the chosen song, back to Now Playing
            st = self.fstate("queue", i=0)
            ipc(["playlist-play-index", st["i"]]); ipc(["set_property", "pause", False])
            self.stack.pop(); self.say("Playing")

    def open_album(self, a, shuffle=False):
        tr = self.by_album.get(a["key"], [])
        if shuffle:
            play_queue(tr, 0, True); self.say("Shuffling"); return
        self.stack.append({"type": "tracks", "id": f"album{a['key']}", "title": a["title"],
                           "sub": " · ".join(str(x) for x in (a["artist"], a.get("year"), f"{len(tr)} songs") if x),
                           "tracks": tr, "thumb": a.get("thumb")})
        sound("cc_choose")

    def open_artist(self, a, shuffle=False):
        tr = self.by_artist.get(a["key"], [])
        if shuffle:
            play_queue(tr, 0, True); self.say("Shuffling"); return
        self.stack.append({"type": "tracks", "id": f"artist{a['key']}", "title": a["title"], "sub": f"{len(tr)} songs",
                           "tracks": tr, "thumb": a.get("thumb"), "circle": True})
        sound("cc_choose")

    def focused_track(self):
        p = self.page()
        if p["type"] == "tracks" and p["tracks"]:
            return p["tracks"][self.fstate(p["id"], i=0)["i"]]
        if p["type"] == "home":
            st = self.fstate("home", row=0, col=0)
            title, kind, items = self.shelves()[st["row"]]
            if kind == "tracks" and items:
                return items[st["col"]]
        return self.status["track"] if self.status else None

    def like(self):
        t = self.focused_track()
        if not t:
            return
        t = self.tracks_by_key.get(t["key"], t)
        new = not t["liked"]
        threading.Thread(target=set_like, args=(t, new), daemon=True).start()
        t["liked"] = new
        json.dump(self.lib, open(LIB, "w"))
        self.say("Added to Liked songs" if new else "Removed from Liked songs")
        sound("cc_choose")

    def back(self):
        if self.stack:
            self.stack.pop(); sound("cc_close")
        else:
            Gtk.main_quit()

    def button(self, name):
        if name == "A":
            self.activate()
        elif name == "B":
            self.back()
        elif name == "X" and self.stack and self.stack[-1]["type"] == "nowplaying":
            self.np_lyrics = not self.np_lyrics; sound("cc_move")
        elif name == "X":
            self.activate(shuffle=True)
        elif name == "Y":
            self.like()
        elif name in ("LB", "RB") and not self.stack:
            self.tab = (self.tab + (1 if name == "RB" else -1)) % len(TABS); sound("cc_move")
        elif name in ("LB", "RB"):
            self.stack.clear(); self.tab = (self.tab + (1 if name == "RB" else -1)) % len(TABS)
        elif name == "LT":
            pos = ipc(["get_property", "time-pos"])
            ipc(["seek", 0, "absolute"]) if isinstance(pos, (int, float)) and pos > 4 else ipc(["playlist-prev"])
        elif name == "RT":
            ipc(["playlist-next"])
        elif name == "START":
            if self.stack and self.stack[-1]["type"] == "nowplaying":
                self.stack.pop()
            elif self.status:
                self.stack.append({"type": "nowplaying"})
        elif name == "SELECT":
            ipc(["cycle", "pause"])
        self.area.queue_draw()

    def say(self, msg):
        self.toast, self.toast_t = msg, time.monotonic()

    # ---- loops
    def poll_player(self):
        threading.Thread(target=self._poll, daemon=True).start()
        return True

    def _poll(self):
        st = player_status()
        GLib.idle_add(lambda: (setattr(self, "status", st), self.area.queue_draw(), False)[2])

    def tick(self):
        if time.monotonic() - self.toast_t < 2.2:
            self.area.queue_draw()
        return True

    # ---- drawing
    def draw(self, w, cr):
        W, H = w.get_allocated_width(), w.get_allocated_height(); s = H / 800
        cr.set_source_rgb(*BG); cr.paint()
        p = self.page()
        if p["type"] == "nowplaying":
            self.draw_nowplaying(cr, W, H, s)
            return False
        if p["type"] == "queue":
            self.draw_queue(cr, W, H, s)
            return False
        self.draw_top(cr, W, H, s)
        body_h = H - (96 if self.status else 0) * s
        cr.save(); cr.rectangle(0, 92 * s, W, body_h - 92 * s); cr.clip()
        {"home": self.draw_home, "tracks": self.draw_tracks, "grid": self.draw_grid}[p["type"]](cr, W, body_h, s, p)
        cr.restore()
        if self.status:
            self.draw_bar(cr, W, H, s)
        if time.monotonic() - self.toast_t < 2.2:
            a = min(1, (2.2 - (time.monotonic() - self.toast_t)) / 0.3)
            tw = text(cr, self.toast, 0, -100, 17 * s, TXT) + 40 * s
            rounded(cr, W / 2 - tw / 2, H - (150 if self.status else 70) * s, tw, 40 * s, 20 * s)
            cr.set_source_rgba(0.95, 0.95, 0.95, 0.95 * a); cr.fill()
            text(cr, self.toast, W / 2, H - (124 if self.status else 44) * s, 17 * s, (0.05, 0.05, 0.05), align="center",
                 alpha=a)
        return False

    def draw_top(self, cr, W, H, s):
        cr.new_path(); cr.arc(52 * s, 46 * s, 17 * s, 0, 2 * math.pi); neon(cr); cr.fill()
        cr.new_path(); cr.arc(52 * s, 46 * s, 10 * s, 0, 2 * math.pi); cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(2 * s); cr.stroke()
        cr.move_to(49 * s, 41 * s); cr.line_to(57 * s, 46 * s); cr.line_to(49 * s, 51 * s); cr.close_path(); cr.fill()
        text(cr, "Music", 78 * s, 55 * s, 26 * s, TXT, bold=True)
        x = 260 * s
        for i, (_, label) in enumerate(TABS):
            sel = i == self.tab and not self.stack
            tw = text(cr, label, 0, -100, 19 * s, TXT, bold=True)
            if i == self.tab:
                rounded(cr, x - 14 * s, 26 * s, tw + 28 * s, 38 * s, 19 * s)
                cr.set_source_rgba(1, 1, 1, 0.95 if sel else 0.25); cr.fill()
            text(cr, label, x, 52 * s, 19 * s, (0.05, 0.05, 0.05) if (i == self.tab and sel) else TXT, bold=True)
            x += tw + 44 * s
        text(cr, "L  R", W - 56 * s, 52 * s, 15 * s, SUB, align="right")

    def card_label(self, cr, x, y, w, title, sub, s):
        text(cr, title, x, y + 24 * s, 16 * s, TXT, bold=True, maxw=w)
        text(cr, sub, x, y + 45 * s, 14 * s, SUB, maxw=w)

    def draw_home(self, cr, W, H, s, p):
        st = self.fstate("home", row=0, col=0)
        sh = self.shelves()
        row_h = 300 * s
        top = 108 * s - max(0, st["row"] - 0) * row_h + min(st["row"], 1) * 0
        top = 108 * s - max(0, st["row"] * row_h - (H - 108 * s - row_h) * 0.15)
        for r, (title, kind, items) in enumerate(sh):
            y = top + r * row_h
            if y > H or y + row_h < 92 * s:
                continue
            text(cr, title, 56 * s, y + 26 * s, 26 * s, TXT, bold=True)
            size = 180 * s; gap = 26 * s
            focus_col = st["col"] if r == st["row"] else 0
            first = max(0, focus_col - 4)
            if kind == "hint":
                rounded(cr, 56 * s, y + 48 * s, 560 * s, 120 * s, 16 * s); cr.set_source_rgb(*CARD); cr.fill()
                heart(cr, 110 * s, y + 108 * s, 22 * s, RED)
                text(cr, "Press Y on any song to like it", 160 * s, y + 102 * s, 20 * s, TXT, bold=True)
                text(cr, "Your likes show up here and play as a playlist", 160 * s, y + 130 * s, 16 * s, SUB)
                continue
            for c in range(first, min(len(items), first + 7)):
                it = items[c]
                x = 56 * s + (c - first) * (size + gap)
                sel = r == st["row"] and c == st["col"]
                circle = kind == "artists"
                if sel:
                    if circle:
                        cr.new_path(); cr.arc(x + size / 2, y + 48 * s + size / 2, size / 2 + 6 * s, 0, 2 * math.pi)
                    else:
                        rounded(cr, x - 6 * s, y + 42 * s, size + 12 * s, size + 12 * s, 14 * s)
                    cr.set_source_rgb(1, 1, 1); cr.fill()
                art(cr, self.art.get(it.get("thumb"), 300), x, y + 48 * s, size, size, 8 * s,
                    it.get("album") or it.get("title"), circle)
                if kind == "tracks" and self.tracks_by_key.get(it["key"], it).get("liked"):
                    heart(cr, x + size - 18 * s, y + 48 * s + 20 * s, 9 * s, RED)
                if kind == "tracks":
                    self.card_label(cr, x, y + 48 * s + size, size, it["title"], it["artist"], s)
                elif kind == "albums":
                    self.card_label(cr, x, y + 48 * s + size, size, it["title"], it["artist"], s)
                else:
                    text(cr, it["title"], x + size / 2, y + 48 * s + size + 26 * s, 16 * s, TXT, bold=True, maxw=size,
                         align="center")

    def draw_tracks(self, cr, W, H, s, p):
        st = self.fstate(p["id"], i=0)
        tracks = p["tracks"]
        # header
        hx, hy, hs = 56 * s, 112 * s, 200 * s
        if p.get("heart"):
            g = cairo.LinearGradient(hx, hy, hx + hs, hy + hs)
            g.add_color_stop_rgb(0, 0.55, 0.2, 0.9); g.add_color_stop_rgb(1, 0.15, 0.55, 0.95)
            rounded(cr, hx, hy, hs, hs, 12 * s); cr.set_source(g); cr.fill()
            heart(cr, hx + hs / 2, hy + hs / 2 + 6 * s, 46 * s, (1, 1, 1))
        else:
            first = tracks[0] if tracks else {}
            art(cr, self.art.get(p.get("thumb") or first.get("thumb"), 400), hx, hy, hs, hs, 12 * s,
                p["title"], p.get("circle", False))
        text(cr, p["title"], hx, hy + hs + 42 * s, 30 * s, TXT, bold=True, maxw=330 * s)
        text(cr, p.get("sub", ""), hx, hy + hs + 72 * s, 16 * s, SUB, maxw=330 * s)
        for i, (lbl, key) in enumerate((("Play", "A"), ("Shuffle", "X"))):
            bx = hx + i * 150 * s; by = hy + hs + 96 * s
            rounded(cr, bx, by, 136 * s, 44 * s, 22 * s)
            cr.set_source_rgb(1, 1, 1) if i == 0 else cr.set_source_rgba(1, 1, 1, 0.12)
            cr.fill()
            text(cr, f"{key}  {lbl}", bx + 68 * s, by + 29 * s, 17 * s, (0.05, 0.05, 0.05) if i == 0 else TXT,
                 bold=True, align="center")
        # list
        lx = 440 * s; lw = W - lx - 56 * s; rh = 64 * s
        if not tracks:
            text(cr, "Nothing here yet" if not p.get("heart") else "No liked songs yet - press Y on a song to like it",
                 lx, 160 * s, 20 * s, SUB)
            return
        visible = int((H - 110 * s) // rh)
        first = max(0, min(st["i"] - visible // 2, len(tracks) - visible))
        now_key = self.status["track"]["key"] if self.status else None
        for i in range(first, min(len(tracks), first + visible + 1)):
            t = self.tracks_by_key.get(tracks[i]["key"], tracks[i])
            y = 104 * s + (i - first) * rh
            if i == st["i"]:
                rounded(cr, lx - 12 * s, y, lw + 24 * s, rh - 6 * s, 10 * s); cr.set_source_rgba(1, 1, 1, 0.13); cr.fill()
            art(cr, self.art.get(t.get("thumb"), 120), lx, y + 6 * s, 46 * s, 46 * s, 5 * s, t["album"] or t["title"])
            playing = t["key"] == now_key
            text(cr, t["title"], lx + 64 * s, y + 27 * s, 17 * s, RED if playing else TXT, bold=True, maxw=lw * 0.5)
            text(cr, f"{t['artist']} · {t['album']}" if t["album"] else t["artist"], lx + 64 * s, y + 48 * s, 14 * s,
                 SUB, maxw=lw * 0.55)
            if t["liked"]:
                heart(cr, lx + lw - 90 * s, y + 30 * s, 9 * s, RED)
            text(cr, fmt(t["duration"] / 1000), lx + lw, y + 35 * s, 15 * s, SUB, align="right")

    def draw_grid(self, cr, W, H, s, p):
        st = self.fstate(p["id"], i=0)
        items = p["items"]
        cols, size, gap = 6, 170 * s, 30 * s
        row_h = size + 80 * s
        rows_vis = max(1, int((H - 110 * s) // row_h))
        row = st["i"] // cols
        first_row = max(0, row - rows_vis + 1)
        x0 = (W - (cols * size + (cols - 1) * gap)) / 2
        for idx in range(first_row * cols, min(len(items), (first_row + rows_vis + 1) * cols)):
            it = items[idx]
            r, c = idx // cols - first_row, idx % cols
            x, y = x0 + c * (size + gap), 112 * s + r * row_h
            sel = idx == st["i"]
            if sel:
                if p["circle"]:
                    cr.new_path(); cr.arc(x + size / 2, y + size / 2, size / 2 + 6 * s, 0, 2 * math.pi)
                else:
                    rounded(cr, x - 6 * s, y - 6 * s, size + 12 * s, size + 12 * s, 14 * s)
                cr.set_source_rgb(1, 1, 1); cr.fill()
            art(cr, self.art.get(it.get("thumb"), 300), x, y, size, size, 8 * s, it["title"], p["circle"])
            if p["circle"]:
                text(cr, it["title"], x + size / 2, y + size + 28 * s, 16 * s, TXT, bold=True, maxw=size, align="center")
            else:
                self.card_label(cr, x, y + size, size, it["title"], it["artist"], s)

    def draw_bar(self, cr, W, H, s):
        st = self.status; t = st["track"]
        y = H - 96 * s
        cr.rectangle(0, y, W, 96 * s); cr.set_source_rgb(0.14, 0.14, 0.13); cr.fill()
        frac = (st["pos"] or 0) / st["dur"] if st["dur"] else 0
        cr.rectangle(0, y, W, 3 * s); cr.set_source_rgba(1, 1, 1, 0.15); cr.fill()
        cr.rectangle(0, y, W * frac, 3 * s); neon(cr); cr.fill()
        art(cr, self.art.get(t.get("thumb"), 120), 24 * s, y + 18 * s, 60 * s, 60 * s, 6 * s, t.get("album") or t["title"])
        text(cr, t["title"], 100 * s, y + 44 * s, 18 * s, TXT, bold=True, maxw=420 * s)
        text(cr, f"{t['artist']} · {fmt(st['pos'])} / {fmt(st['dur'])}", 100 * s, y + 68 * s, 14 * s, SUB, maxw=420 * s)
        if self.tracks_by_key.get(t["key"], t).get("liked"):
            heart(cr, 540 * s, y + 50 * s, 10 * s, RED)
        cx = W / 2 + 60 * s
        cr.set_source_rgb(1, 1, 1)
        if st["paused"]:
            cr.move_to(cx - 9 * s, y + 34 * s); cr.line_to(cx + 13 * s, y + 48 * s); cr.line_to(cx - 9 * s, y + 62 * s)
            cr.close_path(); cr.fill()
        else:
            cr.rectangle(cx - 10 * s, y + 34 * s, 7 * s, 28 * s); cr.rectangle(cx + 3 * s, y + 34 * s, 7 * s, 28 * s); cr.fill()
        text(cr, "LT", cx - 60 * s, y + 54 * s, 14 * s, SUB, align="center")
        text(cr, "RT", cx + 60 * s, y + 54 * s, 14 * s, SUB, align="center")
        text(cr, "View  play/pause   ·   Y like   ·   Start  now playing", W - 30 * s, y + 54 * s, 14 * s, SUB,
             align="right")

    def draw_nowplaying(self, cr, W, H, s):
        st = self.status
        if not st:
            text(cr, "Nothing playing", W / 2, H / 2, 26 * s, SUB, align="center")
            return
        t = st["track"]
        pb = self.art.get(t.get("thumb"), 600)
        if pb:      # blurred, darkened backdrop from the album art
            small = pb.scale_simple(16, 16, GdkPixbuf.InterpType.BILINEAR).scale_simple(W // 4, H // 4,
                                                                                         GdkPixbuf.InterpType.BILINEAR)
            cr.save(); cr.scale(4, 4); Gdk.cairo_set_source_pixbuf(cr, small, 0, 0); cr.paint(); cr.restore()
            cr.set_source_rgba(0, 0, 0, 0.55); cr.paint()
        size = 420 * s
        art(cr, pb, 110 * s, (H - size) / 2 - 20 * s, size, size, 14 * s, t.get("album") or t["title"])
        x = 110 * s + size + 70 * s; mw = W - x - 90 * s
        text(cr, "NOW PLAYING", x, 200 * s, 15 * s, SUB, bold=True)
        text(cr, t["title"], x, 252 * s, 40 * s, TXT, bold=True, maxw=mw)
        text(cr, t["artist"], x, 292 * s, 22 * s, SUB, maxw=mw)
        if t.get("album"):
            text(cr, t["album"], x, 322 * s, 18 * s, SUB, maxw=mw)
        frac = (st["pos"] or 0) / st["dur"] if st["dur"] else 0
        rounded(cr, x, 362 * s, mw, 5 * s, 2.5 * s); cr.set_source_rgba(1, 1, 1, 0.2); cr.fill()
        rounded(cr, x, 362 * s, max(5 * s, mw * frac), 5 * s, 2.5 * s); neon(cr); cr.fill()
        text(cr, fmt(st["pos"]), x, 392 * s, 15 * s, SUB)
        text(cr, fmt(st["dur"]), x + mw, 392 * s, 15 * s, SUB, align="right")
        if self.tracks_by_key.get(t["key"], t).get("liked"):
            heart(cr, x + 14 * s, 440 * s, 14 * s, RED)
            text(cr, "Liked", x + 38 * s, 447 * s, 17 * s, TXT)
        else:
            heart(cr, x + 14 * s, 440 * s, 14 * s, (1, 1, 1), filled=False)
            text(cr, "Y  Like", x + 38 * s, 447 * s, 17 * s, SUB)
        if self.np_lyrics:
            self.draw_lyrics(cr, t, st, x, 490 * s, mw, H - 80 * s, s)
        elif st["next"]:
            text(cr, "UP NEXT", x, 510 * s, 15 * s, SUB, bold=True)
            for i, n in enumerate(st["next"][:4]):
                text(cr, f"{n['title']}  ·  {n['artist']}", x, 542 * s + i * 30 * s, 17 * s, TXT, maxw=mw, alpha=0.85)
        text(cr, "A play/pause  ·  LT/RT previous/next  ·  X " + ("up next" if self.np_lyrics else "lyrics")
             + "  ·  Down queue  ·  B back", W / 2, H - 30 * s, 15 * s, SUB, align="center")

    def draw_lyrics(self, cr, t, st, x, y0, mw, y1, s):
        k = t["key"]
        lyr = self.lyrics.get(k)
        if lyr is None:
            self.lyrics[k] = "loading"
            threading.Thread(target=lambda: (self.lyrics.__setitem__(k, fetch_lyrics(t)), GLib.idle_add(self.area.queue_draw)),
                             daemon=True).start()
            lyr = "loading"
        text(cr, "LYRICS", x, y0 + 20 * s, 15 * s, SUB, bold=True)
        if lyr == "loading":
            text(cr, "Looking for lyrics…", x, y0 + 56 * s, 18 * s, SUB); return
        if not lyr or not (lyr.get("synced") or lyr.get("plain")):
            text(cr, "No lyrics for this song", x, y0 + 56 * s, 18 * s, SUB); return
        lines, pos = lyr.get("synced"), st["pos"] or 0
        row_h, n_rows = 30 * s, max(3, int((y1 - y0 - 40 * s) / (30 * s)))
        if lines:                                   # synced: the current line in white, centred, the rest dimmed
            cur = max([i for i, (t0, _) in enumerate(lines) if t0 <= pos + 0.3] or [0])
            first = max(0, cur - n_rows // 2)
            for j, (_, line) in enumerate(lines[first:first + n_rows]):
                i = first + j
                text(cr, line or "…", x, y0 + 56 * s + j * row_h, (20 if i == cur else 17) * s,
                     TXT if i == cur else SUB, bold=(i == cur), maxw=mw, alpha=1.0 if i == cur else 0.6)
        else:                                       # plain: scroll through the song by position
            plain = [l for l in lyr["plain"]]
            frac = pos / st["dur"] if st["dur"] else 0
            first = max(0, min(len(plain) - n_rows, int(frac * len(plain)) - 2))
            for j, line in enumerate(plain[first:first + n_rows]):
                text(cr, line, x, y0 + 56 * s + j * row_h, 17 * s, TXT, maxw=mw, alpha=0.85)

    def draw_queue(self, cr, W, H, s):
        q, idx = queue_now()
        st = self.fstate("queue", i=0)
        text(cr, "Queue", 90 * s, 80 * s, 34 * s, TXT, bold=True)
        text(cr, f"{max(0, len(q) - idx - 1)} songs up next", 90 * s, 112 * s, 17 * s, SUB)
        row_h = 56 * s; n_rows = int((H - 230 * s) / row_h)
        first = max(0, min(st["i"] - n_rows // 2, max(0, len(q) - n_rows)))
        for j, tr in enumerate(q[first:first + n_rows]):
            i = first + j; y = 150 * s + j * row_h
            if i == st["i"]:
                rounded(cr, 70 * s, y, W - 140 * s, row_h - 6 * s, 12 * s); cr.set_source_rgb(*CARD); cr.fill()
            pb = self.art.get(tr.get("thumb"), 120)
            art(cr, pb, 84 * s, y + 6 * s, row_h - 18 * s, row_h - 18 * s, 6 * s, tr.get("album") or tr["title"])
            col = RED if i == idx else TXT
            text(cr, tr["title"], 84 * s + row_h, y + 24 * s, 18 * s, col, bold=(i == idx),
                 maxw=W * 0.5)
            text(cr, tr["artist"] + ("  ·  Now playing" if i == idx else ""), 84 * s + row_h, y + 44 * s, 14 * s, SUB, maxw=W * 0.5)
            text(cr, fmt((tr.get("duration") or 0) / 1000), W - 90 * s, y + 32 * s, 15 * s, SUB, align="right")
        text(cr, "A play this song  ·  B back to Now Playing", W / 2, H - 30 * s, 15 * s, SUB, align="center")


# ---------------------------------------------------------------- controller input (evdev, not grabbed)
def pads():
    out = []
    for p in evdev.list_devices():
        try:
            d = evdev.InputDevice(p)
            keys = d.capabilities().get(E.EV_KEY, [])
            if E.BTN_SOUTH in keys and E.BTN_EAST in keys and not (d.info.vendor == 0x045e and d.info.product == 0x028e):
                out.append(d)
            else:
                d.close()
        except OSError:
            pass
    return out


def input_thread(app):
    devices = pads()
    held = {}           # direction -> (next repeat time)
    trig = {"LT": False, "RT": False}
    axes = {"x": 0, "y": 0}
    keymap = {E.BTN_SOUTH: "A", E.BTN_EAST: "B", E.BTN_X: "X", E.BTN_Y: "Y", E.BTN_TL: "LB", E.BTN_TR: "RB",
              E.BTN_START: "START", E.BTN_SELECT: "SELECT"}
    while True:
        r, _, _ = select.select(devices, [], [], 0.05)
        now = time.monotonic()
        for d in r:
            try:
                for ev in d.read():
                    if ev.type == E.EV_KEY and ev.value == 1 and ev.code in keymap:
                        GLib.idle_add(app.button, keymap[ev.code])
                    elif ev.type == E.EV_KEY and ev.code in (E.BTN_DPAD_LEFT, E.BTN_DPAD_RIGHT, E.BTN_DPAD_UP, E.BTN_DPAD_DOWN):
                        dirn = {E.BTN_DPAD_LEFT: (-1, 0), E.BTN_DPAD_RIGHT: (1, 0), E.BTN_DPAD_UP: (0, -1),
                                E.BTN_DPAD_DOWN: (0, 1)}[ev.code]
                        if ev.value:
                            held[dirn] = now + 0.38; GLib.idle_add(app.nav, *dirn)
                        else:
                            held.pop(dirn, None)
                    elif ev.type == E.EV_ABS and ev.code in (E.ABS_HAT0X, E.ABS_HAT0Y):
                        for k in [k for k in held if (k[0] if ev.code == E.ABS_HAT0X else k[1])]:
                            held.pop(k, None)
                        if ev.value:
                            dirn = (ev.value, 0) if ev.code == E.ABS_HAT0X else (0, ev.value)
                            held[dirn] = now + 0.38; GLib.idle_add(app.nav, *dirn)
                    elif ev.type == E.EV_ABS and ev.code in (E.ABS_X, E.ABS_Y):
                        v = 1 if ev.value > 22000 else -1 if ev.value < -22000 else 0
                        k = "x" if ev.code == E.ABS_X else "y"
                        if v != axes[k]:
                            old = (axes[k], 0) if k == "x" else (0, axes[k])
                            held.pop(old, None)
                            axes[k] = v
                            if v:
                                dirn = (v, 0) if k == "x" else (0, v)
                                held[dirn] = now + 0.38; GLib.idle_add(app.nav, *dirn)
                    elif ev.type == E.EV_ABS and ev.code in (E.ABS_Z, E.ABS_RZ):
                        name = "LT" if ev.code == E.ABS_Z else "RT"
                        hi = d.absinfo(ev.code).max or 255
                        on = ev.value > hi * 0.6
                        if on and not trig[name]:
                            GLib.idle_add(app.button, name)
                        trig[name] = on
            except OSError:
                pass
        for dirn, t in list(held.items()):
            if now >= t:
                held[dirn] = now + 0.09; GLib.idle_add(app.nav, *dirn)


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "home"
    app = Music(start)
    app.show_all()
    threading.Thread(target=input_thread, args=(app,), daemon=True).start()
    Gtk.main()


if __name__ == "__main__":
    main()
