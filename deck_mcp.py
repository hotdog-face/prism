#!/usr/bin/env python3
"""Steam Deck MCP server.

Speaks MCP (JSON-RPC 2.0, newline-delimited) over stdio, using only the Python standard
library plus the python-evdev that ships with SteamOS, so nothing has to be installed and
OS updates can't break it. Meant to be launched over SSH from the machine running the agent:

    claude mcp add --scope user steamdeck -- ssh -T steamdeck python3 ~/prism/deck_mcp.py

Input goes through virtual uinput devices: an Xbox 360 pad and a keyboard (both modes), an
absolute pointer (Desktop Mode, KDE on X11) and a clone of the touch panel (Game Mode, where
gamescope ignores virtual pointers). README.md next to this file is the guide for agents.
"""
import base64
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

SERVER_NAME = "steamdeck"
SERVER_VERSION = "0.1.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
STEAM_ROOT = os.path.expanduser("~/.local/share/Steam")
MAX_OUTPUT = 40_000


def log(*args):
    print("[deck-mcp]", *args, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- session helpers

def pgrep(name):
    r = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", name], capture_output=True, text=True)
    return [int(p) for p in r.stdout.split()]


def read_environ(pid):
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            pairs = (kv.split(b"=", 1) for kv in f.read().split(b"\0") if b"=" in kv)
            return {k.decode(errors="replace"): v.decode(errors="replace") for k, v in pairs}
    except OSError:
        return {}


SESSION_KEYS = ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "GAMESCOPE_WAYLAND_DISPLAY",
                "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE",
                "XDG_CURRENT_DESKTOP", "STEAM_RUNTIME")


def session_env():
    """Our SSH environment plus the display/dbus variables of the logged-in graphical session."""
    env = dict(os.environ)
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    # Later entries win: steam's view of the session is the most reliable in both modes.
    for name in ("plasmashell", "kwin_x11", "gamescope-wl", "gamescope", "steam"):
        for pid in pgrep(name)[:1]:
            src = read_environ(pid)
            env.update({k: src[k] for k in SESSION_KEYS if k in src})
    return env


def current_mode():
    if pgrep("gamescope-wl") or pgrep("gamescope"):
        return "game"
    if pgrep("plasmashell"):
        return "desktop"
    return "unknown"


def run(cmd, timeout=30, env=None, shell=False):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       env=env or session_env(), shell=shell)
    return r.returncode, r.stdout, r.stderr


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("screenshot is not a PNG")
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


# ---------------------------------------------------------------- screen

_screen = {"w": 1280, "h": 800}


def grab_png(path):
    env = session_env()
    mode = current_mode()
    if mode == "game":
        # gamescope writes the composited frame (game + Steam overlay) to the given path.
        code, out, err = run(["gamescopectl", "screenshot", path], env=env, timeout=10)
        for _ in range(30):
            if os.path.exists(path) and os.path.getsize(path) > 0:
                time.sleep(0.15)  # let the writer finish
                return
            time.sleep(0.1)
        raise RuntimeError(f"gamescope screenshot failed: {(err or out).strip()}")
    display = env.get("DISPLAY", ":0")
    # A powered-down panel (DPMS) captures as solid black, so wake it first.
    code, out, _ = run(["xset", "q"], env=env, timeout=5)
    if "Monitor is Off" in out or "Monitor is in Standby" in out or "Monitor is in Suspend" in out:
        run(["xset", "dpms", "force", "on"], env=env, timeout=5)
        time.sleep(1.0)
    # Spectacle reads KWin's composited output; ffmpeg's x11grab only sees black under KWin.
    code, out, err = run(["spectacle", "--background", "--nonotify", "--fullscreen", "--output", path],
                         env=env, timeout=20)
    if code != 0 or not os.path.exists(path):
        raise RuntimeError(f"spectacle failed: {(err or out).strip()}")


def tool_screenshot(max_width=1280, quality=4):
    tmp = tempfile.mkdtemp(prefix="deckshot-")
    png, jpg = os.path.join(tmp, "s.png"), os.path.join(tmp, "s.jpg")
    try:
        grab_png(png)
        w, h = png_size(png)
        _screen.update(w=w, h=h)
        out_w = min(int(max_width), w)
        code, _, err = run(["ffmpeg", "-loglevel", "error", "-y", "-i", png,
                            "-vf", f"scale={out_w}:-2", "-q:v", str(int(quality)), jpg], timeout=15)
        if code != 0:
            raise RuntimeError(f"jpeg encode failed: {err.strip()}")
        with open(jpg, "rb") as f:
            data = base64.b64encode(f.read()).decode()
    finally:
        for p in (png, jpg):
            if os.path.exists(p):
                os.remove(p)
        os.rmdir(tmp)
    note = f"Screen is {w}x{h} ({current_mode()} mode)."
    if out_w != w:
        note += f" Image is scaled to {out_w} wide; multiply image coordinates by {w / out_w:.3f} for click/drag."
    else:
        note += " Image pixels equal screen pixels for click/drag."
    return [{"type": "image", "data": data, "mimeType": "image/jpeg"}, {"type": "text", "text": note}]


