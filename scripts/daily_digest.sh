#!/usr/bin/env bash
# Daily Digest — every day 08:00 UTC (15:00 Phuket) cron-driven summary.
#
# Reads previous UTC day's signals + trades + service health.
# Output: logs/digest/digest-YYYY-MM-DD.txt (idempotent overwrite).
#
# Usage (manual): bash scripts/daily_digest.sh [YYYY-MM-DD]
#   No arg → defaults to "1 day ago UTC" (the cron use-case).
#
# Crontab entry (smc user):
#   0 8 * * * /home/smc/smc-engine/scripts/daily_digest.sh

set -e
cd "$(dirname "$0")/.."

DATE="${1:-$(date -u -d '1 day ago' +%Y-%m-%d)}"
OUT_DIR="logs/digest"
OUT="${OUT_DIR}/digest-${DATE}.txt"

mkdir -p "${OUT_DIR}"

{
  echo "════════════════════════════════════════════════════════════"
  echo " SMC Engine — Daily Digest for ${DATE} (UTC)"
  echo " Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "════════════════════════════════════════════════════════════"
  echo

  echo "─── Service health ─────────────────────────────────────────"
  systemctl is-active smc-engine 2>/dev/null || echo "(systemctl unavailable)"
  systemctl show smc-engine --property=ActiveEnterTimestamp --value 2>/dev/null || true
  echo "Disk usage (logs/):"
  du -sh logs/ 2>/dev/null || echo "(logs/ missing)"
  echo

  echo "─── Signals (validated_setup + rejection) ──────────────────"
  if [ -x .venv/bin/python ]; then
    .venv/bin/python scripts/analyze_signals.py --date "${DATE}" --log-dir logs 2>&1 \
      || echo "(analyze_signals.py failed for ${DATE})"
  else
    echo "(.venv/bin/python missing)"
  fi
  echo

  echo "─── Trades (orders, fills, PnL, kill switch) ───────────────"
  if [ -x .venv/bin/python ]; then
    .venv/bin/python scripts/analyze_trades.py --date "${DATE}" --log-dir logs/trades 2>&1 \
      || echo "(analyze_trades.py failed for ${DATE})"
  else
    echo "(.venv/bin/python missing)"
  fi
  echo

  echo "─── Recent service log tail (last 50 lines) ────────────────"
  # I2 code review: pipeline exit code = son komut (tail, hep 0). grep'in
  # "no matches" exit-1'i pipe'da yutulur — fallback message hiç ateşlenmez.
  # Önce variable'a topla, sonra empty-check ile fallback'i emniyete al.
  events="$(
    journalctl -u smc-engine -n 50 --no-pager 2>&1 \
      | grep -E "ERROR|Traceback|tick |ORDER_|SETUP_SKIPPED|KILL_SWITCH|RECONCILE" \
      | tail -30 \
      || true
  )"
  echo "${events:-(no notable events in last 50 lines)}"
  echo

  echo "════════════════════════════════════════════════════════════"
  echo " End of digest"
  echo "════════════════════════════════════════════════════════════"
} > "${OUT}" 2>&1

# Symlink "latest" for easy access — read with: cat logs/digest/latest.txt.
# I1 code review: set -e + ln -sf existing-non-symlink-on-BSD/macOS → script
# exit non-zero, cron warning mail. Cosmetic action; failure tolere edilir.
ln -sf "digest-${DATE}.txt" "${OUT_DIR}/latest.txt" || true

echo "Digest written: ${OUT}"
