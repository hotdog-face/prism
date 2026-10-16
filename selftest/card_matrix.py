#!/usr/bin/env python3
"""Home-card reliability matrix (2026-10-09, the owner: "make sure that interaction is reliable ... in all game types").

For one game per emulator (and a Steam game): launch it from Prism's "Card Test" collection, press Home on a virtual
controller, then check: the card opens, the game is paused, Left/Right move the selection, Resume resumes, and a second
Home -> "Save & go Home" / "Quit game" ends the game and puts Prism back in front. Results: selftest/reports/card_matrix.txt

  card_matrix.py setup      write the Card Test collection and put it on the home row (restarts Prism, when idle)
  card_matrix.py run [n..]  run all cases (or the named ones)
  card_matrix.py teardown   Card Test off the home row again (restarts Prism)
"""
import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Prism root
import prism_config  # noqa: E402  (paths)
import json, os, re, socket, sys, time

ROMS = prism_config.ROMS
EVENTS = "/tmp/esde_events.log"
REPORT = f"{prism_config.PRISM}/selftest/reports/card_matrix.txt"
COLL = f"{prism_config.ESDE}/collections/custom-Card Test.cfg"
# name (as ES-DE shows it), rom path, kind, seconds to settle after the emulator appears
import fixtures  # noqa: E402  (this Deck's games: ~/.config/prism/selftest.json)
CASES = fixtures.card_cases()


def log(*a):
    line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    with open(REPORT, "a") as f:
        f.write(line + "\n")


def cc(cmd, **kw):
    try:
        c = socket.socket(socket.AF_UNIX); c.settimeout(2); c.connect("/tmp/deck_cc.sock")
        c.sendall((json.dumps({"cmd": cmd, **kw}) + "\n").encode()); r = c.recv(4096).decode().strip(); c.close()
        return json.loads(r) if r.startswith("{") else r
    except (OSError, ValueError):
        return None


# ---- virtual controller (Steam adopts it like a real pad; Prism also reads its raw device)
class Pad:
    def __init__(self):
        import evdev
        from evdev import UInput, ecodes as E, AbsInfo
        self.E = E
        cap = {E.EV_KEY: [E.BTN_SOUTH, E.BTN_EAST, E.BTN_NORTH, E.BTN_WEST, E.BTN_TL, E.BTN_TR, E.BTN_SELECT,
                          E.BTN_START, E.BTN_MODE, E.BTN_THUMBL, E.BTN_THUMBR],
               E.EV_ABS: [(E.ABS_X, AbsInfo(0, -32768, 32767, 16, 128, 0)), (E.ABS_Y, AbsInfo(0, -32768, 32767, 16, 128, 0)),
                          (E.ABS_RX, AbsInfo(0, -32768, 32767, 16, 128, 0)), (E.ABS_RY, AbsInfo(0, -32768, 32767, 16, 128, 0)),
                          (E.ABS_Z, AbsInfo(0, 0, 255, 0, 0, 0)), (E.ABS_RZ, AbsInfo(0, 0, 255, 0, 0, 0)),
                          (E.ABS_HAT0X, AbsInfo(0, -1, 1, 0, 0, 0)), (E.ABS_HAT0Y, AbsInfo(0, -1, 1, 0, 0, 0))]}
        self.ui = UInput(cap, name="Prism Card Test Pad", vendor=0x045E, product=0x0B13, version=0x0110, bustype=0x03)
        time.sleep(2.0)

    def tap(self, key, hold=0.08):
        self.ui.write(self.E.EV_KEY, key, 1); self.ui.syn(); time.sleep(hold)
        self.ui.write(self.E.EV_KEY, key, 0); self.ui.syn(); time.sleep(0.35)

    def hat(self, x):
        self.ui.write(self.E.EV_ABS, self.E.ABS_HAT0X, x); self.ui.syn(); time.sleep(0.08)
        self.ui.write(self.E.EV_ABS, self.E.ABS_HAT0X, 0); self.ui.syn(); time.sleep(0.35)

    def home(self): self.tap(self.E.BTN_MODE)
    def a(self): self.tap(self.E.BTN_SOUTH)
    def close(self): self.ui.close()


# ---- Prism / game state (prism_state: the game ES-DE launched = its process tree, Steam games = their own launch)
import prism_state as ps


def esde_pid():
    return ps.esde_pid()


def game_procs():
    """(pid, state) of the game ES-DE launched, without the launcher chain the card never pauses"""
    import deck_curate
    return [(k, ps.proc_state(k)) for k in ps.game_procs(ps.esde_pid())
            if not deck_curate.protected(k) and ps.proc_state(k) not in ("Z", "")]


def running(kind):
    return bool(ps.other_apps()) if kind == "steam" else ps.game_running(ps.esde_pid())


def paused(kind):
    if kind == "steam":
        return None                                  # Steam games keep running behind the card (by design)
    procs = game_procs()
    return bool(procs) and all(st == "T" for _, st in procs)


def focus():
    return str(ps.front_app(fresh=True))


def esde_keys(keys):
    import deck_curate
    deck_curate.esde_keys(keys)


def last_select():
    try:
        lines = open(EVENTS).read().splitlines()[-40:]
    except OSError:
        return ""
    for l in reversed(lines):
        m = re.search(r"game-select \S+ (.*)$", l)
        if m:
            return m.group(1).strip()
    return ""


def last_system():
    try:
        lines = open(EVENTS).read().splitlines()[-60:]
    except OSError:
        return ""
    for l in reversed(lines):
        m = re.search(r"system-select (.*)$", l)
        if m:
            return m.group(1).strip()
    return ""


