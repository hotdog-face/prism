#!/usr/bin/env python3
"""Which controllers exist and who is which player: one snapshot for all of Prism (2026-10-13 refactor).

Before this, party_input.layout() (emulator ports), controller_bar.items() (chips, pop-ups, battery, idle auto-off,
disconnect pause) and deck_cc.pads() (the Home card's input) each worked it out on their own, and the copies drifted
apart at the 2026-10-10 party (wrong players, a controller not shown, the docked Deck counted as a player).

  pads.snapshot()        {"docked", "pads": [...], ...}: every Steam virtual pad present, in Steam slot order
  pads.card_inputs()     evdev paths the Home card reads (virtual copies AND raw devices, see below)
  python3 pads.py        print the snapshot

Per pad in snapshot()["pads"]:
  slot      Steam's slot (= SDL player index; DuckStation/PCSX2 "SDL-<slot>")
  sdl       SDL device index (RetroArch, Dolphin, Ryujinx, Eden): the /dev/input/eventN order of Steam's virtual pads,
            which is slot order only until a pad reconnects (2026-10-10, Heave Ho 2)
  name      SDL's name for it (Steam's virtualgamepadinfo.txt), dolphin = "SDL/<n>/<name>"
  type      Steam's type (steam / switchpro / ps5 / xbox360 ...), or the physical kind for a pad Steam didn't list
  kind      deck / switchpro / switch2 (the BLE bridge, switch2_bridge.py) / ps5 / other; None = a stale virtual pad
            Steam left behind (slots aren't compacted until Prism restarts)
  deck      the Deck's own controls
  virtual   Steam's virtual evdev (/dev/input/eventN); raw / hidraw = the physical controller's own devices
  mac       Bluetooth address; label = the chip's name; battery (%, or None) + charging
  port      0-based position in the emulators' player order (None = left out: the docked Deck)
  player    the player number on the chips and pop-ups (None: stale pads, the docked Deck)
How Steam numbers pads: slots in the order of each pad's FIRST BUTTON PRESS, external pads ahead of the Deck.
"""
import glob, json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_profile  # noqa: E402  (imported here, not lazily: switch2_bridge.py has ~/prism on sys.path only briefly)

HOME = os.path.expanduser("~")
INFO = f"{HOME}/.local/share/Steam/config/virtualgamepadinfo.txt"
S2_STATE = f"{HOME}/.cache/switch2-bridge/state.json"
S2_FULL = f"{HOME}/.cache/switch2-bridge/full_mv"
DECK_NAME = "Steam Deck Controller"
STEAM_VPAD = (0x28DE, 0x11FF)        # Steam's virtual Xbox pads, "Microsoft X-Box 360 pad <slot>"
AGENT_PAD = (0x045E, 0x028E)         # deck_mcp's injector pad: Steam must still see the B it sends (not read by the card)
BRIDGE_PAD = (0x045E, 0x028F)        # switch2_bridge.py's uinput pad (phys "switch2-bridge/<mac>")
NINTENDO = 0x057E                    # raw Nintendo devices report buttons by POSITION (a Pro Controller's A is BTN_EAST)
BTN_SOUTH, BTN_EAST = 0x130, 0x131
FORCE_DOCKED = "/tmp/party_force_docked"   # test hook: younger than 3 h = docked


def docked():
    """On the TV (prism_profile.docked(): the dock's DisplayPort is connected), or the party test hook is set."""
    try:
        if time.time() - os.path.getmtime(FORCE_DOCKED) < 3 * 3600:
            return True
    except OSError:
        pass
    return prism_profile.docked()


# ---------- raw sources ----------

def read_info():
    """virtualgamepadinfo.txt -> {slot: {"name":..., "type":..., "handle":..., "VID":..., "PID":...}}
    (may hold stale slots, and can miss a live pad or lose every type)."""
    slots, cur = {}, None
    try:
        for line in open(INFO):
            line = line.strip()
            m = re.match(r"\[slot (\d+)\]", line)
            if m:
                cur = slots.setdefault(int(m.group(1)), {})
            elif cur is not None and "=" in line:
                k, v = line.split("=", 1)
                cur[k] = v
    except OSError:
        pass
    return slots


