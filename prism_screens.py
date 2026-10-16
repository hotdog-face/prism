#!/usr/bin/env python3
"""Prism's moments (2026-10-14, mockups approved by the owner): what happens on screen around docking, and more.

    on_dock_change(docked)   deck_screensaver calls it when the dock's DisplayPort comes or goes:
                             docked in the menus -> the Prism wordmark on the TV while it switches (deck_cc "dock");
                             undocked during a game -> the game pauses behind a "Game paused" card
    maybe_continue(why)      the Continue screen at the home screen (idea 8): the last game, the frame where it stopped,
                             A goes straight back in
    maybe_join(reason)       "Who's playing?" (idea 9): player slots filled as controllers press A, Y changes the order
    open_addgames(why)       the Add games card (idea 7): QR code + address of the upload page (prism_addgames.py)
    prism_screens.py dock | undock | continue | join | addgames      try them by hand
"""
import os, signal, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_config  # noqa: E402  (paths)

LOG = "/tmp/prism_screens.log"
NOT_GAMES = {"needsalook"}          # rows whose "launch" is a Prism card


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def tv_label():
    import prism_profile
    res = prism_profile.PROFILES["docked"]["resolution"]
    return "TV · " + res.replace("x", " × ") if "x" in res else "TV"


def emulator_game():
    """ES-DE's pid if an emulator game it launched is running (not a Prism card, not a movie), else None."""
    import deck_curate as d
    pid = d.esde_pid()
    if not pid or not d.game_running(pid):
        return None
    sel = d.selected() or ("", "", "")
    if sel[2] in NOT_GAMES or sel[2] in ("movies", "tv", "music", "media"):
        return None
    return pid


def on_dock_change(docked):
    try:
        if docked:
            if emulator_game():
                log("docked during a game: no logo")
                return
            import deck_curate as d
            import prism_state as ps
            if d.cc_daemon():
                ps.cc_request({"cmd": "dock", "label": tv_label()})
                log("docked: logo")
        else:
            pid = emulator_game()
            if pid:
                threading.Thread(target=undock_pause, args=(pid,), daemon=True).start()
    except Exception as e:
        log(f"dock change: {e}")


def undock_pause(pid):
    """Undocked mid-game: freeze it, show the card over its last frame, Keep playing / Home."""
    import deck_curate as d
    import prism_state as ps
    if ps.card_up() or os.path.exists("/tmp/game_hub_ready") and _frozen(pid):
        return                                   # a card or the hub page is already holding the game
    d.signal_game(pid, signal.SIGSTOP)
    log("undocked during a game: paused")
    time.sleep(1.6)                              # the panel takes over and Steam switches the game back to 1280x800
    name = (d.selected() or ("", "", ""))[1] or "Your game"
    env = {**os.environ, **d.esde_env(pid), "DECK_CONFIRM_CANCEL": "Home", "DECK_CONFIRM_TIMEOUT": "0"}
    r = subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/deck_confirm.py", "Game paused", name,
                        "You took the Deck off the dock. Your game is waiting right where you left it.",
                        "Keep playing", "-"], env=env, capture_output=True, text=True)
    if "CONFIRMED" in r.stdout:
        d.signal_game(pid, signal.SIGCONT)
        log("undock card: keep playing")
    else:
        d.signal_game(pid, signal.SIGCONT)
        log("undock card: home")
        d.quit_game(cover=True)


def _frozen(pid):
    import deck_curate as d
    for k in d.esde_game_procs(pid):
        try:
            if k != pid and open(f"/proc/{k}/stat").read().rsplit(")", 1)[1].split()[0] == "T":
                return True
        except (OSError, IndexError):
            pass
    return False



# ---- Continue (idea 8) ----------------------------------------------------------------------------------------------
NOT_CONTINUE = {"steam", "movies", "tv", "music", "media", "foryou", "needsalook", "desktop", "emulators"}
CONTINUE_SEEN = f"{prism_config.PRISM}/continue_seen.json"   # last shown: which game (and when it was played), hidden by B
TILE_CACHE = "/tmp/prism_tiles"


