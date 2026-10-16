#!/usr/bin/env python3
"""Curation buttons for ES-DE on the Steam Deck.

  Tap L4           delete the selected game (Are-you-sure card, A = delete; the owner 2026-10-06: the NAS is the backup).
                   The card (deck_confirm.py) covers the screen while ES-DE's own Delete runs behind it (xdotool keys).
  Press R4         favorite / unfavorite the selected game (sends Y).

On the console row (system view) the theme's round icons map to:
  Y                Controllers (Steam controller settings)
  View             Settings (Steam settings)
  Hold B           Power menu (deck_power_menu.py, drawn over ES-DE)
  Down             cursor onto the round icons (deck_icon_focus.py): Left/Right move, A opens, B/Up back
  L / R            previous / next console (ES-DE only does this in game lists; held = repeat)
Touch (touchscreen read through touch_reader.py with sudo -n):
  console row      tap a tile = scroll to it, tap the middle tile = open, swipe = scroll, tap a round icon = open it
  game list        swipe up/down = scroll, swipe left/right = previous/next console, tap the video = play,
                   tap the console logo (top left) = back to the console row

Only acts while ES-DE is in a game list with nothing launched. ES-DE's game-select / system-select event scripts
(~/ES-DE/scripts/) keep /tmp/esde_selected up to date. Every removal is logged to ~/prism/curation_removed.log
with the files it saved; curation_restore.py puts a game back.

Agents can trigger the same actions: `systemctl --user kill -s USR1 deck-curate` removes the selected game,
USR2 toggles its favorite.

Runs as the systemd user service deck-curate.service. The Deck's controller is read from hidraw, so the back
buttons work whatever Steam Input does with them.
"""
import prism_config  # noqa: E402  (paths)
import glob
import json
import os
import re
import select
import signal
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, prism_config.PRISM)
import deck_mcp  # noqa: E402  (virtual gamepad)
import prism_state  # noqa: E402  (what's running / in front / idle: one implementation, 2026-10-13)
import prism_profile  # noqa: E402  (docked / handheld)

HOME = os.path.expanduser("~")
EMU = prism_config.EMU
ROMS = f"{EMU}/roms"
MEDIA = f"{EMU}/tools/downloaded_media"
REMOVED = f"{EMU}/_Removed games"
GAMELISTS = f"{prism_config.ESDE}/gamelists"
STATE = "/tmp/esde_selected"
LOG = f"{prism_config.PRISM}/curation_removed.log"
CONTROLLER_HID_ID = "000028DE:00001205"
HOLD_S = 0.8
# Byte/bit of each back button in the Deck's 64-byte input report (kernel hid-steam.c).
L4, R4 = (13, 1), (13, 2)
L5 = (9, 7)   # hid-steam.c: byte 9 bit 7
R5 = (10, 0)  # back paddle, right lower: Prism Music play/pause (tap) / next (hold)
STEAM_BTN = (9, 5)
BTN_Y, BTN_B, BTN_VIEW = (8, 4), (8, 5), (9, 4)
BTN_L, BTN_R = (8, 3), (8, 2)
BTN_DOWN = (9, 3)
ICON_FOCUS = f"{prism_config.PRISM}/deck_icon_focus.py"
TOUCH_READER = f"{prism_config.PRISM}/touch_reader.py"
LAST_INPUT = "/tmp/deck_last_input"   # touched on real controller/touch input; game_tester.py waits for 20 min idle
# Prism theme geometry in 1280x800 screen pixels
TILE_PITCH, ROW_Y = 370, (240, 560)          # carousel: tile spacing, tile band (bigger centred tiles, 2026-10-09)
ICON_Y, ICON_X = (716, 788), (44, 126, 208, 290)  # icon row band (lower left); left edge | boundaries | right edge
CARD = (480, 105, 1240, 700)                 # game list: video card
LOGO = (0, 0, 340, 95)                       # game list: console logo
STEAM_URL = 'unset LD_LIBRARY_PATH; exec /usr/bin/steam -ifrunning "steam://open/settings%s"'
SKIP_SYSTEMS = {"deck-power", "deck-settings", "deck-controllers", "emulators", "steam", "desktop", "media", "foryou"}
# Files a playlist/cue sheet points at; ES-DE's delete only removes the file the entry itself names.
REF_EXT = {".cue", ".gdi", ".m3u", ".ccd"}


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def open_controller():
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(node + "/device/uevent") as f:
                if CONTROLLER_HID_ID not in f.read():
                    continue
            fd = os.open("/dev/" + os.path.basename(node), os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            return fd
        os.close(fd)
    return None


def esde_pid():
    return prism_state.esde_pid()


def esde_env(pid):
    raw = open(f"/proc/{pid}/environ", "rb").read().decode(errors="replace")
    return dict(kv.split("=", 1) for kv in raw.split("\0") if "=" in kv)


_focus_cache = prism_state._focus   # the same cache: tests set _focus_cache[0] = 0 to bypass it


STEAM_UI = prism_state.STEAM_UI   # gamescope app id of Steam UI / menus (focused_app() returns strings)


def focused_app():
    """The Steam app id gamescope has in front (GAMESCOPE_FOCUSED_APP on the :0 root window), or None."""
    return prism_state.front_app()


def steam_game_running(esde):
    """Something other than ES-DE is in front (a Steam game, Plex, the Steam menu...). Background apps don't count."""
    return prism_state.other_in_front(esde)


def esde_idle(pid):
    """True when ES-DE is running and has not launched anything (event scripts don't count)."""
    return prism_state.esde_idle(pid)


def selected():
    """(path, name, system) of the game ES-DE has selected, or None."""
    try:
        parts = open(STATE, "rb").read().decode(errors="replace").split("\0")
    except OSError:
        return None
    if len(parts) < 4:
        return None
    path, name, system = parts[0], parts[1], parts[2]
    if (system in SKIP_SYSTEMS and system != "foryou") or not path.startswith(f"{ROMS}/{system}/"):
        return None  # collections, the system menu and anything outside the ROM folder
    return path, name, system


def referenced_files(path):
    """Track/disc files a .cue, .gdi, .ccd or .m3u points at (recursively)."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in REF_EXT:
        return []
    try:
        lines = open(path, errors="replace").read().splitlines()
    except OSError:
        return []
    if ext == ".cue":
        names = [m.group(1) for l in lines if (m := re.match(r'\s*FILE\s+"?(.+?)"?\s+\S+\s*$', l, re.I))]
    elif ext == ".gdi":  # "track lba type sectorsize filename offset", filename may be quoted
        names = [m.group(1) or m.group(2) for l in lines[1:]
                 if (m := re.match(r'\s*\d+\s+\d+\s+\d+\s+\d+\s+(?:"([^"]+)"|(\S+))', l))]
    elif ext == ".ccd":
        stem = os.path.splitext(os.path.basename(path))[0]
        names = [stem + ".img", stem + ".sub"]
    else:
        names = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
    out = []
    for n in names:
        p = os.path.normpath(os.path.join(os.path.dirname(path), n))
        if os.path.isfile(p) and p != path and p not in out:
            out.append(p)
            out += [q for q in referenced_files(p) if q not in out]
    return out


def save(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        root, ext = os.path.splitext(dst)
        dst = f"{root} ({time.strftime('%Y%m%d-%H%M%S')}){ext}"
    try:
        os.link(src, dst)
    except OSError:
        subprocess.run(["cp", "-a", src, dst], check=True)
    if os.path.getsize(dst) != os.path.getsize(src):
        raise RuntimeError(f"backup size mismatch for {src}")
    return dst


def gamelist_entry(system, path):
    gl = f"{GAMELISTS}/{system}/gamelist.xml"
    rel = "./" + os.path.relpath(path, f"{ROMS}/{system}")
    try:
        text = open(gl, errors="replace").read()
    except OSError:
        return ""
    for m in re.finditer(r"<game>.*?</game>", text, re.S):
        p = re.search(r"<path>(.*?)</path>", m.group(0), re.S)
        if p and p.group(1).replace("&amp;", "&").replace("&apos;", "'") == rel:
            return m.group(0)
    return ""


def backup(path, name, system):
    stem = os.path.splitext(os.path.basename(path))[0]
    dest = f"{REMOVED}/{system}"
    saved = {"rom": [], "moved": [], "media": []}
    saved["rom"].append(save(path, f"{dest}/{os.path.basename(path)}"))
    for ref in referenced_files(path):
        rel = os.path.relpath(ref, f"{ROMS}/{system}")
        saved["moved"].append((ref, save(ref, f"{dest}/{rel}")))
    for m in glob.glob(f"{MEDIA}/{system}/*/{glob.escape(stem)}.*"):
        kind = os.path.basename(os.path.dirname(m))
        saved["media"].append(save(m, f"{dest}/media/{kind}/{os.path.basename(m)}"))
    entry = gamelist_entry(system, path)
    if entry:
        with open(f"{dest}/{stem}.gamelist.xml", "w") as f:
            f.write(entry + "\n")
    return saved


def esde_keys(keys, gap=0.12):
    """Send keys straight to ES-DE's X window (xdotool --window = XSendEvent). Unlike the virtual pad this still
    reaches ES-DE while the confirm card has focus, so the delete steps stay hidden behind the card.
    ES-DE's default keyboard map: F1 = Select (Gamelist Options), Return = A, BackSpace = B, arrows = d-pad."""
    pid = esde_pid()
    env = {**os.environ, **esde_env(pid)} if pid else dict(os.environ)
    wins = subprocess.run(["xdotool", "search", "--pid", str(pid)], env=env, capture_output=True, text=True).stdout.split()
    if not wins:
        return False
    for k in keys:
        subprocess.run(["xdotool", "key", "--window", wins[0], k], env=env)
        time.sleep(gap if k != "F1" and k != "Return" else 0.9)
    return True


def esde_delete_hidden():
    """Gamelist Options > Edit this game's metadata > Delete > Yes, by keyboard."""
    return esde_keys(["F1", "Down", "Down", "Down", "Return"] + ["Down"] * 40 + ["Right"] * 4 + ["Return", "Return"],
                     gap=0.03)


def esde_delete(pad):
    """ES-DE: View > Edit this game's metadata > Delete > Yes (mapped on ES-DE 3.x, Prism theme)."""
    def tap(btn, gap=0.12):
        inject(pad, btn, 1, gap=gap, hold=0.07)
    tap("SELECT", 0.9)
    for _ in range(3):
        tap("DOWN")
    tap("A", 1.1)
    for _ in range(40):
        tap("DOWN", 0.05)
    for _ in range(4):
        tap("RIGHT", 0.1)
    tap("A", 0.9)  # Delete
    tap("A", 1.0)  # "This will delete the game file... Proceed?" defaults to YES


def confirm(pid, title, name, detail, ok, busy="Removing…"):
    """Show the Are-you-sure card (deck_confirm.py). Returns the running card once A is pressed (it then covers
    the screen with "Removing…" until finish_confirm), or None if cancelled."""
    try:
        os.remove("/tmp/deck_confirm_done")
    except OSError:
        pass
    p = subprocess.Popen(["/usr/bin/python3", f"{prism_config.PRISM}/deck_confirm.py", title, name, detail, ok, busy],
                         env=esde_env(pid), stdout=subprocess.PIPE, stderr=open("/tmp/deck_confirm.log", "w"),
                         text=True, start_new_session=True)
    answer, t0 = None, time.time()
    while time.time() - t0 < 40:
        r, _, _ = select.select([p.stdout], [], [], 0.5)
        if r:
            line = p.stdout.readline().strip()
            if line in ("CONFIRMED", "CANCELLED"):
                answer = line
                if line == "CANCELLED":
                    break
            elif line == "RELEASED" and answer:
                break
            elif not line and p.poll() is not None:
                break
    if answer != "CONFIRMED":
        p.terminate()
        return None
    time.sleep(0.2)
    return p


def finish_confirm(p):
    open("/tmp/deck_confirm_done", "w").close()
    try:
        p.wait(3)
    except subprocess.TimeoutExpired:
        p.terminate()


def dismiss_pick(pad, path, name):
    """For You: hold L4 = not interested. Remember it (foryou.py never picks it again and leans away from games
    like it), then drop the stub from ES-DE. No backup: the entry is a generated launcher script."""
    card = confirm(esde_pid(), "Not interested?", name, "It won't be suggested again, and future picks will lean away from games like it.",
                   "Not interested", "Removing…")
    if not card:
        log(f"For You: kept {name}")
        return
    f = f"{prism_config.PRISM}/foryou/dismissed.json"
    try:
        lst = json.load(open(f))
    except (OSError, ValueError):
        lst = []
    lst.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "title": name, "file": os.path.basename(path)})
    json.dump(lst, open(f, "w"), indent=1)
    esde_delete_hidden()
    time.sleep(1.0)
    if not os.path.exists(path):
        esde_keys(["BackSpace"])
        log(f"For You: not interested in {name}")
    else:
        log(f"For You: noted {name} as not interested, but the ES-DE delete did not happen")
        esde_keys(["BackSpace"] * 3, gap=0.3)
    time.sleep(0.3)
    finish_confirm(card)


