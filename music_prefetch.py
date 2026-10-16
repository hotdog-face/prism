#!/usr/bin/env python3
"""Warm the Music app art cache (album + artist art at the sizes the UI uses)."""
import prism_config  # noqa: E402  (paths)
import hashlib, json, os, urllib.parse, urllib.request, concurrent.futures
H=os.path.expanduser("~"); C=json.load(open(f"{prism_config.PRISM}/plex.json")); CACHE=f"{H}/.cache/deck-music"
os.makedirs(CACHE, exist_ok=True)
lib=json.load(open(f"{prism_config.PRISM}/music/library.json"))
thumbs={a["thumb"] for a in lib["albums"]+lib["artists"] if a.get("thumb")} | {t["thumb"] for t in lib["tracks"] if t.get("thumb")}
jobs=[(t,s) for t in thumbs for s in (300,120)]
def get(job):
    t,s=job; key=(str(t)+str(s)).encode(); p=CACHE+"/"+hashlib.md5(key).hexdigest()+".jpg"
    if os.path.exists(p) and os.path.getsize(p)>0: return 1
    try:
        u=C["url"]+"/photo/:/transcode?"+urllib.parse.urlencode({"width":s,"height":s,"minSize":1,"url":t,"X-Plex-Token":C["token"]})
        d=urllib.request.urlopen(u,timeout=60).read(); open(p,"wb").write(d); return 1
    except Exception: return 0
with concurrent.futures.ThreadPoolExecutor(4) as ex: ok=sum(ex.map(get,jobs))
print(f"cached {ok}/{len(jobs)}")