# ---------------------------------------------------------------- virtual input devices

_devices = {}
# The virtual Xbox 360 pad takes a Steam controller slot while it exists. Kept plugged in, it pushed the Deck's own
# controls to player 2+ in emulators that pick player 1 by slot (PCSX2, DuckStation, Ryujinx) after a Steam
# restart (2026-10-09), so it is unplugged after PAD_IDLE seconds without use and re-created on demand.
PAD_IDLE = 30
_pad_last = [0.0]
_pad_reaper = []


def _reap_pad():
    while True:
        time.sleep(5)
        dev = _devices.get("pad")
        if dev is not None and time.monotonic() - _pad_last[0] > PAD_IDLE:
            _devices.pop("pad", None)
            try:
                dev.close()
            except Exception:
                pass


def _evdev():
    try:
        import evdev
        return evdev
    except ImportError as exc:
        raise RuntimeError("python-evdev is not available on this system") from exc


def gamepad():
    _pad_last[0] = time.monotonic()
    if not _pad_reaper:
        import threading
        _pad_reaper.append(threading.Thread(target=_reap_pad, daemon=True))
        _pad_reaper[0].start()
    if "pad" not in _devices:
        evdev = _evdev()
        e = evdev.ecodes
        stick = evdev.AbsInfo(0, -32768, 32767, 16, 128, 0)
        trigger = evdev.AbsInfo(0, 0, 255, 0, 0, 0)
        hat = evdev.AbsInfo(0, -1, 1, 0, 0, 0)
        caps = {
            e.EV_KEY: [e.BTN_A, e.BTN_B, e.BTN_X, e.BTN_Y, e.BTN_TL, e.BTN_TR, e.BTN_SELECT,
                       e.BTN_START, e.BTN_MODE, e.BTN_THUMBL, e.BTN_THUMBR],
            e.EV_ABS: [(e.ABS_X, stick), (e.ABS_Y, stick), (e.ABS_RX, stick), (e.ABS_RY, stick),
                       (e.ABS_Z, trigger), (e.ABS_RZ, trigger), (e.ABS_HAT0X, hat), (e.ABS_HAT0Y, hat)],
        }
        # Same identity as the kernel xpad driver, so SDL and Steam apply their known mapping.
        _devices["pad"] = evdev.UInput(caps, name="Microsoft X-Box 360 pad", vendor=0x045E,
                                       product=0x028E, version=0x0110, bustype=e.BUS_USB)
        time.sleep(1.5)  # give Steam time to notice the new controller
    return _devices["pad"]


def keyboard():
    if "kbd" not in _devices:
        evdev = _evdev()
        e = evdev.ecodes
        keys = [c for n, c in e.ecodes.items() if n.startswith("KEY_") and 0 < c < 0x2FF]
        _devices["kbd"] = evdev.UInput({e.EV_KEY: sorted(set(keys))}, name="Deck MCP Keyboard",
                                       vendor=0x1209, product=0x0001, bustype=e.BUS_USB)
        time.sleep(0.5)
    return _devices["kbd"]


ABS_MAX = 32767


def pointer():
    if "ptr" not in _devices:
        evdev = _evdev()
        e = evdev.ecodes
        axis = evdev.AbsInfo(0, 0, ABS_MAX, 0, 0, 0)
        # Absolute pointer laid out like a QEMU USB tablet: udev tags it as a mouse and
        # libinput maps the axes onto the whole screen.
        caps = {e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
                e.EV_ABS: [(e.ABS_X, axis), (e.ABS_Y, axis)],
                e.EV_REL: [e.REL_WHEEL, e.REL_HWHEEL]}
        _devices["ptr"] = evdev.UInput(caps, name="Deck MCP Pointer", vendor=0x1209,
                                       product=0x0002, bustype=e.BUS_USB)
        time.sleep(0.5)
    return _devices["ptr"]


PANEL_W, PANEL_H = 800, 1280  # the Deck's touch panel is portrait; gamescope rotates it


