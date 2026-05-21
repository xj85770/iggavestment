"""FIX 4: kill-switch history tests."""
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

from iggavestment.history import compute_kill_switches, WEEKS_FOR_DRAWDOWN


def _snap(dt: datetime, total_value: float, themes: list[dict] | None = None) -> dict:
    return {
        "version": "1.0.0",
        "generated_at": dt.isoformat(),
        "metrics": {
            "total_value": total_value,
            "total_contributed_ytd": 0,
            "vs_spy_ytd_pct": None,
        },
        "themes": themes or [],
    }


def _write_snaps(directory: Path, snaps: list[dict]) -> None:
    for i, s in enumerate(snaps):
        (directory / f"snap-{i:04d}.json").write_text(json.dumps(s))


# ── Empty history → data_collecting ──────────────────────────────────────────

def test_empty_history_data_collecting(tmp_path):
    result = compute_kill_switches(tmp_path, {})
    assert result["status"] == "data_collecting"
    assert result["max_drawdown_pct"] is None
    assert result["rolling_hit_rate"] is None
    assert result["alpha_drawdown_pct"] is None
    assert result["triggered"] is False


def test_sparse_history_data_collecting(tmp_path):
    """2 snapshots < WEEKS_FOR_DRAWDOWN threshold."""
    now = datetime.now(timezone.utc)
    snaps = [_snap(now - timedelta(weeks=i), 1000 + i * 10) for i in range(2)]
    _write_snaps(tmp_path, snaps)
    result = compute_kill_switches(tmp_path, {})
    assert result["status"] == "data_collecting"


# ── Enough data → metrics computed ───────────────────────────────────────────

def test_drawdown_computed_with_enough_data(tmp_path):
    """WEEKS_FOR_DRAWDOWN snapshots with a clear drawdown."""
    now = datetime.now(timezone.utc)
    # Peak at snap 0, then falls
    values = [1000, 1100, 900, 800]   # drawdown from 1100 → 800 = -27.3%
    snaps = [
        _snap(now - timedelta(weeks=WEEKS_FOR_DRAWDOWN - 1 - i), v)
        for i, v in enumerate(values)
    ]
    _write_snaps(tmp_path, snaps)
    result = compute_kill_switches(tmp_path, {})
    assert result["max_drawdown_pct"] is not None
    assert result["max_drawdown_pct"] < 0


def test_weeks_collected_reported(tmp_path):
    now = datetime.now(timezone.utc)
    snaps = [_snap(now - timedelta(weeks=i), 1000) for i in range(3)]
    _write_snaps(tmp_path, snaps)
    result = compute_kill_switches(tmp_path, {})
    assert result["weeks_collected"] == 3
    assert result["weeks_needed"] > 0
