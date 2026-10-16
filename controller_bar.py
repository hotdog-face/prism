#!/usr/bin/env python3
"""Controller bar on Prism's home screen (2026-10-09): which controllers are connected, which player each
one is, and its battery, drawn by the deck_cc overlay at the top of the console row. Supervised thread in
switchhome.py; which controllers there are, their player numbers (the same order games get) and batteries all come
from pads.snapshot(). Shown only while the console row is in front (no game, no Steam menu)."""
import prism_config  # noqa: E402  (paths)
import json, os, socket, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pads                  # noqa: E402  (which controllers exist and who is which player: one snapshot, 2026-10-13)
import prism_state as ps     # noqa: E402  (what's running / in front: one implementation, 2026-10-13)
ESDE_APP = ps.PRISM_APP


NOT_GAMES = {"needsalook"}   # ES-DE rows whose "launch" opens a Prism card: no disconnect pause, no idle-off

def items():
    """The chips: one per controller that is a player (no stale pads, not the docked Deck), in player order."""
    return [{"n": p["player"], "name": p["label"], "bat": p["battery"], "chg": p["charging"], "slot": p["slot"]}
            for p in pads.snapshot()["players"] if p["player"]]


# ---- input glow: a press on a controller lights up its chip, so each person can see which player they are
_slot_to_n = {}
_showing = [False]
_menu_lock = [False]         # docked, Prism's menus in front: only player 1 drives them (2026-10-10 party: mashing)
_p1_slot = [None]
EVENTS = "/tmp/esde_events.log"
_last_press = [0.0]          # any button on any controller (resumes a disconnect pause)
_slot_last = {}              # slot -> last input time (idle auto-off)
IDLE_OFF_MENUS, IDLE_OFF_GAME = 20 * 60, 45 * 60   # Bluetooth pads left on and untouched get switched off


def activity():
    import select, evdev
    from evdev import ecodes as E
    devs, scanned, last = {}, 0.0, {}
    while True:
        # Launch lock: right after a game-start, ALL Steam pads are grabbed for 3 s (2026-10-10 party: mashing A launched
        # games 3-4 times). Checked first: the bar hides as the game starts, and that must not drop the lock (it did).
        try:
            size = os.path.getsize(EVENTS)
            if size != getattr(activity, "ev_size", size):
                with open(EVENTS, "rb") as f:
                    f.seek(max(0, size - 600)); tail = f.read().decode(errors="ignore")
                if "game-start" in tail[-(size - activity.ev_size):]:
                    activity.launch_until = time.time() + 3
                    print("[controller-bar] launch lock: all pads held for 3 s", flush=True)
            activity.ev_size = size
        except (OSError, AttributeError):
            activity.ev_size = os.path.getsize(EVENTS) if os.path.exists(EVENTS) else 0
        launch = time.time() < getattr(activity, "launch_until", 0)
        if not _showing[0] and not launch:             # nothing on screen to glow: don't read pads at all (CPU)
            for d in devs.values():
                try: d.close()
                except Exception: pass
            devs, scanned = {}, 0.0
            time.sleep(0.1); continue                  # (short: a game-start must lock within a press)
        if time.time() - scanned > 5:                  # Steam's virtual pads come and go
            for d in devs.values():
                try: d.close()
                except Exception: pass
            devs = {}
            for slot, path in pads.steam_pads():
                try:
                    d = evdev.InputDevice(path)
                    devs[d.fd] = d; d.slot = slot
                except (OSError, TypeError):
                    pass
            scanned = time.time()
        if not devs:
            time.sleep(1); continue
        # menu lock: grab (EVIOCGRAB) every Steam pad except player 1's, so ES-DE only hears player 1; we still read
        # them here for the chip glow
        for d in devs.values():
            want = launch or (_menu_lock[0] and getattr(d, "slot", -1) != _p1_slot[0])
            if want != getattr(d, "grabbed", False):
                try:
                    d.grab() if want else d.ungrab()
                    d.grabbed = want
                except OSError:
                    pass
        try:
            r, _, _ = select.select(list(devs), [], [], 0.3)
        except (OSError, ValueError):
            scanned = 0; continue
        for fd in r:
            d = devs.get(fd)
            try:
                hit = False
                for ev in d.read():
                    if (ev.type == E.EV_KEY and ev.value == 1) or (ev.type == E.EV_ABS and ev.code in
                            (E.ABS_X, E.ABS_Y, E.ABS_RX, E.ABS_RY) and abs(ev.value) > 16000):
                        hit = True
            except (OSError, BlockingIOError):
                scanned = 0; continue
            if hit:
                _last_press[0] = time.time()
                _slot_last[getattr(d, "slot", -1)] = time.time()
            n = _slot_to_n.get(getattr(d, "slot", -1))
            if hit and n and _showing[0] and time.time() - last.get(n, 0) > 0.12:
                last[n] = time.time()
                send({"cmd": "pads_act", "n": n})


