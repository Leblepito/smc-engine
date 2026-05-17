#!/bin/bash
# Wrapper for kill_switch_reset.py — convenient SSH-friendly entry point.
# Usage: ~/smc-engine/scripts/kill_switch_reset.sh
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/kill_switch_reset.py "$@"
