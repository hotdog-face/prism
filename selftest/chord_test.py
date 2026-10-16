import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Prism root
import prism_config  # noqa: E402  (paths)
import sys
import deck_curate as d
calls = []
d.search = lambda: calls.append("search")
d.rescue = lambda: calls.append("rescue")
d.music_button = lambda hold: calls.append("next" if hold else "playpause")
d.log = lambda m: None

def run(seq):
    """seq: list of (seconds, {buttons held}) segments, fed at 100 Hz"""
    calls.clear(); down, fired, shoulder, b = {}, set(), {}, False
    t = 1000.0
    for dur, held in seq:
        for _ in range(int(dur * 100)):
            st = {k: (k in held) for k in ("L4", "R4", "L5", "R5", "STEAM", "Y", "B", "VIEW")}
            b = d.handle_buttons(None, st, t, down, fired, shoulder, b); t += 0.01
    return list(calls)

cases = [
    ("L5 tap", [(0.2, {"L5"}), (0.3, set())], ["search"]),
    ("R5 tap", [(0.2, {"R5"}), (0.3, set())], ["playpause"]),
    ("R5 hold", [(0.8, {"R5"}), (0.3, set())], ["next"]),
    ("L5+R5 2.3s", [(2.3, {"L5", "R5"}), (0.3, set())], ["rescue"]),
    ("R5 then L5 held", [(0.3, {"R5"}), (2.4, {"L5", "R5"}), (0.3, set())], ["rescue"]),
    ("L5+R5 1s (let go)", [(1.0, {"L5", "R5"}), (0.3, set())], []),
    ("L5+R5 held 6s", [(6.0, {"L5", "R5"}), (0.3, set())], ["rescue"]),
    ("L5 tap after rescue", [(2.3, {"L5", "R5"}), (0.3, set()), (0.2, {"L5"}), (0.3, set())], ["rescue", "search"]),
]
bad = 0
for name, seq, want in cases:
    got = run(seq)
    ok = got == want; bad += not ok
    print(("PASS " if ok else "FAIL ") + f"{name}: {got}" + ("" if ok else f" (wanted {want})"))
print("all chord cases pass" if not bad else f"{bad} failing")
