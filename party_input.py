#!/usr/bin/env python3
"""Local multiplayer controller order for every emulator Prism launches (party setup, 2026-10-09).
Run by ES-DE's game-start hook (console.sh) before each launch:   party_input.py <rom> <system>
                                                                  party_input.py --show   (print the layout)

How controllers reach the emulators
 - Steam Input owns every pad (Deck, Pro Controller, Joy-Con, DualSense) and gives each a virtual Xbox pad
   "Microsoft X-Box 360 pad <slot>" (28de:11ff); SDL hides the physical pads (SDL_GAMECONTROLLER_IGNORE_DEVICES).
 - Steam gives slots in the order of each pad's FIRST BUTTON PRESS, external pads ahead of the Deck's own controls.
   Slots are not compacted when pads leave (until Prism restarts), so the Deck can sit on slot 3 with nothing on 0-2.
 - SDL lists the virtual pads in /dev/input/eventN order ("dense" index 0, 1, 2 ...) and reports the slot as the player
   index. That is slot order only until a pad reconnects: Steam then re-creates its virtual pad with a new event number
   (2026-10-10, Heave Ho 2: SDL order was pads 1,4,3,0,2 - player 1's controller got no port, the docked Deck did).
   RetroArch, Dolphin, Ryujinx and Eden bind by dense index; DuckStation and PCSX2 bind "SDL-<player index>" = slot.
   Dolphin also needs the pad's name (from Steam's virtualgamepadinfo.txt) plus a per-name counter.

What this does
 - Docked (TV): players 1..N = the external pads in Steam's order, then the Deck. Handheld: the Deck is player 1
   (as before), any other pads after it. Each emulator's player->device bindings are rewritten for that order, so
   it doesn't matter which slots the pads landed on.
 - GameCube: all 4 ports on when docked (1 port handheld, as before). Wii: one emulated Wii Remote per pad with the
   game's own control profile; Smash Bros. Brawl and Tatsunoko vs. Capcom use GameCube pads instead.
 - PS1 multitap (4 players) for the games that support it, docked only.
Test hook: /tmp/party_force_docked (younger than 3 h) counts as docked (pads.docked()).
Backups of every file this touches: <file>.bak-party (first run)."""
import prism_config  # noqa: E402  (paths)
import json, os, re, shutil, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pads  # noqa: E402  (imported at the top: switch2_bridge.py imports this module with ~/prism on sys.path only briefly)

HOME = os.path.expanduser("~")
STATE = f"{prism_config.PRISM}/party/state.json"
LAYOUT_OUT = "/tmp/party_players.json"
DOL = f"{HOME}/.var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu"
RA_CFG = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg"
DUCK = f"{HOME}/.var/app/org.duckstation.DuckStation/config/duckstation/settings.ini"
PCSX2 = f"{HOME}/.config/PCSX2/inis/PCSX2.ini"
EDEN = f"{HOME}/.config/eden/qt-config.ini"
DECK_NAME = pads.DECK_NAME
WII_GC_MODE = {"RSBE01", "STKE08"}          # Brawl, Tatsunoko vs. Capcom: GameCube pads, no Wii Remotes
PSX_MULTITAP = ("CTR - Crash Team Racing", "Bomberman - Party Edition", "Crash Bash", "Bomberman World",
                "Micro Machines", "Poy Poy", "Hogs of War", "Speed Freaks", "Speed Punks")
RA_SYSTEMS = {"n64", "snes", "sfc", "genesis", "megadrive", "pcengine", "tg16", "pcenginecd", "tg-cd", "dreamcast",
              "arcade", "fbneo", "mame", "neogeo", "cps1", "cps2", "cps3", "saturn", "nes", "famicom", "atomiswave",
              "naomi", "naomi2", "sega32x", "segacd", "megacd", "mastersystem", "sg-1000", "atari2600", "atari7800",
              "atarilynx", "gb", "gbc", "gba", "virtualboy", "neogeocd", "wonderswan", "ngp", "ngpc", "psp", "nds",
              "msx", "c64", "amiga", "dos", "3do", "jaguar", "colecovision", "intellivision", "vectrex", "odyssey2",
              "satellaview", "sufami", "fds", "pcfx", "supergrafx", "gamegear", "model2", "model3", "daphne"}


def log(*a):
    print("party:", *a, flush=True)