def touchscreen():
    """Clone of the built-in FTS3528 panel, which gamescope (unlike our pointer) accepts."""
    if "touch" not in _devices:
        evdev = _evdev()
        e = evdev.ecodes
        ax = lambda m, res: evdev.AbsInfo(0, 0, m, 0, 0, res)
        caps = {e.EV_KEY: [e.BTN_TOUCH],
                e.EV_ABS: [(e.ABS_X, ax(PANEL_W, 3)), (e.ABS_Y, ax(PANEL_H, 9)), (e.ABS_MT_SLOT, ax(9, 0)),
                           (e.ABS_MT_TRACKING_ID, ax(65535, 0)), (e.ABS_MT_POSITION_X, ax(PANEL_W, 3)),
                           (e.ABS_MT_POSITION_Y, ax(PANEL_H, 9))]}
        _devices["touch"] = evdev.UInput(caps, name="Deck MCP Touchscreen", vendor=0x1209, product=0x0003,
                                         bustype=0x18, input_props=[e.INPUT_PROP_DIRECT])
        time.sleep(1.5)
    return _devices["touch"]


def _panel(x, y):
    """Landscape screen pixels -> portrait panel units (the panel's top edge is the screen's right edge)."""
    w, h = _screen["w"], _screen["h"]
    x, y = max(0, min(w - 1, x)), max(0, min(h - 1, y))
    return round((h - 1 - y) * PANEL_W / max(1, h - 1)), round(x * PANEL_H / max(1, w - 1))


_touch_id = [0]


def _touch(dev, x, y, down):
    e = _evdev().ecodes
    px, py = _panel(x, y)
    if down == "start":
        _touch_id[0] = (_touch_id[0] + 1) % 65535
        dev.write(e.EV_ABS, e.ABS_MT_SLOT, 0)
        dev.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, _touch_id[0])
    dev.write(e.EV_ABS, e.ABS_MT_POSITION_X, px)
    dev.write(e.EV_ABS, e.ABS_MT_POSITION_Y, py)
    if down == "start":
        dev.write(e.EV_KEY, e.BTN_TOUCH, 1)
    dev.write(e.EV_ABS, e.ABS_X, px)
    dev.write(e.EV_ABS, e.ABS_Y, py)
    dev.syn()


def _release(dev):
    e = _evdev().ecodes
    dev.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1)
    dev.write(e.EV_KEY, e.BTN_TOUCH, 0)
    dev.syn()


def touch_tap(x, y, hold_ms=80):
    dev = touchscreen()
    _touch(dev, x, y, "start")
    time.sleep(hold_ms / 1000)
    _release(dev)


def touch_swipe(x1, y1, x2, y2, duration_ms=400):
    dev = touchscreen()
    _touch(dev, x1, y1, "start")
    steps = max(2, int(duration_ms / 16))
    for i in range(1, steps + 1):
        time.sleep(duration_ms / 1000 / steps)
        _touch(dev, x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps, "move")
    time.sleep(0.05)
    _release(dev)


# ---------------------------------------------------------------- gamepad

PAD_ALIASES = {
    "A": "BTN_A", "B": "BTN_B", "X": "BTN_X", "Y": "BTN_Y",
    "LB": "BTN_TL", "L1": "BTN_TL", "RB": "BTN_TR", "R1": "BTN_TR",
    "LT": "ABS_Z", "L2": "ABS_Z", "RT": "ABS_RZ", "R2": "ABS_RZ",
    "L3": "BTN_THUMBL", "R3": "BTN_THUMBR",
    "START": "BTN_START", "MENU": "BTN_START",
    "SELECT": "BTN_SELECT", "VIEW": "BTN_SELECT", "BACK": "BTN_SELECT",
    "STEAM": "BTN_MODE", "GUIDE": "BTN_MODE", "HOME": "BTN_MODE",
    "UP": ("ABS_HAT0Y", -1), "DOWN": ("ABS_HAT0Y", 1),
    "LEFT": ("ABS_HAT0X", -1), "RIGHT": ("ABS_HAT0X", 1),
}
PAD_MACROS = {"QAM": ["STEAM", "A"]}  # Quick Access Menu on a non-Deck controller


def _pad_event(dev, name, pressed):
    e = _evdev().ecodes
    target = PAD_ALIASES[name]
    if isinstance(target, tuple):
        code, value = target
        dev.write(e.EV_ABS, getattr(e, code), value if pressed else 0)
    elif target.startswith("ABS_"):
        dev.write(e.EV_ABS, getattr(e, target), 255 if pressed else 0)
    else:
        dev.write(e.EV_KEY, getattr(e, target), 1 if pressed else 0)
    dev.syn()


