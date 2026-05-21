"""
history.py — compute kill-switch metrics from snapshot history.

All computations require minimum data thresholds; below them, values are
None and status is "data_collecting" so the UI can be honest.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

WEEKS_FOR_DRAWDOWN   = 4    # need ≥4 total_value data points
WEEKS_FOR_HIT_RATE   = 30   # need ≥30 tilt decisions
WEEKS_FOR_ALPHA      = 30   # need comparable benchmark history (same as hit_rate)


def _load_snapshots(history_dir: Path) -> list[dict]:
    """Load and sort all JSON snapshots by generated_at ascending."""
    snapshots = []
    for p in sorted(history_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if "generated_at" in data:
                snapshots.append(data)
        except Exception as exc:
            log.warning("history_load_failed", file=str(p), err=str(exc))
    snapshots.sort(key=lambda s: s.get("generated_at", ""))
    return snapshots


def compute_kill_switches(history_dir: Path, current_state: dict) -> dict[str, Any]:
    """
    Compute kill-switch metrics from snapshot history.

    Returns dict with:
      max_drawdown_pct, rolling_hit_rate, alpha_drawdown_pct  — float or None
      triggered                                                — bool
      warnings                                                 — list[str]
      status                                                   — "data_collecting" | "live"
      weeks_collected                                          — int
      weeks_needed                                             — int
    """
    snapshots = _load_snapshots(history_dir)
    n = len(snapshots)

    weeks_needed = max(WEEKS_FOR_DRAWDOWN, WEEKS_FOR_HIT_RATE)
    result: dict[str, Any] = {
        "triggered": False,
        "warnings": [],
        "status": "data_collecting",
        "weeks_collected": n,
        "weeks_needed": weeks_needed,
    }

    # max_drawdown_pct: peak-to-trough on total_value
    max_drawdown: float | None = None
    if n >= WEEKS_FOR_DRAWDOWN:
        values = [
            s.get("metrics", {}).get("total_value", 0.0)
            for s in snapshots
        ]
        values = [v for v in values if v and v > 0]
        if len(values) >= WEEKS_FOR_DRAWDOWN:
            peak = values[0]
            worst = 0.0
            for v in values[1:]:
                if v > peak:
                    peak = v
                elif peak > 0:
                    dd = (v - peak) / peak * 100
                    worst = min(worst, dd)
            max_drawdown = round(worst, 2) if worst != 0.0 else 0.0

    # rolling_hit_rate: tilt direction vs conviction change in next snapshot
    rolling_hit_rate: float | None = None
    if n >= WEEKS_FOR_HIT_RATE:
        hits = 0
        total_decisions = 0
        for i, snap in enumerate(snapshots[:-1]):
            next_snap = snapshots[i + 1]
            for theme in snap.get("themes", []):
                tid = theme.get("id")
                tilt = theme.get("tilt_pct", 0.0)
                if tilt == 0.0:
                    continue
                next_themes = {t["id"]: t for t in next_snap.get("themes", [])}
                if tid not in next_themes:
                    continue
                next_conv = next_themes[tid].get("conviction", 50)
                curr_conv = theme.get("conviction", 50)
                conv_delta = next_conv - curr_conv
                if tilt > 0 and conv_delta >= 0:
                    hits += 1
                elif tilt < 0 and conv_delta <= 0:
                    hits += 1
                total_decisions += 1
        if total_decisions > 0:
            rolling_hit_rate = round(hits / total_decisions, 2)

    # alpha_drawdown_pct: requires vs_spy_ytd_pct to be populated in history
    alpha_drawdown: float | None = None
    if n >= WEEKS_FOR_ALPHA:
        alpha_vals = [
            s.get("metrics", {}).get("vs_spy_ytd_pct")
            for s in snapshots
            if s.get("metrics", {}).get("vs_spy_ytd_pct") is not None
            and s.get("metrics", {}).get("vs_spy_ytd_pct") != 0.0
        ]
        if len(alpha_vals) >= WEEKS_FOR_ALPHA:
            peak_alpha = alpha_vals[0]
            worst_alpha = 0.0
            for av in alpha_vals[1:]:
                if av > peak_alpha:
                    peak_alpha = av
                elif peak_alpha > 0:
                    dd = av - peak_alpha
                    worst_alpha = min(worst_alpha, dd)
            alpha_drawdown = round(worst_alpha, 2)

    result["max_drawdown_pct"] = max_drawdown
    result["rolling_hit_rate"] = rolling_hit_rate
    result["alpha_drawdown_pct"] = alpha_drawdown

    # Determine status
    all_live = all(v is not None for v in [max_drawdown, rolling_hit_rate, alpha_drawdown])
    result["status"] = "live" if all_live else "data_collecting"

    # Check triggers only when live
    if result["status"] == "live":
        triggered = False
        if max_drawdown is not None and max_drawdown < -8.5:
            triggered = True
            result["warnings"].append(f"Max drawdown {max_drawdown:.1f}% breached -8.5% threshold")
        if rolling_hit_rate is not None and rolling_hit_rate < 0.45:
            triggered = True
            result["warnings"].append(f"Hit rate {rolling_hit_rate:.2f} below 0.45 threshold")
        if alpha_drawdown is not None and alpha_drawdown < -1.2:
            triggered = True
            result["warnings"].append(f"Alpha drawdown {alpha_drawdown:.1f}% breached -1.2% threshold")
        result["triggered"] = triggered

    return result
