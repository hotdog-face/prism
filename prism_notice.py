#!/usr/bin/env python3
"""Prism's notices (2026-10-14): the one way background jobs tell the person holding the Deck something.

    import prism_notice
    prism_notice.toast("fixed-snes-list", "Prism fixed something", "Your Super Nintendo list was restored.")
    prism_notice.card("needs-a-look", "Needs a look", "3 games didn't start", "Last night's check ...",
                      ok="Show me", cancel="Later", action="collection:Needs a look")

Notices wait in notices.json until someone is actually using Prism's menus (ES-DE in front, nothing launched, input in
the last minute, no card or game hub up), then show one at a time: a toast is the small note top-right (deck_cc's
toast), a card is deck_confirm.py's card (A / B). The same id is never queued twice while it waits, and a card that
was answered isn't shown again for `quiet_days`. Supervised thread in switchhome.py ("notices").
    prism_notice.py list | clear | test-card | test-toast
"""
import json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_config  # noqa: E402  (paths)

QUEUE = f"{prism_config.PRISM}/notices.json"
LOG = "/tmp/prism_notice.log"
LAST_INPUT = "/tmp/deck_last_input"


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def _load():
    try:
        return json.load(open(QUEUE))
    except (OSError, ValueError):
        return {"pending": [], "answered": {}}


def _save(q):
    tmp = QUEUE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(q, f, indent=1)
    os.replace(tmp, QUEUE)


def _add(item, quiet_days):
    q = _load()
    last = q["answered"].get(item["id"])
    if last and time.time() - last < quiet_days * 86400:
        return False
    q["pending"] = [p for p in q["pending"] if p["id"] != item["id"]] + [dict(item, queued=int(time.time()))]
    _save(q)
    log(f"queued {item['kind']} {item['id']}")
    return True


def toast(nid, title, sub, quiet_days=0):
    return _add({"id": nid, "kind": "toast", "title": title, "sub": sub}, quiet_days)


def card(nid, title, name, detail, ok="OK", cancel="Later", action="", bar=None, quiet_days=7):
    """bar: (fraction, left label, right label) for the storage card."""
    return _add({"id": nid, "kind": "card", "title": title, "name": name, "detail": detail, "ok": ok,
                 "cancel": cancel, "action": action, "bar": list(bar) if bar else None}, quiet_days)


FIXED = f"{prism_config.PRISM}/fixed_recently.json"


def remember_fix(text):
    """Keep a plain-words line for Settings > System > Prism fixed recently (last 30)."""
    try:
        items = json.load(open(FIXED))
    except (OSError, ValueError):
        items = []
    items.insert(0, {"t": int(time.time()), "text": text})
    tmp = FIXED + ".tmp"
    json.dump(items[:30], open(tmp, "w"), indent=1)
    os.replace(tmp, FIXED)


def fixed_recently():
    try:
        return json.load(open(FIXED))
    except (OSError, ValueError):
        return []


def withdraw(nid):
    """A notice that no longer applies (space was freed, the game got fixed) is dropped before anyone sees it."""
    q = _load()
    n = len(q["pending"])
    q["pending"] = [p for p in q["pending"] if p["id"] != nid]
    if len(q["pending"]) != n:
        _save(q); log(f"withdrew {nid}")


# ---- showing them -------------------------------------------------------------------------------------------------
def someone_here():
    import prism_state
    try:
        recent = time.time() - os.path.getmtime(LAST_INPUT) < 60
    except OSError:
        recent = False
    return (recent and prism_state.in_menus() and not prism_state.card_up() and not prism_state.busy_flag()
            and not os.path.exists("/tmp/game_hub_ready") and not prism_state.selftest_running())


def show(item):
    import deck_curate as d
    if item["kind"] == "toast":
        d.toast(item["title"], item["sub"])
        return "SHOWN"
    pid = d.esde_pid()
    env = {**os.environ, **(d.esde_env(pid) if pid else {}), "DECK_CONFIRM_CANCEL": item.get("cancel") or "Later"}
    if item.get("bar"):
        env["DECK_CONFIRM_BAR"] = "|".join(str(x) for x in item["bar"])
    r = subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/deck_confirm.py", item["title"], item["name"],
                        item["detail"], item.get("ok") or "OK", "-"], env=env, capture_output=True, text=True,
                       timeout=60)
    answer = "CONFIRMED" if "CONFIRMED" in r.stdout else "CANCELLED"
    if answer == "CONFIRMED" and item.get("action"):
        act(item["action"])
    return answer


def act(action):
    kind, _, arg = action.partition(":")
    if kind in ("collection", "system"):   # jump to that tile on the home row and open it
        time.sleep(0.4)
        subprocess.Popen(["/usr/bin/python3", f"{prism_config.PRISM}/esde_nav.py", arg, "open"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif kind == "run":
        subprocess.Popen(["/usr/bin/python3", f"{prism_config.PRISM}/{arg}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(f"action {action}")


def run():
    """switchhome thread: show waiting notices when someone is using Prism."""
    while True:
        time.sleep(4)
        q = _load()
        if not q["pending"]:
            continue
        try:
            if not someone_here():
                continue
            item = q["pending"][0]
            answer = show(item)
            q = _load()
            q["pending"] = [p for p in q["pending"] if p["id"] != item["id"]]
            if item["kind"] == "card":
                q["answered"][item["id"]] = int(time.time())
            _save(q)
            log(f"showed {item['id']}: {answer}")
            time.sleep(8 if item["kind"] == "toast" else 2)
        except Exception as e:
            log(f"notice failed: {e}")
            time.sleep(30)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["list"]:
        print(json.dumps(_load(), indent=1))
    elif a == ["clear"]:
        _save({"pending": [], "answered": {}}); print("cleared")
    elif a == ["test-card"]:
        card("test-card", "Storage is getting low", "18 GB left on the SD card",
             "Below 10 GB Prism pauses new copies so your saves and game lists always have room.",
             ok="See the biggest games", bar=(0.982, "SD card · 1 TB", "18 GB free"), quiet_days=0)
    elif a == ["test-toast"]:
        toast("test-toast", "Prism fixed something", "Your Super Nintendo list was restored from this morning's backup.")
    else:
        sys.exit(__doc__)
