#!/usr/bin/env python3
"""No MENU / OPTIONS in Prism (2026-10-07: "on the lower bar dont show the menu button and dont have the
menu button on the device show anything when pressed. same with options.")

  * Controller: ~/ES-DE/settings/es_input.xml maps every button of Steam's virtual pads (the Deck itself, external
    pads through Steam Input) except Start (menu) and Back/Select (options). The keyboard keeps its default mapping, so
    the automation (xdotool Escape/F1 into ES-DE) still works.
  * Bottom bar: the "menu" / "options" labels become a zero-width space in an en_US catalog override
    (~/ES-DE/resources/locale/...), and the theme gives Start/Back blank icons.
Needs ES-DE closed. `apply_ui.py --check` (ES-DE running): presses Start on the virtual pad and compares screenshots;
reverts everything if a menu still opens.  `apply_ui.py --revert` undoes it.
"""
import prism_config  # noqa: E402  (paths)
import os, re, shutil, struct, subprocess, sys, time

HOME = os.path.expanduser("~")
INPUT = f"{prism_config.ESDE}/settings/es_input.xml"
SETTINGS = f"{prism_config.ESDE}/settings/es_settings.xml"
MO_SRC = f"{prism_config.PRISM}/esde_locale/en_US.mo"
MO_DST = f"{prism_config.ESDE}/resources/locale/en_US/LC_MESSAGES/en_US.mo"
THEME = f"{prism_config.THEME_DIR}/theme.xml"
BLANK = f"{prism_config.THEME_DIR}/assets/blank.svg"
LOG = f"{prism_config.PRISM}/apply_ui.log"
# Steam Input's virtual pads as ES-DE names them (GUID includes SDL's name CRC)
PADS = [("Steam Deck Controller", "030079f6de280000ff11000001000000"),
        ("Xbox 360 Controller", "0300b836de280000ff11000001000000"),
        ("Steam Virtual Gamepad", "0300f837de280000ff11000001000000")]
# SDL GameController ids: buttons A0 B1 X2 Y3 BACK4 GUIDE5 START6 LS7 RS8 LB9 RB10 UP11 DOWN12 LEFT13 RIGHT14;
# axes LX0 LY1 RX2 RY3 LT4 RT5.  start/back deliberately left out.
INPUTS = [("a", "button", 0, 1), ("b", "button", 1, 1), ("x", "button", 2, 1), ("y", "button", 3, 1),
          ("up", "button", 11, 1), ("down", "button", 12, 1), ("left", "button", 13, 1), ("right", "button", 14, 1),
          ("leftshoulder", "button", 9, 1), ("rightshoulder", "button", 10, 1),
          ("lefttrigger", "axis", 4, 1), ("righttrigger", "axis", 5, 1),
          ("leftthumbstickclick", "button", 7, 1), ("rightthumbstickclick", "button", 8, 1),
          ("leftthumbstickup", "axis", 1, -1), ("leftthumbstickdown", "axis", 1, 1),
          ("leftthumbstickleft", "axis", 0, -1), ("leftthumbstickright", "axis", 0, 1),
          ("rightthumbstickup", "axis", 3, -1), ("rightthumbstickdown", "axis", 3, 1),
          ("rightthumbstickleft", "axis", 2, -1), ("rightthumbstickright", "axis", 2, 1)]
ICONS = ["button_back_XBOX", "button_start_XBOX", "button_back_XBOX360", "button_start_XBOX360",
         "button_back_switch", "button_start_switch", "button_back_PS4", "button_start_PS4",
         "button_back_PS5", "button_start_PS5", "button_back_PS123", "button_start_PS123",
         "button_back_SNES", "button_start_SNES"]


def log(m):
    line = time.strftime("%F %T ") + m
    print(line, flush=True); open(LOG, "a").write(line + "\n")


def patched_mo(data, blank={"menu", "options"}):
    """Rewrite the translations of `blank` msgids in place as U+200B (fits: the originals are longer)."""
    data = bytearray(data)
    magic, _, n, otab, ttab = struct.unpack_from("<IIIII", data, 0)
    assert magic == 0x950412de
    done = set()
    for i in range(n):
        ln, off = struct.unpack_from("<II", data, otab + 8 * i)
        msgid = bytes(data[off:off + ln]).decode("utf-8", "replace")
        if msgid in blank:
            tl, toff = struct.unpack_from("<II", data, ttab + 8 * i)
            new = "​".encode()
            assert tl >= len(new)
            data[toff:toff + len(new) + 1] = new + b"\x00"
            struct.pack_into("<I", data, ttab + 8 * i, len(new))
            done.add(msgid)
    assert done == blank, done
    return bytes(data)