def last_game():
    """(system, rom path, gamelist entry) of the most recently played emulator game, or None."""
    import glob, html, re
    best = None
    for gl in glob.glob(f"{prism_config.ESDE}/gamelists/*/gamelist.xml"):
        system = gl.split("/")[-2]
        if system in NOT_CONTINUE:
            continue
        try:
            text = open(gl, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in re.finditer(r"<game>(.*?)</game>", text, re.S):
            lp = re.search(r"<lastplayed>(\d{8}T\d{6})</lastplayed>", m.group(1))
            p = re.search(r"<path>\./(.*?)</path>", m.group(1))
            if lp and p and (best is None or lp.group(1) > best[0]):
                best = (lp.group(1), system, html.unescape(p.group(1)))
    if not best:
        return None
    rom = f"{prism_config.ROMS}/{best[1]}/{best[2]}"
    return (best[1], rom) if os.path.exists(rom) else None


def _png(path):
    """GdkPixbuf can't read webp here: convert once into a cache."""
    if not path or not path.lower().endswith(".webp"):
        return path
    os.makedirs(TILE_CACHE, exist_ok=True)
    out = f"{TILE_CACHE}/{os.path.basename(path)[:-5]}.png"
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(path):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", path, out], capture_output=True)
    return out if os.path.exists(out) else ""


def home_tiles(n=6):
    """The first tiles of the home row, as Prism shows them: Continue Playing, Favorites, then systems in order."""
    import glob, re
    tiles = f"{prism_config.THEME_DIR}/switch/tiles"
    out = [f"{tiles}/auto-lastplayed.webp", f"{tiles}/auto-favorites.webp"]
    try:
        order = re.findall(r"<name>([^<]+)</name>\s*<systemsortname>([^<]+)</systemsortname>",
                           open(f"{prism_config.ESDE}/custom_systems/es_systems_sorting.xml").read())
    except OSError:
        order = []
    for name, _ in sorted(order, key=lambda x: x[1]):
        if name in ("steam", "foryou") or len(out) >= n:
            continue
        d = f"{prism_config.ROMS}/{name}"
        if os.path.isdir(d) and any(not f.startswith(".") for f in os.listdir(d)) and os.path.exists(f"{tiles}/{name}.webp"):
            out.append(f"{tiles}/{name}.webp")
    if os.path.exists(f"{tiles}/steam.webp") and os.path.isdir(f"{prism_config.ROMS}/steam"):
        out.insert(2, f"{tiles}/steam.webp")
    return [_png(t) for t in out[:n]]


def stopped_ago(stamp):
    from datetime import datetime
    try:
        secs = (datetime.now() - datetime.strptime(stamp[:15], "%Y%m%dT%H%M%S")).total_seconds()
    except ValueError:
        return ""
    if secs < 90:
        return "stopped just now"
    if secs < 3600:
        return f"stopped {int(secs // 60)} minutes ago"
    if secs < 86400:
        h = int(secs // 3600)
        return f"stopped {h} hour{'s' if h != 1 else ''} ago"
    d = int(secs // 86400)
    return "stopped yesterday" if d == 1 else f"stopped {d} days ago"


def continue_data():
    g = last_game()
    if not g:
        return None
    system, rom = g
    import game_hub, prism_shelf
    e = game_hub.entry(system, rom)
    h = game_hub.hub_data(rom, os.path.splitext(os.path.basename(rom))[0], system, prism_shelf.fullname(system))
    meta = [prism_shelf.fullname(system), stopped_ago(e.get("lastplayed", ""))]
    try:
        secs = int(e.get("playtime") or 0)
        if secs >= 60:
            meta.append(f"{secs // 3600} h {secs % 3600 // 60} m played" if secs >= 3600 else f"{secs // 60} m played")
    except ValueError:
        pass
    art = game_hub.media(system, rom, ("screenshots", "titlescreens", "fanart", "covers"))
    return {"system": system, "rom": rom, "name": h["name"], "meta": " · ".join(m for m in meta if m),
            "shot": h["resume"] or art, "shot_label": "Where you stopped" if h["resume"] else "",
            "key": f"{rom}|{e.get('lastplayed', '')}"}


def continue_screen(data):
    """The Continue screen (approved mockup): A starts the game where it stopped, B or Down hides it. Returns
    "CONTINUE" or "HIDE"."""
    import math, select, threading
    import cairo, gi
    gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
    from evdev import ecodes as E
    from deck_power_menu import FONT, neon, pads, rounded

    def load(p, w=None, h=None):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(p)
            return pb.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR) if w else pb
        except Exception:
            return None

    bg = load(f"{prism_config.THEME_DIR}/switch/ambient_still.png")
    shot = load(_png(data["shot"])) if data.get("shot") else None
    tiles = [load(t) for t in home_tiles()]
    icons = load(_png(f"{prism_config.THEME_DIR}/switch/iconrow_169.png"))
    st = {"answer": None, "t0": time.monotonic(), "starting": False}

    def bat():
        try:
            return open("/sys/class/power_supply/BAT1/capacity").read().strip() + "%"
        except OSError:
            return ""

    def paint_cover(cr, pb, x, y, w, h, r):
        """pb scaled to cover (x, y, w, h), clipped to a rounded rect."""
        cr.save(); rounded(cr, x, y, w, h, r); cr.clip()
        k = max(w / pb.get_width(), h / pb.get_height())
        cr.translate(x + (w - pb.get_width() * k) / 2, y + (h - pb.get_height() * k) / 2); cr.scale(k, k)
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0); cr.paint(); cr.restore()

    def txt(cr, s, x, y, size, rgba, bold=False, spacing=0.0):
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(size); cr.set_source_rgba(*rgba)
        if spacing:
            for ch in s:
                cr.move_to(x, y); cr.show_text(ch); x += cr.text_extents(ch).x_advance + spacing
            return x
        cr.move_to(x, y); cr.show_text(s)
        return x + cr.text_extents(s).x_advance

    def badge(cr, x, cy, letter, u):
        cr.new_path(); cr.arc(x + 1.2 * u, cy, 1.2 * u, 0, 2 * math.pi); cr.set_source_rgb(0.85, 0.85, 0.85); cr.fill()
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); cr.set_font_size(1.25 * u)
        e = cr.text_extents(letter); cr.set_source_rgb(0.12, 0.12, 0.13)
        cr.move_to(x + 1.2 * u - e.x_advance / 2 - e.x_bearing / 2, cy + e.height / 2); cr.show_text(letter)
        return x + 2.4 * u

    def draw(w, cr):
        W, H = w.get_allocated_width(), w.get_allocated_height()
        u = H / 62.5                     # the mockup's unit (1 % of a 16:10 width), from the height: works on 16:9 too
        now = time.monotonic()
        a = min(1.0, (now - st["t0"]) / 0.2)
        cr.set_source_rgb(0.05, 0.05, 0.055); cr.paint()
        if bg:
            cr.save(); cr.scale(W / bg.get_width(), H / bg.get_height()); Gdk.cairo_set_source_pixbuf(cr, bg, 0, 0)
            cr.paint(); cr.restore()
        txt(cr, time.strftime("%-I:%M %p"), 3 * u, 3.8 * u, 1.6 * u, (0.87, 0.87, 0.87, 1))
        b = bat(); cr.set_font_size(1.6 * u); e = cr.text_extents(b)
        txt(cr, b, W - 3 * u - e.x_advance, 3.8 * u, 1.6 * u, (0.87, 0.87, 0.87, 1))
        # the card
        cx, cy, cw, ch = 6 * u, 10 * u - (1 - a) * 1.5 * u, 58 * u, 22 * u
        cr.save(); rounded(cr, cx, cy, cw, ch, 1.6 * u); cr.set_source_rgba(0.12, 0.12, 0.13, 0.97); cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.06); cr.set_line_width(1); cr.stroke(); cr.restore()
        if shot:
            cr.save(); rounded(cr, cx, cy, cw, ch, 1.6 * u); cr.clip()
            paint_cover(cr, shot, cx, cy, 26 * u, ch, 0); cr.restore()
            if data.get("shot_label"):
                cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(1.2 * u)
                e = cr.text_extents(data["shot_label"])
                rounded(cr, cx + 1 * u, cy + ch - 3.2 * u, e.x_advance + 1.6 * u, 2.2 * u, 0.6 * u)
                cr.set_source_rgba(0, 0, 0, 0.6); cr.fill()
                txt(cr, data["shot_label"], cx + 1.8 * u, cy + ch - 1.65 * u, 1.2 * u, (1, 1, 1, 0.95))
        ix = cx + 26 * u + 2.6 * u
        txt(cr, "CONTINUE", ix, cy + 5.2 * u, 1.25 * u, (0.64, 0.64, 0.67, 1), spacing=0.15 * u)
        title = data["name"]
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); cr.set_font_size(2.9 * u)
        while cr.text_extents(title).x_advance > cw - 30 * u and len(title) > 4:
            title = title[:-2].rstrip() + "…" if not title.endswith("…") else title[:-2].rstrip() + "…"
        txt(cr, title, ix, cy + 9.2 * u, 2.9 * u, (1, 1, 1, 1), bold=True)
        room = cw - 26 * u - 5.2 * u
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(1.4 * u)
        parts = data["meta"].split(" · ")
        short = [parts[0].replace("Nintendo ", "").replace("Sega ", "").replace("Sony ", "")] + parts[1:]
        tries = [parts, short, short[:2], short[1:2]]
        meta = next((" · ".join(t) for t in tries if t and cr.text_extents(" · ".join(t)).x_advance <= room),
                    " · ".join(short[1:2]))
        txt(cr, meta, ix, cy + 12 * u, 1.4 * u, (0.74, 0.74, 0.74, 1))
        # the A Continue button, neon outline pulsing
        label = "Starting…" if st["starting"] else "Continue"
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(1.8 * u)
        bw = 1.4 * u + 2.4 * u + 1 * u + cr.text_extents(label).x_advance + 2.4 * u
        bx, by, bh = ix, cy + 14.4 * u, 4.4 * u
        pulse = 0.72 + 0.28 * (0.5 + 0.5 * math.cos((now - st["t0"]) * 2 * math.pi / 1.6))
        rounded(cr, bx, by, bw, bh, bh / 2); neon(cr, pulse, (bx, by, bx + bw, by + bh)); cr.set_line_width(0.26 * u)
        cr.stroke()
        x = badge(cr, bx + 1.4 * u, by + bh / 2, "A", u)
        txt(cr, label, x + 1 * u, by + bh / 2 + 0.62 * u, 1.8 * u, (1, 1, 1, 1))
        # the home row below it and the icon row
        tx = 6 * u
        for pb in tiles:
            if pb:
                paint_cover(cr, pb, tx, 37 * u, 15 * u, 15 * u, 1.2 * u)
            tx += 16.6 * u
        if icons:                        # Controllers / Settings / Power, in the bottom bar on the left like the home screen
            ih = 5.2 * u; iw = ih * icons.get_width() / icons.get_height()
            cr.save(); cr.translate(2.2 * u, H - 3.25 * u - ih / 2); cr.scale(iw / icons.get_width(), ih / icons.get_height())
            Gdk.cairo_set_source_pixbuf(cr, icons, 0, 0); cr.paint(); cr.restore()
        # hints
        hy = H - 3.25 * u
        cr.set_source_rgba(1, 1, 1, 0.10); cr.set_line_width(1); cr.move_to(0, H - 6.5 * u); cr.line_to(W, H - 6.5 * u)
        cr.stroke()
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(1.55 * u)
        x = W - 3 * u - (2.4 * u + 0.8 * u + cr.text_extents("Continue").x_advance)
        x2 = x - 3 * u - (2.4 * u + 0.8 * u + cr.text_extents("Hide").x_advance)
        for lx, letter, lab in ((x2, "B", "Hide"), (x, "A", "Continue")):
            nx = badge(cr, lx, hy, letter, u)
            txt(cr, lab, nx + 0.8 * u, hy + 0.55 * u, 1.55 * u, (0.87, 0.87, 0.87, 1))
        return False

    win = Gtk.Window(title="Continue"); win.set_decorated(False); win.fullscreen()
    area = Gtk.DrawingArea(); area.connect("draw", draw); win.add(area)

    def tag(*_):
        appid = os.environ.get("SteamAppId")
        if appid:
            subprocess.Popen(["xprop", "-id", str(win.get_window().get_xid()), "-f", "STEAM_GAME", "32c", "-set",
                              "STEAM_GAME", appid])
    win.connect("realize", tag)

    def answer(a):
        if st["answer"] is None:
            st["answer"] = a
            if a == "CONTINUE":
                st["starting"] = True
                threading.Thread(target=start_game, daemon=True).start()
            else:
                Gtk.main_quit()
        return False

    def start_game():
        try:
            launch_last(data["rom"])
        except Exception as e:
            log(f"continue: launch failed ({e})")
        GLib.idle_add(Gtk.main_quit)

    def pad_thread(devs):
        for d in devs:
            try: d.grab()
            except OSError: pass
        while st["answer"] is None:
            r, _, _ = select.select(devs, [], [], 0.1)
            for d in r:
                try:
                    for ev in d.read():
                        if ev.type == E.EV_KEY and ev.value == 1:
                            code = ev.code
                            if d.info.vendor == 0x057E and code in (E.BTN_SOUTH, E.BTN_EAST):   # Nintendo: by label
                                code = E.BTN_EAST if code == E.BTN_SOUTH else E.BTN_SOUTH
                            if code == E.BTN_SOUTH:
                                GLib.idle_add(answer, "CONTINUE")
                            elif code in (E.BTN_EAST, E.BTN_DPAD_DOWN):
                                GLib.idle_add(answer, "HIDE-BY-B")
                        elif ev.type == E.EV_ABS and ev.code == E.ABS_HAT0Y and ev.value > 0:
                            GLib.idle_add(answer, "HIDE-BY-B")
                except OSError:
                    pass
        time.sleep(0.25)
        for d in devs:
            try:
                for _ in d.read(): pass
            except (OSError, BlockingIOError): pass
            try: d.ungrab()
            except OSError: pass

    GLib.timeout_add(33, lambda: (area.queue_draw(), True)[1])
    GLib.timeout_add_seconds(30, lambda: answer("HIDE"))
    win.show_all()
    try:
        open("/tmp/prism_card_up", "w").write(str(os.getpid()))
    except OSError:
        pass
    threading.Thread(target=pad_thread, args=(pads(),), daemon=True).start()
    Gtk.main()
    win.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()
    try:
        if open("/tmp/prism_card_up").read().strip() == str(os.getpid()):
            os.remove("/tmp/prism_card_up")
    except OSError:
        pass
    return st["answer"] or "HIDE"


