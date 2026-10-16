#!/usr/bin/env python3
"""Wii pointer before every Wii launch (2026-10-09). Run by ES-DE's game-start hook (console.sh).
Handheld: the pointer follows the Deck's gyro (SteamDeckGyroDSU -> Dolphin IMU pointer). Docked: the Deck sits still in
the dock, so the gyro pointer is switched off and the pointer follows the cursor (right trackpad = mouse in Steam's
Prism layout)."""
import glob, os, re, sys

DOL = os.path.expanduser("~/.var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu")
FILES = [f"{DOL}/WiimoteNew.ini", f"{DOL}/Profiles/Wiimote/galaxy.ini", f"{DOL}/Profiles/Wiimote/pointer_gyro.ini"]


def docked():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import party_input   # same dock test (incl. the /tmp/party_force_docked test hook)
    return party_input.docked()


def main():
    want = "False" if docked() else "True"
    for f in FILES:
        if not os.path.exists(f):
            continue
        s = open(f).read()
        new = re.sub(r"(?m)^IMUIR/Enabled = \w+$", f"IMUIR/Enabled = {want}", s)
        # Dolphin drops default values (IMUIR/Enabled = True) when it saves on exit: add the line where it is missing
        import party_input
        for sec in (["Profile"] if "/Profiles/" in f else [f"Wiimote{k}" for k in range(1, 5)]):
            if party_input.ini_get(new, sec, "IMUIR/Enabled") is None and f"[{sec}]" in new:
                new = party_input.ini_set(new, sec, "IMUIR/Enabled", want)
        if new != s:
            open(f, "w").write(new)
    print("dolphin: wii pointer =", "cursor (docked)" if want == "False" else "gyro (handheld)")


if __name__ == "__main__":
    main()