def remove_selected(pad):
    pid = esde_pid()
    sel = selected()
    if not pid or not esde_idle(pid) or not sel:
        log(f"L4 ignored (es-de={pid}, selection={sel})")
        return
    path, name, system = sel
    if system == "foryou":
        return dismiss_pick(pad, path, name)
    if system in FLOW_SYSTEMS:
        threading.Thread(target=flow_watch, args=("watched",), daemon=True).start()   # L4 = watched / unwatched
        return
    if system in ("music", "media"):
        log(f"L4 ignored in {system} (not games: nothing to delete)")
        return
    if not os.path.isfile(path):
        log(f"L4 ignored: {path} is a folder or missing")
        return
    card = confirm(pid, "Delete this game?", name, "It's deleted from the Deck. Your NAS still has a copy.",
                   "Delete", "Deleting…")
    if not card:
        log(f"L4 cancelled: {system}/{name}")
        return
    # the owner (2026-10-06): delete for real, the NAS is the backup. ES-DE's own delete removes the entry, its media
    # and the file the entry names; we then remove the disc files a .cue/.m3u points at and a linked .rvz's target.
    refs = [r for r in referenced_files(path) if os.path.exists(r)]
    real = os.path.realpath(path) if os.path.islink(path) else None
    esde_delete_hidden()
    time.sleep(1.0)
    gone = not os.path.lexists(path)
    extra = []
    if gone:
        esde_keys(["BackSpace"])   # ES-DE reopens Gamelist Options after a delete
        for r in refs + ([real] if real and real.startswith(f"{HOME}/Games/") else []):
            try:
                os.unlink(r); extra.append(r)
            except OSError:
                pass
    else:
        esde_keys(["BackSpace"] * 3, gap=0.3)   # back out of whatever menu is open
    with open(LOG, "a") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "system": system, "name": name,
                            "path": path, "deleted": gone, "also_deleted": extra}) + "\n")
    log(("deleted " if gone else "DELETE DID NOT HAPPEN: ") + f"{system}/{name}" + (f" (+{len(extra)} files)" if extra else ""))
    time.sleep(0.3)
    finish_confirm(card)


def search():
    """L5: search the whole library. Goes to the All Games collection and opens ES-DE's own name filter
    (Gamelist Options > Filter gamelist > Game name), which brings up ES-DE's on-screen keyboard."""
    pid = esde_pid()
    if not pid or not esde_idle(pid):
        return
    try:   # All Games has to be switched on (esde_pending.py does it at the next ES-DE restart)
        if not re.search(r'CollectionSystemsAuto" value="[^"]*\ball\b', open(f"{prism_config.ESDE}/settings/es_settings.xml").read()):
            log("search: All Games is not on yet"); return
    except OSError:
        return
    for _ in range(3):                       # back out of a game list to the console row
        if system_view():
            break
        esde_keys(["BackSpace"], gap=0.5)
    if not system_view():
        log("search: could not reach the console row"); return
    def current():
        try:
            return open("/tmp/esde_system").read().strip()
        except OSError:
            return ""
    def step(key):   # one move, then wait for ES-DE's system-select event to report the new centre
        before = os.path.getmtime("/tmp/esde_system") if os.path.exists("/tmp/esde_system") else 0
        esde_keys([key], gap=0.02)
        for _ in range(40):
            if os.path.exists("/tmp/esde_system") and os.path.getmtime("/tmp/esde_system") != before:
                return True
            time.sleep(0.025)
        return False
    for key in ("Left", "Right"):            # All Games sits near the front; try left first, then the other way
        seen = set()
        for _ in range(60):
            if current() == "all":
                break
            if not step(key) or current() in seen:
                break                          # reached the end of the row (no event / wrapped around)
            seen.add(current())
        if current() == "all":
            break
    if current() != "all":
        log(f"search: All Games not found (at {current()!r})"); return
    esde_keys(["Return"], gap=0.9)           # into All Games
    esde_keys(["F1", "Down", "Down", "Return"], gap=0.25)   # Gamelist Options > Filter gamelist
    esde_keys(["Return", "Down", "Return"], gap=0.4)   # Reset all filters (fresh search), Game name -> keyboard
    log("search opened")


def pine_save(slot=1, timeout=3.0):
    """Ask PCSX2 to save a state over PINE (its IPC socket; EnablePINE = true in PCSX2.ini). No key press, so it
    works whatever window has focus (the F1 key landed on ES-DE after the control center closed, 2026-10-07)."""
    import socket
    path = f"/run/user/{os.getuid()}/pcsx2.sock"
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); c.settimeout(timeout); c.connect(path)
        c.sendall(struct.pack("<IBB", 6, 0x09, slot))          # MsgSaveState
        reply = c.recv(5); c.close()
        return len(reply) == 5 and reply[4] == 0
    except OSError as e:
        log(f"PINE save failed: {e}")
        return False


def pre_quit_save(proc, key, state_dir, ext, wait=4.0):
    """Send an emulator its save-state hotkey (xdotool to its window on ES-DE's display) and wait for the file."""
    pid = esde_pid()
    env = {**os.environ, **esde_env(pid)} if pid else dict(os.environ)
    t0 = time.time()
    wins = subprocess.run(["xdotool", "search", "--pid", str(proc)], env=env, capture_output=True, text=True).stdout.split()
    if not wins:
        wins = subprocess.run(["xdotool", "search", "--class", "PPSSPP|pcsx2|PCSX2"], env=env, capture_output=True, text=True).stdout.split()
    if key.startswith("pine:"):      # PCSX2: save over its IPC socket; fall back to the F1 key
        if not pine_save(int(key.split(":", 1)[1])):
            key = "uinput:f1"
    if key.startswith("uinput:"):   # Qt emulators ignore synthetic X events: use the virtual keyboard (focus = game)
        try:
            deck_mcp.tool_key(key.split(":", 1)[1])
        except Exception as e:
            log(f"virtual key failed: {e}")
    else:
        for w in wins[-1:]:
            subprocess.run(["xdotool", "key", "--window", w, key], env=env)
    while time.time() - t0 < wait:
        try:
            if any(f.endswith(ext) and os.path.getmtime(os.path.join(state_dir, f)) >= t0 - 0.5 for f in os.listdir(state_dir)):
                time.sleep(0.4)   # let the write finish
                log(f"L4+R4: saved state ({ext}) before quitting"); return True
        except OSError:
            pass
        time.sleep(0.2)
    log(f"L4+R4: no save state appeared ({ext}), quitting anyway"); return False


LAST_PLAYED = {"switch", "psvita"}   # no save states: their launch screen shows the last frame as "Last played"
NO_RESUME = {"switch", "psvita", "xbox360", "n3ds", "scummvm", "steam", "media", "foryou", "ps3", "wiiu", "ps4",
             "model2"}   # emulators without save states: no resume picture, no "Resuming" splash


def grab_shot(path):
    try:
        os.remove(path)
    except OSError:
        pass
    subprocess.run(["gamescopectl", "screenshot", path], timeout=4, capture_output=True)
    for _ in range(30):
        if os.path.exists(path) and os.path.getsize(path) > 0:
            time.sleep(0.1); return path
        time.sleep(0.04)
    return None


PROTECTED = (b"ES-DE.AppImage", b"squashfuse", b"es-de.sh", b"/usr/bin/es-de", b"steam-launch-wrapper", b"reaper",
             b"deck_cc.py", b"deck_splash.py", b"game-start/", b"game-end/")


def protected(k):
    """Never pause or kill ES-DE itself, its AppImage runtime (it serves ES-DE's files over FUSE - stopping or killing
    it makes ES-DE lose its fonts and crash, 2026-10-07 23:03), its launcher, or our own overlays."""
    try:
        cmd = open(f"/proc/{k}/cmdline", "rb").read()
    except OSError:
        return True
    return any(x in cmd for x in PROTECTED)


def signal_game(pid, sig):
    for k in esde_game_procs(pid):
        if k == pid or protected(k):
            continue
        try:
            os.kill(k, sig)
        except OSError:
            pass


def kill_game_nosave(pid):
    """End the game without letting it write a save state (SIGKILL); its last resume point stays."""
    signal_game(pid, signal.SIGCONT)
    signal_game(pid, signal.SIGKILL)
    for _ in range(40):
        if not game_running(pid):
            break
        time.sleep(0.25)


def focus_now():
    return prism_state.front_app(fresh=True)     # bypass the 0.4 s cache


def blur_clear(limit=0.8):
    """Wait until Steam's menu has finished animating away (GAMESCOPE_BLUR_MODE back to 0), so the control center's
    background frame is the clean game picture, not a blurred half-closed menu."""
    end = time.time() + limit
    while time.time() < end:
        try:
            out = subprocess.run(["xprop", "-root", "GAMESCOPE_BLUR_MODE"], env={**deck_mcp.session_env(), "DISPLAY": ":0"},
                                 capture_output=True, text=True, timeout=1).stdout
            if out.strip().endswith("= 0") or "=" not in out:
                break
        except Exception:
            break
        time.sleep(0.04)
    time.sleep(0.12)


def steam_menu_open():
    """Steam's own menu is up: focus on Steam's UI (769) or its background blur on. With the control-center overlay
    drawn on top, gamescope can keep reporting the game as focused while Steam's menu is open underneath, so the
    blur flag is checked too (2026-10-07: A on the card also went to Steam's hidden menu)."""
    if focus_now() == STEAM_UI:
        return True
    try:
        out = subprocess.run(["xprop", "-root", "GAMESCOPE_BLUR_MODE"], env={**deck_mcp.session_env(), "DISPLAY": ":0"},
                             capture_output=True, text=True, timeout=1).stdout
        return out.strip().endswith(("= 1", "= 2"))
    except Exception:
        return False


_closing_menu = threading.Lock()