def launch_last(rom):
    """Start the game through ES-DE (so play time, Continue Playing and the game-start hooks all work): Continue
    Playing tile, its first entry, A - without the hub page, so it goes straight back to where it stopped."""
    import deck_curate as d
    open("/tmp/game_hub_skip", "w").close()
    subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/esde_nav.py", "recent", "open"], timeout=90,
                   capture_output=True)
    for _ in range(6):                               # Continue Playing is newest first; step down if needed
        sel = d.selected()
        if sel and os.path.realpath(sel[0]) == os.path.realpath(rom):
            break
        d.esde_keys(["Down"]); time.sleep(0.4)
    sel = d.selected()
    if not sel or os.path.realpath(sel[0]) != os.path.realpath(rom):
        log(f"continue: couldn't find {rom} in Continue Playing (at {sel})")
        os.remove("/tmp/game_hub_skip")
        return False
    d.esde_keys(["Return"])
    t0 = time.time()
    while time.time() - t0 < 20:                     # keep the screen up until the game's launch screen takes over
        pid = d.esde_pid()
        if pid and d.game_running(pid):
            time.sleep(1.0)
            break
        time.sleep(0.2)
    log(f"continue: started {rom}")
    return True


def maybe_continue(why, wait=0.0):
    """Show the Continue screen if someone is at Prism's home screen and there's a game to go back to.
    After a game ends: always (it's the game just played). On wake / start: unless B hid it for that game, and at
    most once an hour for the same game. B hides it until another game is played."""
    import json
    import prism_state as ps
    try:
        if wait:
            time.sleep(wait)
        for _ in range(10):                          # ES-DE takes a moment to be back in front after a game
            if ps.in_menus():
                break
            time.sleep(1)
        import prism_features
        if not ps.in_menus() or ps.card_up() or ps.busy_flag() or os.path.exists(f"{prism_config.PRISM}/party_mode") \
                or ps.selftest_running() or not prism_features.feel("continue_screen"):
            return
        data = continue_data()
        if not data:
            return
        try:
            seen = json.load(open(CONTINUE_SEEN))
        except (OSError, ValueError):
            seen = {}
        same = seen.get("key") == data["key"]
        if why not in ("game", "test") and same and (seen.get("hidden") or time.time() - seen.get("at", 0) < 3600):
            return
        import deck_curate as d
        pid = d.esde_pid()
        env = {**os.environ, **(d.esde_env(pid) if pid else {})}
        r = subprocess.run(["/usr/bin/python3", os.path.abspath(__file__), "continue-ui"], env=env,
                           capture_output=True, text=True, timeout=180)
        answer = (r.stdout.strip().splitlines() or [""])[-1]
        json.dump({"key": data["key"], "at": time.time(), "hidden": answer == "HIDE-BY-B"}, open(CONTINUE_SEEN, "w"))
        log(f"continue ({why}): {answer or r.stderr.strip()[-200:]}")
    except Exception as e:
        log(f"continue ({why}): {e}")