def input_devices():
    """/proc/bus/input/devices -> [{name, vendor, product, uniq, phys, event, js, keys (EV_KEY bitmap as an int)}]"""
    out = []
    try:
        text = open("/proc/bus/input/devices").read()
    except OSError:
        return out
    for block in text.split("\n\n"):
        n = re.search(r'^N: Name="(.*)"$', block, re.M)
        if not n:
            continue
        i = re.search(r"Vendor=([0-9a-fA-F]{4}) Product=([0-9a-fA-F]{4})", block)
        h = re.search(r"^H: Handlers=(.*)$", block, re.M)
        handlers = h.group(1).split() if h else []
        u = re.search(r"^U: Uniq=([0-9a-fA-F:]{17})\s*$", block, re.M)
        ph = re.search(r"^P: Phys=(.*)$", block, re.M)
        k = re.search(r"^B: KEY=(.*)$", block, re.M)
        keys = 0
        for w in (k.group(1).split() if k else []):        # highest word first, 64 bits each
            keys = (keys << 64) | int(w, 16)
        out.append({"name": n.group(1), "vendor": int(i.group(1), 16) if i else 0, "product": int(i.group(2), 16) if i else 0,
                    "uniq": u.group(1).lower() if u else "", "phys": ph.group(1) if ph else "",
                    "event": next((x for x in handlers if re.fullmatch(r"event\d+", x)), None),
                    "js": any(re.fullmatch(r"js\d+", x) for x in handlers), "keys": keys})
    return out


def _evnum(dev):
    return int(dev["event"][5:]) if dev.get("event") else 1 << 30


def steam_pads(devs=None):
    """Steam's virtual pads present now, in the order SDL numbers them (/dev/input/eventN): [(slot, /dev/input/eventN)]"""
    out = []
    for d in devs if devs is not None else input_devices():
        m = re.fullmatch(r"Microsoft X-Box 360 pad (\d+)", d["name"])
        if m and (d["vendor"], d["product"]) == STEAM_VPAD:
            out.append((_evnum(d), int(m.group(1)), f"/dev/input/{d['event']}" if d["event"] else None))
    return [(slot, path) for _, slot, path in sorted(out)]


def sdl_order(devs=None):
    """Steam's virtual pads (slots) in the order SDL numbers them: by /dev/input/eventN (checked with SDL itself)."""
    return [slot for slot, _ in steam_pads(devs)]


def _hidraw(event):
    h = sorted(glob.glob(f"/sys/class/input/{event}/device/device/hidraw/hidraw*")) if event else []
    return f"/dev/{os.path.basename(h[0])}" if h else None


def physical_list(devs=None):
    """Real controllers connected right now, from their own input devices (not Steam's virtual copies, not the Deck):
    [{kind, mac, raw, hidraw}], kind: switchpro / switch2 / ps5 / other. The Switch 2 Pro's device is the bridge's
    uinput pad (no Uniq; its MAC is in its phys "switch2-bridge/<mac>")."""
    out = []
    for d in devs if devs is not None else input_devices():
        if not d["js"]:
            continue
        name, mac = d["name"], d["uniq"]
        if name in ("Pro Controller", "Joy-Con (L)", "Joy-Con (R)", "Joy-Con (L/R)"):
            kind = "switchpro"
        elif name.startswith("Switch 2 Pro"):
            kind = "switch2"
        elif "DualSense" in name or "Wireless Controller" in name:
            kind = "ps5"
        elif "Xbox Wireless" in name:
            kind = "other"
        else:
            continue
        if kind == "switch2" and d["phys"].startswith("switch2-bridge/"):
            bridge_mac = d["phys"].split("/", 1)[1].lower()
        else:
            bridge_mac = ""
        out.append({"kind": kind, "mac": mac, "bridge_mac": bridge_mac,
                    "raw": f"/dev/input/{d['event']}" if d["event"] else None, "hidraw": _hidraw(d["event"])})
    return out