# Which controllers exist, their slots / SDL indices / kinds and the player order all come from pads.py (one snapshot
# shared with the controller chips and the Home card). These names stay for ryujinx_mode, dolphin_mode, switch2_bridge.
docked = pads.docked
read_info = pads.read_info
sdl_order = pads.sdl_order


def present_slots():
    return sorted(sdl_order())


def physical_list():
    """Real controllers connected right now: [(kind, mac or "")] (not Steam's virtual copies, not the Deck)."""
    return [(ph["kind"], ph["mac"]) for ph in pads.physical_list()]


def physical_pads():
    return len(pads.physical_list())


def layout(dock=None):
    """Pads as emulators see them, in player order (pads.snapshot()). Each: slot (= SDL player index), dense (= SDL
    device index), name (SDL name), dolphin ("SDL/<n>/<name>"), deck (the Deck's own controls), type; mac for a pad
    Steam's list missed. Docked: the external pads only; handheld: the Deck first, then the others, stale pads last."""
    s = pads.snapshot(dock)

    def legacy(p):
        d = {"slot": p["slot"], "dense": p["sdl"], "name": p["name"], "dolphin": p["dolphin"], "deck": p["deck"],
             "type": p["type"]}
        if p["recovered"]:
            d["mac"] = p["mac"]
        return d
    return {"docked": s["docked"], "players": [legacy(p) for p in s["players"]], "externals": s["externals"],
            "dolphin_next": s["dolphin_next"], "guess_names": s["guess_names"],
            "reserved_dense": s["reserved_dense"], "reserved_slots": s["reserved_slots"]}


# ---------- small file helpers ----------

def backup(path):
    if os.path.exists(path) and not os.path.exists(path + ".bak-party"):
        shutil.copy2(path, path + ".bak-party")


def write_if_changed(path, text):
    try:
        old = open(path).read()
    except OSError:
        old = None
    if old == text:
        return False
    backup(path)
    tmp = path + ".party-tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    return True


def ini_set(text, section, key, value):
    """Set key = value inside [section] (Dolphin/DuckStation/PCSX2 style "key = value"); value None removes it."""
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if l.strip() == f"[{section}]"), None)
    if start is None:
        if value is None:
            return text
        if lines and lines[-1] == "":
            lines.pop()
        lines += [f"[{section}]", f"{key} = {value}", ""]
        return "\n".join(lines)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    for i in range(start + 1, end):
        if re.match(rf"{re.escape(key)}\s*=", lines[i]):
            if value is None:
                del lines[i]
            else:
                lines[i] = f"{key} = {value}"
            return "\n".join(lines)
    if value is not None:
        ins = end
        while ins > start + 1 and lines[ins - 1].strip() == "":
            ins -= 1
        lines.insert(ins, f"{key} = {value}")
    return "\n".join(lines)


def ini_get(text, section, key):
    m = re.search(rf"^\[{re.escape(section)}\]\n(.*?)(?=^\[|\Z)", text, re.S | re.M)
    if not m:
        return None
    k = re.search(rf"^{re.escape(key)}\s*=\s*(.*)$", m.group(1), re.M)
    return k.group(1) if k else None


def ini_section_body(text, section):
    m = re.search(rf"^\[{re.escape(section)}\]\n(.*?)(?=^\[|\Z)", text, re.S | re.M)
    return m.group(1) if m else None


def load_state():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(s, open(STATE, "w"), indent=1)


def device_for(lay, k):
    """Dolphin device string for player k (0-based); beyond the connected pads, guess the next pad of the most
    common external model so a late pad still lands somewhere sensible."""
    pl = lay["players"]
    if k < len(pl):
        return pl[k]["dolphin"]
    names = lay.get("guess_names") or [p["name"] for p in pl if not p["deck"]] or [DECK_NAME]
    name = max(set(names), key=names.count)
    nxt = dict(lay["dolphin_next"])
    for _ in range(k - len(pl) + 1):
        n = nxt.get(name, 0)
        nxt[name] = n + 1
    return f"SDL/{n}/{name}"


def slot_for(lay, k):
    """SDL player index (= Steam slot) for player k; free slot numbers for players without a pad."""
    pl = lay["players"]
    if k < len(pl):
        return pl[k]["slot"]
    used = {p["slot"] for p in pl} | set(lay.get("reserved_slots", []))
    free = [s for s in range(16) if s not in used]
    return free[k - len(pl)]