# ---- Who's playing? (idea 9) -----------------------------------------------------------------------------------------
PLAYER_RGB = [(0xF0, 0x43, 0x6F), (0xF6, 0x9F, 0x3C), (0xB6, 0xD3, 0x51), (0x27, 0xB6, 0xD5)]
ORDER_FILE = "/tmp/prism_player_order.json"      # the order chosen with "Change order" (pads.snapshot follows it)


def set_player_leds(snap):
    """Player lights on controllers that have them (hid-nintendo / hid-playstation player LEDs), best effort."""
    import glob
    for p in snap["pads"]:
        if not p.get("player") or not p.get("raw"):
            continue
        ev = os.path.basename(p["raw"])
        leds = sorted(glob.glob(f"/sys/class/input/{ev}/device/device/leds/*:player*") +
                      glob.glob(f"/sys/class/input/{ev}/device/leds/*:player*"))
        for i, led in enumerate(leds[:4]):
            val = "1" if i == p["player"] - 1 else "0"
            try:
                open(f"{led}/brightness", "w").write(val)
            except OSError:
                subprocess.run(["sudo", "-n", "tee", f"{led}/brightness"], input=val, capture_output=True, text=True)


def join_screen(reason="controller"):
    """'Who's playing?': four player slots filled live from pads.snapshot() as Steam registers each controller's
    first press. START (or 10 s without a new player) closes, Y re-orders (everyone presses A in the order they want),
    X opens Steam's controller settings. Returns DONE / SETTINGS."""
    import json, math, select, threading
    import cairo, gi
    gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
    import evdev
    from evdev import ecodes as E
    import pads as padmod
    from deck_power_menu import FONT, capture, rounded

    shot = capture("/tmp/prism_join_bg.png")
    try:                     # blurred like the other cards' backdrops (down to 1/8, back up)
        pb = GdkPixbuf.Pixbuf.new_from_file(shot)
        small = pb.scale_simple(max(1, pb.get_width() // 8), max(1, pb.get_height() // 8), GdkPixbuf.InterpType.BILINEAR)
        bg = small.scale_simple(pb.get_width(), pb.get_height(), GdkPixbuf.InterpType.BILINEAR)
    except Exception:
        bg = None
    st = {"answer": None, "t0": time.monotonic(), "snap": padmod.snapshot(), "last_new": time.monotonic(),
          "reorder": None, "devs": {}}
    st["count"] = len([p for p in st["snap"]["pads"] if p.get("player")])

    def ident(p):
        return p.get("mac") or f"slot{p['slot']}"

    def players():
        """[(player number, pad)] in order; during Change order only those who pressed A again."""
        snap = st["snap"]
        ps_ = sorted((p for p in snap["pads"] if p.get("player")), key=lambda p: p["player"])
        if st["reorder"] is not None:
            fixed = [p for p in ps_ if p["deck"] and not snap["docked"]]       # handheld: the Deck stays player 1
            chosen = [next(p for p in ps_ if ident(p) == i) for i in st["reorder"] if any(ident(p) == i for p in ps_)]
            return list(enumerate(fixed + chosen, 1))
        return [(p["player"], p) for p in ps_]

    def refresh():
        if st["answer"]:
            return False
        try:
            snap = padmod.snapshot()
        except Exception:
            return True
        n = len([p for p in snap["pads"] if p.get("player")])
        if n > st["count"]:
            st["last_new"] = time.monotonic()
            _sound("select")
        st["count"], st["snap"] = n, snap
        set_player_leds(snap)
        if st["reorder"] is None and time.monotonic() - st["last_new"] > 10:
            answer("DONE")
        return True

    def answer(a):
        if st["answer"] is None:
            st["answer"] = a
            Gtk.main_quit()
        return False

    def finish_reorder():
        order = list(st["reorder"])
        try:
            json.dump({"order": order, "t": time.time()}, open(ORDER_FILE, "w"))
        except OSError:
            pass
        log(f"join: player order {order}")
        st["reorder"] = None
        st["last_new"] = time.monotonic()

    def on_button(path, code):
        snap = st["snap"]
        slot = next((s for s, pth in padmod.steam_pads() if pth == path), None)
        pad = next((p for p in snap["pads"] if p["slot"] == slot), None)
        st["last_new"] = time.monotonic()
        if code == E.BTN_START:
            if st["reorder"] is not None:
                finish_reorder()
            answer("DONE")
        elif code == E.BTN_Y and st["reorder"] is None:
            st["reorder"] = []
            _sound("select")
        elif code == E.BTN_X and st["reorder"] is None:
            answer("SETTINGS")
        elif code == E.BTN_A and st["reorder"] is not None and pad and pad.get("player"):
            if not (pad["deck"] and not snap["docked"]) and ident(pad) not in st["reorder"]:
                st["reorder"].append(ident(pad))
                _sound("select")
                need = [p for p in snap["pads"] if p.get("player") and not (p["deck"] and not snap["docked"])]
                if len(st["reorder"]) >= len(need):
                    finish_reorder()
        return False

    def input_thread():
        while st["answer"] is None:
            live = dict(padmod.steam_pads())                 # slot -> path; new controllers appear here
            for path in set(live.values()) - set(st["devs"]):
                try:
                    d = evdev.InputDevice(path); d.grab(); st["devs"][path] = d
                except OSError:
                    pass
            for path in list(st["devs"]):
                if path not in live.values():
                    try: st["devs"].pop(path).close()
                    except Exception: pass
            r, _, _ = select.select(list(st["devs"].values()), [], [], 0.25) if st["devs"] else ([], [], time.sleep(0.25))
            for d in r or []:
                try:
                    for ev in d.read():
                        if ev.type == E.EV_KEY and ev.value == 1:
                            GLib.idle_add(on_button, d.path, ev.code)
                except OSError:
                    pass
        time.sleep(0.25)
        for d in st["devs"].values():
            try:
                for _ in d.read(): pass
            except (OSError, BlockingIOError): pass
            try: d.ungrab(); d.close()
            except OSError: pass

    def txt(cr, s, cx, y, size, rgba, bold=False):
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(size); e = cr.text_extents(s); cr.set_source_rgba(*rgba)
        cr.move_to(cx - e.x_advance / 2, y); cr.show_text(s)

    def badge(cr, x, cy, letter, u, wide=False):
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); cr.set_font_size(1.2 * u)
        e = cr.text_extents(letter)
        w = max(2.4 * u, e.x_advance + 1.6 * u) if wide else 2.4 * u
        rounded(cr, x, cy - 1.2 * u, w, 2.4 * u, 1.2 * u); cr.set_source_rgb(0.85, 0.85, 0.85); cr.fill()
        cr.set_source_rgb(0.12, 0.12, 0.13); cr.move_to(x + w / 2 - e.x_advance / 2 - e.x_bearing / 2, cy + e.height / 2)
        cr.show_text(letter)
        return x + w

    def draw(w, cr):
        W, H = w.get_allocated_width(), w.get_allocated_height()
        u = H / 62.5
        now = time.monotonic()
        if bg:
            cr.save(); cr.scale(W / bg.get_width(), H / bg.get_height()); Gdk.cairo_set_source_pixbuf(cr, bg, 0, 0)
            cr.paint(); cr.restore()
        else:
            cr.set_source_rgb(0.05, 0.05, 0.055); cr.paint()
        cr.set_source_rgba(0, 0, 0, 0.66); cr.paint()
        title = "Press A in the order you want to play" if st["reorder"] is not None else "Who's playing?"
        txt(cr, title, W / 2, H / 2 - 17 * u, 3 * u, (1, 1, 1, 1), bold=True)
        sw, gap = 17 * u, 2 * u
        sh = sw * 4 / 3
        x0, y0 = W / 2 - (4 * sw + 3 * gap) / 2, H / 2 - sh / 2 + 2 * u
        joined = dict(players())
        nxt = len(joined) + 1
        pulse = 0.6 + 0.4 * (0.5 + 0.5 * math.cos((now - st["t0"]) * 2 * math.pi / 1.4))
        for i in range(4):
            n = i + 1
            x = x0 + i * (sw + gap)
            rounded(cr, x, y0, sw, sh, 1.6 * u); cr.set_source_rgba(0.12, 0.12, 0.13, 0.97); cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, 0.06); cr.set_line_width(1); cr.stroke()
            cx = x + sw / 2; cyc = y0 + sh * 0.38
            if n in joined:
                rgb = [c / 255 for c in PLAYER_RGB[i]]
                rounded(cr, x + 0.15 * u, y0 + 0.15 * u, sw - 0.3 * u, sh - 0.3 * u, 1.5 * u)
                cr.set_source_rgb(*rgb); cr.set_line_width(0.3 * u); cr.stroke()
                cr.new_path(); cr.arc(cx, cyc, 3.5 * u, 0, 2 * math.pi); cr.set_source_rgb(*rgb); cr.fill()
                txt(cr, str(n), cx, cyc + 1.05 * u, 3 * u, (0.1, 0.1, 0.11, 1), bold=True)
                name = joined[n].get("label") or joined[n].get("name") or "Controller"
                txt(cr, name, cx, cyc + 7 * u, 1.45 * u, (1, 1, 1, 1))
                for k in range(4):                    # the four player lights, this player's lit
                    lx = cx - 2.6 * u + k * 1.6 * u
                    rounded(cr, lx, cyc + 9.4 * u, 1.1 * u, 0.5 * u, 0.25 * u)
                    cr.set_source_rgb(*rgb) if k == i else cr.set_source_rgb(0.23, 0.23, 0.24); cr.fill()
            else:
                a = pulse if n == nxt else 0.55
                cr.new_path(); cr.arc(cx, cyc, 3.5 * u, 0, 2 * math.pi)
                cr.set_source_rgba(0.29, 0.29, 0.31, a); cr.set_line_width(0.25 * u); cr.stroke()
                txt(cr, str(n), cx, cyc + 1.05 * u, 3 * u, (0.42, 0.42, 0.44, a), bold=True)
                txt(cr, "Press A to join", cx, cyc + 7 * u, 1.45 * u,
                    (1, 1, 1, a) if n == nxt else (0.43, 0.43, 0.46, 1))
                if n == nxt and st["reorder"] is None:
                    txt(cr, "or pair a new controller", cx, cyc + 9.2 * u, 1.2 * u, (0.64, 0.64, 0.67, 1))
        # hints
        hy = H - 3.25 * u
        cr.rectangle(0, H - 6.5 * u, W, 6.5 * u); cr.set_source_rgba(0.06, 0.06, 0.065, 0.94); cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.10); cr.set_line_width(1); cr.move_to(0, H - 6.5 * u); cr.line_to(W, H - 6.5 * u)
        cr.stroke()
        hints = [("X", "Controller settings", False), ("Y", "Change order", False), ("START", "Let's play", True)]
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(1.55 * u)
        widths = []
        for letter, lab, wide in hints:
            cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); cr.set_font_size(1.2 * u)
            bw = max(2.4 * u, cr.text_extents(letter).x_advance + 1.6 * u) if wide else 2.4 * u
            cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(1.55 * u)
            widths.append(bw + 0.8 * u + cr.text_extents(lab).x_advance)
        x = W - 3 * u - sum(widths) - 3 * u * (len(hints) - 1)
        for (letter, lab, wide), wd in zip(hints, widths):
            nx = badge(cr, x, hy, letter, u, wide)
            cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(1.55 * u)
            cr.set_source_rgba(0.87, 0.87, 0.87, 1); cr.move_to(nx + 0.8 * u, hy + 0.55 * u); cr.show_text(lab)
            x += wd + 3 * u
        return False

    win = Gtk.Window(title="Who's playing"); win.set_decorated(False); win.fullscreen()
    area = Gtk.DrawingArea(); area.connect("draw", draw); win.add(area)

    def tag(*_):
        appid = os.environ.get("SteamAppId")
        if appid:
            subprocess.Popen(["xprop", "-id", str(win.get_window().get_xid()), "-f", "STEAM_GAME", "32c", "-set",
                              "STEAM_GAME", appid])
    win.connect("realize", tag)
    GLib.timeout_add(33, lambda: (area.queue_draw(), True)[1])
    GLib.timeout_add(400, refresh)
    GLib.timeout_add_seconds(120, lambda: answer("DONE"))           # never left up forever
    win.show_all()
    try:
        open("/tmp/prism_card_up", "w").write(str(os.getpid()))
    except OSError:
        pass
    threading.Thread(target=input_thread, daemon=True).start()
    log(f"join screen ({reason})")
    Gtk.main()
    win.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()
    try:
        if open("/tmp/prism_card_up").read().strip() == str(os.getpid()):
            os.remove("/tmp/prism_card_up")
    except OSError:
        pass
    return st["answer"] or "DONE"


