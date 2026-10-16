#!/usr/bin/env python3
"""console-style game hub (2026-10-08): picking a game opens its hub page (hero art, logo, details, the frame you
left off at, play time) with Play / Load state / Back, while the game loads behind it. Supervised thread in
switchhome.py; ES-DE's game-start script writes /tmp/esde_game_start, deck_cc.py draws the hub ("hub" command).

Behind the hub the game loads, and once its window is up it is frozen (SIGSTOP) until you choose: Play un-freezes it
(instant start), Back ends it without saving (your resume point is untouched), Load state un-freezes and loads it.
Not for Movies / TV / Music / Steam games, nor while boot tests, clip recording or the self-test drive the Deck.
/tmp/game_hub_off turns it off; /tmp/game_hub_skip skips the next launch only (the card's "Back to last save")."""
import prism_config  # noqa: E402  (paths)
import glob, json, os, re, signal, socket, subprocess, threading, time
from datetime import datetime

EVENT = "/tmp/esde_game_start"
READY = "/tmp/game_hub_ready"
SKIP_SYSTEMS = {"movies", "tv", "music", "media", "foryou", "steam", "desktop", "emulators"}
MEDIA = f"{prism_config.TOOLS}/downloaded_media"
SHOTS = f"{prism_config.PRISM}/resume_shots"
GL = f"{prism_config.ESDE}/gamelists"
BUSY = ("game_tester.py", "record_clips.py", "selftest/run.py", "continuous_run.py")


def busy():
    if os.path.exists("/tmp/game_hub_off") or os.path.exists(f"{prism_config.PRISM}/party_mode"):
        return True
    out = subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True).stdout
    return any(b in out for b in BUSY)


def media(system, rom, kinds):
    base = os.path.basename(rom.rstrip("/"))
    for kind in kinds:
        for stem in (base, os.path.splitext(base)[0]):
            for f in glob.glob(f"{MEDIA}/{system}/{kind}/{glob.escape(stem)}.*"):
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    return f
    return ""


def entry(system, rom):
    """The game's gamelist entry as a dict (desc, releasedate, genre, players, lastplayed, playtime...)."""
    try:
        txt = re.sub(r"^\s*<\?xml[^>]*\?>", "", open(f"{GL}/{system}/gamelist.xml", encoding="utf-8", errors="replace").read())
        import xml.etree.ElementTree as ET
        root = ET.fromstring(f"<root>{txt}</root>")
    except Exception:
        return {}
    base = os.path.basename(rom.rstrip("/"))
    for g in root.iter("game"):
        if os.path.basename((g.findtext("path") or "").rstrip("/")) == base:
            return {c.tag: (c.text or "") for c in g}
    return {}