def mac_from_handle(handle):
    """virtualgamepadinfo handle 0x0004bcce253c2233 -> bc:ce:25:3c:22:33 (meaningful for Bluetooth pads only)"""
    h = (handle or "").lower().replace("0x", "")[-12:]
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)) if len(h) == 12 else ""


def batteries():
    """bluetooth address (lower case) -> (percent or None, charging), from the kernel's power_supply entries"""
    out = {}
    for d in glob.glob("/sys/class/power_supply/*"):
        if os.path.basename(d) in ("ACAD", "BAT1", "BAT0"):
            continue
        try:
            uev = open(f"{d}/device/uevent").read()
        except OSError:
            continue
        m = re.search(r"HID_UNIQ=([0-9a-fA-F:]{17})", uev)
        if not m:
            continue
        pct = None
        try:
            pct = int(open(f"{d}/capacity").read())
        except (OSError, ValueError):
            lvl = (open(f"{d}/capacity_level").read().strip() if os.path.exists(f"{d}/capacity_level") else "")
            pct = {"Full": 100, "High": 75, "Normal": 50, "Low": 20, "Critical": 5}.get(lvl)
        try:
            chg = open(f"{d}/status").read().strip() == "Charging"
        except OSError:
            chg = False
        out[m.group(1).lower()] = (pct, chg)
    return out


_S2_CURVE = ((3000, 0), (3300, 15), (3500, 30), (3650, 45), (3750, 60), (3850, 72), (3950, 84), (4050, 94), (4150, 100))


def switch2_battery():
    """(pct, charging) of the Switch 2 Pro. The bridge's mV -> % table assumed a full cell reads ~4.15 V, but this pad
    reports ~3.70-3.73 V when it says "charge complete" (2026-10-10: the owner "never seems fully charged" - it showed
    ~50% straight off the charger). So the voltage is scaled by the pad's own full reading (learned each time it
    reports charge complete, state 0x20)."""
    try:
        st = json.load(open(S2_STATE))
        for pd in st.get("pads", []):
            if not pd.get("connected"):
                continue
            mv, raw = pd.get("battery_mv"), pd.get("charge_state_raw")
            if raw == 0x20 and mv:                       # on power, charge complete: full - remember what full reads
                try:
                    if abs(int(open(S2_FULL).read()) - mv) > 3:
                        open(S2_FULL, "w").write(str(mv))
                except (OSError, ValueError):
                    open(S2_FULL, "w").write(str(mv))
                return 100, True
            if not mv:
                return pd.get("battery_pct"), bool(pd.get("charging"))
            try:
                full = int(open(S2_FULL).read())
            except (OSError, ValueError):
                full = 3720
            v = mv * 4150 / max(3400, full)
            pct = 100
            for (v0, p0), (v1, p1) in zip(_S2_CURVE, _S2_CURVE[1:]):
                if v < v1:
                    pct = max(0, round(p0 + (p1 - p0) * (v - v0) / (v1 - v0))) if v >= v0 else 0
                    break
            return pct, bool(pd.get("charging"))
    except (OSError, ValueError):
        pass
    return None, False


# ---------- the snapshot ----------

_FULL_NAME = {"switchpro": "Nintendo Switch Pro Controller", "ps5": "DualSense Wireless Controller"}


def _kind_of(p, i):
    """Controller kind from Steam's entry for a pad."""
    typ = p["type"]
    if p["deck"]:
        return "deck"
    if not typ:
        return None
    try:
        vp = (int(i.get("VID", ""), 16), int(i.get("PID", ""), 16))
    except ValueError:
        vp = None
    if typ == "switch2" or (typ == "xbox360" and vp == BRIDGE_PAD):
        return "switch2"
    if typ.startswith("switch"):
        return "switchpro"
    if typ in ("ps5", "ps4"):
        return "ps5"
    return "other"


