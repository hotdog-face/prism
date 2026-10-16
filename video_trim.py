#!/usr/bin/env python3
"""Trim the fade-from-black at the start of game preview clips (2026-10-08: previews "pop to black").

Most ScreenScraper clips open on black and fade in over ~0.6 s; their keyframes are ~4 s apart, so the clip is
re-encoded from just after the fade (GPU h264_vaapi, libx264 fallback; audio AAC). Only clips whose first frame is
near black and that brighten within ~1.5 s are touched. Originals go to Emulation/_video_originals/<system>/.
Progress: ~/prism/video_trim_done.json. Runs inside the nightly job with a time budget:
  video_trim.py [--minutes=60] [--dry] [--limit=N] [file ...]
"""
import prism_config  # noqa: E402  (paths)
import glob, json, os, shutil, subprocess, sys, time

MEDIA = f"{prism_config.TOOLS}/downloaded_media"
BACKUP = f"{prism_config.EMU}/_video_originals"
DONE = f"{prism_config.PRISM}/video_trim_done.json"
LOG = f"{prism_config.PRISM}/video_trim.log"
args = {a.split("=", 1)[0]: (a.split("=", 1)[1] if "=" in a else True) for a in sys.argv[1:] if a.startswith("--")}
files = [a for a in sys.argv[1:] if not a.startswith("--")]
BUDGET = float(args.get("--minutes", 60)) * 60
DRY = "--dry" in args
LIMIT = int(args.get("--limit", 0) or 0)


def log(m):
    line = time.strftime("%m-%d %H:%M:%S ") + m
    print(line, flush=True)
    open(LOG, "a").write(line + "\n")


def run(cmd, timeout=120):
    return subprocess.run(["nice", "-n", "15", "ionice", "-c3"] + cmd, capture_output=True, text=True, timeout=timeout)


def brightness(f, seconds=2.0, fps=10):
    r = run(["ffmpeg", "-v", "error", "-t", str(seconds), "-i", f, "-vf",
             f"fps={fps},signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-", "-an", "-f", "null", "-"], 60)
    return [float(l.split("=", 1)[1]) for l in r.stdout.splitlines() if "YAVG=" in l]


def fade_end(f):
    """Seconds of black / fade-in at the start, or 0 if the clip starts on a picture."""
    y = brightness(f)
    if len(y) < 15 or y[0] > 24:
        return 0.0
    target = max(y[12:20] or y[-3:])          # how bright the picture is once it's up (1.2-2.0 s)
    if target < 40:
        return 0.0                            # a dark game, not a fade
    for i, v in enumerate(y):
        if v >= 0.85 * target:
            return round(i / 10 + 0.05, 2) if i >= 2 else 0.0
    return 0.0


def duration(f):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f], 30)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def encode(src, dst, start):
    base = ["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-i", src, "-map", "0:v:0", "-map", "0:a:0?",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]
    gpu = ["ffmpeg", "-v", "error", "-y", "-vaapi_device", "/dev/dri/renderD128", "-ss", str(start), "-i", src,
           "-map", "0:v:0", "-map", "0:a:0?", "-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-qp", "20",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", dst]
    r = run(gpu, 300)
    if r.returncode == 0 and os.path.getsize(dst) > 10000:
        return "vaapi"
    r = run(base + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", dst], 600)
    return "x264" if r.returncode == 0 else None


def main():
    try:
        done = json.load(open(DONE))
    except (OSError, ValueError):
        done = {}
    todo = files or sorted(glob.glob(f"{MEDIA}/*/videos/*.mp4"))
    t0, n_trim, n_seen = time.time(), 0, 0
    for f in todo:
        if time.time() - t0 > BUDGET or (LIMIT and n_seen >= LIMIT):
            break
        key = os.path.relpath(f, MEDIA)
        if key in done and not files:
            continue
        n_seen += 1
        try:
            cut = fade_end(f)
        except Exception as e:
            done[key] = f"error: {e}"; continue
        if cut <= 0:
            done[key] = "ok"; continue
        before = duration(f)
        if before > 150:
            done[key] = "long, skipped"; continue      # longplays: not worth re-encoding minutes of video
        if DRY:
            log(f"would trim {cut:.2f}s from {key} ({before:.1f}s)"); n_trim += 1; continue
        tmp = f[:-4] + ".trim.mp4"
        how = encode(f, tmp, cut)
        after = duration(tmp) if how else 0
        if not how or after < max(5.0, before - cut - 1.0):
            log(f"FAILED {key}: encoder={how} {before:.1f}s -> {after:.1f}s")
            try:
                os.remove(tmp)
            except OSError:
                pass
            done[key] = "failed"; continue
        bdir = os.path.join(BACKUP, os.path.dirname(key))
        os.makedirs(bdir, exist_ok=True)
        shutil.move(f, os.path.join(bdir, os.path.basename(f)))
        os.replace(tmp, f)
        done[key] = f"trimmed {cut:.2f}s ({how})"; n_trim += 1
        log(f"trimmed {cut:.2f}s from {key} ({how}, {before:.1f}s -> {after:.1f}s)")
        if n_trim % 20 == 0:
            json.dump(done, open(DONE, "w"))
    json.dump(done, open(DONE, "w"))
    left = sum(1 for f in glob.glob(f"{MEDIA}/*/videos/*.mp4") if os.path.relpath(f, MEDIA) not in done)
    log(f"video trim: {n_seen} checked, {n_trim} {'would be ' if DRY else ''}trimmed in {(time.time() - t0) / 60:.0f} min, {left} left")


if __name__ == "__main__":
    main()
