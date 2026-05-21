"""Tests for synthesize.py"""
import pytest
from datetime import datetime, timezone

from iggavestment.normalize import Score
from iggavestment.synthesize import (
    conviction_to_tilt,
    apply_ai_capex_cap,
    build_conviction_map,
    build_sparkline,
    synthesize_state,
)
from iggavestment.config import THEMES


def _make_score(theme: str, salience: int, direction: int) -> Score:
    return Score(
        event_id="aabbcc" + theme[:6].ljust(6, "0"),
        theme=theme,
        scored_at=datetime.now(timezone.utc).isoformat(),
        salience=salience,
        direction=direction,
        rationale="test rationale text for scoring",
        model="mock",
    )


# ── Tilt table ─────────────────────────────────────────────────────────────────

def test_tilt_low_conviction():
    assert conviction_to_tilt(20) == -0.03

def test_tilt_neutral():
    assert conviction_to_tilt(50) == 0.00

def test_tilt_mid():
    assert conviction_to_tilt(65) == 0.015

def test_tilt_high():
    assert conviction_to_tilt(80) == 0.03


# ── AI capex cap ───────────────────────────────────────────────────────────────

def test_ai_capex_cap_not_triggered():
    tilts = {"tech_ai": 0.015, "space": 0.015}
    result = apply_ai_capex_cap(tilts)
    # Combined = 0.03 = cap, no reduction needed
    assert result["tech_ai"] == 0.015
    assert result["space"] == 0.015

def test_ai_capex_cap_triggered():
    tilts = {"tech_ai": 0.03, "space": 0.03}
    result = apply_ai_capex_cap(tilts)
    assert result["tech_ai"] + result["space"] <= 0.03 + 1e-9


# ── Conviction map ────────────────────────────────────────────────────────────

def test_build_conviction_map_empty():
    result = build_conviction_map([])
    for key in THEMES:
        assert result[key] == 50  # neutral default

def test_build_conviction_map_positive():
    scores = [_make_score("bio", 80, 1), _make_score("bio", 60, 1)]
    result = build_conviction_map(scores)
    assert result["bio"] > 50  # positive events → above neutral

def test_build_conviction_map_negative():
    scores = [_make_score("energy", 90, -1)]
    result = build_conviction_map(scores)
    assert result["energy"] < 50  # negative event → below neutral


# ── Sparkline ─────────────────────────────────────────────────────────────────

def test_sparkline_length():
    # With history_count >= 4 we get 14 points
    result = build_sparkline(72, [60, 65, 68, 70], history_count=10)
    assert len(result) == 14

def test_sparkline_length_cold_start():
    # Cold start (history_count < 4) returns empty list
    result = build_sparkline(72, [60, 65, 68, 70], history_count=0)
    assert result == []

def test_sparkline_ends_at_current():
    result = build_sparkline(85, [], history_count=10)
    assert result[-1] == 85


# ── Dry-run synthesis ─────────────────────────────────────────────────────────

def test_synthesize_state_dry_run():
    scores = [
        _make_score("bio", 75, 1),
        _make_score("tech_ai", 80, 1),
        _make_score("space", 65, 1),
        _make_score("energy", 60, -1),
    ]
    state = synthesize_state(scores=scores, client=None, dry_run=True)

    assert state["version"] == "1.0.0"
    assert "generated_at" in state
    assert "next_refresh_at" in state
    assert len(state["themes"]) == len(THEMES)

    for t in state["themes"]:
        assert 0 <= t["conviction"] <= 100
        assert t["id"] in THEMES
        assert isinstance(t["sparkline"], list)
        # sparkline is [] on cold start (no history dir populated), or 14 points with history
        assert len(t["sparkline"]) in (0, 14)
        assert "weekly_dollars" in t
        assert t["weekly_dollars"] > 0

    assert "deploy" in state
    assert state["deploy"]["roth_total"] > 0 or state["deploy"]["taxable_total"] > 0
    assert "kill_switches" in state


def test_synthesize_all_themes_present():
    """Ensure all 7 themes always appear in state, even with no scores."""
    state = synthesize_state(scores=[], client=None, dry_run=True)
    theme_ids = {t["id"] for t in state["themes"]}
    assert theme_ids == set(THEMES.keys())