def tool_gamepad(sequence, hold_ms=120, gap_ms=200):
    steps = []
    for step in sequence:
        names = []
        for part in str(step).upper().replace(" ", "").split("+"):
            names.extend(PAD_MACROS.get(part, [part]))
        unknown = [n for n in names if n not in PAD_ALIASES]
        if unknown:
            raise ValueError(f"unknown button(s) {unknown}; valid: {sorted(PAD_ALIASES) + sorted(PAD_MACROS)}")
        steps.append(names)
    dev = gamepad()
    for i, names in enumerate(steps):
        for n in names:  # staggered so chords like STEAM+A register as "hold STEAM, press A"
            _pad_event(dev, n, True)
            time.sleep(0.04)
        time.sleep(hold_ms / 1000)
        for n in reversed(names):
            _pad_event(dev, n, False)
            time.sleep(0.02)
        if i < len(steps) - 1:
            time.sleep(gap_ms / 1000)
    return f"pressed {len(steps)} step(s): {' '.join('+'.join(s) for s in steps)}"


def tool_gamepad_stick(stick="left", x=0.0, y=0.0, duration_ms=500):
    e = _evdev().ecodes
    ax, ay = (e.ABS_X, e.ABS_Y) if stick == "left" else (e.ABS_RX, e.ABS_RY)
    clamp = lambda v: max(-32768, min(32767, int(float(v) * 32767)))
    dev = gamepad()
    dev.write(e.EV_ABS, ax, clamp(x))
    dev.write(e.EV_ABS, ay, clamp(y))
    dev.syn()
    time.sleep(duration_ms / 1000)
    dev.write(e.EV_ABS, ax, 0)
    dev.write(e.EV_ABS, ay, 0)
    dev.syn()
    return f"held {stick} stick at ({x}, {y}) for {duration_ms} ms"


# ---------------------------------------------------------------- keyboard

UNSHIFTED = {" ": "SPACE", "\n": "ENTER", "\t": "TAB", "-": "MINUS", "=": "EQUAL",
             "[": "LEFTBRACE", "]": "RIGHTBRACE", "\\": "BACKSLASH", ";": "SEMICOLON",
             "'": "APOSTROPHE", "`": "GRAVE", ",": "COMMA", ".": "DOT", "/": "SLASH"}
SHIFTED = {"_": "MINUS", "+": "EQUAL", "{": "LEFTBRACE", "}": "RIGHTBRACE", "|": "BACKSLASH",
           ":": "SEMICOLON", '"': "APOSTROPHE", "~": "GRAVE", "<": "COMMA", ">": "DOT", "?": "SLASH",
           "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
           "(": "9", ")": "0"}
KEY_ALIASES = {"CTRL": "LEFTCTRL", "CONTROL": "LEFTCTRL", "ALT": "LEFTALT", "SHIFT": "LEFTSHIFT",
               "SUPER": "LEFTMETA", "META": "LEFTMETA", "WIN": "LEFTMETA", "CMD": "LEFTMETA",
               "RETURN": "ENTER", "ESCAPE": "ESC", "DEL": "DELETE", "PGUP": "PAGEUP",
               "PGDN": "PAGEDOWN", "INS": "INSERT", "BKSP": "BACKSPACE", "PRINTSCREEN": "SYSRQ"}


def _char_key(ch):
    if ch.isascii() and ch.isalpha():
        return ch.upper(), ch.isupper()
    if ch.isascii() and ch.isdigit():
        return ch, False
    if ch in UNSHIFTED:
        return UNSHIFTED[ch], False
    if ch in SHIFTED:
        return SHIFTED[ch], True
    return None


def _tap(dev, codes):
    e = _evdev().ecodes
    for c in codes:
        dev.write(e.EV_KEY, c, 1)
        dev.syn()
    time.sleep(0.015)
    for c in reversed(codes):
        dev.write(e.EV_KEY, c, 0)
        dev.syn()
    time.sleep(0.015)


def tool_type_text(text):
    e = _evdev().ecodes
    if not all(_char_key(c) for c in text):
        if current_mode() == "desktop":  # xdotool handles any unicode on X11
            code, _, err = run(["xdotool", "type", "--delay", "15", "--", text])
            if code != 0:
                raise RuntimeError(err.strip())
            return f"typed {len(text)} characters (xdotool)"
        bad = sorted({c for c in text if not _char_key(c)})
        raise ValueError(f"cannot type {bad} in Game Mode (US-ASCII only)")
    dev = keyboard()
    for ch in text:
        name, shift = _char_key(ch)
        code = getattr(e, "KEY_" + name)
        _tap(dev, [e.KEY_LEFTSHIFT, code] if shift else [code])
    return f"typed {len(text)} characters"


