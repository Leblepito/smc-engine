"""Mevcut SMCConfig default'larini Pine v6 input bloku olarak stdout'a basar.

Kullanim:
    python3 examples/export_pine_config.py > pine_inputs.pine
    python3 examples/export_pine_config.py --config config.yaml > pine_inputs.pine
"""

from __future__ import annotations

import argparse
import sys

from smc_engine.config import SMCConfig, load_config
from smc_engine.integrations.tradingview.pine_config_export import to_pine_inputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Optional path to a config.yaml — yoksa default SMCConfig kullanilir.",
    )
    args = parser.parse_args()
    cfg: SMCConfig = load_config(args.config) if args.config else SMCConfig()
    sys.stdout.write(to_pine_inputs(cfg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