def send(msg):
    try:
        c = socket.socket(socket.AF_UNIX); c.settimeout(1); c.connect("/tmp/deck_cc.sock")
        c.sendall((json.dumps(msg) + "\n").encode()); c.recv(64); c.close()
    except OSError:
        pass


def selftest_running():
    return ps.selftest_running()


class Guard:
    """In a game: low-battery toasts, and a pause when a player's controller drops (2026-10-09)."""
    def __init__(self, dc):
        self.dc, self.alerted, self.seen, self.missing, self.paused = dc, {}, {}, {}, None

    def idle_off(self, cur, in_game):
        """A Bluetooth controller untouched for 20 min (45 in a game) is disconnected, which switches it off; Home wakes
        it again. Saves the batteries of pads left lying around lit (2026-10-09)."""
        now = time.time()
        limit = IDLE_OFF_GAME if in_game else IDLE_OFF_MENUS
        macs = None
        for slot, it in cur.items():
            if it["name"] == "Steam Deck":
                continue
            _slot_last.setdefault(slot, now)             # first seen = active now
            if now - _slot_last[slot] < limit or it.get("chg"):
                continue
            if macs is None:
                macs = {p["slot"]: p["mac"] for p in pads.snapshot()["pads"]}
            mac = macs.get(slot)
            _slot_last[slot] = now                       # don't retry every second
            if it["name"] == "Switch 2 Pro":
                try:                                     # the bridge powers its pad off on request (switch2_bridge.py)
                    os.makedirs(os.path.expanduser("~/.cache/switch2-bridge"), exist_ok=True)
                    open(os.path.expanduser("~/.cache/switch2-bridge/sleep_request"), "w").write(str(now))
                except OSError:
                    pass
                print(f"[controller-bar] idle: asked the bridge to switch off player {it['n']}", flush=True)
            elif mac:
                import subprocess
                if it["name"] == "Switch Pro":           # its own sleep command first (a disconnect left it lit)
                    subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/pad_sleep.py", mac],
                                   capture_output=True, timeout=15)
                else:
                    subprocess.run(["bluetoothctl", "disconnect", mac], capture_output=True, timeout=10)
                print(f"[controller-bar] idle {int(limit / 60)} min: switched off player {it['n']} ({it['name']}, {mac})", flush=True)

    def unfreeze_guard(self, pid):
        if selftest_running():
            self.frozen_since = None; return          # the rescue test reproduces a stuck pause on purpose
        """Self-healing (2026-10-09): a game paused (SIGSTOP) with no card up and no disconnect notice for more than
        5 s is a stuck state (a card that died, a crash mid-pause): resume it."""
        import signal as _sig
        if self.paused or self.card_up():
            self.frozen_since = None; return
        stopped = False
        for k in self.dc.esde_game_procs(pid):
            if k == pid:
                continue
            try:
                if open(f"/proc/{k}/stat").read().rsplit(")", 1)[1].split()[0] == "T":
                    stopped = True; break
            except (OSError, IndexError):
                pass
        if not stopped:
            self.frozen_since = None; return
        now = time.time()
        if getattr(self, "frozen_since", None) is None:
            self.frozen_since = now
        elif now - self.frozen_since > 5:
            self.dc.signal_game(pid, _sig.SIGCONT)
            print("[controller-bar] self-heal: game was paused with no card up - resumed it", flush=True)
            self.frozen_since = None

    def card_up(self):
        return ps.card_up()

    def tick(self):
        dc = self.dc
        pid = dc.esde_pid()
        in_game = bool(pid) and dc.game_running(pid)
        if in_game and (dc.selected() or ("", "", ""))[2] in NOT_GAMES:
            in_game = False                              # a Prism card launched from a row (Needs a look), not a game
        now = time.time()
        if not in_game:
            if self.paused:                              # game ended while paused: just clear the notice
                send({"cmd": "unnotice"}); self.paused = None
            self.seen.clear(); self.missing.clear()
            self.idle_off({it["slot"]: it for it in items()}, in_game=False)
            return
        self.unfreeze_guard(pid)
        cur = {it["slot"]: it for it in items()}
        # low battery: once per controller at "low", again at "critical"; never while charging
        for slot, it in cur.items():
            b = it.get("bat")
            if b is None or it.get("chg"):
                self.alerted.pop(slot, None); continue
            lvl = 2 if b <= 5 else 1 if b <= 20 else 0
            if lvl and self.alerted.get(slot, 0) < lvl:
                self.alerted[slot] = lvl
                dc.toast(f"Player {it['n']} battery {'critical' if lvl == 2 else 'low'}",
                         f"{it['name']} - charge it after this game")
            elif not lvl:
                self.alerted.pop(slot, None)
        self.idle_off(cur, in_game=True)
        # disconnect pause
        if self.paused:
            slot, t0 = self.paused["slot"], self.paused["t"]
            if slot in cur or (_last_press[0] > t0 + 0.6):
                dc.signal_game(pid, __import__("signal").SIGCONT)
                send({"cmd": "unnotice"})
                print(f"[controller-bar] resumed ({'reconnected' if slot in cur else 'continue pressed'})", flush=True)
                self.paused = None
                self.seen = dict(cur); self.missing.clear()
            return
        for slot, it in cur.items():
            self.seen[slot] = it
        for slot in list(self.seen):
            if slot in cur:
                self.missing.pop(slot, None); continue
            self.missing.setdefault(slot, now)
            if now - self.missing[slot] > 1.5 and not self.card_up():
                it = self.seen[slot]
                import signal as _sig
                dc.signal_game(pid, _sig.SIGSTOP)
                send({"cmd": "notice", "n": it["n"], "title": f"Player {it['n']} disconnected",
                      "sub": f"Turn the {it['name']} back on, or press A on any controller to keep playing"})
                print(f"[controller-bar] paused: player {it['n']} ({it['name']}) disconnected", flush=True)
                self.paused = {"slot": slot, "t": now}
                self.seen.pop(slot, None)
                return


