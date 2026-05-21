"""
Tests for Fixes A, C, D, E, J (2026-05-21).

  Fix A — Force neutral (conviction=50, tilt=0) when event_count_7d < HAS_SIGNAL_MIN_EVENTS
  Fix C — Symmetric pro-rata AI-capex cap
  Fix D — Real event_count_14d >= event_count_7d
  Fix E — HAS_SIGNAL_MIN_EVENTS in config, env-tunable
  Fix J — last_change per theme
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from iggavestment.config import HAS_SIGNAL_MIN_EVENTS, THEMES
from iggavestment.normalize import Score
from iggavestment.synthesize import (
    apply_ai_capex_cap,
    synthesize_state,
    _last_change_for_theme,
)


def _make_score(theme: str, n: int = 1, salience: int = 80, direction: int = 1) -> list[Score]:
    now = datetime.now(timezone.utc).isoformat()
    return [
        Score(
            event_id=f"ev{i:04d}{theme[:4]}",
            theme=theme,
            scored_at=now,
            salience=salience,
            direction=direction,
            rationale="test event",
            model="mock",
        )
        for i in range(n)
    ]


# ── Fix A: force neutral below threshold ──────────────────────────────────────

def test_fix_a_below_threshold_conviction_50():
    """Theme with < HAS_SIGNAL_MIN_EVENTS events → conviction forced to 50."""
    n_low = max(0, HAS_SIGNAL_MIN_EVENTS - 1)
    scores = _make_score("bio", n=n_low, salience=90, direction=1)
    state = synthesize_state(scores=scores, dry_run=True)
    bio = next(t for t in state["themes"] if t["id"] == "bio")
    assert bio["conviction"] == 50, (
        f"Expected conviction=50 for {n_low} events (<{HAS_SIGNAL_MIN_EVENTS}), got {bio['conviction']}"
    )


def test_fix_a_below_threshold_tilt_zero():
    """Theme with < HAS_SIGNAL_MIN_EVENTS events → tilt forced to 0."""
    n_low = max(0, HAS_SIGNAL_MIN_EVENTS - 1)
    scores = _make_score("bio", n=n_low, salience=90, direction=1)
    state = synthesize_state(scores=scores, dry_run=True)
    bio = next(t for t in state["themes"] if t["id"] == "bio")
    assert bio["tilt_pct"] == 0.0, (
        f"Expected tilt=0 for {n_low} events, got {bio['tilt_pct']}"
    )


def test_fix_a_at_threshold_uses_llm_score():
    """Theme with exactly HAS_SIGNAL_MIN_EVENTS events → conviction NOT forced to 50."""
    n_sig = HAS_SIGNAL_MIN_EVENTS
    scores = _make_score("bio", n=n_sig, salience=90, direction=1)
    state = synthesize_state(scores=scores, dry_run=True)
    bio = next(t for t in state["themes"] if t["id"] == "bio")
    # With n_sig events of salience 90 direction +1 the conviction should be > 50
    assert bio["conviction"] > 50, (
        f"Expected conviction>50 for {n_sig} events at salience=90, got {bio['conviction']}"
    )
    assert bio["has_signal"] is True


def test_fix_a_zero_events_all_themes_neutral():
    """No events → all themes conviction=50, tilt=0."""
    state = synthesize_state(scores=[], dry_run=True)
    for t in state["themes"]:
        assert t["conviction"] == 50
        assert t["tilt_pct"] == 0.0


# ── Fix C: symmetric pro-rata cap ─────────────────────────────────────────────

def test_fix_c_symmetric_both_full():
    """Both tech_ai and space at +3% → both reduced symmetrically to +1.5% each."""
    tilts = {"tech_ai": 0.03, "space": 0.03, "bio": 0.0}
    result = apply_ai_capex_cap(tilts)
    combined = result["tech_ai"] + result["space"]
    assert combined <= 0.03 + 1e-9, f"Combined {combined} > cap 0.03"
    # Symmetric: neither should be zeroed while the other keeps full tilt
    assert result["tech_ai"] > 0, "tech_ai should keep some positive tilt"
    assert result["space"] > 0, "space should keep some positive tilt"
    assert abs(result["tech_ai"] - result["space"]) < 1e-9, (
        f"Symmetric allocation expected; got tech_ai={result['tech_ai']}, space={result['space']}"
    )


def test_fix_c_prorata_unequal():
    """tech_ai=+3%, space=+1.5% → pro-rata within cap: tech_ai≈+2%, space≈+1%."""
    tilts = {"tech_ai": 0.03, "space": 0.015}
    result = apply_ai_capex_cap(tilts)
    combined = result["tech_ai"] + result["space"]
    assert combined <= 0.03 + 1e-9
    # tech_ai should still be larger than space (preserves proportionality)
    assert result["tech_ai"] > result["space"]
    assert result["space"] > 0


def test_fix_c_below_cap_unchanged():
    """Combined < cap → no reduction applied."""
    tilts = {"tech_ai": 0.015, "space": 0.015}
    result = apply_ai_capex_cap(tilts)
    assert result["tech_ai"] == 0.015
    assert result["space"] == 0.015


def test_fix_c_negative_unaffected():
    """Negative tilt on space doesn't count toward cap."""
    tilts = {"tech_ai": 0.03, "space": -0.03}
    result = apply_ai_capex_cap(tilts)
    # Only tech_ai is positive; combined_up = 0.03 = cap; no reduction
    assert result["tech_ai"] == 0.03
    assert result["space"] == -0.03


