#!/usr/bin/env python3
"""Overseerr (Request Movies) API for Prism's Discover shelves (2026-10-08).
Signs in with the Plex token already on the Deck (Overseerr's own Plex login; the owner's choice), keeps the session
cookie in ~/prism/plex/overseerr_cookie (owner-only)."""
import prism_config  # noqa: E402  (paths)
import json, os, urllib.parse, urllib.request

HOME = os.path.expanduser("~")
URL = (prism_config.get("overseerr_url") or "").rstrip("/")   # config.toml: overseerr_url
COOKIE = f"{prism_config.PRISM}/plex/overseerr_cookie"
TMDB_IMG = "https://image.tmdb.org/t/p/"
STATUS = {2: "Requested", 3: "Requested", 4: "Partly in your library", 5: "In your library"}


def _login():
    tok = json.load(open(f"{prism_config.PRISM}/plex.json"))["token"]
    r = urllib.request.urlopen(urllib.request.Request(f"{URL}/api/v1/auth/plex", data=json.dumps({"authToken": tok}).encode(),
                                                      headers={"Content-Type": "application/json"}), timeout=20)
    sid = next((c.split(";")[0] for c in r.headers.get_all("Set-Cookie") or [] if c.startswith("connect.sid=")), None)
    if not sid:
        raise RuntimeError("Overseerr sign-in gave no session")
    fd = os.open(COOKIE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(sid)
    return sid


def api(path, method="GET", body=None, retry=True):
    try:
        sid = open(COOKIE).read().strip()
    except OSError:
        sid = _login()
    req = urllib.request.Request(f"{URL}/api/v1{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Cookie": sid, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=25))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403) and retry:
            _login()
            return api(path, method, body, retry=False)
        raise


def discover(path, pages=2, **params):
    out, seen = [], set()
    for page in range(1, pages + 1):
        q = urllib.parse.urlencode({**params, "page": page})
        for it in api(f"{path}?{q}").get("results", []):
            if it.get("mediaType") not in ("movie", "tv") or it["id"] in seen:
                continue
            seen.add(it["id"]); out.append(it)
    return out


def request(media_type, tmdb_id):
    body = {"mediaType": media_type, "mediaId": int(tmdb_id)}
    if media_type == "tv":
        body["seasons"] = "all"
    return api("/request", "POST", body)


def details(media_type, tmdb_id):
    return api(f"/{media_type}/{int(tmdb_id)}")