def card_now(dc):
    return ps.card_up()


def run():
    import deck_curate, threading
    threading.Thread(target=activity, daemon=True).start()
    guard = Guard(deck_curate)
    last = None
    known, cand = None, {}        # joined / left pop-ups: slot -> item; a change must hold for 2 checks (Steam churns)
    while True:
        try:
            pid = deck_curate.esde_pid()
            in_menus = ps.in_menus()
            home = in_menus and deck_curate.system_view() is not None
            # console row: full chips at the top centre; game lists: compact chips beside the status icons (the
            # top centre there is the console switcher) - the owner wants it whenever Prism's menus are up
            show = in_menus
            its = items() if (show or (pid and not in_menus)) else []     # in games too: the Home card shows them
            _menu_lock[0] = bool(in_menus and pads.docked() and not card_now(deck_curate))
            pl = [it for it in its if it.get("name") != "Steam Deck"]
            _p1_slot[0] = pl[0]["slot"] if pl else None
            if _p1_slot[0] is None or len(pl) < 2:
                _menu_lock[0] = False        # no player 1 (or only one controller): never lock anyone out
            msg = {"cmd": "pads", "show": bool(show), "items": its, "mode": "full" if home else "compact"}
            card = ps.card_up()
            _showing[0] = bool(show) or card          # input glow on the bar and on the Home card
            _slot_to_n.clear(); _slot_to_n.update({it["slot"]: it["n"] for it in msg["items"]})
            if msg != last or show:                   # re-sent every 2 s while shown (the overlay may have hidden)
                send(msg); last = msg
            guard.tick()
            # joined / left pop-ups (Prism's own; Steam's are off)
            cur = {it["slot"]: it for it in (its if its else items()) if it.get("name") != "Steam Deck"}
            if known is None:
                known = cur
            else:
                for slot in set(cur) | set(known):
                    if (slot in cur) != (slot in known):
                        cand[slot] = cand.get(slot, 0) + 1
                        if cand[slot] >= 2:
                            joined = slot in cur
                            gone_in_game = not joined and pid and not in_menus     # in a game: Guard's pause notice says it
                            if not gone_in_game and not selftest_running():
                                send({"cmd": "padtoast", "item": cur.get(slot) or known.get(slot), "joined": joined})
                            print(f"[controller-bar] {'joined' if joined else 'left'}: slot {slot}", flush=True)
                            known = dict(known)
                            if joined:
                                known[slot] = cur[slot]
                                import prism_features
                                if in_menus and (0 if pads.docked() else 1) + len(cur) >= 2 and not card \
                                        and prism_features.feel("join_on_connect"):
                                    import subprocess                 # a second player: "Who's playing?"
                                    subprocess.Popen(["systemd-run", "--user", "--collect", "--quiet", "/usr/bin/python3",
                              f"{prism_config.PRISM}/prism_screens.py", "join", "second controller"])
                            else:
                                known.pop(slot, None)
                            cand.pop(slot, None)
                    else:
                        cand.pop(slot, None)
                        if slot in cur:
                            known[slot] = cur[slot]                       # keep name / battery / number current
            if int(time.time()) % 5 == 0:
                import prism_profile
                prism_profile.keep()             # docked / handheld profile kept in force (incl. Steam menus guard)
                deck_curate.steam_pad_toasts_off()
        except Exception as e:
            print("[controller-bar]", e, flush=True)
        time.sleep(1)


if __name__ == "__main__":
    print(json.dumps(items(), indent=1))