def tool_key(combo, repeat=1):
    e = _evdev().ecodes
    codes = []
    for part in combo.replace(" ", "").split("+"):
        name = KEY_ALIASES.get(part.upper(), part.upper())
        code = getattr(e, "KEY_" + name, None)
        if code is None:
            raise ValueError(f"unknown key {part!r} (use evdev names without KEY_, e.g. enter, esc, f5, ctrl+alt+t)")
        codes.append(code)
    dev = keyboard()
    for _ in range(max(1, int(repeat))):
        _tap(dev, codes)
    return f"pressed {combo}" + (f" x{repeat}" if repeat > 1 else "")


# ---------------------------------------------------------------- pointer

def _move(dev, x, y):
    e = _evdev().ecodes
    w, h = _screen["w"], _screen["h"]
    dev.write(e.EV_ABS, e.ABS_X, round(max(0, min(w - 1, x)) * ABS_MAX / max(1, w - 1)))
    dev.write(e.EV_ABS, e.ABS_Y, round(max(0, min(h - 1, y)) * ABS_MAX / max(1, h - 1)))
    dev.syn()


BUTTONS = {"left": "BTN_LEFT", "right": "BTN_RIGHT", "middle": "BTN_MIDDLE"}


def tool_click(x, y, button="left", count=1):
    if current_mode() == "game":
        # gamescope ignores virtual pointers, so Game Mode clicks are touchscreen taps.
        if button == "right":
            touch_tap(x, y, hold_ms=900)
            return f"long-pressed ({x}, {y}) on the touchscreen (Game Mode has no right click)"
        for _ in range(max(1, int(count))):
            touch_tap(x, y)
            time.sleep(0.08)
        return f"tapped ({x}, {y}) x{count} on the touchscreen (Game Mode)"
    e = _evdev().ecodes
    code = getattr(e, BUTTONS[button])
    dev = pointer()
    _move(dev, x, y)
    time.sleep(0.05)
    for _ in range(max(1, int(count))):
        dev.write(e.EV_KEY, code, 1)
        dev.syn()
        time.sleep(0.05)
        dev.write(e.EV_KEY, code, 0)
        dev.syn()
        time.sleep(0.08)
    return f"{button} click x{count} at ({x}, {y}) on a {_screen['w']}x{_screen['h']} screen"


def tool_move_pointer(x, y):
    if current_mode() == "game":
        return "Game Mode has no hover; use click (tap) or the gamepad instead"
    _move(pointer(), x, y)
    return f"pointer at ({x}, {y})"


