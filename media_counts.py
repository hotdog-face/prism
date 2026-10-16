#!/usr/bin/env python3
"""Count pills for the media tiles on the Prism row (2026-10-08). ES-DE's gamecount says "5929 games" for TV
(it counts episode entries); this writes switch/screens/_count_<system>.xml that hides gameCount for that system
and shows the right words instead: "111 shows · 5,929 episodes", "458 movies", "3,872 songs · 138 albums".
Run by esde_pending.py (the pills only change on an ES-DE restart)."""
import prism_config  # noqa: E402  (paths)
import json, os, urllib.request
H = os.path.expanduser("~")
OUT = f"{prism_config.THEME_DIR}/switch/screens"
PILL = """<theme>
    <view name="system">
        <text name="gameCount"><visible>false</visible></text>
        <text name="mediaCount">
            <pos>0.5 0.765</pos>
            <size>0.30 0.05</size>
            <origin>0.5 0.5</origin>
            <text>{text}</text>
            <fontPath>./../NotoSans-Regular.ttf</fontPath>   <!-- relative to screens/ (the theme fontBody variable resolved to screens/switch/) -->
            <fontSize>0.03</fontSize>
            <horizontalAlignment>center</horizontalAlignment>
            <color>${{inkSoft}}</color>
            <backgroundColor>${{cardSolid}}</backgroundColor>
            <backgroundCornerRadius>0.02</backgroundCornerRadius>
            <zIndex>50</zIndex>
        </text>
    </view>
</theme>
"""

def plex(path, count_only=False):
    c = json.load(open(f"{prism_config.PRISM}/plex.json"))
    sep = "&" if "?" in path else "?"
    page = (sep + "X-Plex-Container-Start=0&X-Plex-Container-Size=0") if count_only else ""
    req = urllib.request.Request(c["url"].rstrip("/") + path + page,
                                 headers={"X-Plex-Token": c["token"], "Accept": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=20))["MediaContainer"]

def n(x, one, many):
    return f"{x:,} {one if x == 1 else many}"

def main():
    texts = {"media": "Ask for any movie or show"}
    try:
        secs = plex("/library/sections").get("Directory", [])
        mov = next((s for s in secs if s["type"] == "movie" and s["title"] == "Movies"), None)
        tv = next((s for s in secs if s["type"] == "show" and s["title"] == "TV Shows"), None)
        if mov:
            texts["movies"] = n(plex(f"/library/sections/{mov['key']}/all?type=1", True)["totalSize"], "movie", "movies")
        if tv:
            shows = plex(f"/library/sections/{tv['key']}/all?type=2", True)["totalSize"]
            eps = plex(f"/library/sections/{tv['key']}/all?type=4", True)["totalSize"]
            texts["tv"] = f"{n(shows, 'show', 'shows')} · {n(eps, 'episode', 'episodes')}"
    except Exception as e:
        print("plex unavailable, keeping old movie/TV pills:", e)
    try:
        lib = json.load(open(f"{prism_config.PRISM}/music/library.json"))
        texts["music"] = f"{n(len(lib['tracks']), 'song', 'songs')} · {n(len(lib['albums']), 'album', 'albums')}"
    except Exception as e:
        print("music library unavailable:", e)
    for sysname, text in texts.items():
        p = f"{OUT}/_count_{sysname}.xml"
        open(p + ".tmp", "w").write(PILL.format(text=text.replace("&", "&amp;")))
        os.replace(p + ".tmp", p)
        print(f"{sysname}: {text}")

if __name__ == "__main__":
    main()
