"""Real-money promotion gate."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from app.backtest.harness import ARTIFACT_DIR


def go_live(typed_confirmation: str) -> bool:
    """Refuse live promotion unless every explicit gate is satisfied."""
    failures: list[str] = []
    results_path = ARTIFACT_DIR / "backtest_results.json"
    ledger_path = ARTIFACT_DIR / "paper_trade_ledger.csv"

    if not results_path.exists():
        failures.append("missing per-strategy backtest_results.json")
    else:
        results = json.loads(results_path.read_text())
        if not results:
            failures.append("empty backtest results")
        bad = [r["strategy"] for r in results if float(r.get("expectancy", 0)) <= 0]
        if bad:
            failures.append(f"non-positive expectancy strategies: {', '.join(bad)}")

    sessions = _paper_sessions(ledger_path)
    if sessions < 30:
        failures.append(f"paper sessions logged < 30 ({sessions})")

    if os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "true":
        failures.append("LIVE_TRADING_ENABLED is not true")
    if os.getenv("LIVE_TRADING_UNLOCKED", "false").lower() != "true":
        failures.append("LIVE_TRADING_UNLOCKED is not true")
    if typed_confirmation != "ENABLE LIVE TRADING":
        failures.append("typed confirmation mismatch")

    if failures:
        raise RuntimeError("Live trading gate failed: " + "; ".join(failures))
    return True


def _paper_sessions(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    return len({row.get("session_date", "") for row in rows if row.get("session_date")})
