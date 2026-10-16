#!/usr/bin/env python3
"""ES-DE changes that need ES-DE closed (it rewrites es_settings.xml on exit). Run by deck_autosleep.py when
~/prism/esde_restart_pending exists (applied while nobody is using the Deck), or by hand.
2026-10-06: All Games auto collection on (L5 search uses it); newest-first console order comes from
~/ES-DE/custom_systems/es_systems_sorting.xml and only needs the restart.
2026-10-13: closes / reopens ES-DE with prism_esde.esde_closed (busy flag, startup cover, Prism's Steam session ended,
relaunch retried); never while something is being played (the marker then stays for the next idle moment)."""
import prism_config  # noqa: E402  (paths)
import os, subprocess, sys, time
if os.path.exists(f"{prism_config.PRISM}/party_mode") and "--force" not in sys.argv:
    sys.exit("party mode: ES-DE restart postponed")
sys.path.insert(0, prism_config.PRISM)
import prism_esde  # noqa: E402

UI = f"{prism_config.PRISM}/ui_pending"
ui_now = os.path.exists(UI)


def apply_pending():
    try:                     # settings changed in Prism Settings that are ES-DE's own (menu sounds)
        import prism_features
        prism_features.apply_pending_esde_settings()
    except Exception as e:
        print("prism settings:", e)
    for name, value in (("CollectionSystemsAuto", "all,favorites,recent"),
                        # Prism's own dim screensaver off (2026-10-09): waking with the power button sends it no input,
                        # so the screen stayed dimmed to black; deck_screensaver.py already turns the panel off when idle
                        ("ScreensaverTimer", "0")):
        try:
            prism_esde.set_setting(name, value)
        except KeyError:
            pass
    import esde_tidy; esde_tidy.tidy()
    subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/plex_sync.py", "--apply"])   # Movies / TV lists
    subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/media_counts.py"])            # "111 shows · 5,929 episodes" pills
    # home_backdrops.py retired 2026-10-08: one calm shared background instead of per-tile backdrops
    subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/new_on_deck.py"])                 # New on Deck collection
    subprocess.run(["/usr/bin/python3", f"{prism_config.PRISM}/foryou/foryou.py", "apply"])   # staged For You picks/prices
    # one-off UI change waiting for a restart (apply_ui.py: no MENU/OPTIONS), checked after ES-DE is back up
    if ui_now:
        import apply_ui; apply_ui.apply(); os.remove(UI)


try:
    with prism_esde.esde_closed("pending ES-DE changes", relaunch="always"):
        apply_pending()
except prism_esde.NotIdle as e:
    sys.exit(f"not now: {e}")
if ui_now:
    time.sleep(90)
    import apply_ui
    if not apply_ui.check():
        with prism_esde.esde_closed("UI change reverted", require_idle=False, relaunch="always"):
            apply_ui.revert()
try:
    os.remove(f"{prism_config.PRISM}/esde_restart_pending")
except OSError:
    pass
print("applied")
