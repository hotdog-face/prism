#!/usr/bin/env python3
"""A Discover title was picked in Movies / TV (plex_play.py hands discover stubs here): "Request <title>?" card,
then the request goes to Overseerr, which downloads it into Plex like Request Movies always has (2026-10-08).
The "Search & request…" entry opens the full Overseerr app instead."""
import prism_config  # noqa: E402  (paths)
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import overseerr  # noqa: E402


def toast(title, sub):
    try:
        import deck_curate
        deck_curate.cc_request({"cmd": "toast", "title": title, "sub": sub, "cover": ""}, timeout=0.5)
    except Exception:
        pass


def main(stub):
    import prism_features
    if not prism_features.enabled("overseerr"):
        toast("Request Movies is off", "Turn it on in Settings > Extras")
        return
    st = json.load(open(stub))
    if st.get("type") == "overseerr":
        os.execv("/bin/bash", ["bash", f"{HERE}/request_movies.sh"])
    mt, tmdb, title = st["media"], st["tmdb"], st.get("title", "")
    try:
        info = overseerr.details(mt, tmdb).get("mediaInfo") or {}
    except Exception:
        info = {}
    status = info.get("status")
    if status == 5:
        toast("Already in your library", title); return
    if status in (2, 3):
        toast("Already requested", title); return
    kind = "movie" if mt == "movie" else "show (all seasons)"
    p = subprocess.run(["/usr/bin/python3", f"{HERE}/deck_confirm.py", f"Request this {kind}?", title,
                        "It downloads to Plex and shows up in Movies / TV when it's ready.", "Request", "Requesting…"],
                       stdout=subprocess.PIPE, text=True, timeout=120)
    if "CONFIRMED" not in p.stdout:
        return
    try:
        overseerr.request(mt, tmdb)
        open("/tmp/deck_confirm_done", "w").close()
        toast("Requested", f"{title} is on its way to Plex")
        with open(f"{prism_config.PRISM}/plex/requests.log", "a") as f:
            f.write(time.strftime("%F %T ") + f"{mt} {tmdb} {title}\n")
    except Exception as e:
        open("/tmp/deck_confirm_done", "w").close()
        toast("Request failed", str(e)[:60])


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]))
