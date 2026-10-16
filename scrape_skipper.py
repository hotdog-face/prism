#!/usr/bin/env python3
"""While ES-DE's scraper runs, answer its error dialogs with Skip so a bad lookup doesn't stall the run.
ES-DE retries a failed lookup 3 times (logged as "Attempting automatic retry"); an error line with no retry after
it within 10 s means the Retry/Skip/Cancel dialog is up. Skip = Right, A. Stops when scrape-then-test finishes."""
import prism_config  # noqa: E402  (paths)
import os, re, subprocess, sys, time
sys.path.insert(0, prism_config.PRISM); import deck_mcp
LOG = f"{prism_config.ESDE}/logs/es_log.txt"
pad = deck_mcp.gamepad(); time.sleep(1.5)
def tap(b):
    deck_mcp._pad_event(pad, b, True); time.sleep(0.08); deck_mcp._pad_event(pad, b, False); time.sleep(0.5)
f = open(LOG, errors="replace"); f.seek(0, 2)
pending = None
while True:
    line = f.readline()
    if not line:
        if pending and time.time() - pending > 10:
            tap("RIGHT"); tap("A")
            print(time.strftime("%H:%M:%S"), "skipped a game after a scraper error", flush=True)
            pending = None
        if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "scrape-then-test"]).returncode != 0:
            break
        time.sleep(1)
        continue
    if "Attempting automatic retry" in line:
        pending = None
    elif "Error:" in line and "GuiScraperSearch" in line:   # the error that ends in the Retry/Skip dialog
        pending = time.time()
print("scraper finished; skipper exiting", flush=True)
