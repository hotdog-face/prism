#!/usr/bin/env python3
"""Haptics (2026-10-14): a light tick from the Deck's trackpad actuators as the selection moves in Prism's menus, a
firmer tap when a game starts - timed with the Glass UI sounds. Off when docked (the Deck sits in the dock) and when
config.toml has haptics = false.

ES-DE's selection hooks write one byte to the pipe /tmp/prism_haptics (opened read-write, so it never blocks ES-DE):
"t" = tick, "T" = tap. The supervised thread in switchhome.py ("haptics") turns them into a haptic pulse: a feature
report to the Deck controller's hidraw interface (the same ID_TRIGGER_HAPTIC_PULSE message Steam and SDL use).
    prism_haptics.py tick | tap      try one
"""
import fcntl, glob, os, stat, struct, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_config  # noqa: E402  (paths)

FIFO = "/tmp/prism_haptics"
ID_TRIGGER_HAPTIC_PULSE = 0x8F
LEFT, RIGHT, BOTH = 1, 0, 2
TICK = (RIGHT, 1600, 0, 1, 0)        # side, pulse length (µs), gap (µs), count, gain (dB)
TAP = (BOTH, 4500, 0, 1, 4)
_dev = {"fd": None, "path": None}


def controller_hidraw():
    """The Deck controller's vendor interface (Valve 28de:1205, input2)."""
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            ue = open(f"{node}/device/uevent").read()
        except OSError:
            continue
        if "28DE" in ue.upper() and "1205" in ue and "input2" in ue:
            return f"/dev/{os.path.basename(node)}"
    return None


def _ioc_feature(length):
    return 0xC0004806 | (length << 16)          # HIDIOCSFEATURE(len)


def pulse(side, dur_us, gap_us, count, gain_db):
    if _dev["fd"] is None:
        _dev["path"] = controller_hidraw()
        if not _dev["path"]:
            return False
        _dev["fd"] = os.open(_dev["path"], os.O_RDWR)
    payload = struct.pack("<BHHHhB", side, dur_us, gap_us, count, gain_db, 0)
    buf = bytearray(65)
    buf[1], buf[2] = ID_TRIGGER_HAPTIC_PULSE, len(payload)
    buf[3:3 + len(payload)] = payload
    try:
        fcntl.ioctl(_dev["fd"], _ioc_feature(len(buf)), bytes(buf))
        return True
    except OSError:
        os.close(_dev["fd"]); _dev["fd"] = None           # the controller came back as a new device: reopen next time
        return False


def enabled():
    if prism_config.get("haptics", True) is False or os.path.exists("/tmp/esde_screensaver"):
        return False
    try:
        import prism_profile
        return not prism_profile.docked()
    except Exception:
        return True


def run():
    """switchhome thread: read ticks from the pipe, pulse (at most one tick per 25 ms)."""
    if os.path.exists(FIFO) and not stat.S_ISFIFO(os.stat(FIFO).st_mode):
        os.remove(FIFO)
    if not os.path.exists(FIFO):
        os.mkfifo(FIFO, 0o666)
    fd = os.open(FIFO, os.O_RDWR)
    last = 0.0
    while True:
        data = os.read(fd, 64)
        now = time.monotonic()
        if not data or not enabled():
            continue
        if b"T" in data:
            pulse(*TAP); last = now
        elif now - last > 0.025:
            pulse(*TICK); last = now


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["tick"]:
        print(pulse(*TICK))
    elif a == ["tap"]:
        print(pulse(*TAP))
    else:
        sys.exit(__doc__)