def tool_drag(x1, y1, x2, y2, duration_ms=400, button="left"):
    if current_mode() == "game":
        touch_swipe(x1, y1, x2, y2, duration_ms)
        return f"swiped ({x1}, {y1}) -> ({x2}, {y2}) on the touchscreen (Game Mode)"
    e = _evdev().ecodes
    code = getattr(e, BUTTONS[button])
    dev = pointer()
    _move(dev, x1, y1)
    time.sleep(0.05)
    dev.write(e.EV_KEY, code, 1)
    dev.syn()
    steps = max(2, int(duration_ms / 16))
    for i in range(1, steps + 1):
        _move(dev, x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
        time.sleep(duration_ms / 1000 / steps)
    dev.write(e.EV_KEY, code, 0)
    dev.syn()
    return f"dragged ({x1}, {y1}) -> ({x2}, {y2})"


def tool_scroll(amount, x=None, y=None, horizontal=False):
    if current_mode() == "game":
        # A finger swipe the opposite way, ~100 px per wheel notch.
        cx = _screen["w"] / 2 if x is None else x
        cy = _screen["h"] / 2 if y is None else y
        dist = max(-_screen["h"] / 2 + 10, min(_screen["h"] / 2 - 10, 100 * int(amount)))
        if horizontal:
            touch_swipe(cx + dist / 2, cy, cx - dist / 2, cy, 300)
        else:
            touch_swipe(cx, cy + dist / 2, cx, cy - dist / 2, 300)
        return f"swiped to scroll {amount} {'horizontally' if horizontal else 'vertically'} (Game Mode)"
    e = _evdev().ecodes
    dev = pointer()
    if x is not None and y is not None:
        _move(dev, x, y)
        time.sleep(0.05)
    axis = e.REL_HWHEEL if horizontal else e.REL_WHEEL
    step = -1 if amount > 0 else 1  # positive amount scrolls down / right
    if horizontal:
        step = -step
    for _ in range(abs(int(amount))):
        dev.write(e.EV_REL, axis, step)
        dev.syn()
        time.sleep(0.03)
    return f"scrolled {amount} {'horizontally' if horizontal else 'vertically'}"


# ---------------------------------------------------------------- steam library

def library_paths():
    paths = [STEAM_ROOT]
    try:
        with open(os.path.join(STEAM_ROOT, "steamapps", "libraryfolders.vdf")) as f:
            paths += re.findall(r'"path"\s+"([^"]+)"', f.read())
    except OSError:
        pass
    seen, out = set(), []
    for p in paths:
        real = os.path.realpath(p)
        if real not in seen and os.path.isdir(os.path.join(p, "steamapps")):
            seen.add(real)
            out.append(p)
    return out


def parse_acf(path):
    with open(path, errors="replace") as f:
        text = f.read()
    get = lambda k: (re.search(rf'"{k}"\s+"([^"]*)"', text) or [None, None])[1]
    return {"appid": get("appid"), "name": get("name"), "size_gb": round(int(get("SizeOnDisk") or 0) / 1e9, 1),
            "installed": int(get("StateFlags") or 0) & 4 == 4}


TOOL_NAMES = re.compile(r"^(Proton|Steam Linux Runtime|Steamworks Common|SteamVR)", re.I)


def installed_games(include_tools=False):
    games = {}
    for lib in library_paths():
        steamapps = os.path.join(lib, "steamapps")
        for fn in os.listdir(steamapps):
            if fn.startswith("appmanifest_") and fn.endswith(".acf"):
                try:
                    g = parse_acf(os.path.join(steamapps, fn))
                except OSError:
                    continue
                if not g["appid"] or (not include_tools and TOOL_NAMES.match(g["name"] or "")):
                    continue
                g["library"] = "sd card" if lib.startswith("/run/media") else "internal"
                games[g["appid"]] = g
    return sorted(games.values(), key=lambda g: (g["name"] or "").lower())


def tool_list_games(query="", include_tools=False):
    games = installed_games(include_tools)
    if query:
        games = [g for g in games if query.lower() in (g["name"] or "").lower()]
    lines = [f"{g['appid']}\t{g['name']}\t{g['size_gb']} GB\t{g['library']}"
             + ("" if g["installed"] else "\t(not fully installed)") for g in games]
    return f"{len(games)} game(s): appid, name, size, location\n" + "\n".join(lines)


def running_games():
    """Steam starts every game under `reaper SteamLaunch AppId=N`."""
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if args and args[0].endswith(b"reaper") and b"SteamLaunch" in args:
            for a in args:
                if a.startswith(b"AppId="):
                    found.append((int(pid), a[6:].decode()))
    return found


def game_name(appid):
    for lib in library_paths():
        acf = os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf")
        if os.path.exists(acf):
            return parse_acf(acf)["name"]
    return None


def tool_launch_game(appid):
    appid = str(appid).strip()
    if not appid.isdigit():
        raise ValueError("appid must be numeric; use list_games to find it")
    subprocess.Popen(["steam", f"steam://rungameid/{appid}"], env=session_env(),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return (f"asked Steam to launch {game_name(appid) or appid} ({appid}). It can take a while; "
            "use status or screenshot to see when it is running.")


def descendants(pid):
    children = {}
    for p in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{p}/stat") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
            children.setdefault(ppid, []).append(int(p))
        except (OSError, IndexError, ValueError):
            pass
    out, stack = [], [pid]
    while stack:
        for c in children.get(stack.pop(), []):
            out.append(c)
            stack.append(c)
    return out


def tool_stop_game(force=False):
    games = running_games()
    if not games:
        return "no game is running"
    stopped = []
    for reaper, appid in games:
        procs = descendants(reaper)
        for p in procs:
            try:
                os.kill(p, signal.SIGTERM)
            except ProcessLookupError:
                pass
        stopped.append((reaper, appid, procs))
    deadline = time.time() + 8
    while time.time() < deadline and running_games():
        time.sleep(0.5)
    if running_games() and force:
        for _, _, procs in stopped:
            for p in procs:
                try:
                    os.kill(p, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        time.sleep(1)
    still = running_games()
    names = ", ".join(f"{game_name(a) or a} ({a})" for _, a, _ in stopped)
    if still:
        return f"sent SIGTERM to {names}, but it is still running; call again with force=true to kill it"
    return f"stopped {names}"


# ---------------------------------------------------------------- system

def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def tool_status():
    lines = [f"mode: {current_mode()}"]
    bat = "/sys/class/power_supply/BAT1"
    if os.path.isdir(bat):
        lines.append(f"battery: {_read(bat + '/capacity')}% ({_read(bat + '/status')})")
    games = running_games()
    lines.append("running game: " + (", ".join(f"{game_name(a) or '?'} ({a})" for _, a in games) or "none"))
    lines.append(f"screen: {_screen['w']}x{_screen['h']} (updated by screenshot)")
    up = float(_read("/proc/uptime", "0").split()[0])
    lines.append(f"uptime: {int(up // 3600)}h {int(up % 3600 // 60)}m")
    st = os.statvfs(os.path.expanduser("~"))
    lines.append(f"home free: {st.f_bavail * st.f_frsize / 1e9:.0f} GB")
    sd = [m for m in os.listdir("/run/media/deck")] if os.path.isdir("/run/media/deck") else []
    lines.append("sd card: " + (", ".join(sd) or "none"))
    return "\n".join(lines)


def tool_run_command(command, timeout=120, root=False):
    cmd = ["sudo", "-n", "bash", "-lc", command] if root else ["bash", "-lc", command]
    try:
        code, out, err = run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"isError": True, "text": f"timed out after {timeout}s"}
    text = f"exit code: {code}\n"
    if out:
        text += f"--- stdout ---\n{out[-MAX_OUTPUT:]}\n"
    if err:
        text += f"--- stderr ---\n{err[-MAX_OUTPUT:]}\n"
    return {"isError": code != 0, "text": text}


def tool_switch_mode(mode):
    target = {"game": "gamescope", "desktop": "plasma"}[mode]
    if current_mode() == mode:
        return f"already in {mode} mode"
    subprocess.Popen(["steamos-session-select", target], env=session_env(), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return f"switching to {mode} mode; the session restarts, so wait ~15 s before the next screenshot"


# ---------------------------------------------------------------- tool registry

def schema(props=None, required=()):
    return {"type": "object", "properties": props or {}, "required": list(required)}


NUM = {"type": "number"}
GUIDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "README.md")


def tool_guide():
    try:
        with open(GUIDE) as f:
            return f.read()
    except OSError:
        return f"guide not found at {GUIDE}"


TOOLS = {
    "guide": (tool_guide, "Read the Steam Deck agent guide: what's installed, file locations, how Game/Desktop Mode differ, "
                          "and step-by-step recipes. Call this first if you haven't used this Deck before.", schema()),
    "status":(tool_status, "Steam Deck overview: Game/Desktop mode, battery, running game, screen size, storage.",
               schema()),
    "screenshot": (tool_screenshot,
                   "Capture what's on the Deck's screen (works in Game Mode and Desktop Mode). Returns a JPEG; "
                   "use its pixel coordinates with click/drag.",
                   schema({"max_width": {"type": "integer", "description": "Downscale to this width (default 1280 = native)."},
                           "quality": {"type": "integer", "description": "JPEG quality 2 (best) to 31 (smallest); default 4."}})),
    "gamepad": (tool_gamepad,
                "Press buttons on a virtual Xbox controller, the most reliable way to drive Steam's Game Mode UI and games. "
                "Each sequence step is a button or a '+' chord. Buttons: A B X Y LB RB LT RT L3 R3 UP DOWN LEFT RIGHT "
                "START(menu) SELECT(view) STEAM(guide). Macro: QAM opens the Quick Access Menu.",
                schema({"sequence": {"type": "array", "items": {"type": "string"},
                                     "description": "e.g. [\"DOWN\", \"DOWN\", \"A\"] or [\"STEAM\"] or [\"LB+RB\"]"},
                        "hold_ms": {"type": "integer", "description": "How long each step is held (default 120)."},
                        "gap_ms": {"type": "integer", "description": "Pause between steps (default 200)."}},
                       ["sequence"])),
    "gamepad_stick": (tool_gamepad_stick, "Push a virtual controller stick in a direction for a duration (x/y from -1 to 1; y=-1 is up).",
                      schema({"stick": {"type": "string", "enum": ["left", "right"]}, "x": NUM, "y": NUM,
                              "duration_ms": {"type": "integer"}}, ["x", "y"])),
    "click": (tool_click,
              "Click at screen pixel coordinates from a screenshot. Desktop Mode: mouse click. Game Mode: touchscreen "
              "tap (right = long press).",
              schema({"x": NUM, "y": NUM, "button": {"type": "string", "enum": ["left", "right", "middle"]},
                      "count": {"type": "integer", "description": "2 for a double click."}}, ["x", "y"])),
    "move_pointer": (tool_move_pointer, "Move the mouse pointer to screen pixel coordinates (hover; Desktop Mode only).",
                     schema({"x": NUM, "y": NUM}, ["x", "y"])),
    "drag": (tool_drag, "Drag between two screen points (mouse drag in Desktop Mode, finger swipe in Game Mode).",
             schema({"x1": NUM, "y1": NUM, "x2": NUM, "y2": NUM, "duration_ms": {"type": "integer"},
                     "button": {"type": "string", "enum": ["left", "right", "middle"]}}, ["x1", "y1", "x2", "y2"])),
    "scroll": (tool_scroll,
               "Scroll; positive amount scrolls down (or right), optionally at a point. Mouse wheel in Desktop Mode, "
               "swipe in Game Mode (where the gamepad's DOWN/RB is often better).",
               schema({"amount": {"type": "integer"}, "x": NUM, "y": NUM, "horizontal": {"type": "boolean"}}, ["amount"])),
    "type_text": (tool_type_text, "Type text with a virtual keyboard into whatever has focus.",
                  schema({"text": {"type": "string"}}, ["text"])),
    "key": (tool_key, "Press a key or combination, e.g. 'enter', 'esc', 'tab', 'ctrl+l', 'alt+f4', 'super'.",
            schema({"combo": {"type": "string"}, "repeat": {"type": "integer"}}, ["combo"])),
    "list_games": (tool_list_games, "List installed Steam games with their appids (internal storage and SD card).",
                   schema({"query": {"type": "string", "description": "Case-insensitive name filter."},
                           "include_tools": {"type": "boolean", "description": "Also list Proton and runtimes."}})),
    "launch_game": (tool_launch_game, "Launch an installed Steam game by appid.",
                    schema({"appid": {"type": "string"}}, ["appid"])),
    "stop_game": (tool_stop_game, "Close the running Steam game (SIGTERM; force=true kills it).",
                  schema({"force": {"type": "boolean"}})),
    "switch_mode": (tool_switch_mode, "Switch the Deck between Game Mode and Desktop Mode.",
                    schema({"mode": {"type": "string", "enum": ["game", "desktop"]}}, ["mode"])),
    "run_command": (tool_run_command,
                    "Run a bash command on the Deck as user deck (root=true runs it with passwordless sudo). "
                    "The graphical session's DISPLAY/DBus variables are set, so GUI tools work.",
                    schema({"command": {"type": "string"}, "timeout": {"type": "integer", "description": "Seconds, default 120."},
                            "root": {"type": "boolean"}}, ["command"])),
}


def call_tool(name, args):
    try:   # deck_autosleep.py keeps the Deck awake while an agent is working (touched on every tool call)
        open("/tmp/deck_agent_active", "w").write(name)
    except OSError:
        pass
    if name not in TOOLS:
        return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
    try:
        result = TOOLS[name][0](**(args or {}))
    except TypeError as exc:
        return {"content": [{"type": "text", "text": f"bad arguments: {exc}"}], "isError": True}
    except Exception as exc:  # report every failure to the agent rather than crashing the server
        log(f"{name} failed: {exc!r}")
        return {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
    if isinstance(result, list):
        return {"content": result, "isError": False}
    if isinstance(result, dict):
        return {"content": [{"type": "text", "text": result["text"]}], "isError": result["isError"]}
    return {"content": [{"type": "text", "text": str(result)}], "isError": False}


# ---------------------------------------------------------------- JSON-RPC loop

INSTRUCTIONS = """\
Full control of the owner's Steam Deck (SteamOS 3.7, user `deck`, passwordless sudo). The owner has authorized agents to do anything on it.
- Start with `status` (Game vs Desktop Mode, running game) and `screenshot`; call `guide` for recipes and file locations.
- Work in a loop: act, then screenshot to verify. Screens are 1280x800; click/drag take coordinates in screen pixels.
- Game Mode (Steam's console UI): navigate with `gamepad` (DPAD + A to select, B back, STEAM = main menu, QAM = quick menu). `click` taps the touchscreen.
- Desktop Mode (KDE Plasma): `click`/`type_text`/`key` act like a mouse and keyboard.
- `list_games` + `launch_game`/`stop_game` handle Steam games directly; `run_command` runs bash (root=true for sudo) with the GUI session's environment.
- The root filesystem is read-only and replaced by OS updates: install into /home, Flatpak (`flatpak install --user`) or Distrobox, never pacman.
- Never type the owner's passwords (Steam etc.); ask the owner to do sign-ins themselves.
"""

def send(msg):
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notification (initialized, cancelled, ...)
        return
    params = msg.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        result = {"protocolVersion": requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0],
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                  "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (_, d, s) in TOOLS.items()]}
    elif method == "tools/call":
        result = call_tool(params.get("name"), params.get("arguments"))
    else:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}})
        return
    send({"jsonrpc": "2.0", "id": mid, "result": result})


def main():
    log(f"{SERVER_NAME} {SERVER_VERSION} ready (mode: {current_mode()})")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        try:
            handle(msg)
        except Exception as exc:
            log(f"internal error: {exc!r}")
            if msg.get("id") is not None:
                send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": str(exc)}})


if __name__ == "__main__":
    main()