def close_steam_menu(pad, appear=1.5, tries=3):
    """After a Steam-button press: wait up to `appear` s for Steam's menu, then press B ONCE. A further B only while
    Steam's UI still has focus (769) - never on the blur flag alone, which lingers after the menu has closed: a B
    sent when Steam's menu is already gone reaches the game/card instead (it 'resumed' the card, 2026-10-07).
    One closer at a time."""
    if not _closing_menu.acquire(blocking=False):
        return True
    try:
        t0 = time.time()
        end = t0 + appear
        # only once Steam has really taken focus (769): its blur comes on a moment EARLIER, and a B sent then lands
        # on the game/card instead of Steam's menu (closed the card at once in the 2026-10-07 test)
        while time.time() < end and focus_now() != STEAM_UI:
            time.sleep(0.03)
        if focus_now() != STEAM_UI:
            return True
        time.sleep(0.08)
        steam_js(JS_CLOSE, "close menu")      # Steam's own call first; no controller press needed
        t = time.time()
        while time.time() - t < 0.6 and focus_now() == STEAM_UI:
            time.sleep(0.05)
        if focus_now() != STEAM_UI:
            blur_clear()
            log(f"Steam's menu closed (js, {time.time() - t0:.2f}s)")
            return True
        for n in range(tries):
            deck_mcp._pad_event(pad, "B", True); time.sleep(0.12)
            deck_mcp._pad_event(pad, "B", False)
            t = time.time()
            while time.time() - t < 1.5 and focus_now() == STEAM_UI:
                time.sleep(0.05)
            if focus_now() != STEAM_UI:
                blur_clear()
                log(f"Steam's menu closed ({n + 1} B, {time.time() - t0:.2f}s)")
                return True
        log(f"Steam's menu STILL OPEN after {tries} B")
        return False
    finally:
        _closing_menu.release()


def cc_choice(cc, pad=None):
    """Wait for the control center's answer. If Steam's menu takes the screen while it's open (the card would sit
    hidden under it with the game frozen), close the card: the caller then resumes the game."""
    over = None
    while True:
        r, _, _ = select.select([cc.stdout], [], [], 0.25)
        if r:
            return (cc.stdout.readline() or "RESUME").strip()
        if cc.poll() is not None:
            return "RESUME"
        if focus_now() == STEAM_UI:
            over = over or time.time()
            if time.time() - over > 0.15:   # Steam button pressed again: close the card, then Steam's menu
                log("Steam button while the control center was open: resuming")
                cc.terminate()
                try:
                    cc.wait(timeout=2)       # it holds the pads (grab) until it exits
                except subprocess.TimeoutExpired:
                    cc.kill()
                if pad is not None:
                    close_steam_menu(pad, appear=0.3)
                return "RESUME"
        else:
            over = None


CC_SOCK = "/tmp/deck_cc.sock"
cc_open = {}        # the card shown on Steam press-down: {"conn", "pid", "shot"}


def cc_request(msg, timeout=2.0, keep=False):
    import socket
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); c.settimeout(timeout)
    c.connect(CC_SOCK); c.sendall((json.dumps(msg) + "\n").encode())
    if keep:
        return c
    r = c.makefile().readline().strip(); c.close()
    return r


def cc_daemon():
    """The always-ready control center (deck_cc.py --daemon) on Steam's display, as a gamescope overlay above
    Steam's own menu. Started on demand; True when it answers."""
    try:
        return cc_request({"cmd": "ping"}, timeout=0.5) == "PONG"
    except OSError:
        pass
    subprocess.Popen(["/usr/bin/python3", f"{prism_config.PRISM}/deck_cc.py", "--daemon"],
                     env={**deck_mcp.session_env(), "DISPLAY": ":0"}, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=open("/tmp/deck_cc_daemon.log", "w"))
    for _ in range(30):
        time.sleep(0.1)
        try:
            if cc_request({"cmd": "ping"}, timeout=0.5) == "PONG":
                return True
        except OSError:
            pass
    log("control center daemon did not start")
    return False