def dense_for(lay, k):
    pl = lay["players"]
    if k < len(pl):
        return pl[k]["dense"]
    # a pad that connects later is appended after every existing SDL device (Ryujinx/RetroArch/Eden number them in
    # arrival order; verified 2026-10-09 with Boomerang Fu), so the Deck's index is skipped too
    used = {p["dense"] for p in pl} | set(lay.get("reserved_dense", []))
    free = [s for s in range(16) if s not in used]
    return free[k - len(pl)]


# ---------- Dolphin (GameCube + Wii) ----------

def game_id(rom):
    m = re.search(r"\[([A-Z0-9]{6})\]", os.path.basename(rom))
    if m:
        return m.group(1)
    off = {".wbfs": 0x200, ".rvz": 0x58, ".wia": 0x58, ".iso": 0, ".gcm": 0}.get(os.path.splitext(rom)[1].lower())
    if off is None:
        return None
    try:
        with open(rom, "rb") as f:
            f.seek(off)
            gid = f.read(6).decode("ascii")
        return gid if re.fullmatch(r"[A-Z0-9]{6}", gid) else None
    except (OSError, UnicodeDecodeError):
        return None


def dolphin(lay, system, rom):
    dock = lay["docked"] and lay["externals"] > 0
    gid = game_id(rom) if system == "wii" else None
    gc_mode = system == "gc" or gid in WII_GC_MODE
    ini = f"{DOL}/Dolphin.ini"
    t = open(ini).read()
    for k in range(4):
        if dock:
            v = 6 if gc_mode else 0                      # Wii games docked: Wii Remotes only (no double input)
        else:
            v = 6 if k == 0 else 0                       # as before: port 1 only (Wii games too)
        t = ini_set(t, "Core", f"SIDevice{k}", v)
    write_if_changed(ini, t)

    pad = f"{DOL}/GCPadNew.ini"
    t = open(pad).read()
    for k in range(4):
        t = ini_set(t, f"GCPad{k + 1}", "Device", device_for(lay, k) if dock else f"SDL/{k}/{DECK_NAME}")
    write_if_changed(pad, t)

    wm = f"{DOL}/WiimoteNew.ini"
    t = open(wm).read()
    n_wii = 0 if (not dock or gc_mode) else max(1, min(4, len(lay["players"])))
    for k in range(4):
        if dock:
            t = ini_set(t, f"Wiimote{k + 1}", "Device", device_for(lay, k))
            t = ini_set(t, f"Wiimote{k + 1}", "Source", 1 if k < n_wii else 0)
        else:                                            # handheld: as before (Wii Remote 1 only, default source)
            t = ini_set(t, f"Wiimote{k + 1}", "Device", f"SDL/{k}/{DECK_NAME}")
            t = ini_set(t, f"Wiimote{k + 1}", "Source", None)
    write_if_changed(wm, t)
    if system == "wii":
        wii_profiles(lay, gid, dock and not gc_mode)
    what = ("GameCube pads x4" if gc_mode else f"Wii Remotes x{n_wii}") if dock else "handheld (Deck = player 1)"
    log(f"dolphin {system} {gid or ''}: {what}")


