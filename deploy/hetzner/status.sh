#!/bin/bash
# smc-engine VPS quick status — runs on the VPS, called from cheat sheet.
set -u
echo "=== systemd ==="
sudo systemctl status smc-engine --no-pager | head -15
echo
echo "=== latest signals ==="
TODAY=$(date -u +%Y%m%d)
LOG="$HOME/smc-engine/logs/signals-${TODAY}.jsonl"
if [[ -f "$LOG" ]]; then
    echo "$(wc -l <"$LOG") events today (${LOG})"
    echo "--- last 3 ---"
    tail -3 "$LOG"
else
    echo "no log yet for ${TODAY}"
fi
echo
echo "=== uptime / load ==="
uptime
echo
echo "=== disk ==="
df -h "$HOME/smc-engine" | tail -1
