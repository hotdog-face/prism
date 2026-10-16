#!/usr/bin/env python3
"""Ryujinx controls before every Switch launch (2026-10-09). Run by ES-DE's game-start hook (console.sh).
 - Buttons match the Deck's labels: Deck A = Switch A, B = B, X = X, Y = Y (EmuDeck had them by position, so
   "press A" meant the Deck's B), so prompts match and confirm/back work like Prism.
 - Docked (dock display connected): docked mode (1080p), one Pro Controller per external pad in Steam's player order
   (party_input.layout(); up to 8, the Deck's own controls after them). Handheld: handheld mode on the Deck's controls.
Ryujinx reads Config.json at start and writes it back on exit, so this runs before every launch."""
import glob, json, os, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import party_input  # noqa: E402

CFG = os.path.expanduser("~/.config/Ryujinx/Config.json")
PAD = "{}-00000003-28de-0000-ff11-000001000000"     # Steam virtual gamepad, SDL index {}
LABELS = {"button_a": "A", "button_b": "B", "button_x": "X", "button_y": "Y"}


def docked():
    return party_input.docked()


def main():
    c = json.load(open(CFG))
    if not os.path.exists(CFG + ".bak-20261009"):
        shutil.copy2(CFG, CFG + ".bak-20261009")
    base = dict(c["input_config"][0])
    base["right_joycon"] = {**base["right_joycon"], **LABELS}
    dock = docked()
    lay = party_input.layout(dock)
    if dock:   # SDL device index of each player = Ryujinx's "<n>-<guid>" (all Steam virtual pads share one GUID)
        n_pl = min(8, max(4, lay["externals"]))   # 4+: pads that wake after launch still get a player (2026-10-09)
        players = []
        for n in range(n_pl):
            p = json.loads(json.dumps(base))
            p.update(player_index=f"Player{n + 1}", controller_type="ProController", id=PAD.format(party_input.dense_for(lay, n)))
            if n >= len(lay["players"]) or not lay["players"][n]["deck"]:
                p["motion"] = {**p["motion"], "enable_motion": False}   # the Deck gyro (DSU slot 0) is the Deck's only
            players.append(p)
    else:   # handheld: the Deck is "Handheld" (player 1); any other pads are Player2.. as Pro Controllers (2026-10-09)
        p = json.loads(json.dumps(base))
        p.update(player_index="Handheld", controller_type="Handheld", id=PAD.format(party_input.dense_for(lay, 0)))
        players = [p]
        for n in range(1, min(8, max(4, len(lay["players"])))):   # Player2-4 pre-bound: a pad that wakes later joins
            if n < len(lay["players"]) and not lay["players"][n]["type"]:
                continue                                   # a stale virtual pad Steam left behind
            q = json.loads(json.dumps(base))
            q.update(player_index=f"Player{n + 1}", controller_type="ProController", id=PAD.format(party_input.dense_for(lay, n)))
            q["motion"] = {**q["motion"], "enable_motion": False}         # the Deck gyro stays with the Deck
            players.append(q)
    # ignore_applet: no blocking "Controller Applet" dialog when a game asks for players or styles (2026-10-09 party prep)
    # docked_mode stays False even on the TV: 720p internal (gamescope upscales) roughly halves the GPU load, so
    # 4-player MK8D keeps up (parent session, 2026-10-09). Pro Controllers in tabletop mode are valid on a Switch.
    new = {**c, "docked_mode": False, "input_config": players, "ignore_applet": True}
    if new != c:
        tmp = CFG + ".tmp"
        json.dump(new, open(tmp, "w"), indent=2)
        os.replace(tmp, CFG)
    print("ryujinx:", f"TV (720p tabletop), Pro Controller x{len(players)} on pads {[p['id'].split('-')[0] for p in players]}"
          if dock else f"handheld on pad {players[0]['id'].split('-')[0]}"
          + "".join(f", {q['player_index']} Pro Controller on pad {q['id'].split('-')[0]}" for q in players[1:]),
          "| buttons by label")


if __name__ == "__main__":
    main()