def open_list():
    """Prism into the Card Test list from wherever it is on the home row (it sits right of "recent"). Back only, never
    Escape (= ES-DE's main menu). True when a game in it is selected."""
    esde_keys(["BackSpace"]); time.sleep(1.0)
    for _ in range(40):
        if last_system() == "Card Test":
            break
        esde_keys(["Right"] if last_system() == "recent" else ["Left"])
        time.sleep(0.35)
    if last_system() != "Card Test":
        log(f"FAIL not on Card Test (at {last_system()!r}): run `card_matrix.py setup` first")
        return False
    esde_keys(["Return"]); time.sleep(1.5)
    return bool(last_select())


def goto(name):
    """In the Card Test list, move the cursor to `name` (ES-DE's game-select events tell where it is)."""
    for step in range(30):
        cur = last_select()
        if cur in (name, name.lstrip("#")) or cur.startswith(name.lstrip("#") + " ["):
            return True
        esde_keys(["Down"]); time.sleep(0.45)
    return last_select() == name or last_select().startswith(name + " [")


def wait(cond, sec, step=0.25):
    end = time.time() + sec
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


def card_info():
    r = cc("info")
    return r if isinstance(r, dict) else {"active": False}


def case(pad, name, rom, kind, settle):
    res = {}
    log(f"== {name} ({kind})")
    if not goto(name):
        log(f"  FAIL could not select it in the list (at {last_select()!r})"); return {"select": False}
    esde_keys(["Return"])
    res["launch"] = wait(lambda: running(kind), 45)
    if not res["launch"]:
        log("  FAIL game didn't start"); return res
    time.sleep(settle)
    log(f"  running: {ps.other_apps() if kind == 'steam' else sorted({ps.comm(k) for k, _ in game_procs()})}")
    # 1st Home: card opens, game paused, selection moves, Resume
    pad.home()
    res["open"] = wait(lambda: card_info().get("active"), 5)
    info = card_info(); log(f"  card: {info}")
    if not res["open"]:
        log("  FAIL card didn't open"); return finish(pad, kind, res)
    p = paused(kind)
    res["paused"] = True if p is None else p
    pad.hat(1); s1 = card_info().get("sel")
    pad.hat(-1); s0 = card_info().get("sel")
    res["nav"] = (s1 == 1 and s0 == 0)
    log(f"  nav right->{s1} left->{s0}")
    pad.a()                                          # Resume (item 0)
    res["resume"] = wait(lambda: not card_info().get("active"), 4) and (kind == "steam" or wait(lambda: not paused(kind), 3))
    time.sleep(2.5)
    # 2nd Home: Save & go Home / Quit game
    pad.home()
    ok2 = wait(lambda: card_info().get("active"), 5)
    items = card_info().get("items") or []
    target = "STEAMQUIT" if kind == "steam" else "HOME"
    if not ok2 or target not in items:
        res["exit"] = False; log(f"  FAIL second card: active={ok2} items={items}"); return finish(pad, kind, res)
    for _ in range(items.index(target)):
        pad.hat(1)
    log(f"  at {card_info().get('sel')} of {items}, choosing {target}")
    pad.a()
    res["exit"] = wait(lambda: not running(kind), 40)
    esde_pidv = esde_pid()
    res["prism_front"] = bool(esde_pidv) and wait(lambda: focus() == prism_config.PRISM_APPID, 15)
    return finish(pad, kind, res)


def finish(pad, kind, res):
    # leave nothing behind: close a card that's still up, end a game that's still running
    if card_info().get("active"):
        cc("close"); time.sleep(1.5)
    if running(kind):
        import deck_curate
        if kind == "steam":
            deck_curate.steam_js("(SteamClient.Apps.TerminateApp(String(SteamUIStore.MainRunningAppID), false), 1)", "test")
        else:
            deck_curate.quit_game(fade=False)
        wait(lambda: not running(kind), 30)
    bad = [k for k, v in res.items() if v is False]
    log(f"  {'PASS' if not bad else 'FAIL ' + ', '.join(bad)}  {res}")
    time.sleep(3)
    return res


def write_collection():
    with open(COLL, "w") as f:
        f.write("".join(f"%ROMPATH%/{r}\n" for _, r, _, _ in CASES if os.path.exists(f"{ROMS}/{r}")))
    missing = [r for _, r, _, _ in CASES if not os.path.exists(f"{ROMS}/{r}")]
    print("Card Test collection written; missing:", missing)


def setup():
    """The Card Test collection written and shown first on the home row (ES-DE closed and reopened)."""
    import prism_esde
    with prism_esde.esde_closed("card matrix: Card Test collection on"):
        write_collection()
        prism_esde.set_custom_collection("Card Test", True)
    print("Card Test on")


def teardown():
    """Card Test off the home row again (its .cfg stays)."""
    import prism_esde
    with prism_esde.esde_closed("card matrix: Card Test collection off"):
        prism_esde.set_custom_collection("Card Test", False)
    print("Card Test off")


def run(names):
    open(REPORT, "w").write(f"Home-card matrix {time.strftime('%Y-%m-%d %H:%M')}\n")
    if not open_list():
        return
    pad = Pad()
    results = {}
    try:
        for name, rom, kind, settle in CASES:
            if names and name not in names and kind not in names:
                continue
            try:
                results[name] = case(pad, name, rom, kind, settle)
            except Exception as e:
                log(f"  ERROR {e!r}"); results[name] = {"error": False}
    finally:
        pad.close()
    passed = sum(1 for r in results.values() if r and all(v is not False for v in r.values()))
    log(f"RESULT {passed}/{len(results)} game types passed")


if __name__ == "__main__":
    if sys.argv[1:2] == ["setup"]:
        setup()
    elif sys.argv[1:2] == ["teardown"]:
        teardown()
    else:
        run(sys.argv[2:] if sys.argv[1:2] == ["run"] else [])