def _sound(name):
    p = f"{prism_config.THEME_DIR}/switch/sounds/{name}.wav"
    if os.path.exists(p):
        subprocess.Popen(["pw-play", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def maybe_join(reason):
    """Show 'Who's playing?' at Prism's home screen (never in a game)."""
    import prism_state as ps
    if not ps.in_menus() or ps.card_up() or ps.busy_flag() or ps.selftest_running():
        return
    import deck_curate as d
    pid = d.esde_pid()
    env = {**os.environ, **(d.esde_env(pid) if pid else {})}
    r = subprocess.run(["/usr/bin/python3", os.path.abspath(__file__), "join-ui", reason], env=env,
                       capture_output=True, text=True, timeout=200)
    answer = (r.stdout.strip().splitlines() or [""])[-1]
    log(f"join ({reason}): {answer or r.stderr.strip()[-200:]}")
    if answer == "SETTINGS":
        d.open_steam("/controller", "controller settings (from Who's playing)")


# ---- Add games (idea 7) ------------------------------------------------------------------------------------------------
def addgames_screen():
    """The Add games card (approved mockup): a QR code and the address of the upload page, which runs only while the
    card is up. B closes it. Returns how many games were added."""
    import math, select, threading
    import cairo, gi
    gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
    from evdev import ecodes as E
    from deck_power_menu import FONT, capture, neon, pads, rounded
    import prism_addgames as ag

    url = ag.serve()
    qr_png = "/tmp/prism_addgames_qr.png"
    subprocess.run(["qrencode", "-o", qr_png, "-s", "12", "-m", "0", url], capture_output=True)
    shot = capture("/tmp/prism_addgames_bg.png")

    def load(p):
        try:
            return GdkPixbuf.Pixbuf.new_from_file(p)
        except Exception:
            return None
    bgpb = load(shot) if shot else None
    if bgpb:
        bgpb = bgpb.scale_simple(max(1, bgpb.get_width() // 8), max(1, bgpb.get_height() // 8),
                                 GdkPixbuf.InterpType.BILINEAR).scale_simple(bgpb.get_width(), bgpb.get_height(),
                                                                             GdkPixbuf.InterpType.BILINEAR)
    qr = load(qr_png)
    st = {"answer": None, "t0": time.monotonic()}

    def para(cr, text, x, y, width, size, rgba, lh=1.5):
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(size)
        cr.set_source_rgba(*rgba)
        words, line = text.split(), ""
        for w in words:
            t = (line + " " + w).strip()
            if cr.text_extents(t).x_advance > width and line:
                cr.move_to(x, y); cr.show_text(line); y += size * lh; line = w
            else:
                line = t
        if line:
            cr.move_to(x, y); cr.show_text(line); y += size * lh
        return y

    def draw(w, cr):
        W, H = w.get_allocated_width(), w.get_allocated_height()
        u = H / 62.5
        if bgpb:
            cr.save(); cr.scale(W / bgpb.get_width(), H / bgpb.get_height()); Gdk.cairo_set_source_pixbuf(cr, bgpb, 0, 0)
            cr.paint(); cr.restore()
        else:
            cr.set_source_rgb(0.05, 0.05, 0.055); cr.paint()
        cr.set_source_rgba(0, 0, 0, 0.66); cr.paint()
        cw, pad = 60 * u, 3 * u
        qs = 22 * u
        ch = qs + 2 * pad
        cx, cy = W / 2 - cw / 2, H * 0.47 - ch / 2
        rounded(cr, cx, cy, cw, ch, 1.4 * u); cr.set_source_rgba(0.12, 0.12, 0.13, 0.97); cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.06); cr.set_line_width(1); cr.stroke()
        qx, qy = cx + pad, cy + pad
        rounded(cr, qx, qy, qs, qs, 1 * u); cr.set_source_rgb(0.953, 0.953, 0.953); cr.fill()
        if qr:
            inner = qs - 2.4 * u
            cr.save(); cr.translate(qx + 1.2 * u, qy + 1.2 * u); cr.scale(inner / qr.get_width(), inner / qr.get_height())
            Gdk.cairo_set_source_pixbuf(cr, qr, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_NEAREST); cr.paint(); cr.restore()
        tx = qx + qs + 4.5 * u; tw = cx + cw - pad - tx
        y = qy + 3.4 * u
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); cr.set_font_size(2.4 * u)
        cr.set_source_rgb(1, 1, 1); cr.move_to(tx, y); cr.show_text("Add games"); y += 3.4 * u
        y = para(cr, "Scan with your phone, or open this on any computer on your Wi-Fi:", tx, y, tw, 1.4 * u,
                 (0.74, 0.74, 0.74, 1)) + 0.6 * u
        addr = url.replace("http://", "")
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); cr.set_font_size(1.6 * u)
        e = cr.text_extents(addr)
        cr.move_to(tx, y); cr.text_path(addr); neon(cr, 1.0, (tx, y - e.height, tx + e.x_advance, y)); cr.fill()
        y += 3.4 * u
        n = len(ag.state["added"])
        y = para(cr, "Drop in games and Prism files them into the right system. Art arrives overnight.", tx, y, tw,
                 1.4 * u, (0.74, 0.74, 0.74, 1))
        if n:
            cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); cr.set_font_size(1.4 * u)
            cr.set_source_rgb(0.714, 0.827, 0.318); cr.move_to(tx, y + 0.6 * u)
            cr.show_text(f"{n} game{'s' if n != 1 else ''} added")
        # hint bar: B Done
        hy = H - 3.25 * u
        cr.rectangle(0, H - 6.5 * u, W, 6.5 * u); cr.set_source_rgba(0.06, 0.06, 0.065, 0.94); cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.10); cr.set_line_width(1); cr.move_to(0, H - 6.5 * u); cr.line_to(W, H - 6.5 * u)
        cr.stroke()
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(1.55 * u)
        lx = W - 3 * u - (2.4 * u + 0.8 * u + cr.text_extents("Done").x_advance)
        cr.new_path(); cr.arc(lx + 1.2 * u, hy, 1.2 * u, 0, 2 * math.pi); cr.set_source_rgb(0.85, 0.85, 0.85); cr.fill()
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD); cr.set_font_size(1.25 * u)
        e = cr.text_extents("B"); cr.set_source_rgb(0.12, 0.12, 0.13)
        cr.move_to(lx + 1.2 * u - e.x_advance / 2 - e.x_bearing / 2, hy + e.height / 2); cr.show_text("B")
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL); cr.set_font_size(1.55 * u)
        cr.set_source_rgba(0.87, 0.87, 0.87, 1); cr.move_to(lx + 3.2 * u, hy + 0.55 * u); cr.show_text("Done")
        return False

    win = Gtk.Window(title="Add games"); win.set_decorated(False); win.fullscreen()
    area = Gtk.DrawingArea(); area.connect("draw", draw); win.add(area)

    def tag(*_):
        appid = os.environ.get("SteamAppId")
        if appid:
            subprocess.Popen(["xprop", "-id", str(win.get_window().get_xid()), "-f", "STEAM_GAME", "32c", "-set",
                              "STEAM_GAME", appid])
    win.connect("realize", tag)

    def answer(a):
        if st["answer"] is None:
            st["answer"] = a; Gtk.main_quit()
        return False

    def pad_thread(devs):
        for d in devs:
            try: d.grab()
            except OSError: pass
        while st["answer"] is None:
            r, _, _ = select.select(devs, [], [], 0.2)
            for d in r:
                try:
                    for ev in d.read():
                        if ev.type == E.EV_KEY and ev.value == 1:
                            code = ev.code
                            if d.info.vendor == 0x057E and code in (E.BTN_SOUTH, E.BTN_EAST):
                                code = E.BTN_EAST if code == E.BTN_SOUTH else E.BTN_SOUTH
                            if code in (E.BTN_EAST, E.BTN_START):
                                GLib.idle_add(answer, "DONE")
                except OSError:
                    pass
        time.sleep(0.25)
        for d in devs:
            try:
                for _ in d.read(): pass
            except (OSError, BlockingIOError): pass
            try: d.ungrab()
            except OSError: pass

    GLib.timeout_add(500, lambda: (area.queue_draw(), True)[1])
    GLib.timeout_add_seconds(1800, lambda: answer("DONE"))          # half an hour at most
    win.show_all()
    try:
        open("/tmp/prism_card_up", "w").write(str(os.getpid()))
    except OSError:
        pass
    threading.Thread(target=pad_thread, args=(pads(),), daemon=True).start()
    Gtk.main()
    win.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()
    added = ag.stop()
    try:
        if open("/tmp/prism_card_up").read().strip() == str(os.getpid()):
            os.remove("/tmp/prism_card_up")
    except OSError:
        pass
    if added:                    # show them soon: the new-game sync restarts ES-DE once nobody is using it
        subprocess.Popen(["systemctl", "--user", "start", "prism-sync.service"])
    log(f"add games: {len(added)} added")
    return len(added)