def label(p):
    """The chip's name for a pad. (Any Xbox-type pad used to be called "Switch 2 Pro" and given the bridge's battery;
    now only the bridge's own pad is.)"""
    name, typ = p.get("name", ""), p.get("type", "")
    if p.get("deck"):
        return "Steam Deck"
    if typ == "switch2" or p.get("kind") == "switch2":
        return "Switch 2 Pro"
    if typ == "switchpro" or "Pro Controller" in name:
        return "Switch Pro"
    if typ in ("ps5", "ps4") or "DualSense" in name or "Wireless Controller" in name:
        return "DualSense"
    return name or "Controller"


def snapshot(dock=None):
    dock = docked() if dock is None else dock
    devs = input_devices()
    info = read_info()
    phys = physical_list(devs)
    vpads = steam_pads(devs)
    order = [slot for slot, _ in vpads]                   # SDL's numbering (device index, Dolphin's SDL/<n>/<name>)
    path_of = dict(vpads)
    sdl_of = {slot: d for d, slot in enumerate(order)}
    seen, nth = {}, {}
    for slot in order:
        name = info.get(slot, {}).get("name") or f"Microsoft X-Box 360 pad {slot}"
        nth[slot] = seen.get(name, 0)
        seen[name] = nth[slot] + 1
    pads = []
    for slot in sorted(order):                            # players go by slot (= the chips' numbers)
        i = info.get(slot, {})
        name = i.get("name") or f"Microsoft X-Box 360 pad {slot}"
        p = {"slot": slot, "sdl": sdl_of[slot], "name": name, "dolphin": f"SDL/{nth[slot]}/{name}",
             "deck": i.get("type") == "steam" or name == DECK_NAME, "type": i.get("type", ""),
             "virtual": path_of.get(slot), "raw": None, "hidraw": None, "recovered": False,
             "mac": mac_from_handle(i.get("handle"))}
        p["kind"] = _kind_of(p, i)
        pads.append(p)

    used = []                                             # physical controllers already tied to a pad

    def tie(p, ph):
        used.append(ph)
        p["raw"], p["hidraw"] = ph["raw"], ph["hidraw"]
        if ph["bridge_mac"]:
            p["mac"] = ph["bridge_mac"]

    def adopt(p, ph, rename=True):                        # a live pad identified by its physical controller
        p["type"] = p["kind"] = ph["kind"]
        p["recovered"] = True
        p["mac"] = ph["mac"] or p["mac"]
        if rename:
            p["name"] = _FULL_NAME.get(ph["kind"], p["name"])
        tie(p, ph)

    typed = [p for p in pads if not p["deck"] and p["type"]]
    untyped = [p for p in pads if not p["deck"] and not p["type"]]
    deck = [p for p in pads if p["deck"]]
    if typed:
        # Steam's list knows the real pads, but it can miss one (2026-10-10: a Switch Pro played but had no entry, so
        # it wasn't shown or given a player). More physical controllers than listed ones -> that many untyped live pads
        # are real (identity taken from the unlisted physical controller); the rest are orphans Steam left behind.
        listed = {p["mac"] for p in typed}
        missing = [ph for ph in phys if ph["mac"] and ph["mac"] not in listed]
        extra = untyped[:len(missing)]
        for p, ph in zip(extra, missing):
            adopt(p, ph)
        ext = sorted(typed + extra, key=lambda p: p["slot"])
        stale = [p for p in untyped if p not in extra]
    else:
        # Steam's list lost every controller's type (after a Steam restart; the docked Deck then became player 1 at the
        # 2026-10-10 party, NES 1943 / Bomberman II): the untyped pads ARE the controllers - as many as are really
        # connected (raw Pro / Switch 2 / DualSense devices), lowest slots first. They take the physical controllers'
        # identities in order, so they get chips too (they used to get emulator ports but no chip). Their names stay
        # as SDL sees them (Dolphin's guess for a late pad uses them).
        ext = untyped[:len(phys)] if phys else untyped
        for p, ph in zip(ext, phys):
            adopt(p, ph, rename=False)
        stale = [p for p in untyped if p not in ext]

    # the physical device behind each listed pad: by MAC (Bluetooth), the Switch 2 Pro by its bridge pad
    bats = batteries()
    for p in pads:
        if p in stale:
            p["kind"] = None
        if not p["recovered"] and p["kind"] not in (None, "deck"):
            free = [ph for ph in phys if ph not in used]
            ph = next((ph for ph in free if ph["kind"] == "switch2"), None) if p["kind"] == "switch2" else \
                next((ph for ph in free if ph["mac"] and ph["mac"] == p["mac"]), None)
            if ph:
                tie(p, ph)
        p["label"] = label(p)
        p["battery"], p["charging"] = bats.get(p["mac"], (None, False)) if p["mac"] else (None, False)
        if p["label"] == "Switch 2 Pro" and p["battery"] is None:   # the bridge writes its pad's battery
            p["battery"], p["charging"] = switch2_battery()

    # Docked: the Deck sits in the dock, so it is left out. Players beyond the connected pads get the free indices, which
    # is where pads that wake up after launch land once Steam puts the externals first (2026-10-09 MK8D: the Pros
    # connected 30 s after the game started).
    try:                  # the order chosen on "Who's playing?" (Y, Change order): those pads first, in that order
        chosen = json.load(open("/tmp/prism_player_order.json")).get("order") or []
    except (OSError, ValueError):
        chosen = []
    if chosen:
        ident = lambda p: p.get("mac") or f"slot{p['slot']}"
        ext.sort(key=lambda p: (chosen.index(ident(p)) if ident(p) in chosen else len(chosen), p["slot"]))
    players = ext if (dock and ext) else deck + ext + stale
    n = 0
    for p in pads:
        p["port"] = next((k for k, q in enumerate(players) if q is p), None)
        p["player"] = None
    for p in players:                                     # chips: no stale pads, no docked Deck
        if p["kind"] is not None and not (dock and p["deck"]):
            n += 1
            p["player"] = n
    guess = [i.get("name") for s, i in info.items() if i.get("type") not in ("", "steam", None) and i.get("name")]
    left_out = [p for p in pads if p["port"] is None]     # the docked Deck: its index is not handed to a late pad
    return {"docked": dock, "pads": pads, "players": players, "externals": len(ext), "physical": len(phys),
            "dolphin_next": seen, "guess_names": [p["name"] for p in ext] + guess,
            "reserved_dense": [p["sdl"] for p in left_out], "reserved_slots": [p["slot"] for p in left_out]}


def card_inputs(devs=None):
    """Input devices the Home card reads (and grabs) while it is up: every controller, both Steam's virtual copy and
    the raw device - a copy can vanish or go quiet (Steam dropped two Pros' copies while Eden ran; the Switch 2 Pro's
    copy went silent with the game paused, 2026-10-09). Anything with A and B buttons counts, except the agent's
    injector pad. Paths, in eventN order. Raw Nintendo devices (vendor NINTENDO) report buttons by position, so the
    card swaps their A/B."""
    out = []
    for d in devs if devs is not None else input_devices():
        if not d["event"] or (d["vendor"], d["product"]) == AGENT_PAD:
            continue
        if (d["keys"] >> BTN_SOUTH) & 1 and (d["keys"] >> BTN_EAST) & 1:
            out.append(f"/dev/input/{d['event']}")
    return sorted(out, key=lambda p: int(p.rsplit("event", 1)[1]))


if __name__ == "__main__":
    s = snapshot()
    print(json.dumps({k: v for k, v in s.items() if k != "players"}, indent=1))
