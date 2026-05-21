"""FIX 1: arithmetic tests — themes + reserve must equal $250 exactly."""
import pytest
from datetime import datetime, timezone

from iggavestment.config import THEMES
from iggavestment.synthesize import (
    WEEKLY_BUDGET, RESERVE_TOTAL, SGOV_DRY_POWDER, LEAP_ACCUMULATION,
    synthesize_state,
)
from iggavestment.normalize import Score


def _make_score(theme: str, salience: int = 65, direction: int = 1) -> Score:
    return Score(
        event_id="aa" + theme[:10].ljust(10, "0"),
        theme=theme,
        scored_at=datetime.now(timezone.utc).isoformat(),
        salience=salience,
        direction=direction,
        rationale="test",
        model="mock",
    )


# ── Config-level arithmetic ───────────────────────────────────────────────────

def test_theme_sum_plus_reserve_equals_budget():
    theme_sum = sum(t.weekly_usd for t in THEMES.values())
    assert abs(theme_sum + RESERVE_TOTAL - WEEKLY_BUDGET) < 0.01, (
        f"theme_sum ${theme_sum} + reserve ${RESERVE_TOTAL} "
        f"= ${theme_sum + RESERVE_TOTAL}, expected ${WEEKLY_BUDGET}"
    )


def test_reserve_parts_sum():
    assert abs(SGOV_DRY_POWDER + LEAP_ACCUMULATION - RESERVE_TOTAL) < 0.01


def test_budget_constant():
    assert WEEKLY_BUDGET == 250.0


# ── State-level arithmetic (post-tilt) ────────────────────────────────────────

def test_state_total_equals_budget_no_tilt():
    """All neutral conviction → no tilt → themes + reserve = $250."""
    state = synthesize_state(scores=[], dry_run=True)
    themes_total = sum(t["weekly_dollars"] for t in state["themes"])
    reserve_total = state["reserve"]["total_reserve"]
    total = themes_total + reserve_total
    assert abs(total - 250.0) < 0.02, (
        f"themes ${themes_total:.2f} + reserve ${reserve_total:.2f} = ${total:.2f}, expected $250.00"
    )


def test_state_total_equals_budget_with_tilt():
    """With tilts applied, reserve absorbs rounding — sum still = $250."""
    scores = [_make_score(k) for k in THEMES]
    state = synthesize_state(scores=scores, dry_run=True)
    themes_total = sum(t["weekly_dollars"] for t in state["themes"])
    reserve_total = state["reserve"]["total_reserve"]
    total = themes_total + reserve_total
    assert abs(total - 250.0) < 0.02, (
        f"themes ${themes_total:.2f} + reserve ${reserve_total:.2f} = ${total:.2f}, expected $250.00"
    )


def test_deploy_block_has_reserve_total():
    state = synthesize_state(scores=[], dry_run=True)
    assert "reserve_total" in state["deploy"]
    assert state["deploy"]["reserve_total"] > 0


def test_reserve_block_present():
    state = synthesize_state(scores=[], dry_run=True)
    assert "reserve" in state
    assert "sgov_dry_powder" in state["reserve"]
    assert "leap_accumulation" in state["reserve"]
    assert "total_reserve" in state["reserve"]