def open_addgames(why):
    import prism_state as ps
    if ps.card_up():
        return
    import deck_curate as d
    pid = d.esde_pid()
    env = {**os.environ, **(d.esde_env(pid) if pid else {})}
    r = subprocess.run(["/usr/bin/python3", os.path.abspath(__file__), "addgames-ui"], env=env,
                       capture_output=True, text=True, timeout=1900)
    log(f"add games ({why}): {(r.stdout.strip().splitlines() or [''])[-1] or r.stderr.strip()[-200:]}")

if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["dock"]:
        on_dock_change(True)
    elif a == ["continue-ui"]:
        data = continue_data()
        print(continue_screen(data) if data else "nothing to continue")
    elif a == ["continue"]:
        maybe_continue("test")
    elif a == ["addgames-ui"]:
        print(addgames_screen())
    elif a[:1] == ["addgames"]:
        open_addgames((a[1:] or ["by hand"])[0])
    elif a[:1] == ["join-ui"]:
        print(join_screen((a[1:] or ["test"])[0]))
    elif a[:1] == ["join"]:
        maybe_join((a[1:] or ["by hand"])[0])
    elif a[:1] == ["after-game"]:              # ES-DE game-end hook: $3 = the system
        if (a[1:] or [""])[0] not in NOT_CONTINUE:
            maybe_continue("game", wait=2.0)
    elif a == ["after-wake"]:                 # only once someone touches a control (not the 01:30 night wake)
        t0 = time.time()
        while time.time() - t0 < 120:
            try:
                if os.path.getmtime("/tmp/deck_last_input") > t0:
                    maybe_continue("wake", wait=0.5)
                    break
            except OSError:
                pass
            time.sleep(1)
    elif a == ["after-boot"]:                 # a real start of the Deck, not one of Prism's own ES-DE restarts
        if float(open("/proc/uptime").read().split()[0]) < 300:
            maybe_continue("start", wait=3.0)
    elif a == ["undock"]:
        pid = emulator_game()
        undock_pause(pid) if pid else print("no game running")
    else:
        sys.exit(__doc__)
