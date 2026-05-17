#!/bin/bash
# smc-engine VPS deploy — runs ON the VPS (smc user). Idempotent.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Leblepito/smc-engine/main/deploy/hetzner/deploy.sh | bash
#   # OR (after first deploy, for pulling updates):
#   ~/smc-engine/deploy/hetzner/deploy.sh
set -euo pipefail

REPO_URL="git@github.com:Leblepito/smc-engine.git"
REPO_DIR="$HOME/smc-engine"

echo "[deploy] checking repo..."
if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "[deploy] cloning fresh..."
    git clone "$REPO_URL" "$REPO_DIR"
else
    echo "[deploy] pulling latest..."
    cd "$REPO_DIR"
    git pull --ff-only
fi

cd "$REPO_DIR"

echo "[deploy] venv setup..."
if [[ ! -d .venv ]]; then
    python3.12 -m venv .venv
fi
.venv/bin/pip install --upgrade pip wheel -q
.venv/bin/pip install -e ".[dev]" -q

echo "[deploy] verifying tests pass..."
.venv/bin/python -m pytest tests/ -q \
    --ignore=tests/test_harness.py \
    --ignore=tests/test_backtest_e2e.py \
    --ignore=tests/test_walk_forward.py \
    --ignore=tests/test_r2a_walkforward_content.py \
    --ignore=tests/test_r2a_lookahead_trade.py \
    --ignore=tests/test_r2a_determinism_fill_cost.py \
    2>&1 | tail -3

echo "[deploy] installing systemd unit..."
sudo cp "$REPO_DIR/deploy/hetzner/smc-engine.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable smc-engine

echo "[deploy] restarting service..."
if sudo systemctl is-active --quiet smc-engine; then
    sudo systemctl restart smc-engine
else
    sudo systemctl start smc-engine
fi

sleep 2
sudo systemctl status smc-engine --no-pager | head -10
echo "[deploy] done."
