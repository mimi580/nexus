#!/usr/bin/env python3
"""Convenience wrapper: python scripts/simulate.py --days 10"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json

from app.core.logging import configure_logging
from app.simulation.runner import run_simulation

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--database-url", default="sqlite+pysqlite:///./data/simulation.sqlite3")
    args = parser.parse_args()
    configure_logging()
    report = run_simulation(database_url=args.database_url, days=args.days, verbose=True)
    print(json.dumps(report, indent=2, default=str))
