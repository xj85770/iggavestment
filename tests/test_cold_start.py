"""FIX 7: cold-start sparkline and honest validation status tests."""
import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from iggavestment.normalize import Score
from iggavestment.synthesize import (
    synthesize_state,
    build_sparkline,
    _derive_validation_status,
)


# ── sparkline ──────────────────────────────────────────────────────────────────

def test_sparkline_empty_when_cold_start():
    result = build_sparkline(current=65, history_values=[], history_count=0)
    assert result == []


def test_sparkline_empty_below_threshold():
    result = build_sparkline(current=65, history_values=[50, 55, 60], history_count=3)
    assert result == []


def test_sparkline_14_points_when_enough_history():
    result = build_sparkline(current=72, history_values=[60, 65], history_count=10)
    assert len(result) == 14
    assert result[-1] == 72


def test_sparkline_ends_at_current_any_case():
    # With enough history
    result = build_sparkline(current=85, history_values=list(range(50, 63)), history_count=20)
    assert result[-1] == 85


# ── validation status ──────────────────────────────────────────────────────────

def test_validation_collecting_low_weeks():
    assert _derive_validation_status(0) == "collecting"
    assert _derive_validation_status(15) == "collecting"
    assert _derive_validation_status(29) == "collecting"


def test_validation_in_progress():
    assert _derive_validation_status(30) == "in_progress"
    assert _derive_validation_status(80) == "in_progress"


def test_validation_live():
    assert _derive_validation_status(104) == "live"
    assert _derive_validation_status(200) == "live"


# ── state output ──────────────────────────────────────────────────────────────

def test_cold_start_sparklines_empty(tmp_path):
    """With 0 history snapshots, all sparklines must be []."""
    with patch("iggavestment.config.HIST_DIR", tmp_path):
        state = synthesize_state(scores=[], dry_run=True)
    for t in state["themes"]:
        assert t["sparkline"] == [], f"{t['id']} sparkline not empty: {t['sparkline']}"


def test_cold_start_validation_collecting(tmp_path):
    with patch("iggavestment.config.HIST_DIR", tmp_path):
        state = synthesize_state(scores=[], dry_run=True)
    assert state["metrics"]["validation_status"] == "collecting"
    assert state["metrics"]["vs_spy_ytd_pct"] is None
    assert state["metrics"]["vs_equal_weight_pct"] is None
    assert state["metrics"]["tilt_alpha_ytd_pct"] is None


def test_cold_start_metrics_null(tmp_path):
    with patch("iggavestment.config.HIST_DIR", tmp_path):
        state = synthesize_state(scores=[], dry_run=True)
    m = state["metrics"]
    assert m["vs_spy_ytd_pct"] is None
    assert m["vs_equal_weight_pct"] is None
    assert m["tilt_alpha_ytd_pct"] is None


# ── DST recency guard ──────────────────────────────────────────────────────────

def test_is_too_recent_fresh_state(tmp_path):
    """State written < 5h ago → skip."""
    import json
    from datetime import timedelta
    from iggavestment.cli import _is_too_recent
    from iggavestment.config import STATE_JSON
    state_file = tmp_path / "state.json"
    now_str = datetime.now(timezone.utc).isoformat()
    state_file.write_text(json.dumps({"generated_at": now_str}))
    with patch("iggavestment.cli.STATE_JSON", state_file):
        assert _is_too_recent() is True


def test_is_too_recent_old_state(tmp_path):
    """State written 6h ago → proceed."""
    import json
    from datetime import timedelta
    from iggavestment.cli import _is_too_recent
    old = datetime.now(timezone.utc) - timedelta(hours=6)
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"generated_at": old.isoformat()}))
    with patch("iggavestment.cli.STATE_JSON", state_file):
        assert _is_too_recent() is False
