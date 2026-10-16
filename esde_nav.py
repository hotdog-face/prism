#!/usr/bin/env python3
"""Agent helper: move Prism (ES-DE) to a system tile by watching /tmp/esde_system, then optionally open it.
  esde_nav.py <system> [open]"""
import prism_config  # noqa: E402  (paths)
import sys, time
sys.path.insert(0, prism_config.PRISM)
import deck_curate as d
target = sys.argv[1]
cur = lambda: open("/tmp/esde_system").read().strip() if __import__("os").path.exists("/tmp/esde_system") else ""
for _ in range(4):
    if d.selected() is None and cur():
        break
    d.esde_keys(["BackSpace"]); time.sleep(0.8)
for _ in range(60):
    if cur() == target:
        break
    before = cur(); d.esde_keys(["Right"]); t = time.time()
    while cur() == before and time.time() - t < 2:
        time.sleep(0.05)
print("at", cur())
if cur() == target and "open" in sys.argv:
    d.esde_keys(["Return"]); time.sleep(1.5); print("selected", d.selected())