def ago(stamp):
    try:
        t = datetime.strptime(stamp[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return ""
    days = (datetime.now() - t).days
    return "today" if days < 1 else "yesterday" if days == 1 else f"{days} days ago"


def hub_data(rom, name, system, fullname):
    e = entry(system, rom)
    year = (e.get("releasedate") or "")[:4]
    players = e.get("players", "")
    meta = " · ".join(x for x in (fullname or system.upper(), year, (e.get("genre") or "").split("/")[0].strip(),
                                  f"{players} players" if players and players not in ("1", "") else "") if x)
    played = []
    if e.get("lastplayed"):
        played.append("Last played " + ago(e["lastplayed"]))
    try:
        secs = int(e.get("playtime") or 0)
        if secs >= 60:
            played.append(f"{secs // 3600} h {secs % 3600 // 60} min played" if secs >= 3600 else f"{secs // 60} min played")
    except ValueError:
        pass
    shot = f"{SHOTS}/{system}/{os.path.basename(rom.rstrip('/'))}.png"
    return {"name": e.get("name") or name, "meta": meta, "desc": e.get("desc", ""),
            "hero": media(system, rom, ("fanart", "screenshots", "titlescreens", "covers")),
            "logo": media(system, rom, ("marquees",)), "cover": media(system, rom, ("covers", "3dboxes")),
            "resume": shot if os.path.exists(shot) else "",
            "resume_title": "Where you left off" if os.path.exists(shot) else "Box art",
            "played": " · ".join(played)}


def focused_window():
    try:
        out = subprocess.run(["xprop", "-root", "GAMESCOPE_FOCUSED_WINDOW"], env={**os.environ, "DISPLAY": ":0"},
                             capture_output=True, text=True, timeout=2).stdout
        return out.strip().split("=")[-1].strip()
    except Exception:
        return None


def show(d, ev):
    rom = re.sub(r"\\(.)", r"\1", ev["rom"])
    system, name = ev["system"], ev["name"]
    fullname = ""
    try:
        parts = open("/tmp/esde_selected", "rb").read().decode(errors="replace").split("\0")
        fullname = parts[3] if len(parts) > 3 else ""
    except OSError:
        pass
    data = hub_data(rom, name, system, fullname)
    states = d.retroarch_states(rom)
    items = [("PLAY", "Play", "", "play")]
    if states:
        items.append(("LOADSTATE", "Load state", f"{len(states)} saved", "load"))
    items.append(("BACK", "Back", "", "close"))
    try:
        os.remove(READY)
    except OSError:
        pass
    st = {"frozen": False, "chosen": None, "pid": None}
    win0 = focused_window()

    def freezer():
        """Once the game's window is up (and RetroArch has loaded its resume state), freeze it until a choice."""
        t0 = time.time()
        while st["chosen"] is None and time.time() - t0 < 90:
            pid = d.esde_pid()
            if pid and d.game_running(pid):
                st["pid"] = pid
                w = focused_window()
                if w and w != win0:
                    time.sleep(2.0)
                    if st["chosen"] is None:
                        d.signal_game(pid, signal.SIGSTOP); st["frozen"] = True
                        open(READY, "w").close()
                    return
            time.sleep(0.15)
    threading.Thread(target=freezer, daemon=True).start()
    d.hub_open[0] = True
    try:
        conn = d.cc_request({"cmd": "hub", "name": data["name"], "system": system, "items": items,
                             "states": [{k: v for k, v in s.items() if k != "mtime"} for s in states], "hub": data},
                            keep=True, timeout=2)
        conn.settimeout(900)
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(64)
            if not chunk:
                break
            buf += chunk
        choice = buf.split(b"\n")[0].decode().strip() or "PLAY"
        conn.close()
    except (OSError, socket.timeout) as e:
        d.log(f"hub: {e}"); choice = "PLAY"
    finally:
        d.hub_open[0] = False
    st["chosen"] = choice
    d.log(f"hub: {choice} ({name})")
    pid = st["pid"] or d.esde_pid()
    if choice == "BACK":
        for _ in range(100):                       # the game may still be starting: end it as soon as it is there
            if pid and d.game_running(pid):
                break
            time.sleep(0.2); pid = d.esde_pid()
        d.kill_game_nosave(pid)                    # SIGCONT + SIGKILL: nothing saved, resume point untouched
        return
    if st["frozen"] and pid:
        d.signal_game(pid, signal.SIGCONT)
    if choice.startswith("LOAD:"):
        slot = choice.split(":", 1)[1]
        for _ in range(100):
            if pid and d.game_running(pid):
                break
            time.sleep(0.2); pid = d.esde_pid()
        time.sleep(1.5 if st["frozen"] else 4)
        d.ra_goto_slot(pid, -1 if slot == "auto" else int(slot)); d.retroarch_cmd("LOAD_STATE")
        d.toast("State loaded", next((s["label"] for s in states if str(s["slot"]) == slot), ""))


def run():
    import deck_curate as d
    try:
        last = os.path.getmtime(EVENT)              # an event from before this started doesn't count
    except OSError:
        last = 0
    while True:
        time.sleep(0.05)
        try:
            m = os.path.getmtime(EVENT)
        except OSError:
            continue
        if m == last:
            continue
        last = m
        try:
            ev = json.load(open(EVENT))
        except (OSError, ValueError):
            continue
        if ev.get("system") in SKIP_SYSTEMS or busy():
            continue
        if os.path.exists("/tmp/game_hub_skip"):
            os.remove("/tmp/game_hub_skip"); continue
        try:
            show(d, ev)
        except Exception as e:
            d.log(f"hub failed: {e}")