# ── Fix D: real event_count_14d ───────────────────────────────────────────────

def test_fix_d_14d_gte_7d():
    """event_count_14d must be >= event_count_7d for all themes."""
    scores = _make_score("bio", n=5) + _make_score("energy", n=3)
    state = synthesize_state(scores=scores, dry_run=True)
    for t in state["themes"]:
        assert t["event_count_14d"] >= t["event_count_7d"], (
            f"{t['id']}: 14d={t['event_count_14d']} < 7d={t['event_count_7d']}"
        )


# ── Fix E: HAS_SIGNAL_MIN_EVENTS is in config ─────────────────────────────────

def test_fix_e_constant_in_config():
    """HAS_SIGNAL_MIN_EVENTS must be importable from config and == 3 by default."""
    assert isinstance(HAS_SIGNAL_MIN_EVENTS, int)
    assert HAS_SIGNAL_MIN_EVENTS == 3


def test_fix_e_has_signal_matches_threshold():
    """has_signal == (event_count_7d >= HAS_SIGNAL_MIN_EVENTS)."""
    for n in [0, 1, 2, 3, 5]:
        scores = _make_score("bio", n=n)
        state = synthesize_state(scores=scores, dry_run=True)
        bio = next(t for t in state["themes"] if t["id"] == "bio")
        expected = n >= HAS_SIGNAL_MIN_EVENTS
        assert bio["has_signal"] == expected, (
            f"n={n}: expected has_signal={expected}, got {bio['has_signal']}"
        )


# ── Fix J: last_change per theme ─────────────────────────────────────────────

def test_fix_j_no_history_returns_new():
    """No history dir → last_change.direction == 'new'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        hist_dir = Path(tmpdir) / "history_nonexistent"
        result = _last_change_for_theme("bio", 65, hist_dir, {})
    assert result["direction"] == "new"
    assert result["delta"] == 0


def test_fix_j_old_snapshot_detected():
    """Snapshot > 7 days old → last_change computes delta."""
    with tempfile.TemporaryDirectory() as tmpdir:
        hist_dir = Path(tmpdir)
        old_date = (datetime.now(timezone.utc) - timedelta(days=10))
        snap = {
            "generated_at": old_date.isoformat(),
            "themes": [
                {"id": "bio", "conviction": 50},
                {"id": "tech_ai", "conviction": 60},
            ],
        }
        (hist_dir / "old_snap.json").write_text(json.dumps(snap))
        cache: dict = {}
        result = _last_change_for_theme("bio", 65, hist_dir, cache)
    assert result["direction"] == "up"
    assert result["delta"] == 15
    assert result["since_date"] != ""


def test_fix_j_recent_snapshot_ignored():
    """Snapshot < 7 days old → not used as comparison baseline."""
    with tempfile.TemporaryDirectory() as tmpdir:
        hist_dir = Path(tmpdir)
        recent_date = (datetime.now(timezone.utc) - timedelta(days=3))
        snap = {
            "generated_at": recent_date.isoformat(),
            "themes": [{"id": "bio", "conviction": 50}],
        }
        (hist_dir / "recent.json").write_text(json.dumps(snap))
        cache: dict = {}
        result = _last_change_for_theme("bio", 65, hist_dir, cache)
    # No snapshot > 7 days old → direction == "new"
    assert result["direction"] == "new"


def test_fix_j_flat_when_delta_small():
    """Delta of ±2 or less → direction 'flat'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        hist_dir = Path(tmpdir)
        old_date = (datetime.now(timezone.utc) - timedelta(days=8))
        snap = {
            "generated_at": old_date.isoformat(),
            "themes": [{"id": "bio", "conviction": 50}],
        }
        (hist_dir / "old.json").write_text(json.dumps(snap))
        cache: dict = {}
        result = _last_change_for_theme("bio", 51, hist_dir, cache)
    assert result["direction"] == "flat"


def test_fix_j_state_has_last_change_field():
    """synthesize_state output includes last_change for every theme."""
    state = synthesize_state(scores=[], dry_run=True)
    for t in state["themes"]:
        assert "last_change" in t, f"Missing last_change on theme {t['id']}"
        lc = t["last_change"]
        assert lc["direction"] in ("up", "down", "flat", "new")
        assert isinstance(lc["delta"], int)
