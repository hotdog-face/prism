#!/bin/bash
. "$(dirname "$(readlink -f "$0")")/prism_env.sh"  # Prism paths (PRISM_*)
# Nightly save backup to the NAS (2026-10-07). Run by save-backup.timer (23:25, Persistent).
# One dated folder per night under "Steam Deck Hub/Saves" on the NAS; unchanged files are hard links to the previous
# night's copy (cp -al on the NAS + tar of changed files; the NAS rsync service is off), so each night costs only
# what changed. Nothing is ever deleted on the NAS.
# Skips (and tries again the next night) while the NAS RAID is reshaping/resyncing or the NAS is unreachable.
#   save_backup.sh [--dry-run]
NAS="${PRISM_CFG_BACKUP_SSH:?set backup_ssh (user@host) in ~/.config/prism/config.toml}"
DEST="${PRISM_CFG_BACKUP_DIR:?set backup_dir in ~/.config/prism/config.toml}"
LOG=${PRISM_DIR}/save_backup.log
SRC=${PRISM_DIR}/.save_sources
EMU=${PRISM_EMU}
set -o pipefail
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

ssh -o BatchMode=yes -o ConnectTimeout=10 $NAS 'grep -q -E "reshape|resync|recovery" /proc/mdstat'
rc=$?; [ -n "$FORCE_BACKUP" ] && [ $rc -eq 0 ] && rc=1   # FORCE_BACKUP=1: ignore a busy RAID (testing)
case $rc in
  0) log "NAS RAID busy (expansion/resync), skipped"; exit 0 ;;
  1) ;;
  *) log "NAS unreachable, skipped"; exit 0 ;;
esac

# what to back up: symlinks to every save location (rsync -L copies what they point at)
rm -rf "$SRC"; mkdir -p "$SRC/es-de" "$SRC/dreamcast-vmu" "$SRC/deck-mcp"
ln -s "$EMU/saves" "$SRC/emulation-saves"
ln -s "$HOME/.local/share/eden/nand/user/save" "$SRC/eden-save"
ln -s "${PRISM_ESDE}/collections" "$SRC/es-de/collections"
ln -s "${PRISM_ESDE}/settings" "$SRC/es-de/settings"
for f in "$EMU"/bios/dc/vmu_save_*.bin "$EMU"/bios/dc/dc_nvmem.bin; do [ -e "$f" ] && ln -s "$f" "$SRC/dreamcast-vmu/"; done
for f in playcounts_archive.json test_results.jsonl hidden_by_tester.txt game_emulators.json arcade_cores.json \
         emu/duckstation_serials.txt emu/pcsx2_states.txt emu/dolphin_states.txt foryou/history.json foryou/dismissed.json; do
  [ -e "${PRISM_DIR}/$f" ] && ln -s "${PRISM_DIR}/$f" "$SRC/deck-mcp/$(basename "$f")"
done

day=$(date +%F)
STAMP=${PRISM_DIR}/.save_backup_stamp          # files newer than the last good backup are sent
prev=$(ssh $NAS "ls -1 '$DEST' 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | grep -v '^$day$' | sort | tail -1")
newer=(); [ -n "$prev" ] && [ -e "$STAMP" ] && newer=(-newer "$STAMP")
next=$(mktemp); touch "$next"; list=$(mktemp)
cd "$SRC" && find -L . -type f "${newer[@]}" ! -path './emulation-saves/ryujinx/system/*' ! -path './emulation-saves/xenia/*' \
    ! -name '*.cache' ! -path '*/shadercache/*' -print0 > "$list"
count=$(tr -cd '\0' < "$list" | wc -c)
size=$(xargs -0 -r du -cLk < "$list" 2>/dev/null | tail -1 | cut -f1)
if [ "$1" = "--dry-run" ]; then log "dry run: $count files (${size:-0} KB) would go to $DEST/$day (previous: ${prev:-none})"; rm -f "$list" "$next"; exit 0; fi
# tonight's folder starts as hard links to the previous night (no extra space); changed files replace their link
# (tar -U unlinks first, so the previous night's copy is never modified)
ssh $NAS "set -e; if [ ! -d '$DEST/$day' ]; then mkdir -p '$DEST'; if [ -n '$prev' ]; then cp -al '$DEST/$prev' '$DEST/$day'; else mkdir '$DEST/$day'; fi; fi" \
  || { log "could not create $DEST/$day"; rm -f "$list" "$next"; exit 1; }
if [ "$count" -gt 0 ]; then
  tar -czh --null -T "$list" | ssh $NAS "tar -xz -U -C '$DEST/$day'"
  rc=$?
else
  rc=0
fi
[ $rc -eq 0 ] && mv "$next" "$STAMP"
log "backup $day rc=$rc: $count changed files (${size:-0} KB), previous: ${prev:-none}"
rm -f "$list" "$next"
exit $rc