def apply():
    if subprocess.run(["pgrep", "-x", "es-de"], capture_output=True).returncode == 0:
        sys.exit("ES-DE is running")
    # 1. controller mapping
    if os.path.exists(INPUT) and not os.path.exists(INPUT + ".pre-ui"):
        shutil.copy2(INPUT, INPUT + ".pre-ui")
    x = ['<?xml version="1.0"?>', "<inputList>"]
    for name, guid in PADS:
        x.append(f'\t<inputConfig type="controller" deviceName="{name}" deviceGUID="{guid}">')
        x += [f'\t\t<input name="{n}" type="{t}" id="{i}" value="{v}" />' for n, t, i, v in INPUTS]
        x.append("\t</inputConfig>")
    x.append("</inputList>")
    open(INPUT, "w").write("\n".join(x) + "\n")
    # 2. labels
    os.makedirs(os.path.dirname(MO_DST), exist_ok=True)
    open(MO_DST, "wb").write(patched_mo(open(MO_SRC, "rb").read()))
    t = open(SETTINGS).read()
    if not os.path.exists(SETTINGS + ".pre-ui"):
        open(SETTINGS + ".pre-ui", "w").write(t)
    t = re.sub(r'<string name="ApplicationLanguage" value="[^"]*" />',
               '<string name="ApplicationLanguage" value="en_US" />', t)
    open(SETTINGS, "w").write(t)
    # 3. icons
    open(BLANK, "w").write('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>\n')
    th = open(THEME).read()
    if "customButtonIcon" not in th:
        if not os.path.exists(THEME + ".pre-ui"):
            open(THEME + ".pre-ui", "w").write(th)
        icons = "".join(f'\n                <customButtonIcon button="{b}">./assets/blank.svg</customButtonIcon>'
                        for b in ICONS)
        th = th.replace("<entrySpacing>0.02</entrySpacing>\n            </helpsystem>",
                        "<entrySpacing>0.02</entrySpacing>" + icons + "\n            </helpsystem>", 1)
        assert "customButtonIcon" in th
        open(THEME, "w").write(th)
    log("applied: es_input.xml (no start/back on Steam pads), menu/options labels blanked, blank icons")


def revert():
    for p in (INPUT, SETTINGS, THEME):
        if os.path.exists(p + ".pre-ui"):
            shutil.move(p + ".pre-ui", p)
        elif p == INPUT and os.path.exists(INPUT):
            os.remove(INPUT)       # there was none before: ES-DE goes back to its default mappings
    if os.path.exists(MO_DST):
        os.remove(MO_DST)
    log("reverted")


def shot(path):
    env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}", "DISPLAY": ":0"}
    if os.path.exists(path):
        os.remove(path)
    subprocess.run(["gamescopectl", "screenshot", path], env=env, capture_output=True, timeout=10)
    for _ in range(20):
        if os.path.exists(path) and os.path.getsize(path) > 0:
            break
        time.sleep(0.25)
    time.sleep(0.3)
    return subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-vf", "scale=64:40,format=gray", "-f",
                           "rawvideo", "-"], capture_output=True).stdout


def check():
    """ES-DE running on the home screen: Start on the virtual pad must change nothing."""
    if subprocess.run(["pgrep", "-x", "es-de"], capture_output=True).returncode != 0:
        log("check FAILED: ES-DE is not running"); return False
    sys.path.insert(0, prism_config.PRISM)
    import deck_mcp
    a = shot("/tmp/ui_a.png")
    deck_mcp.tool_gamepad(["START"])
    time.sleep(1.5)
    b = shot("/tmp/ui_b.png")
    diff = sum(abs(p - q) for p, q in zip(a, b)) / max(1, len(a))
    if diff > 6:
        log(f"check FAILED: the screen changed after Start (diff {diff:.1f}), reverting")
        deck_mcp.tool_gamepad(["B"])
        return False
    log(f"check ok: Start does nothing (diff {diff:.1f})")
    return True


if __name__ == "__main__":
    if "--revert" in sys.argv:
        revert()
    elif "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    else:
        apply()