def wii_profiles(lay, gid, party):
    """Per-game Wii Remote profile (GameSettings/<ID>.ini WiimoteProfile1) for every player: docked, players 1-4 get
    party_p<k> = the game's profile with their own pad; handheld, the game's own lines come back."""
    st = load_state()
    saved = st.setdefault("wii_controls", {})
    for old_gid in list(saved):                         # put back any game left in party mode
        if old_gid != gid or not party:
            _restore_controls(old_gid, saved.pop(old_gid))
    if not gid or not party:
        save_state(st)
        return
    gs = f"{DOL}/GameSettings/{gid}.ini"
    t = open(gs).read() if os.path.exists(gs) else ""
    if gid not in saved:
        saved[gid] = {"existed": os.path.exists(gs),
                      "lines": {f"WiimoteProfile{k}": ini_get(t, "Controls", f"WiimoteProfile{k}") for k in range(1, 5)}}
    base_name = saved[gid]["lines"].get("WiimoteProfile1")
    body = None
    if base_name and os.path.exists(f"{DOL}/Profiles/Wiimote/{base_name}.ini"):
        body = ini_section_body(open(f"{DOL}/Profiles/Wiimote/{base_name}.ini").read(), "Profile")
    if body is None:
        src = f"{DOL}/WiimoteNew.ini.bak-party" if os.path.exists(f"{DOL}/WiimoteNew.ini.bak-party") else f"{DOL}/WiimoteNew.ini"
        body = ini_section_body(open(src).read(), "Wiimote1") or ""
        body = "\n".join(l for l in body.split("\n") if not l.startswith("Source"))
    for k in range(4):
        b = re.sub(r"(?m)^Device = .*$", f"Device = {device_for(lay, k)}", body)
        if "Device = " not in b:
            b = f"Device = {device_for(lay, k)}\n" + b
        write_if_changed(f"{DOL}/Profiles/Wiimote/party_p{k + 1}.ini", "[Profile]\n" + b.rstrip("\n") + "\n")
        t = ini_set(t, "Controls", f"WiimoteProfile{k + 1}", f"party_p{k + 1}")
    os.makedirs(os.path.dirname(gs), exist_ok=True)
    write_if_changed(gs, t if t.endswith("\n") else t + "\n")
    save_state(st)


def _restore_controls(gid, rec):
    gs = f"{DOL}/GameSettings/{gid}.ini"
    if not os.path.exists(gs):
        return
    t = open(gs).read()
    for k, v in rec["lines"].items():
        t = ini_set(t, "Controls", k, v)
    if not rec["existed"] and re.fullmatch(r"\s*(\[Controls\]\s*)?", t):
        os.remove(gs)
        return
    t = re.sub(r"(?m)^\[Controls\]\n(?=\[|\Z)", "", t)
    write_if_changed(gs, t)


# ---------- RetroArch ----------

def retroarch(lay):
    t = open(RA_CFG).read()
    for k in range(5):
        t = re.sub(rf'(?m)^input_player{k + 1}_joypad_index = ".*"$',
                   f'input_player{k + 1}_joypad_index = "{dense_for(lay, k)}"', t)
    write_if_changed(RA_CFG, t)
    log("retroarch: players ->", [dense_for(lay, k) for k in range(5)], "(SDL device index)")


# ---------- DuckStation / PCSX2 (SDL-<player index>) ----------

def _rebind_sdl(t, section, n):
    body = ini_section_body(t, section)
    if body is None:
        return t
    new = re.sub(r"SDL-\d+/", f"SDL-{n}/", body)
    return t.replace(f"[{section}]\n{body}", f"[{section}]\n{new}", 1)


def _copy_section(t, src, dst, n):
    """[dst] = [src]'s bindings on SDL-<n> (used for the multitap's 1B-1D pads)."""
    body = ini_section_body(t, src)
    if body is None:
        return t
    new = re.sub(r"SDL-\d+/", f"SDL-{n}/", body).rstrip("\n") + "\n\n"
    old = ini_section_body(t, dst)
    if old is None:
        return t.rstrip("\n") + f"\n[{dst}]\n{new}"
    return t.replace(f"[{dst}]\n{old}", f"[{dst}]\n{new}", 1)


def duckstation(lay, rom):
    t = open(DUCK).read()
    tap = lay["docked"] and lay["externals"] > 0 and any(g.lower() in os.path.basename(rom).lower() for g in PSX_MULTITAP)
    st = load_state()
    if "duck_pads" not in st:                           # the original Pad2-5 sections, put back when the tap goes
        st["duck_pads"] = {s: ini_section_body(t, s) for s in ("Pad2", "Pad3", "Pad4", "Pad5")}
        save_state(st)
    for sec, body in st["duck_pads"].items():
        cur = ini_section_body(t, sec)
        if body is not None and cur is not None and cur != body:
            t = t.replace(f"[{sec}]\n{cur}", f"[{sec}]\n{body}", 1)
    if tap:   # multitap on port 1: Pad1 = 1A, Pad3/4/5 = 1B/1C/1D (DuckStation numbering), port 2 unused
        t = ini_set(t, "ControllerPorts", "MultitapMode", "Port1Only")
        t = _rebind_sdl(t, "Pad1", slot_for(lay, 0))
        for pad, k in (("Pad3", 1), ("Pad4", 2), ("Pad5", 3)):
            t = _copy_section(t, "Pad1", pad, slot_for(lay, k))
        t = ini_set(t, "Pad2", "Type", "None")
    else:
        t = ini_set(t, "ControllerPorts", "MultitapMode", "Disabled")
        for k in range(4):
            t = _rebind_sdl(t, f"Pad{k + 1}", slot_for(lay, k))
    write_if_changed(DUCK, t)
    log("duckstation:", "multitap 4p" if tap else "2 ports",
        "players ->", [f"SDL-{slot_for(lay, k)}" for k in range(4)])


