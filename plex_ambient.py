#!/usr/bin/env python3
"""Ambient backgrounds for the Movies / TV cover flow (2026-10-08: the sharp backdrop behind the posters
looked like the cover repeated and popped from one to the next). For every backdrop / episode still in
downloaded_media/{movies,tv}/screenshots this writes a soft, blurred, slightly darker version at the same path under
fanart/ (the theme's background uses fanart). Runs after plex_sync.py downloads; only new or changed images.
  plex_ambient.py [--force]"""
import prism_config  # noqa: E402  (paths)
import os, subprocess, sys, time

MEDIA = f"{prism_config.TOOLS}/downloaded_media"
VF = "scale=480:-2,gblur=sigma=12,eq=brightness=-0.05:saturation=1.25,scale=960:-2"


def make(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = f"{dst}.{os.getpid()}.tmp.jpg"   # per process: a sync and a batch run may overlap
    r = subprocess.run(["nice", "-n", "15", "ffmpeg", "-v", "error", "-y", "-i", src, "-vf", VF, "-q:v", "4", tmp],
                       capture_output=True, timeout=60)
    try:
        if r.returncode == 0 and os.path.getsize(tmp) > 2000:
            os.replace(tmp, dst)        # also breaks the old hard link to the sharp backdrop
            return True
    except OSError:
        pass
    try:
        os.remove(tmp)
    except OSError:
        pass
    return False


def main():
    force = "--force" in sys.argv
    t0, n, skipped = time.time(), 0, 0
    for system in ("movies", "tv"):
        base = f"{MEDIA}/{system}/screenshots"
        for root, _, files in os.walk(base):
            for f in files:
                if not f.endswith(".jpg") or ".tmp." in f:
                    continue
                src = os.path.join(root, f)
                dst = os.path.join(f"{MEDIA}/{system}/fanart", os.path.relpath(src, base))
                try:
                    st = os.stat(dst)
                    fresh = st.st_nlink == 1 and st.st_mtime >= os.path.getmtime(src)
                except OSError:
                    fresh = False
                if fresh and not force:
                    skipped += 1; continue
                n += make(src, dst)
    print(f"ambient backgrounds: {n} made, {skipped} up to date, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
