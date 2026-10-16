#!/usr/bin/env python3
"""Boot-test every game the way ES-DE would launch it, and record whether it shows a picture.

For each game: build ES-DE's launch command (its bundled es_systems/es_find_rules, EmuDeck's custom rules and the
per-system emulator chosen in ES-DE), queue it in ~/prism/tester_next.sh, start the hidden "Game Tester" Steam
shortcut (so Game Mode actually shows it), wait, take a gamescope screenshot, then close the game.
Verdicts: ok (picture), black (nothing on screen), crashed (exited early), nolaunch (couldn't build a command).
Results append to ~/prism/test_results.jsonl (resumable); thumbnails go to ~/prism/test_shots/<system>/.
Usage: game_tester.py [--retest] [system ...]   (default: all, riskiest systems first; --retest also re-runs
games whose last verdict wasn't ok)
"""
import prism_config  # noqa: E402  (paths)
import glob
import html
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

HOME = os.path.expanduser("~")
EMU = prism_config.EMU
ROMS = f"{EMU}/roms"
RES = f"{prism_config.PRISM}/test_results.jsonl"
SHOTS = f"{prism_config.PRISM}/test_shots"
NEXT = f"{prism_config.PRISM}/tester_next.sh"
TESTER_APPID = int(prism_config.TESTER_APPID or 0)
ESDE_RES = prism_config.ESDE_RESOURCES       # ES-DE's bundled systems + find rules (from the extracted app)
ENV = {"DISPLAY": ":0", "XDG_RUNTIME_DIR": "/run/user/1000", "GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0",
       "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
WAIT = {"psvita": 45, "ps2": 45, "gc": 40, "wii": 40, "switch": 90, "psp": 30, "dreamcast": 30, "saturn": 30,
        "neogeocd": 30, "model2": 30, "psx": 25, "segacd": 20, "n64": 15, "nds": 15}
ORDER = ["psvita", "ps2", "gc", "wii", "psp", "dreamcast", "saturn", "segacd", "neogeocd", "psx", "n64", "nds",
         "model2", "sega32x", "pcengine", "neogeo", "arcade", "mame", "cps1", "cps2", "snes", "satellaview",
         "genesis", "nes", "gba", "gbc", "msx", "ngpc", "wonderswancolor", "virtualboy", "switch"]


def load_xml(path):
    return ET.parse(path).getroot() if os.path.exists(path) else None


def systems():
    out = {}
    for root in (load_xml(f"{ESDE_RES}/es_systems.xml"), load_xml(f"{prism_config.ESDE}/custom_systems/es_systems.xml")):
        if root is None:
            continue
        for s in root.findall("system"):
            out[s.findtext("name")] = s
    return out


def find_rules():
    emus, cores = {}, {}
    for root in (load_xml(f"{ESDE_RES}/es_find_rules.xml"), load_xml(f"{prism_config.ESDE}/custom_systems/es_find_rules.xml")):
        if root is None:
            continue
        for e in root.findall("emulator"):
            emus[e.get("name")] = e          # custom file comes last and wins
        for c in root.findall("core"):
            cores[c.get("name")] = c
    return emus, cores


SYS = systems()
EMUS, CORES = find_rules()


def resolve_emulator(name):
    e = EMUS.get(name)
    if e is None:
        return None
    for rule in e.findall("rule"):
        for ent in rule.findall("entry"):
            p = os.path.expanduser(ent.text.replace("%ESPATH%", f"{HOME}/Applications"))
            if rule.get("type") == "systempath":
                w = shutil.which(p)
                if w:
                    return w
            elif rule.get("type") == "staticpath":
                hits = sorted(glob.glob(p))
                if hits:
                    return hits[-1]
    return None


def resolve_core(name):
    c = CORES.get(name)
    for rule in (c.findall("rule") if c is not None else []):
        for ent in rule.findall("entry"):
            p = os.path.expanduser(ent.text)
            if os.path.isdir(p):
                return p
    return None


ARCADE_CORES = f"{prism_config.PRISM}/arcade_cores.json"   # per-game emulator that works for an arcade set (rom -> label)
ARCADE_FALLBACKS = ["MAME 2003-Plus", "FB Alpha 2012", "FinalBurn Neo"]   # older/other cores for older romsets
FALLBACK_SYSTEMS = {"arcade", "mame", "cps1", "cps2", "cps3"}
CORES_DIR = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/cores"


def core_key(system, rom):
    """arcade_cores.json key: the file name for arcade (kept for older entries), "system/file" for the others."""
    base = os.path.basename(rom)
    return base if system == "arcade" else f"{system}/{base}"


def usable(system, label):
    """The label exists for this system and its RetroArch core is installed."""
    cmd = label_command(system, label)
    if not cmd:
        return False
    m = re.search(r"%CORE_RETROARCH%/(\S+?\.so)", cmd)
    return not m or os.path.exists(f"{CORES_DIR}/{m.group(1)}")


def label_command(system, label):
    s = SYS.get(system)
    for c in (s.findall("command") if s is not None else []):
        if c.get("label") == label:
            return c.text
    return None


def chosen_command(system, rom=None):
    s = SYS.get(system)
    if s is None:
        return None
    cmds = s.findall("command")
    if rom:   # per-game emulator: arcade_cores.json (tester's own findings) or the gamelist's <altemulator>
        try:
            lab = json.load(open(ARCADE_CORES)).get(core_key(system, rom)) if system in FALLBACK_SYSTEMS else None
        except (OSError, ValueError):
            lab = None
        if not lab:
            gl = f"{prism_config.ESDE}/gamelists/{system}/gamelist.xml"
            if os.path.exists(gl):
                t = open(gl, errors="replace").read()
                m = re.search(r"<path>\./" + re.escape(html.escape(os.path.basename(rom), quote=False)) +
                              r"</path>(?:(?!</game>).)*?<altemulator>([^<]+)</altemulator>", t, re.S)
                lab = m.group(1) if m else None
        if lab and label_command(system, lab):
            return label_command(system, lab)
    gl = f"{prism_config.ESDE}/gamelists/{system}/gamelist.xml"
    if os.path.exists(gl):
        m = re.search(r"<alternativeEmulator>\s*<label>([^<]+)</label>", open(gl, errors="replace").read())
        if m:
            for c in cmds:
                if c.get("label") == m.group(1):
                    return c.text
    return cmds[0].text if cmds else None


def build(system, rom):
    tpl = chosen_command(system, rom)
    if not tpl:
        return None, None
    if os.path.isdir(rom):                                   # "Game.m3u" folder = launch the file inside it
        rom = os.path.join(rom, os.path.basename(rom))
    base = os.path.splitext(os.path.basename(rom))[0]
    cmd = tpl
    for flag in ("%HIDEWINDOW%", "%RUNINBACKGROUND%", "%ESCAPESPECIALS%", "%ENABLESHORTCUTS%"):
        cmd = cmd.replace(flag, "")
    startdir = None
    m = re.search(r"%STARTDIR%=(\S+)", cmd)
    if m:
        startdir = m.group(1)
        cmd = cmd.replace(m.group(0), "")
    m = re.search(r"%INJECT%=(\S+)", cmd)
    if m:
        f = m.group(1).replace("%BASENAME%", base)
        f = os.path.join(os.path.dirname(rom), f) if not f.startswith("/") else f
        cmd = cmd.replace(m.group(0), open(f).read().strip() if os.path.exists(f) else "")
    for em in re.findall(r"%EMULATOR_([A-Z0-9-]+)%", cmd):
        p = resolve_emulator(em)
        if not p:
            return None, f"emulator {em} not found"
        cmd = cmd.replace(f"%EMULATOR_{em}%", shlex.quote(p))
    for co in re.findall(r"%CORE_([A-Z0-9-]+)%", cmd):
        p = resolve_core(co)
        if not p:
            return None, f"core path {co} not found"
        cmd = cmd.replace(f"%CORE_{co}%", p)
    m = re.search(r"-L (\S+_libretro\.so)", cmd)
    if m and not os.path.exists(m.group(1)):
        return None, f"core missing {os.path.basename(m.group(1))}"
    cmd = (cmd.replace("%ROMRAW%", rom).replace("%ROM%", shlex.quote(rom)).replace("%BASENAME%", shlex.quote(base))
              .replace("%FILENAME%", shlex.quote(os.path.basename(rom))).replace("%GAMEDIR%", shlex.quote(os.path.dirname(rom)))
              .replace("%ROMPATH%", ROMS).replace("%ESPATH%", f"{HOME}/Applications"))
    if startdir:
        startdir = startdir.replace("%GAMEDIR%", os.path.dirname(rom)).replace("%EMUDIR%", "")
    return cmd.strip(), startdir


def games(system):
    d = f"{ROMS}/{system}"
    exts = set()
    s = SYS.get(system)
    if s is not None:
        exts = {e.lower() for e in (s.findtext("extension") or "").split()}
    out = []
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        p = f"{d}/{n}"
        ext = os.path.splitext(n)[1].lower()
        if n.startswith(".") or n in ("systeminfo.txt", "metadata.txt", "gamelist.xml"):
            continue
        if ext in exts and (os.path.isfile(p) or (os.path.isdir(p) and os.path.exists(os.path.join(p, n)))):
            out.append(p)
    return out


def descendants(pid):
    kids, todo = [], [pid]
    while todo:
        p = todo.pop()
        for c in glob.glob(f"/proc/{p}/task/*/children"):
            try:
                for k in open(c).read().split():
                    kids.append(int(k)); todo.append(int(k))
            except OSError:
                pass
    return kids


def reaper_pid():
    for c in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            a = open(c, "rb").read().split(b"\0")
        except OSError:
            continue
        if a and a[0].endswith(b"reaper") and f"AppId={TESTER_APPID}".encode() in a:
            return int(c.split("/")[2])
    return None


def stop_game():
    r = reaper_pid()
    if not r:
        return
    pids = descendants(r)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for p in reversed(pids):
            try:
                os.kill(p, sig)
            except OSError:
                pass
        for _ in range(10):
            if not reaper_pid():
                return
            time.sleep(0.5)


def kill_leftovers(rom):
    """Emulators that daemonize (Eden's AppImage) escape the reaper's process tree; two Eden test launches ran for
    8 hours on 2026-10-07. After a test, end anything still running this ROM."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", "replace")
        except OSError:
            continue
        if rom in cmd:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.kill(int(pid), sig); time.sleep(2)
                except OSError:
                    break


def screenshot(dest_thumb):
    png = "/tmp/tester_shot.png"
    if os.path.exists(png):
        os.remove(png)
    subprocess.run(["gamescopectl", "screenshot", png], env={**os.environ, **ENV}, capture_output=True, timeout=10)
    for _ in range(20):
        if os.path.exists(png) and os.path.getsize(png) > 0:
            break
        time.sleep(0.25)
    else:
        return None
    time.sleep(0.3)
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", png, "-vf", "scale=80:50,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    os.makedirs(os.path.dirname(dest_thumb), exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", png, "-vf", "scale=320:-1", "-q:v", "6", dest_thumb], capture_output=True)
    if not raw:
        return None
    bright = sum(1 for b in raw if b > 24) / len(raw)
    levels = len({b // 16 for b in raw})
    return {"bright": round(bright, 3), "levels": levels}


QUIET = f"{prism_config.PRISM}/quiet_hours"          # e.g. "00:30-09:00": no test launches in that window (local time)


LAST_INPUT = "/tmp/deck_last_input"   # written by deck_curate.py on real buttons/touch
NOW_STARTED = time.time()


def last_input():
    try:
        return os.path.getmtime(LAST_INPUT)
    except OSError:
        return 0.0


def idle_seconds():
    return time.time() - last_input()


def esde_in_use():
    """ES-DE is open and somebody may be using it. After 10 minutes of screensaver it counts as idle and is closed."""
    if subprocess.run(["pgrep", "-x", "es-de"], capture_output=True).returncode != 0:
        return False
    flag = "/tmp/esde_screensaver"
    if os.path.exists(flag) and time.time() - os.path.getmtime(flag) > 600:
        subprocess.run(["pkill", "-TERM", "-x", "es-de"])
        time.sleep(5)
        return False
    return True


NIGHT = "--night" in sys.argv     # nightly charger run (esde_night_scrape.py): quiet hours waived by the owner, time-boxed
NOW_MODE = "--now" in sys.argv or NIGHT   # no idle wait, short cooldown (still yields to real input)
HOURS = float(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--hours=")), "0") or 0)
DEADLINE = NOW_STARTED + HOURS * 3600 if HOURS else None


def on_charger():
    try:
        return open("/sys/class/power_supply/ACAD/online").read().strip() == "1"
    except OSError:
        return True


def quiet_wait():
    """Pause during quiet hours and while ES-DE is in use, and leave a short cooldown between games."""
    time.sleep(3 if NOW_MODE else 8)
    if DEADLINE and time.time() > DEADLINE:
        print("time box reached, stopping", flush=True)
        sys.exit(0)
    if NIGHT and not on_charger():
        print("unplugged, stopping", flush=True)
        sys.exit(0)
    if NOW_MODE and last_input() < NOW_STARTED:
        if subprocess.run(["pgrep", "-x", "es-de"], capture_output=True).returncode == 0:
            subprocess.run(["pkill", "-TERM", "-x", "es-de"]); time.sleep(5)
    else:
        while esde_in_use() or idle_seconds() < 1200:   # someone used the Deck in the last 20 minutes
            time.sleep(60)
    while os.path.exists(QUIET) and not NIGHT:
        try:
            a, b = open(QUIET).read().strip().split("-")
            now = time.strftime("%H:%M")
            inside = (a <= now < b) if a < b else (now >= a or now < b)
        except ValueError:
            return
        if not inside:
            return
        time.sleep(300)


STATE_DIRS = [f"{prism_config.SAVES}/retroarch/states",
              f"{prism_config.SAVES}/duckstation/states",
              f"{prism_config.SAVES}/pcsx2/states"]


RESUME_BAK = f"{prism_config.PRISM}/.resume_bak"   # copies of the player's resume states (see snapshot_resume)


def _resume_files():
    for d in STATE_DIRS:
        for root, _, files in os.walk(d):
            for f in files:
                if ".state.auto" in f or "_resume.sav" in f or f.endswith(".resume.p2s"):
                    yield os.path.join(root, f)


def snapshot_resume():
    """Before a test: copy the player's resume states (only ones changed since their last copy). A test launch loads
    the player's resume point and writes a new one on quit; clean_resume_states() then puts the player's back."""
    import shutil
    live = set()
    for p in _resume_files():
        live.add(p)
        b = RESUME_BAK + p
        try:
            if not os.path.exists(b) or os.path.getmtime(b) != os.path.getmtime(p):
                os.makedirs(os.path.dirname(b), exist_ok=True); shutil.copy2(p, b)
        except OSError:
            pass
    for root, _, files in os.walk(RESUME_BAK):   # forget copies of states the player no longer has
        for f in files:
            b = os.path.join(root, f)
            if b[len(RESUME_BAK):] not in live:
                try:
                    os.remove(b)
                except OSError:
                    pass


def clean_resume_states(since):
    """After a test: resume states written by the test launch are put back to the player's copy (or removed if the
    player had none), so the player's next launch resumes where THEY left off, not at the tester's point."""
    import shutil
    for p in list(_resume_files()):
        try:
            if os.path.getmtime(p) < since - 1:
                continue
            b = RESUME_BAK + p
            if os.path.exists(b):
                shutil.copy2(b, p)
            else:
                os.remove(p)
        except OSError:
            pass


MEDIA = f"{prism_config.TOOLS}/downloaded_media"
IMAGE_KINDS = ("screenshots", "titlescreens", "miximages", "covers", "3dboxes")


def fallback_screenshot(system, rom):
    """A game with no picture at all gets the tester's own capture as its screenshot (every game shows something).
    ES-DE names a folder entry's media after the full folder name (Game.m3u)."""
    stem = os.path.basename(rom) if os.path.isdir(rom) else os.path.splitext(os.path.basename(rom))[0]
    for k in IMAGE_KINDS:
        if glob.glob(f"{MEDIA}/{system}/{k}/{glob.escape(stem)}.*"):
            return
    os.makedirs(f"{MEDIA}/{system}/screenshots", exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", "/tmp/tester_shot.png", "-vf", "scale=960:-2",
                    f"{MEDIA}/{system}/screenshots/{stem}.png"], capture_output=True)