def grab_shot_fast(path, pending_ok=False):
    """gamescope screenshot, returned as soon as the PNG is complete (no fixed wait). pending_ok: return the path even
    if it is still being written (the card waits for it itself: a docked 4K frame takes longer than 1.5 s)."""
    try:
        os.remove(path)
    except OSError:
        pass
    subprocess.Popen(["gamescopectl", "screenshot", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + 1.5
    while time.time() < end:
        try:
            with open(path, "rb") as f:
                f.seek(-12, 2)
                if f.read().find(b"IEND") >= 0:
                    return path
        except OSError:
            pass
        time.sleep(0.01)
    return path if pending_ok else None


_MENU = "SteamUIStore.WindowStore.GamepadUIMainWindowInstance.MenuStore"
JS_SUPPRESS = ("(()=>{const m=%s; if(!window.__deckSup){window.__deckSup=m.SetSuppressMenus();} "
               "return m.m_cSuppressRequests})()" % _MENU)
JS_RELEASE = ("(()=>{const m=%s; if(window.__deckSup){window.__deckSup(); window.__deckSup=null;} "
              "SteamUIStore.CloseSideMenus(); return m.m_cSuppressRequests})()" % _MENU)
JS_CLOSE = "(SteamUIStore.CloseSideMenus(), 1)"


BRIDGE_SOCK = f"/run/user/{os.getuid()}/switchhome_bridge.sock"


def bridge(cmd, arg=None):
    """Steam UI command through the Prism Bridge Decky plugin (2026-10-08): {"ok", "data"} or None if the plugin
    isn't there. Decky reloads it with Steam's UI, so it survives Steam restarts; steam_js stays as the fallback."""
    import socket as _s
    try:
        c = _s.socket(_s.AF_UNIX); c.settimeout(2.5); c.connect(BRIDGE_SOCK)
        c.sendall((json.dumps({"cmd": cmd, "arg": arg}) + "\n").encode())
        r = c.makefile().readline(); c.close()
        r = json.loads(r)
        return r if r.get("ok") else None
    except (OSError, ValueError):
        return None


def steam_js(js, what):
    """Run JavaScript in Steam's UI (CEF debugging port, see steam_cef.py). Steam's menu can't be closed with a B
    while the control-center overlay is up (gamescope keeps focus on the game, the B reached the card instead), so
    the card SUPPRESSES Steam's menus while it's shown and releases them afterwards (2026-10-07)."""
    try:
        import steam_cef
        r = steam_cef.evaluate(js)
        return r.get("result", {}).get("value")
    except Exception as e:
        log(f"Steam UI call failed ({what}): {e}")
        return None


def steam_ui(cmd, js):
    """Bridge first, raw CEF if the plugin isn't loaded."""
    r = bridge(cmd)
    return r["data"] if r else steam_js(js, cmd)


def controllers_screen(why):
    """The Controllers icon: "Who's playing?" (prism_screens.py; X there opens Steam's controller settings)."""
    import prism_state as ps
    if ps.card_up() or not system_view():          # only on the console row, like Steam's settings before
        return
    subprocess.Popen(["systemd-run", "--user", "--collect", "--quiet", "/usr/bin/python3",
                      f"{prism_config.PRISM}/prism_screens.py", "join", f"Controllers ({why})"])
    log(f"controllers: Who's playing ({why})")


def settings_screen(why):
    """The Settings icon: Prism Settings (prism_settings.py; Steam's settings are under System there)."""
    import prism_state as ps
    if ps.card_up() or not system_view():
        return
    subprocess.Popen(["systemd-run", "--user", "--collect", "--quiet", "/usr/bin/python3",
                      f"{prism_config.PRISM}/prism_settings.py", why])
    log(f"settings: Prism Settings ({why})")


def steam_menus_suppress():
    threading.Thread(target=steam_ui, args=("suppress", JS_SUPPRESS), daemon=True).start()


STEAM_MENU_WINDOW = "/tmp/prism_steam_menu_open"     # touched while the user deliberately opened Steam's menu


def steam_menus_release():
    if docked():
        # on the TV Steam's own menus never come up by accident (kids mashing Home opened them at the 2026-10-10 party):
        # keep the suppression, just close anything open. "Steam menu" on Prism's card is the deliberate way in.
        steam_ui("close", JS_CLOSE)
        return
    steam_ui("release", JS_RELEASE)


def steam_menu_on_purpose():
    """Open Steam's main menu deliberately (card choice): lift the suppression, open it, and suppress again as soon as
    it closes (checked every second, at most 10 min)."""
    open(STEAM_MENU_WINDOW, "w").close()
    steam_ui("release", JS_RELEASE)
    time.sleep(0.25)
    steam_js("(SteamUIStore.WindowStore.GamepadUIMainWindowInstance.MenuStore.OpenMainMenu(), 1)", "Steam menu")

    def watch():
        time.sleep(3)
        for _ in range(600):
            vis = steam_js("SteamUIStore.WindowStore.GamepadUIMainWindowInstance.MenuStore.IsAnySideMenuVisible() ? 1 : 0",
                           "menu open?")
            if vis == 0 and focus_now() != STEAM_UI:
                break
            time.sleep(1)
        try:
            os.remove(STEAM_MENU_WINDOW)
        except OSError:
            pass
        if docked():
            steam_menus_suppress()
    threading.Thread(target=watch, daemon=True).start()


JS_PAD_TOASTS_OFF = ("(() => { const n = NotificationStore; if (!n || n.__prismPadToasts) return 0; n.__prismPadToasts = 1;"
                     " n.ControllerConnectedNotification = function () {};"
                     " n.ControllerDisconnectedNotification = function () {}; return 1; })()")


def steam_pad_toasts_off():
    """Steam's small "controller connected / disconnected" pop-ups off: Prism shows its own, bigger ones (controller_bar
    -> deck_cc padtoast; the owner 2026-10-10). Re-applied every few seconds, so it holds after a Steam restart."""
    try:
        if steam_js(JS_PAD_TOASTS_OFF, "pad toasts off") == 1:
            log("Steam's controller pop-ups switched off (Prism shows its own)")
    except Exception:
        pass


def steam_menus_guard():
    """Docked and Prism running: Steam's menus suppressed at all times (except while opened on purpose)."""
    if docked() and esde_pid() and not os.path.exists(STEAM_MENU_WINDOW):
        st = bridge("state")
        if st and not (st.get("data") or {}).get("held"):
            steam_menus_suppress()
            log("Steam menus suppressed (docked)")


def steam_menu_close_soon():
    """Release happened before the suppression landed (very quick tap): close Steam's menu if it opened anyway."""
    def run():
        for delay in (0.25, 0.6, 1.2):
            time.sleep(delay)
            # close unconditionally: docked, Steam's menu over the card wasn't detected (no blur flag / focus change)
            # and presses meant for it chose "Quit" on the hidden card (2026-10-09). Closing nothing is harmless.
            steam_ui("close", JS_CLOSE)
    threading.Thread(target=run, daemon=True).start()


MOVIE_SOCK = f"/run/user/{os.getuid()}/app/io.mpv.Mpv/switchhome-mpv.sock"
MUSIC_SOCK = f"/run/user/{os.getuid()}/app/io.mpv.Mpv/music.sock"


def mpv_ipc(sock, cmd):
    """One mpv JSON-IPC command; returns its data (or None)."""
    import socket as _s
    try:
        c = _s.socket(_s.AF_UNIX); c.settimeout(1); c.connect(sock)
        c.sendall((json.dumps({"command": cmd}) + "\n").encode())
        data = c.recv(65536).decode(); c.close()
        for line in data.splitlines():
            r = json.loads(line)
            if "error" in r:
                return r.get("data")
    except (OSError, ValueError):
        pass
    return None


def music_toast():
    """Mini player: tell the overlay to show the current song for a few seconds."""
    import hashlib
    title = mpv_ipc(MUSIC_SOCK, ["get_property", "media-title"])
    if not title:
        return
    idx = mpv_ipc(MUSIC_SOCK, ["get_property", "playlist-pos"])
    sub, cover = "", ""
    try:
        q = json.load(open(f"{prism_config.PRISM}/music/queue.json"))
        tr = q[idx] if isinstance(idx, int) and 0 <= idx < len(q) else None
    except (OSError, ValueError):
        tr = None
    if tr:
        title, sub = tr.get("title") or title, tr.get("artist", "")
        for size in (300, 120):
            c = f"{HOME}/.cache/deck-music/{hashlib.md5((str(tr.get('thumb')) + str(size)).encode()).hexdigest()}.jpg"
            if os.path.exists(c) and os.path.getsize(c) > 0:
                cover = c; break
    if mpv_ipc(MUSIC_SOCK, ["get_property", "pause"]) is True:
        sub = ("Paused  ·  " + sub) if sub else "Paused"
    try:
        cc_daemon() and cc_request({"cmd": "toast", "title": title, "sub": sub, "cover": cover})
    except OSError:
        pass


def music_button(hold):
    if mpv_ipc(MUSIC_SOCK, ["get_property", "idle-active"]) is None:
        return                                   # no music player running: R5 does nothing
    if hold:
        mpv_ipc(MUSIC_SOCK, ["playlist-next"]); time.sleep(0.4)
        log("R5 held: next song")
    else:
        mpv_ipc(MUSIC_SOCK, ["cycle", "pause"]); time.sleep(0.1)
        log("R5: music play/pause")
    music_toast()


def steam_down_media(pad, pid, sel):
    """Steam button in a movie/episode or the Music app: no game pause menu - media cards instead
    (2026-10-08). Movies: Resume / Start over / Back to Movies (mpv paused, Plex keeps the position).
    Music app: Back to Music / Pause-Play / Next song / Prism (music keeps playing)."""
    system = sel[2]
    steam_menus_suppress()
    shot = grab_shot_fast("/tmp/deck_cc_shot.png", pending_ok=True)
    stem = os.path.splitext(os.path.basename(sel[0].rstrip("/")))[0]
    rel = os.path.relpath(os.path.dirname(sel[0]), f"{prism_config.ROMS}/{system}")
    sub = "" if rel == "." else rel + "/"
    cover = next(iter(glob.glob(f"{prism_config.TOOLS}/downloaded_media/{system}/covers/"
                                f"{glob.escape(sub + stem)}.*")), "")
    if system in ("movies", "tv"):
        mpv_ipc(MOVIE_SOCK, ["set_property", "pause", True])
        name = mpv_ipc(MOVIE_SOCK, ["get_property", "media-title"]) or sel[1]
        items = [("RESUME", "Resume", "Back to where you were", "play"),
                 ("RESTART", "Start over", "Play from the beginning", "restart"),
                 ("SLEEP", "Sleep timer", sleep_timer.label("movie"), "moon"),
                 ("LATER", "Watch later", "Save your spot for later", "clock"),
                 ("WATCHED", "Mark watched", "Done with this one", "check"),
                 ("HOME", "Back to Movies" if system == "movies" else "Back to TV Shows", "Your place is saved", "home")]
        status = "Paused"
    else:
        title = mpv_ipc(MUSIC_SOCK, ["get_property", "media-title"])
        paused = mpv_ipc(MUSIC_SOCK, ["get_property", "pause"])
        name = title or "Music"
        items = [("RESUME", "Back to Music", "Keep browsing", "note"),
                 ("TOGGLE", "Play" if paused else "Pause", "The current song", "play" if paused else "pause"),
                 ("NEXT", "Next song", "Skip ahead", "next"),
                 ("SLEEP", "Sleep timer", sleep_timer.label("music"), "moon"),
                 ("HOME", "Prism", "Music keeps playing", "home")]
        status = "Paused" if paused else ("Now playing" if title else "Music")
    try:
        conn = cc_request({"cmd": "open", "name": name, "shot": shot or "", "cover": cover, "system": system,
                           "items": items, "saving": False, "status": status}, keep=True)
    except OSError as e:
        log(f"control center daemon unreachable: {e}")
        steam_menus_release()
        if system in ("movies", "tv"):
            mpv_ipc(MOVIE_SOCK, ["set_property", "pause", False])
        return
    conn.setblocking(False)
    cc_open.update(conn=conn, pid=pid, shot=shot, buf=b"", media=system)
    log(f"Steam button down: media card ({system})")


import sleep_timer  # noqa: E402  (sleep_timer.py)


def toast(title, sub):
    try:
        cc_request({"cmd": "toast", "title": title, "sub": sub, "cover": ""}, timeout=0.5)
    except OSError:
        pass


def media_watch_action(choice):
    """Steam-button card on a movie / episode: Watch later (Plex watchlist; the player saves your spot) or Mark watched.
    Both close the player and go back to the list (2026-10-08)."""
    import plex_watch
    now = plex_watch.now_playing()
    key = now.get("key")
    mpv_ipc(MOVIE_SOCK, ["quit"])                          # plex_play reports the position first
    if not key:
        return
    time.sleep(2.5)
    try:
        if choice == "LATER":
            title = plex_watch.set_watch_later(key, True)
            toast("Saved to Watch Later", title)
        else:
            plex_watch.set_watched(key, True)
            toast("Marked as watched", now.get("title", ""))
        log(f"card: {choice} {key}")
    except Exception as e:
        log(f"card {choice} failed: {e}"); toast("Couldn't reach Plex", "Try again in a moment")
    open(f"{prism_config.PRISM}/esde_restart_pending", "a").close()   # list markers refresh at the next idle restart


def flow_watch(kind):
    """Movies / TV list: R4 = Watch later on / off (Plex watchlist), L4 = watched / unwatched (2026-10-08)."""
    import plex_watch
    sel = selected()
    if not sel or sel[2] not in FLOW_SYSTEMS or sel[0].endswith(("Continue Watching", "Watch Later")):
        return
    key = plex_watch.key_for(sel[0])
    stub = {}
    if sel[0].endswith(".plex"):
        try:
            stub = json.load(open(sel[0]))
        except (OSError, ValueError):
            pass
    if stub.get("type") == "discover":           # a Discover title (not in the library yet)
        if kind != "later":
            toast("Not in your library yet", "Press A to request it"); return
        try:
            on, title = plex_watch.toggle_watch_later_tmdb(stub["media"], stub["tmdb"])
            toast("Added to Watch Later" if on else "Removed from Watch Later", title)
            log(f"later (discover): {'on' if on else 'off'} {stub['tmdb']}")
        except Exception as e:
            log(f"later (discover) failed: {e}"); toast("Couldn't reach Plex", "Try again in a moment")
        return
    if not key:
        toast("Not available here", sel[1]); return
    try:
        if kind == "later":
            on, title = plex_watch.toggle_watch_later(key)
            toast("Added to Watch Later" if on else "Removed from Watch Later", title)
        else:
            on, title = plex_watch.toggle_watched(key)
            toast("Marked as watched" if on else "Marked as unwatched", title)
        log(f"{kind}: {'on' if on else 'off'} {key}")
        threading.Thread(target=lambda: subprocess.run(["systemd-run", "--user", "--nice=10", "--collect", "/usr/bin/python3",
                                                         f"{prism_config.PRISM}/plex_sync.py"], capture_output=True), daemon=True).start()
        open(f"{prism_config.PRISM}/esde_restart_pending", "a").close()
    except Exception as e:
        log(f"{kind} failed: {e}"); toast("Couldn't reach Plex", "Try again in a moment")


def media_choice(system, choice):
    steam_menus_release()
    if choice == "SLEEP":                                   # cycle the timer, keep watching / listening
        kind = "music" if system == "music" else "movie"
        toast("Sleep timer", sleep_timer.cycle(kind))
        if kind == "movie":
            mpv_ipc(MOVIE_SOCK, ["set_property", "pause", False])
        return
    if system in ("movies", "tv"):
        if choice in ("LATER", "WATCHED"):
            threading.Thread(target=media_watch_action, args=(choice,), daemon=True).start()
        elif choice == "HOME":
            mpv_ipc(MOVIE_SOCK, ["quit"])                  # plex_play reports the position to Plex
        elif choice == "RESTART":
            mpv_ipc(MOVIE_SOCK, ["seek", 0, "absolute"]); mpv_ipc(MOVIE_SOCK, ["set_property", "pause", False])
        else:
            mpv_ipc(MOVIE_SOCK, ["set_property", "pause", False])
    else:
        if choice == "TOGGLE":
            mpv_ipc(MUSIC_SOCK, ["cycle", "pause"])
        elif choice == "NEXT":
            mpv_ipc(MUSIC_SOCK, ["playlist-next"])
        elif choice == "HOME":
            for line in subprocess.run(["pgrep", "-f", "deck_music.py"], capture_output=True, text=True).stdout.split():
                try:
                    os.kill(int(line), signal.SIGTERM)
                except (OSError, ValueError):
                    pass


def rescue():
    """L5 + R5 held 2 s: get out of any stuck state without restarting anything (2026-10-08).
    Hides the control-center card / toast, releases Steam's menu suppression and closes its menu, unpauses any
    SIGSTOPped game, unpauses a paused movie, and puts Prism (ES-DE) back in front."""
    log("RESCUE (L5 + R5)")
    cc_open.clear()
    try:
        cc_request({"cmd": "hide"}, timeout=0.5)
    except OSError:
        pass
    steam_menus_release()
    if esde_pid():               # the card freezes the game's whole tree (bwrap, dbus proxy too): wake all of it
        signal_game(esde_pid(), signal.SIGCONT)
    for line in subprocess.run(["ps", "-eo", "pid=,stat=,args="], capture_output=True, text=True).stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1].startswith("T") and "/Emulation/roms/" in parts[2]:
            try:
                os.kill(int(parts[0]), signal.SIGCONT)
            except OSError:
                pass
    mpv_ipc(MOVIE_SOCK, ["set_property", "pause", False])
    if focus_now() == STEAM_UI:
        steam_ui("close", JS_CLOSE)
        if esde_pid():               # Steam's UI on top of a running Prism: back to it
            time.sleep(0.3)
            steam_ui("to_game", "SteamUIStore.WindowStore.GamepadUIMainWindowInstance.NavigateToRunningApp(), 1")
    if not esde_pid():
        # Prism is gone. A leftover helper still inside its Steam session would make Steam answer the relaunch
        # with "Game already running", so end what is left in that session first, then start it again.
        r = subprocess.run(["pgrep", "-f", f"reaper SteamLaunch AppId={prism_config.PRISM_APPID}"], capture_output=True, text=True)
        for reaper in r.stdout.split():
            kids = subprocess.run(["pgrep", "-P", reaper], capture_output=True, text=True).stdout.split()
            for k in kids:
                if not protected(int(k)):
                    subprocess.run(["pkill", "-TERM", "-P", k], capture_output=True)
                    subprocess.run(["kill", "-TERM", k], capture_output=True)
                    subprocess.run(["sudo", "-n", "pkill", "-TERM", "-P", k], capture_output=True)
                    subprocess.run(["sudo", "-n", "kill", "-TERM", k], capture_output=True)
                    log(f"rescue: ended leftover {k} in the Prism session")
        for _ in range(10):
            if subprocess.run(["pgrep", "-f", f"reaper SteamLaunch AppId={prism_config.PRISM_APPID}"], capture_output=True).returncode:
                break
            time.sleep(0.5)
        else:
            log("rescue: old Prism session still open, not relaunching on top of it")
            return
        subprocess.Popen(["/usr/bin/steam", "-ifrunning", f"steam://rungameid/{prism_config.PRISM_RUNGAMEID}"], env=__import__("deck_mcp").session_env(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        log("rescue: Prism relaunched")


_press_focus = [""]   # focus when the Steam button went down with no game running
hub_open = [False]    # game_hub.py's hub is up: taps go to it


def steam_down(pad):
    """Steam button pressed (not yet released) in an emulator game: freeze the frame and show the card now, before
    Steam opens its menu on release (that menu then opens and closes hidden under the card). A press while the card
    is already up = close it and resume (like the PS button)."""
    if os.path.exists(f"{prism_config.PRISM}/cc_overlay_off") and not cc_open:
        return                           # overlay card switched off: the release uses the one-shot card (steam_home)
    if cc_open:
        if not cc_open.get("closing"):
            log("Steam button again: closing the control center")
            try:
                cc_request({"cmd": "close"})
            except OSError:
                pass
            cc_open["closing"] = True
        cc_open["again"] = True          # Steam opens its menu again on this release: close it then
        return
    pid = esde_pid()
    if not pid or not game_running(pid):
        front = str(focus_now())
        _press_focus[0] = front              # in Steam's own screens (Store...)? the release takes you home
        cc_clear_stale()
        own = ""
        try:
            own = esde_env(pid).get("SteamAppId", "") if pid else ""
        except OSError:
            pass
        if pid and front not in ("", "None", "0", str(STEAM_UI), own) and cc_daemon():
            steam_down_steamgame(pad, pid, front)   # a Steam game launched from Prism: Prism's card too (2026-10-09)
        elif pid and docked() and front in (own, str(STEAM_UI)) and cc_daemon():
            steam_down_menu(pad, pid)            # in Prism's menus on the TV: Prism's card, never Steam's menu
        return
    if not cc_daemon():
        return
    sel0 = selected()
    if sel0 and sel0[2] in ("movies", "tv", "music"):
        return steam_down_media(pad, pid, sel0)
    steam_menus_suppress()           # Steam opens its menu on RELEASE: block it before that happens
    shot = grab_shot_fast("/tmp/deck_cc_shot.png", pending_ok=True)
    signal_game(pid, signal.SIGSTOP)
    sel = selected()
    cover = ""
    if sel:
        stem = os.path.splitext(os.path.basename(sel[0].rstrip("/")))[0]
        for kind in ("covers", "3dboxes", "miximages"):
            hits = glob.glob(f"{prism_config.TOOLS}/downloaded_media/{sel[2]}/{kind}/{glob.escape(stem)}.*")
            if hits:
                cover = hits[0]; break
    states, items = [], None
    if sel and any(b"retroarch" in proc_cmdline(k).lower() for k in esde_game_procs(pid)):
        states = retroarch_states(sel[0])            # Save state / Load state tiles (RetroArch games, 2026-10-08)
        items = [("RESUME", "Resume", "Back to the game", "play"),
                 ("SAVESTATE", "Save state", "Save here, keep playing", "save")]
        if states:
            items.append(("LOADSTATE", "Load state", f"{len(states)} saved", "load"))
        items += [("HOME", "Save & go Home", "Save here and quit", "home"),
                  ("LASTSAVE", "Back to last save", "Reload your resume point", "rewind"),
                  ("QUIT", "Quit without saving", "Lose progress since then", "close")]
    try:
        msg = {"cmd": "open", "name": sel[1] if sel else "", "shot": shot or "", "cover": cover,
               "system": sel[2] if sel else ""}
        if items:
            msg.update(items=items, states=[{k: v for k, v in st.items() if k != "mtime"} for st in states])
        conn = cc_request(msg, keep=True)
    except OSError as e:
        log(f"control center daemon unreachable: {e}")
        signal_game(pid, signal.SIGCONT); return
    conn.setblocking(False)
    cc_open.update(conn=conn, pid=pid, shot=shot, buf=b"", states=states, rom=sel[0] if sel else "")
    log("Steam button down: control center shown")


def steam_down_menu(pad, pid):
    """Home in Prism's menus while docked (2026-10-10): Back / Steam menu / Sleep. Steam's own menu only on purpose."""
    steam_menus_suppress()
    items = [("RESUME", "Back", "Return to Prism", "play"),
             ("STEAMMENU", "Steam menu", "Settings, downloads, friends", "info"),
             ("SLEEP", "Sleep", "Turn off the TV and the controllers", "moon")]
    try:
        conn = cc_request({"cmd": "open", "name": "Prism", "shot": "", "cover": "", "system": "",
                           "items": items, "status": "Menu"}, keep=True)
    except OSError as e:
        log(f"control center daemon unreachable: {e}"); return
    conn.setblocking(False)
    cc_open.update(conn=conn, pid=pid, shot="", buf=b"", menucard=True)
    log("Steam button down: Prism menu card shown")


def steam_down_steamgame(pad, pid, appid):
    """Home in a Steam game: Prism's card with Resume / Quit game / Steam menu. The game keeps running behind it (a
    native PC game isn't frozen from outside); Steam's own menu is one choice away."""
    steam_menus_suppress()
    shot = grab_shot_fast("/tmp/deck_cc_shot.png", pending_ok=True)
    name = steam_js(f"(appStore.GetAppOverviewByAppID({int(appid)})||{{}}).display_name||''", "game name") or ""
    sel = selected()
    cover = ""
    if sel and sel[2] == "steam":
        stem = os.path.splitext(os.path.basename(sel[0].rstrip("/")))[0]
        hits = glob.glob(f"{prism_config.TOOLS}/downloaded_media/steam/covers/{glob.escape(stem)}.*")
        cover = hits[0] if hits else ""
        name = name or sel[1]
    items = [("RESUME", "Resume", "Back to the game", "play"),
             ("STEAMQUIT", "Quit game", "Close it and go back to Prism", "home"),
             ("STEAMMENU", "Steam menu", "Achievements, guides, settings", "info")]
    try:
        conn = cc_request({"cmd": "open", "name": name, "shot": shot or "", "cover": cover, "system": "steam",
                           "items": items, "status": "Steam game"}, keep=True)
    except OSError as e:
        log(f"control center daemon unreachable: {e}")
        steam_menus_release(); return
    conn.setblocking(False)
    cc_open.update(conn=conn, pid=pid, shot=shot, buf=b"", steamgame=appid)
    log(f"Steam button down: control center shown (Steam game {appid})")


def proc_cmdline(p):
    try:
        return open(f"/proc/{p}/cmdline", "rb").read()
    except OSError:
        return b""


RA_STATE_DIRS = [f"{prism_config.SAVES}/retroarch/states",
                 os.path.expanduser("~/.var/app/org.libretro.RetroArch/config/retroarch/states")]


def ago(t):
    m = int((time.time() - t) / 60)
    return "just now" if m < 1 else f"{m} min ago" if m < 60 else f"{m // 60} h ago" if m < 1440 else f"{m // 1440} d ago"


def retroarch_states(rom):
    """Saved states for this ROM, newest first (max 6): [{"slot": n | "auto", "png", "label", "mtime"}]."""
    stem = os.path.splitext(os.path.basename(rom))[0]
    found = {}
    for d in RA_STATE_DIRS:
        for f in glob.glob(f"{d}/**/{glob.escape(stem)}.state*", recursive=True):
            if f.endswith(".png"):
                continue
            tail = f[len(f.rsplit(".state", 1)[0]) + 6:]
            slot = "auto" if tail == ".auto" else (int(tail) if tail.isdigit() else 0 if tail == "" else None)
            if slot is None:
                continue
            mt = os.path.getmtime(f)
            if slot not in found or mt > found[slot]["mtime"]:
                png = f + ".png"
                found[slot] = {"slot": slot, "png": png if os.path.exists(png) else "", "mtime": mt,
                               "label": ("Auto save" if slot == "auto" else f"Slot {slot}") + " · " + ago(mt)}
    return sorted(found.values(), key=lambda s: -s["mtime"])[:6]


def next_state_slot(rom):
    """A free slot 1-9, else the oldest one."""
    used = {st["slot"]: st["mtime"] for st in retroarch_states(rom) if isinstance(st["slot"], int)}
    for n in range(1, 10):
        if n not in used:
            return n
    return min(used, key=used.get)


_ra_slot = {"pid": None, "slot": 0}


def ra_goto_slot(pid, n):
    """RetroArch 1.22's network commands have no 'save/load slot N': step the slot with STATE_SLOT_PLUS/MINUS from
    where it is (state_slot = 0 at launch; tracked per game process; -1 = the auto state)."""
    if _ra_slot["pid"] != pid:
        _ra_slot.update(pid=pid, slot=0)
    diff = n - _ra_slot["slot"]
    for _ in range(abs(diff)):
        retroarch_cmd("STATE_SLOT_PLUS" if diff > 0 else "STATE_SLOT_MINUS", quiet=True); time.sleep(0.06)
    _ra_slot["slot"] = n
    time.sleep(0.15)


def retroarch_cmd(cmd, quiet=False):
    """RetroArch network command (retroarch.cfg network_cmd_enable, UDP 55355)."""
    import socket as _s
    try:
        c = _s.socket(_s.AF_INET, _s.SOCK_DGRAM); c.sendto(cmd.encode(), ("127.0.0.1", 55355)); c.close()
        if not quiet:
            log(f"RetroArch: {cmd}")
    except OSError as e:
        log(f"RetroArch command failed: {e}")


def cc_clear_stale():
    """No card should be up and no game should be paused unless we're waiting on a choice: fix both."""
    if cc_open:
        return
    st = bridge("state")
    held = (st["data"] or {}).get("held") or st["data"].get("suppress", 0) > 0 if st else         steam_js("window.__deckSup ? 1 : 0", "check") == 1
    if held and not (docked() and not os.path.exists(STEAM_MENU_WINDOW)):   # docked: kept on by steam_menus_guard
        steam_menus_release(); log("released a leftover Steam-menu suppression")
    try:
        if cc_request({"cmd": "state"}, timeout=0.5) in ("ACTIVE", "SAVING"):
            cc_request({"cmd": "hide"}); log("hid a leftover control center card")
    except OSError:
        pass
    for line in subprocess.run(["ps", "-eo", "pid=,stat=,args="], capture_output=True, text=True).stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1].startswith("T") and "/Emulation/roms/" in parts[2]:
            try:
                os.kill(int(parts[0]), signal.SIGCONT); log(f"unpaused a leftover paused game process {parts[0]}")
            except OSError:
                pass


def steam_cancel():
    """Long press / chord: not a HOME press after all."""
    if cc_open and not cc_open.get("waiting"):
        try:
            cc_request({"cmd": "hide"})
        except OSError:
            pass
        steam_menus_release()
        signal_game(cc_open["pid"], signal.SIGCONT)
        try:
            cc_open["conn"].close()
        except OSError:
            pass
        cc_open.clear()


def steam_up(pad):
    """Steam button released: close Steam's menu (it opened hidden under the card). Doesn't wait for the choice -
    the main loop keeps reading the controller (cc_poll), so a second Steam press is seen."""
    if not cc_open:
        return
    cc_open["again"] = False
    cc_open["waiting"] = True
    steam_menu_close_soon()          # in case the tap beat the suppression


def cc_poll(pad):
    """Called from the main loop: the card's answer, once it arrives."""
    if not cc_open.get("waiting"):
        return
    try:
        data = cc_open["conn"].recv(64)
    except BlockingIOError:
        return
    except OSError:
        data = b"RESUME\n"
    cc_open["buf"] += data or b"RESUME\n"
    if b"\n" not in cc_open["buf"]:
        return
    choice = cc_open["buf"].split(b"\n")[0].decode().strip() or "RESUME"
    media = cc_open.get("media")
    steamgame = cc_open.get("steamgame")
    menucard = cc_open.get("menucard")
    conn, pid, shot = cc_open["conn"], cc_open["pid"], cc_open["shot"]
    states, rom = cc_open.get("states") or [], cc_open.get("rom", "")
    cc_open.clear()
    try:
        conn.close()
    except OSError:
        pass
    log(f"control center: {choice}")
    if media:
        media_choice(media, choice)
        return
    if menucard:
        for _ in range(10):
            try:
                if cc_request({"cmd": "state"}, timeout=0.3) == "IDLE":
                    break
            except OSError:
                break
            time.sleep(0.1)
        steam_ui("close", JS_CLOSE)
        if choice == "STEAMMENU":
            steam_menu_on_purpose()
        elif choice == "SLEEP":
            subprocess.Popen(["/bin/bash", "-c", f"/usr/bin/python3 {prism_config.PRISM}/pad_sleep.py all; "
                              f"/usr/bin/python3 {prism_config.PRISM}/prism_bridge.py suspend || systemctl suspend"],
                             start_new_session=True)
        return
    if steamgame:
        for _ in range(10):             # card gone first
            try:
                if cc_request({"cmd": "state"}, timeout=0.3) == "IDLE":
                    break
            except OSError:
                break
            time.sleep(0.1)
        steam_menus_release()
        if choice == "STEAMQUIT":
            steam_js(f"(SteamClient.Apps.TerminateApp('{steamgame}', false), 1)", "quit Steam game")
            time.sleep(2.5)
            steam_ui("to_game", "SteamUIStore.WindowStore.GamepadUIMainWindowInstance.NavigateToRunningApp(), 1")
            log(f"Steam game {steamgame} closed, back to Prism")
        elif choice == "STEAMMENU":
            steam_menu_on_purpose()
        return
    if choice == "HOME":                # the card stays up as "Saving your game..." while we save and quit
        signal_game(pid, signal.SIGCONT)
        time.sleep(0.15)
        try:
            quit_game(cover=False, shot=shot)
        finally:
            try:
                cc_request({"cmd": "saved"})
            except OSError:
                pass
            steam_menus_release()
            for _ in range(25):         # "Saved" (0.8 s) + fade: then it must be gone
                time.sleep(0.1)
                try:
                    if cc_request({"cmd": "state"}, timeout=0.3) == "IDLE":
                        break
                except OSError:
                    break
            else:
                log("control center still up after saving: restarting it")
                subprocess.run(["pkill", "-f", "deck_cc.py --daemon"])
        return
    for _ in range(6):                  # the card must really be gone; if not, end its process (window goes with it)
        try:
            if cc_request({"cmd": "state"}, timeout=0.3) == "IDLE":
                break
        except OSError:
            break
        time.sleep(0.1)
    else:
        log("control center still up after the choice: restarting it")
        subprocess.run(["pkill", "-f", "deck_cc.py --daemon"])
    steam_menus_release()               # Steam button works normally again; closes Steam's menu if one is open
    signal_game(pid, signal.SIGCONT)
    if choice == "SAVESTATE":
        time.sleep(0.5)
        slot = next_state_slot(rom)
        ra_goto_slot(pid, slot); retroarch_cmd("SAVE_STATE")
        toast("State saved", f"Slot {slot}")
        return
    if choice.startswith("LOAD:"):
        time.sleep(0.5)
        slot = choice.split(":", 1)[1]
        ra_goto_slot(pid, -1 if slot == "auto" else int(slot)); retroarch_cmd("LOAD_STATE")
        toast("State loaded", next((st["label"] for st in states if str(st["slot"]) == slot), ""))
        return
    if choice == "HOME":
        time.sleep(0.3)
        quit_game(cover=True, shot=shot)
    elif choice in ("LASTSAVE", "QUIT"):
        kill_game_nosave(pid)
        if choice == "LASTSAVE":
            time.sleep(1.2)
            open("/tmp/game_hub_skip", "w").close()   # relaunching to the resume point: straight in, no hub
            esde_keys(["Return"])


def steam_home(pad):
    """Steam button in an emulator game: console-style control center. Close the Steam menu it opened, freeze the game
    (SIGSTOP), show deck_cc.py over it: Resume / Save & go Home / Back to last save / Quit without saving."""
    pid = esde_pid()
    if pid and not game_running(pid) and _press_focus[0] == str(STEAM_UI):
        # pressed while in Steam's own screens (the Store from For You, the library...) with Prism behind:
        # like the Switch's HOME button, go back to Prism (2026-10-08)
        time.sleep(0.25)                 # Steam opens its menu on this release: close it, then return
        steam_ui("close", JS_CLOSE)
        steam_ui("to_game", "SteamUIStore.WindowStore.GamepadUIMainWindowInstance.NavigateToRunningApp(), 1")
        log("Steam button in Steam's UI: back to Prism")
        _press_focus[0] = ""
        return
    if not pid or not game_running(pid):
        if docked():
            steam_ui("close", JS_CLOSE)  # docked: Steam's menu never stays up by accident
            log("Steam button: no ES-DE game, docked - Steam menu kept closed")
            return
        log("Steam button: no ES-DE game running, leaving it to Steam")
        return   # menus / Steam games: the Steam button does its normal thing
    log("Steam button: control center")
    # Steam opens its own menu on release (focus -> Steam UI, app 769). Wait for it, close it with B on the virtual
    # pad, and only continue once it's really gone: the control center opened under Steam's menu froze the game
    # behind an invisible card (2026-10-07).
    if not close_steam_menu(pad):
        log("Steam button: Steam's menu would not close, leaving it to Steam")
        return
    shot = grab_shot("/tmp/deck_cc_shot.png")
    signal_game(pid, signal.SIGSTOP)
    sel = selected()
    name = sel[1] if sel else ""
    cc = subprocess.Popen(["/usr/bin/python3", f"{prism_config.PRISM}/deck_cc.py", name, shot or ""], env=esde_env(pid),
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    choice = cc_choice(cc, pad)
    log(f"control center: {choice}")
    signal_game(pid, signal.SIGCONT)
    if choice == "HOME":
        time.sleep(0.3)
        quit_game(cover=True, shot=shot)   # the frame from before the pause = where you left off
    elif choice in ("LASTSAVE", "QUIT"):
        kill_game_nosave(pid)
        if choice == "LASTSAVE":   # ES-DE still has the game selected: launch it again -> loads the last save point
            time.sleep(1.2)
            esde_keys(["Return"])


def esde_game_procs(pid):
    """PIDs of the game ES-DE launched (emulator + wrappers), found via ES-DE's children and Steam's reaper.
    Empty in the menus and for Steam/PC games (those run under their own Steam launch)."""
    return prism_state.game_procs(pid)


def game_running(pid):
    return prism_state.game_running(pid)


def quit_game(cover=False, shot=None, fade=False):
    """L4+R4: close the emulator ES-DE launched. SIGTERM goes to the innermost processes first (RetroArch saves its
    auto-resume state on a clean exit; flatpak/bwrap wrappers would SIGKILL their child if they died first).
    Steam/PC games are left alone (they have their own save/exit)."""
    pid = esde_pid()
    if not pid or not game_running(pid):
        return   # menus, or a Steam/PC game (it has its own save and exit)

    tree = lambda: esde_game_procs(pid)

    def children(p):
        out = []
        for t in glob.glob(f"/proc/{p}/task/*/children"):
            try:
                out += [int(k) for k in open(t).read().split()]
            except OSError:
                pass
        return out

    def cmdline(p):
        try:
            return open(f"/proc/{p}/cmdline", "rb").read()
        except OSError:
            return b""

    log("L4+R4: quitting the game")
    t0 = time.time()
    if not cover:
        if shot and os.path.exists(shot):
            subprocess.run(["cp", shot, "/tmp/deck_quit_shot.png"])
        else:
            grab_shot("/tmp/deck_quit_shot.png")
    snap = [k for k in tree() if k != pid and not protected(k)]   # taken once (re-parenting); never ES-DE's own
    if fade:    # L4+R4 (2026-10-09): the home wave fades in over the game (after the resume shot), out once ES-DE is back
        try:
            cc_request({"cmd": "cover"}, timeout=0.5); time.sleep(0.4)
        except OSError:
            fade = False

    def alive():
        out = []
        for k in snap:
            try:
                if open(f"/proc/{k}/stat").read().rsplit(")", 1)[1].split()[0] != "Z":
                    out.append(k)
            except (OSError, IndexError):
                pass
        return out
    # emulators without save-on-exit: press their save-state key first (PPSSPP loads the newest state on boot)
    for k in snap:
        if b"PPSSPP" in cmdline(k) and not children(k):
            pre_quit_save(k, "uinput:f2", f"{HOME}/.var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP/PPSSPP_STATE", ".ppst")
            break
        if b"dolphin-emu" in cmdline(k) and not children(k):     # GameCube / Wii: Save State Slot 1 = Shift+F1
            pre_quit_save(k, "uinput:f6",
                          f"{HOME}/.var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/StateSaves", ".s01")
            break
        if b"pcsx2" in cmdline(k).lower() and not children(k):   # PCSX2 saves on shutdown only from its own menu
            pre_quit_save(k, "pine:1", f"{prism_config.SAVES}/pcsx2/states", ".p2s", wait=8.0)
            break
    splash = None
    if cover:   # console-style HOME transition over the closing game (deck_splash.py home)
        try:
            os.remove("/tmp/deck_splash_done")
        except OSError:
            pass
        sel = selected() or ("", "", "")
        splash = subprocess.Popen(["/usr/bin/python3", f"{prism_config.PRISM}/deck_splash.py", "home", sel[2], sel[0]],
                                  env={**esde_env(pid), "DECK_SPLASH_SHOT": shot or ""},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(0.35)
    for k in snap:   # a frozen helper (bwrap, dbus proxy) left from a pause stalls the exit by 10 s (2026-10-08)
        try:
            os.kill(k, signal.SIGCONT)
        except OSError:
            pass
    for k in [k for k in snap if not children(k)]:   # the emulator itself; wrappers above it exit on their own
        try:
            os.kill(k, signal.SIGTERM)
        except OSError:
            pass
    if any(b"eden" in cmdline(k).lower() for k in snap):   # Switch: Stop Emulation (F5) lets the game finish
        try:                                                 # writing its save before the window is closed
            deck_mcp.tool_key("f5"); log("Eden: Stop Emulation (F5) before closing")
        except Exception as e:
            log(f"Eden F5 failed: {e}")
        time.sleep(2.0)
    stubborn = any(x in cmdline(k).lower() for k in snap for x in (b"eden", b"ryujinx", b"vita3k"))
    patience = 2.5 if stubborn else 8   # Switch/Vita emulators ignore SIGTERM; their games save on their own
    while alive() and time.time() - t0 < patience:   # (launch wrappers may still have a moment of bookkeeping)
        time.sleep(0.25)
    if stubborn:
        for k in alive():
            try:
                os.kill(k, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(0.5)
    if alive():
        log(f"still alive after {time.time() - t0:.1f}s: " +
            ", ".join(f"{k}:{cmdline(k).split(b'\\0')[0].decode(errors='replace')[-30:]}" for k in alive()))
        for k in alive():
            try:
                os.kill(k, signal.SIGTERM)
            except OSError:
                pass
        time.sleep(3)
    for k in alive():   # anything that ignored SIGTERM
        try:
            os.kill(k, signal.SIGKILL)
        except OSError:
            pass
    # emulators that detach from ES-DE's process tree (Eden's AppImage re-parents to systemd): end anything still
    # running this game's file (2026-10-08: Eden kept running after Save & go Home)
    sel_now = selected()
    if sel_now and sel_now[0]:
        rom = sel_now[0].encode()
        for d_ in os.listdir("/proc"):
            if not d_.isdigit() or int(d_) in (os.getpid(), pid):
                continue
            c = cmdline(int(d_))
            if rom in c and not protected(int(d_)) and b"deck_curate" not in c:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.kill(int(d_), sig); time.sleep(1.5)
                    except OSError:
                        break
                log(f"ended leftover {c.split(b'\\0')[0].decode(errors='replace')[-40:]} ({d_})")
    if not cover:   # L4+R4: still remember where you left off for the "Resuming" splash
        sel = selected()
        if sel and (sel[2] not in NO_RESUME or sel[2] in LAST_PLAYED) and os.path.exists("/tmp/deck_quit_shot.png"):
            d = f"{prism_config.PRISM}/resume_shots/{sel[2]}"; os.makedirs(d, exist_ok=True)
            subprocess.run(["cp", "/tmp/deck_quit_shot.png", f"{d}/{os.path.basename(sel[0])}.png"])
    if splash:   # let ES-DE redraw its list, then fade the cover away
        time.sleep(0.7)
        open("/tmp/deck_splash_done", "w").close()
    if fade:
        time.sleep(0.6)
        try:
            cc_request({"cmd": "uncover"}, timeout=0.5)
        except OSError:
            pass
    log(f"L4+R4: done in {time.time() - t0:.1f}s")


def favorite(pad):
    pid = esde_pid()
    sel = selected()
    if pid and esde_idle(pid) and sel and sel[2] in FLOW_SYSTEMS:
        threading.Thread(target=flow_watch, args=("later",), daemon=True).start()     # R4 = Watch later
        return
    if pid and esde_idle(pid) and sel and sel[2] != "foryou":
        inject(pad, "Y")
        log("R4 favorite toggled")


def system_view():
    """ES-DE is showing the console row: running, nothing launched, no game selected, screensaver not in play."""
    pid = esde_pid()
    if not pid or not esde_idle(pid) or os.path.exists(STATE) or os.path.exists("/tmp/esde_screensaver"):
        return None
    try:
        if time.time() - float(open("/tmp/esde_screensaver_end").read()) < 1.5:
            return None  # this press woke the screensaver
    except (OSError, ValueError):
        pass
    return pid


def open_steam(page, label):
    if system_view():
        subprocess.Popen(["/bin/bash", "-c", STEAM_URL % page], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"{label} opened")


def power_menu():
    pid = system_view()
    if pid:
        subprocess.Popen(["/usr/bin/python3", f"{prism_config.PRISM}/deck_power_menu.py"], env=esde_env(pid),
                         start_new_session=True, stdout=open("/tmp/deck_power_menu.log", "w"),
                         stderr=subprocess.STDOUT)
        log("power menu opened")


def icon_focus():
    pid = system_view()
    if pid and subprocess.run(["pgrep", "-f", "deck_icon_focus.py"], capture_output=True).returncode != 0:
        subprocess.Popen(["/usr/bin/python3", ICON_FOCUS], env=esde_env(pid), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("icon cursor opened")


_pending = []


def bit(rep, b):
    return bool(rep[b[0]] >> b[1] & 1)


KEYS = {"LEFT": "LEFT", "RIGHT": "RIGHT", "UP": "UP", "DOWN": "DOWN", "A": "ENTER", "B": "BACKSPACE",
        "Y": "INSERT", "X": "DELETE", "SELECT": "F1", "START": "ESC", "LB": "PAGEUP", "RB": "PAGEDOWN"}


def docked():
    """On the TV: the dock's display port is connected (ES-DE then ignores injected gamepads, so keys are used)."""
    return prism_profile.docked()


_last_mark = [0.0]


_input_at = [time.time()]   # newest real controller/touch input, in memory (switchhome.py's screensaver uses it)


def mark_input():
    now = time.time()
    _input_at[0] = now
    if now - _last_mark[0] > 5:
        _last_mark[0] = now
        try:
            with open(LAST_INPUT, "w") as f:
                f.write(str(now))
        except OSError:
            pass


class LazyPad:
    """Stands in for the virtual Xbox pad: deck_mcp.gamepad() is fetched per write, so the pad only exists while it
    is used (deck_mcp unplugs it after 30 s idle) and doesn't hold a Steam controller slot during play (2026-10-09)."""
    def write(self, *a):
        return deck_mcp.gamepad().write(*a)

    def syn(self):
        return deck_mcp.gamepad().syn()


def inject(pad, btn, n=1, gap=0.09, hold=0.06):
    # ES-DE takes its keyboard map in handheld and docked mode alike; the keyboard holds no controller slot
    if btn in KEYS:
        e = deck_mcp._evdev().ecodes; kbd = deck_mcp.keyboard(); code = getattr(e, "KEY_" + KEYS[btn])
        for _ in range(n):
            kbd.write(e.EV_KEY, code, 1); kbd.syn(); time.sleep(hold)
            kbd.write(e.EV_KEY, code, 0); kbd.syn(); time.sleep(gap)
        return
    for _ in range(n):
        deck_mcp._pad_event(pad, btn, True); time.sleep(hold)
        deck_mcp._pad_event(pad, btn, False); time.sleep(gap)


def in_box(x, y, box):
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def help_tap(pad, x):
    """A tap on Prism's bottom hint bar presses that hint's button (help_bar.py finds the badges), like the
    Switch's touchable hints (2026-10-08). Only while Prism itself is in front, not in a game."""
    pid = esde_pid()
    if not pid or not esde_idle(pid) or focus_now() == STEAM_UI:
        return False
    try:
        import help_bar
        btn = help_bar.button_at(x, grab_shot_fast("/tmp/help_tap.png"))
    except Exception as e:
        log(f"hint bar tap failed: {e}")
        return False
    if not btn:
        return False
    log(f"hint bar tap at x={x}: {btn}")
    inject(pad, btn)
    return True


FLOW_SYSTEMS = ("movies", "tv")   # cover-flow lists: swipe sideways, tap a poster
FLOW_STEP = 150                   # finger travel per poster while dragging
FLOW_PITCH = 256                  # centre-to-centre distance of the posters (1280 px / 5 shown)
FLOW_Y = (290, 715)               # the poster band


class Touch:
    """One finger on the screen. Drags step ES-DE while the finger moves; taps and flicks act on release."""
    ROW_STEP, LIST_STEP, AXIS_LOCK = 150, 40, 18

    def __init__(self, pad, x, y):
        self.pad, self.x0, self.y0 = pad, x, y
        self.ax, self.ay = x, y                  # anchor: where the last step was emitted
        self.axis, self.steps = None, 0
        self.hist = [(time.monotonic(), x, y)]
        if system_view():
            self.ctx = "row"
        else:
            pid = esde_pid()
            self.ctx = "list" if pid and esde_idle(pid) and os.path.exists(STATE) else None
            sel = selected() if self.ctx else None
            if sel and sel[2] in FLOW_SYSTEMS:
                self.ctx = "flow"                # Movies / TV: horizontal cover flow (theme _coverflow.xml)

    def step(self, btn, n=1):
        inject(self.pad, btn, n, gap=0.015, hold=0.025)
        self.steps += n

    def move(self, x, y):
        self.hist.append((time.monotonic(), x, y))
        self.hist = self.hist[-12:]
        if self.ctx is None:
            return
        dx, dy = x - self.x0, y - self.y0
        if self.axis is None and max(abs(dx), abs(dy)) >= self.AXIS_LOCK:
            self.axis = "h" if abs(dx) > abs(dy) else "v"
            if self.axis == "h":   # first console step after ~90 px of travel, then every ROW_STEP
                self.ax = self.x0 + (60 if dx > 0 else -60)
        if self.ctx == "flow" and self.axis == "h":
            while abs(x - self.ax) >= FLOW_STEP:
                d = -1 if x < self.ax else 1      # finger moves left -> later posters
                self.step("RIGHT" if d < 0 else "LEFT")
                self.ax += d * FLOW_STEP
        if self.ctx == "row" and self.axis == "h":
            while abs(x - self.ax) >= self.ROW_STEP:
                d = -1 if x < self.ax else 1      # finger moves left -> next console
                self.step("RIGHT" if d < 0 else "LEFT")
                self.ax += d * self.ROW_STEP
        elif self.ctx == "list" and self.axis == "v":
            while abs(y - self.ay) >= self.LIST_STEP:
                d = -1 if y < self.ay else 1      # finger moves up -> later games
                self.step("DOWN" if d < 0 else "UP")
                self.ay += d * self.LIST_STEP

    def velocity(self):
        now = time.monotonic()
        recent = [h for h in self.hist if now - h[0] <= 0.09]
        if len(recent) < 2:
            return 0.0, 0.0
        (t0, x0, y0), (t1, x1, y1) = recent[0], recent[-1]
        dt = max(t1 - t0, 1e-3)
        return (x1 - x0) / dt, (y1 - y0) / dt

    def up(self, x, y):
        self.move(x, y)
        dx, dy = x - self.x0, y - self.y0
        dt = time.monotonic() - self.hist[0][0] if len(self.hist) == 1 else self.hist[-1][0] - self.hist[0][0]
        tap = self.axis is None and abs(dx) < 30 and abs(dy) < 30
        if cc_open or hub_open[0]:   # the card / game hub is up: taps go to it (gamescope routes touch to the game)
            if tap:
                try:
                    cc_request({"cmd": "tap", "x": self.x0, "y": self.y0}, timeout=0.5)
                    log(f"card tap at {self.x0},{self.y0}")
                except OSError:
                    pass
            return
        if tap and self.y0 >= 720 and help_tap(self.pad, self.x0):
            return
        vx, vy = self.velocity()
        if self.ctx == "row":
            if tap and ICON_Y[0] <= self.y0 <= ICON_Y[1] and ICON_X[0] <= self.x0 <= ICON_X[3]:
                if self.x0 < ICON_X[1]:
                    controllers_screen("touch")
                elif self.x0 < ICON_X[2]:
                    settings_screen("touch")
                else:
                    power_menu()
            elif tap and ROW_Y[0] <= self.y0 <= ROW_Y[1]:
                n = round((self.x0 - 640) / TILE_PITCH)
                inject(self.pad, "A") if n == 0 else inject(self.pad, "RIGHT" if n > 0 else "LEFT", abs(n), gap=0.015, hold=0.025)
            elif self.axis == "h" and abs(vx) > 1400:          # flick: coast a little
                self.step("RIGHT" if vx < 0 else "LEFT", min(3, int(abs(vx) / 1400)))
            elif self.axis == "h" and self.steps == 0 and abs(dx) > 50:
                self.step("RIGHT" if dx < 0 else "LEFT")      # short swipe still moves one
        elif self.ctx == "flow":
            if tap and FLOW_Y[0] <= self.y0 <= FLOW_Y[1]:
                n = round((self.x0 - 640) / FLOW_PITCH)
                inject(self.pad, "A") if n == 0 else inject(self.pad, "RIGHT" if n > 0 else "LEFT", abs(n), gap=0.015, hold=0.025)
            elif self.axis == "h" and abs(vx) > 1300:          # flick: coast through a few posters
                self.step("RIGHT" if vx < 0 else "LEFT", min(6, int(abs(vx) / 900)))
            elif self.axis == "h" and self.steps == 0 and abs(dx) > 50:
                self.step("RIGHT" if dx < 0 else "LEFT")
        elif self.ctx == "list":
            if tap and in_box(self.x0, self.y0, CARD):
                inject(self.pad, "A")
            elif tap and in_box(self.x0, self.y0, LOGO):
                inject(self.pad, "B")
            elif self.axis == "v" and abs(vy) > 900:
                self.step("DOWN" if vy < 0 else "UP", min(15, int(abs(vy) / 450)))
            elif self.axis == "h" and abs(dx) > 80:
                inject(self.pad, "RB" if dx < 0 else "LB")


def selftest_driving():
    """A self-test (selftest/run.py or one of its *_test.py) is running: it imports this module and handles its own
    Steam presses, so the supervisor must not act on the same press too (it closed the test's card as Resume)."""
    return prism_state.selftest_running()


def external_pads(own_path):
    """Gamepads as ES-DE sees them (Steam's virtual pads), minus our own injector. Used only when docked."""
    import evdev
    out = []
    for p in evdev.list_devices():
        if p == own_path:
            continue
        try:
            d = evdev.InputDevice(p)
            keys = d.capabilities().get(evdev.ecodes.EV_KEY, [])
            if evdev.ecodes.BTN_SOUTH in keys and evdev.ecodes.BTN_TL in keys:
                out.append(d)
            else:
                d.close()
        except OSError:
            pass
    return out


def start_touch():
    try:
        import touch_reader                     # no sudo when Prism's udev rule lets us read the panel
        return subprocess.Popen(touch_reader.command(), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, bufsize=0)
    except OSError as e:
        log(f"touch reader failed: {e}")
        return None


EXT_MAP = None


def drain_external(devs, st):
    """Read pending events from the TV's controllers into a button-state dict (non-blocking)."""
    import evdev
    E = evdev.ecodes
    m = {E.BTN_TL: "LB", E.BTN_TR: "RB", E.BTN_Y: "Y", E.BTN_B: "B", E.BTN_SELECT: "VIEW"}
    for d in list(devs):
        try:
            while True:
                ev = d.read_one()
                if ev is None:
                    break
                if ev.type == E.EV_KEY and ev.value:
                    mark_input()
                elif ev.type == E.EV_ABS and ev.code in (E.ABS_X, E.ABS_Y, E.ABS_RX, E.ABS_RY) and abs(ev.value) > 12000:
                    mark_input()
                if ev.type == E.EV_KEY and ev.code == E.BTN_MODE:
                    st["STEAM"] = ev.value != 0
                if ev.type == E.EV_KEY and ev.code in m:
                    st[m[ev.code]] = ev.value != 0
                elif ev.type == E.EV_KEY and ev.code == E.BTN_DPAD_DOWN:
                    st["DOWN"] = ev.value != 0
                elif ev.type == E.EV_ABS and ev.code == E.ABS_HAT0Y:
                    st["DOWN"] = ev.value > 0
        except (OSError, BlockingIOError):
            pass


def handle_buttons(pad, state, now, down, fired, shoulder, b_on_row):
    """Shared button logic for the Deck's controller (handheld) and any controller (docked)."""
    for name in ("LB", "RB"):
        pressed = state.get(name, False)
        if pressed and name not in shoulder:
            if system_view():
                inject(pad, "LEFT" if name == "LB" else "RIGHT", gap=0.02)
                shoulder[name] = now + 0.45
            else:
                shoulder[name] = None  # in a game list ES-DE handles L/R itself
        elif pressed and shoulder.get(name) and now >= shoulder[name]:
            inject(pad, "LEFT" if name == "LB" else "RIGHT", gap=0.02)
            shoulder[name] = now + 0.16
        elif not pressed and name in shoulder:
            del shoulder[name]
    down_now = state.get("DOWN", False)
    if down_now and not down.get("DPAD_DOWN"):
        icon_focus()
    down["DPAD_DOWN"] = down_now
    if state.get("R5"):
        if "R5" not in down:
            down["R5"] = now; fired.discard("R5")
        elif "R5" not in fired and now - down["R5"] >= 0.6:
            fired.add("R5"); music_button(hold=True)
    elif "R5" in down:
        if "R5" not in fired:
            music_button(hold=False)
        del down["R5"]
    for name in ("L4", "R4", "L5", "Y", "B", "VIEW"):
        pressed = state.get(name, False)
        if pressed and name not in down:
            down[name] = now
            fired.discard(name)
            if name == "B":
                b_on_row = system_view() is not None
            if name == "L5":
                pass                     # search happens on release (L5 + R5 is the rescue chord)
            elif name == "Y":
                controllers_screen("Y")
            elif name == "VIEW":         # in a game list: cycle the sort (list_sort.py, 2026-10-08)
                import list_sort
                threading.Thread(target=list_sort.cycle, daemon=True).start()
        elif pressed and name not in fired and now - down[name] >= HOLD_S:
            fired.add(name)
            if name == "B" and b_on_row:
                power_menu()
        elif not pressed and name in down:
            if name == "L5" and "L5" not in fired and now - down[name] < 1.0:
                search()
            fired.discard("L5") if name == "L5" else None
            if name in ("L4", "R4") and "COMBO" not in fired and now - down[name] < 1.5:
                (remove_selected if name == "L4" else favorite)(pad)
            del down[name]
            if "L4" not in down and "R4" not in down:
                fired.discard("COMBO")
    # Steam button = console-style HOME in an ES-DE-launched emulator game: short press on its own (not a Steam chord)
    if state.get("L5") and state.get("R5"):     # rescue chord: both lower back paddles held 2 s
        down.setdefault("RESCUE", now)
        fired.update(("R5", "L5"))               # no music toggle / search from this press
        if "RESCUE" not in fired and now - down["RESCUE"] >= 2.0:
            fired.add("RESCUE"); rescue()
    else:
        down.pop("RESCUE", None); fired.discard("RESCUE")
    if state.get("STEAM"):
        if "STEAM" not in down:
            down["STEAM"] = now; fired.discard("STEAM_CHORD")
            if not state.get("_others"):
                steam_down(pad)          # card up right away, ahead of Steam's menu
                # time the press from when the card is up: putting it up can take ~1.5 s on the TV (4K frame grab),
                # which made every quick tap look like a long press and hid the card again (2026-10-09)
                down["STEAM"] = time.monotonic()
        if state.get("_others") or now - down["STEAM"] >= 0.8:
            if "STEAM_CHORD" not in fired:
                fired.add("STEAM_CHORD"); steam_cancel()
    elif "STEAM" in down:
        t = now - down.pop("STEAM")
        log(f"Steam button released after {t:.2f}s" + (" (chord, ignored)" if "STEAM_CHORD" in fired else ""))
        if cc_open:
            steam_up(pad) if (t < 0.8 and "STEAM_CHORD" not in fired) or cc_open.get("waiting") else steam_cancel()
        elif t < 0.8 and "STEAM_CHORD" not in fired:
            steam_home(pad)
    # L4 + R4 held together: quit the game that ES-DE launched (any emulator), back to Prism
    if state.get("L4") and state.get("R4"):
        both = max(down.get("L4", now), down.get("R4", now))
        if "COMBO" not in fired and now - both >= 0.6:
            fired.add("COMBO")
            quit_game(fade=True)
    return b_on_row


def cc_warm(tries=40, gap=3.0):
    """Start the card overlay and keep trying: at boot Steam's X display isn't ready for a few seconds, the first
    start died (Gtk couldn't be initialized) and the screensaver and pop-ups stayed dead until the Steam button
    happened to restart it (2026-10-16)."""
    if os.path.exists(f"{prism_config.PRISM}/cc_overlay_off"):
        return
    for _ in range(tries):
        if cc_daemon():
            return
        time.sleep(gap)


def main():
    threading.Thread(target=lambda: (cc_warm(), cc_clear_stale()), daemon=True).start()   # warm the Steam-button card; nothing left frozen from before a restart
    signal.signal(signal.SIGUSR1, lambda *_: _pending.append("remove"))
    signal.signal(signal.SIGUSR2, lambda *_: _pending.append("favorite"))
    pad = LazyPad()           # virtual pad only on demand (presses to ES-DE go through the keyboard, see inject)
    deck_mcp.keyboard()       # likewise the virtual keyboard (save-state keys before L4+R4 quits PCSX2)
    ctrl = None
    touch, touch_started = start_touch(), time.monotonic()
    finger, tbuf = None, b""
    prev_buttons = b""
    down = {}      # button -> time pressed
    fired = set()  # holds already acted on
    b_on_row = False  # B went down on the console row (not while leaving a game list)
    shoulder = {}  # "LB"/"RB" -> next repeat time (only while on the console row)
    ext, ext_scan, ext_state = None, 0.0, {}
    last_rep = None                    # the Deck's last report, reused when only another controller woke us   # docked: other controllers (Steam virtual pads)
    while True:
        while _pending:
            (remove_selected if _pending.pop(0) == "remove" else favorite)(pad)
        if ctrl is None:
            ctrl = open_controller()
            if ctrl is None:
                time.sleep(2)
                continue
            log("controller open")
        if (touch is None or touch.poll() is not None) and time.monotonic() - touch_started > 5:
            touch, tbuf, touch_started = start_touch(), b"", time.monotonic()
        fds = [ctrl] + ([touch.stdout] if touch and touch.poll() is None else []) + [d.fd for d in (ext or [])]
        r, _, _ = select.select(fds, [], [], 0.5)   # other controllers wake us too: docked, the Deck's own pad goes quiet
                                                    # and their Home presses were seen seconds late (= "held", 2026-10-09)
        cc_poll(pad)                 # the control center's answer (Steam button card)
        if touch and touch.stdout in r:
            try:
                chunk = os.read(touch.stdout.fileno(), 65536)
            except OSError:
                chunk = b""
            if not chunk:
                touch = None
            tbuf += chunk
            *lines, tbuf = tbuf.split(b"\n")
            # only the newest move matters: collapse runs of M lines so a backlog never replays slowly
            todo = []
            for raw in lines:
                parts = raw.split()
                if len(parts) != 3:
                    continue
                if parts[0] == b"M" and todo and todo[-1][0] == b"M":
                    todo[-1] = parts
                else:
                    todo.append(parts)
            for kind, x, y in todo:
                x, y = int(x), int(y)
                try:
                    if kind == b"D":
                        mark_input()
                        finger = Touch(pad, x, y)
                    elif kind == b"M" and finger:
                        finger.move(x, y)
                    elif kind == b"U" and finger:
                        finger.up(x, y)
                        finger = None
                except Exception as e:
                    log(f"touch handling failed: {e}")
                    finger = None
        ext_ready = bool(ext) and any(d.fd in r for d in ext)
        if ctrl not in r and not ext_ready:
            continue
        if ctrl in r:
            try:
                rep = os.read(ctrl, 64)
            except OSError:
                os.close(ctrl)
                ctrl = None
                continue
            if len(rep) < 14 or rep[2] != 0x09:
                continue
            if any(rep[8:16]) or rep[8:16] != prev_buttons:
                mark_input()            # (rate-limited inside) any held button counts, not just changes
            elif len(rep) >= 56:
                lt, rt = struct.unpack_from("<HH", rep, 44)
                if lt > 3000 or rt > 3000 or any(abs(v) > 6000 for v in struct.unpack_from("<hhhh", rep, 48)):
                    mark_input()        # sticks / triggers in use
            prev_buttons = rep[8:16]
            last_rep = rep
        else:
            if last_rep is None:
                drain_external(ext, ext_state)
                continue
            rep = last_rep
        now = time.monotonic()
        state = {"L4": bit(rep, L4), "R4": bit(rep, R4), "L5": bit(rep, L5), "R5": bit(rep, R5), "STEAM": bit(rep, STEAM_BTN),
                 "B_ANY": bit(rep, BTN_B),
                 # other REAL buttons held (a Steam chord like Steam+R1); capacitive touch bits (thumb resting on a
                 # stick or trackpad, bytes 10/13) must not count
                 "_others": bool(rep[8] or (rep[9] & 0xDF) or (rep[10] & 0x01) or (rep[13] & 0x06) or (rep[14] & 0x04))}
        # other controllers (Pro Controllers, DualSense...): their Home / PS button opens Prism's card in a game, docked
        # or not (2026-10-09: handheld with a Pro Controller, Home still went to Steam's menu)
        if ext is None or now - ext_scan > 10:              # controllers come and go
            for d in ext or []:
                try: d.close()
                except Exception: pass
            ext, ext_scan = external_pads(pad.device.path if hasattr(pad, "device") else ""), now
        drain_external(ext, ext_state)
        if selftest_driving():
            ext_state["STEAM"] = False   # the self-test presses Home through its own copy of this code
        deck_steam = state["STEAM"]
        if docked():
            state.update(ext_state)
            state["STEAM"] = deck_steam or bool(ext_state.get("STEAM"))
        else:
            state["STEAM"] = deck_steam or bool(ext_state.get("STEAM"))
            state.update(LB=bit(rep, BTN_L), RB=bit(rep, BTN_R), DOWN=bit(rep, BTN_DOWN), Y=bit(rep, BTN_Y),
                         B=bit(rep, BTN_B), VIEW=bit(rep, BTN_VIEW))
        b_on_row = handle_buttons(pad, state, now, down, fired, shoulder, b_on_row)


if __name__ == "__main__":
    main()
