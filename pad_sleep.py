#!/usr/bin/env python3
"""Put Switch (1) Pro Controllers properly to sleep (2026-10-09): a plain Bluetooth disconnect left them awake with
the Home ring lit. Sends the controller's own "set HCI state: disconnect/sleep" subcommand (0x06 0x00) over hidraw,
then drops the link.

  pad_sleep.py <mac> [<mac> ...]     sleep these controllers
  pad_sleep.py all                   every connected Switch Pro Controller
"""
import glob, os, re, subprocess, sys, time

RUMBLE_NEUTRAL = bytes([0x00, 0x01, 0x40, 0x40, 0x00, 0x01, 0x40, 0x40])


def hidraw_for(mac):
    mac = mac.lower()
    for d in glob.glob("/sys/class/hidraw/hidraw*"):
        try:
            uev = open(f"{d}/device/uevent").read()
        except OSError:
            continue
        if f"hid_uniq={mac}" in uev.lower() and "057E" in uev.upper():
            return "/dev/" + os.path.basename(d)
    return None


def connected_pros():
    out = subprocess.run(["bluetoothctl", "devices", "Connected"], capture_output=True, text=True).stdout
    return [m.group(1) for m in re.finditer(r"Device ([0-9A-F:]{17}) Pro Controller", out)]


def sleep_pad(mac):
    dev = hidraw_for(mac)
    sent = False
    if dev:
        try:
            fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
            for counter in range(3):                    # a couple of tries: it can drop one while busy
                os.write(fd, bytes([0x01, counter & 0x0F]) + RUMBLE_NEUTRAL + bytes([0x06, 0x00]))
                time.sleep(0.05)
            os.close(fd); sent = True
        except OSError as e:
            print(f"{mac}: hidraw write failed: {e}")
    time.sleep(0.6)
    subprocess.run(["bluetoothctl", "disconnect", mac], capture_output=True, timeout=10)
    print(f"{mac}: {'sleep command sent' if sent else 'no hidraw (disconnect only)'}")
    return sent


if __name__ == "__main__":
    macs = connected_pros() if sys.argv[1:] == ["all"] else sys.argv[1:]
    for m in macs:
        sleep_pad(m)