def test(system, rom, done, _fallback=False):
    key = f"{system}/{os.path.basename(rom)}"
    if key in done:
        return
    if not _fallback:
        quiet_wait()
    cmd, extra = build(system, rom)
    rec = {"system": system, "game": os.path.basename(rom), "time": int(time.time())}
    if not cmd:
        rec.update(verdict="nolaunch", why=extra)
    else:
        snapshot_resume()
        with open(NEXT, "w") as f:
            f.write("#!/bin/bash\n" + "".join(f"export {k}={v}\n" for k, v in ENV.items()))
            f.write("export DECK_TESTER=1\n")   # emulator wrappers skip resume for test launches
            if extra:
                f.write(f"cd {shlex.quote(extra)} 2>/dev/null\n")
            f.write(f"exec {cmd} >/tmp/tester_game.log 2>&1\n")
        os.chmod(NEXT, 0o755)
        subprocess.Popen(["steam", f"steam://rungameid/{(TESTER_APPID << 32) | 0x02000000}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, **ENV})
        t0 = time.time()
        while time.time() - t0 < 20 and not reaper_pid():
            time.sleep(0.5)
        started = bool(reaper_pid())
        wait = WAIT.get(system, 10)
        end = time.time() + wait
        alive = started
        mark = last_input()
        interrupted = False
        while started and time.time() < end:
            if not reaper_pid():
                alive = False
                break
            if last_input() > mark:
                interrupted = True
                break
            time.sleep(1)
        if interrupted:
            stop_game()
            time.sleep(3)
            kill_leftovers(rom)
            clean_resume_states(t0)
            print(f"skipped   {key} (Deck in use)", flush=True)
            return None
        thumb = f"{SHOTS}/{system}/{os.path.splitext(os.path.basename(rom))[0]}.jpg"
        shot = screenshot(thumb) if alive else None
        extra_end = time.time() + 48          # loading screens and intros: look again every 8 s before calling it black
        while alive and (not shot or shot["bright"] < 0.02 or shot["levels"] <= 1) and time.time() < extra_end:
            time.sleep(8)
            if not reaper_pid():
                alive = False
                break
            shot = screenshot(thumb)
        stop_game()
        time.sleep(3)
        kill_leftovers(rom)
        clean_resume_states(t0)
        if not started:
            rec.update(verdict="nolaunch", why="Steam did not start the tester")
        elif not alive:
            tail = open("/tmp/tester_game.log", errors="replace").read()[-300:] if os.path.exists("/tmp/tester_game.log") else ""
            rec.update(verdict="crashed", after=round(time.time() - t0, 1), log=tail)
        elif not shot or shot["bright"] < 0.02 or shot["levels"] <= 1:
            rec.update(verdict="black", shot=shot)
        else:
            rec.update(verdict="ok", shot=shot)
            fallback_screenshot(system, rom)
        time.sleep(2)
    if _fallback:
        return rec
    # arcade: an older/other core often runs an older romset ("required files are missing" in current MAME)
    if system in FALLBACK_SYSTEMS and rec.get("verdict") in ("crashed", "black"):
        base = core_key(system, rom)
        try:
            cores = json.load(open(ARCADE_CORES))
        except (OSError, ValueError):
            cores = {}
        before = cores.get(base)
        for lab in ARCADE_FALLBACKS:
            if lab == before or not usable(system, lab):
                continue
            cores[base] = lab; json.dump(cores, open(ARCADE_CORES, "w"), indent=1)
            r2 = test(system, rom, set(), _fallback=True)
            if r2 and r2.get("verdict") == "ok":
                rec = {**r2, "core": lab}
                break
        else:
            if before: cores[base] = before
            else: cores.pop(base, None)
            json.dump(cores, open(ARCADE_CORES, "w"), indent=1)
    with open(RES, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"{rec['verdict']:9} {key}" + (f" (core: {rec['core']})" if rec.get("core") else ""), flush=True)


def main():
    retest = "--retest" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    last = {}
    if os.path.exists(RES):
        for line in open(RES):
            try:
                r = json.loads(line); last[f"{r['system']}/{r['game']}"] = r["verdict"]
            except (ValueError, KeyError):
                pass
    def has_image(key):
        system, game = key.split("/", 1)
        rom = f"{ROMS}/{system}/{game}" if "ROMS" in globals() else f"{prism_config.ROMS}/{system}/{game}"
        stem = game if os.path.isdir(rom) else os.path.splitext(game)[0]
        return any(glob.glob(f"{MEDIA}/{system}/{k}/{glob.escape(stem)}.*") for k in IMAGE_KINDS)
    # ok games without any picture are run again so the tester can save a screenshot for them
    done = {k for k, v in last.items() if (v == "ok" and has_image(k)) or not retest}
    todo = args or ORDER
    for s in todo:
        for rom in games(s):
            test(s, rom, done)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
