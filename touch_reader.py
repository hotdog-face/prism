#!/usr/bin/env python3
"""Prints the Deck touchscreen first finger as lines "D x y" / "M x y" / "U x y" in landscape screen pixels.
The touch panel node is root:input; Prism's udev rule (install/root_setup.sh) lets the signed-in user read it, and
without that rule callers run this with sudo -n: command() gives the right way.
    import touch_reader; subprocess.Popen(touch_reader.command(), stdout=PIPE)"""
import glob, os, sys
import evdev
from evdev import ecodes as E


def find():
    """The built-in touchscreen: a direct-touch multitouch device (LCD Deck: FTS3528; the OLED panel too)."""
    best = None
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        abs_ = dict(d.capabilities().get(E.EV_ABS, []))
        if E.ABS_MT_POSITION_X in abs_ and E.INPUT_PROP_DIRECT in d.input_props():
            if d.name.startswith("FTS3528") or best is None:
                best = d
    return best


def node():
    """/dev/input/eventN of the touchscreen from sysfs (readable without root)."""
    for n in glob.glob("/sys/class/input/event*/device/name"):
        try:
            name = open(n).read().strip()
        except OSError:
            continue
        if name.startswith("FTS3528") or "touchscreen" in name.lower():
            return "/dev/input/" + n.split("/")[4]
    return None


def command():
    me = [sys.executable or "/usr/bin/python3", "-u", os.path.abspath(__file__)]
    n = node()
    return me if n and os.access(n, os.R_OK) else ["sudo", "-n"] + me
PW, PH, SW, SH = 800, 1280, 1280, 800
slot, down, started, moved, px, py = 0, False, False, False, 0, 0


def screen():  # the panel top edge is the screen right edge
    return round(py * (SW - 1) / PH), round((SH - 1) - px * (SH - 1) / PW)


def main():
    dev = find()
    a = {k: v for k, v in dev.capabilities(absinfo=True).get(E.EV_ABS, [])}
    global PW, PH
    PW, PH = a[E.ABS_MT_POSITION_X].max, a[E.ABS_MT_POSITION_Y].max   # 800 x 1280 on the LCD panel
    global slot, down, started, moved, px, py
    for ev in dev.read_loop():
        if ev.type == E.EV_ABS:
            if ev.code == E.ABS_MT_SLOT:
                slot = ev.value
            elif slot != 0:
                continue
            elif ev.code == E.ABS_MT_TRACKING_ID:
                if ev.value == -1:
                    if down:
                        down = False
                        x, y = screen()
                        print(f"U {x} {y}", flush=True)
                else:
                    down, started, moved = True, True, False
            elif ev.code == E.ABS_MT_POSITION_X:
                px, moved = ev.value, True
            elif ev.code == E.ABS_MT_POSITION_Y:
                py, moved = ev.value, True
        elif ev.type == E.EV_SYN and down and (moved or started):
            x, y = screen()
            print(("D" if started else "M") + f" {x} {y}", flush=True)
            started = moved = False


if __name__ == "__main__":
    main()