def pcsx2(lay):
    if not os.path.exists(PCSX2):
        return
    t = open(PCSX2).read()
    for k in range(2):
        t = _rebind_sdl(t, f"Pad{k + 1}", slot_for(lay, k))
    write_if_changed(PCSX2, t)
    log("pcsx2: players ->", [f"SDL-{slot_for(lay, k)}" for k in range(2)])


# ---------- Eden (Switch, a few games) ----------

def eden(lay):
    if not os.path.exists(EDEN):
        return
    t = open(EDEN).read()
    dock = lay["docked"] and lay["externals"] > 0
    n = min(8, max(4, lay["externals"])) if dock else min(8, 1 + lay["externals"])   # docked: 4+ for pads that wake late
    p0 = {m.group(1): m.group(2) for m in re.finditer(r"(?m)^player_0_(button_(?!color)\w+|lstick|rstick)=(.*)$", t)}
    for k in range(8):
        on = k < n
        port = dense_for(lay, k)
        if k == 0 or on:
            for key, val in p0.items():
                if "engine:sdl" in val:
                    val = re.sub(r"port:\d+", f"port:{port}", val)
                t = re.sub(rf"(?m)^player_{k}_{key}=.*$", f"player_{k}_{key}={val}".replace("\\", "\\\\"), t)
                t = re.sub(rf"(?m)^player_{k}_{key}\\default=.*$", f"player_{k}_{key}\\\\default=false", t)
        if k > 0:
            t = re.sub(rf"(?m)^player_{k}_connected=.*$", f"player_{k}_connected={'true' if on else 'false'}", t)
            t = re.sub(rf"(?m)^player_{k}_connected\\default=.*$",
                       f"player_{k}_connected\\\\default={'false' if on else 'true'}", t)
            t = re.sub(rf"(?m)^player_{k}_type=.*$", f"player_{k}_type=0", t)
    write_if_changed(EDEN, t)
    log(f"eden: {n} Pro Controller(s) ->", [dense_for(lay, k) for k in range(n)])


def settled_layout(wait=4.0):
    """layout(), but if fewer players than connected controllers show up, give Steam a moment: a controller that just
    woke (or reconnected) gets its virtual pad a second or two later (2026-10-10, Heave Ho 2: launched with one
    controller listed of four, the other three got wrong ports)."""
    end = time.time() + wait
    lay = layout()
    while time.time() < end:
        n = physical_pads()
        if not lay["docked"] or lay["externals"] >= n:
            break
        time.sleep(0.4)
        lay = layout()
    else:
        log(f"controllers still settling after {wait:.0f}s: {lay['externals']} of {physical_pads()} listed")
    return lay


def main(argv):
    if argv[:1] == ["--show"]:
        print(json.dumps(layout(), indent=1))
        return
    rom = re.sub(r"\\(.)", r"\1", argv[0]) if argv else ""   # ES-DE passes the path shell-escaped (\ , \(, \[)
    system = argv[1] if len(argv) > 1 else ""
    lay = settled_layout()
    json.dump({**lay, "time": time.time(), "system": system, "rom": rom}, open(LAYOUT_OUT, "w"), indent=1)
    names = ", ".join(f"P{i + 1}={p['name']}@{p['slot']}" for i, p in enumerate(lay["players"]))
    log(f"{'docked' if lay['docked'] else 'handheld'}: {names}")
    try:
        if system in ("gc", "wii"):
            dolphin(lay, system, rom)
        elif system == "psx":
            duckstation(lay, rom)
        elif system == "ps2":
            pcsx2(lay)
        elif system == "switch":
            eden(lay)                                    # Ryujinx: ryujinx_mode.py (uses layout())
        elif system in RA_SYSTEMS:
            retroarch(lay)
    except Exception as e:                               # never block a launch
        log(f"error ({system}): {e!r}")


if __name__ == "__main__":
    main(sys.argv[1:])
