"""
schema.py — pydantic StateV1 model for state.json validation.

Called after synthesize writes state.json. On failure: write
data/state.invalid.json and raise so CI exits 1 without touching
data/state.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

import structlog

log = structlog.get_logger(__name__)

VALID_TILT_PCTS = {-3.0, -1.5, 0.0, 1.5, 3.0}
# Pro-rata AI-capex cap (Fix C) can produce intermediate values; allow any tilt in [-3, +3].
TILT_RANGE = (-3.0, 3.0)
BUDGET = 250.0
BUDGET_TOLERANCE = 0.02   # allow ±$0.02 for floating-point rounding


# ── Sub-models ─────────────────────────────────────────────────────────────────

class TopEvent(BaseModel):
    title: str
    url: str = ""
    salience: int = Field(ge=0, le=100)
    direction: int = Field(ge=-1, le=1)
    rationale: str = ""


class Theme(BaseModel):
    id: str
    name: str
    conviction: int = Field(ge=0, le=100)
    tilt_pct: float
    weekly_dollars: float = Field(ge=0.0)
    account: Literal["Roth", "Taxable"]
    core_tickers: list[str]
    bench_tickers: list[str]
    watch_tickers: list[str]
    sparkline: list[int]
    why: str = ""
    thesis_state: Literal["intact", "weakening", "threatened"] = "intact"
    kill_flags_triggered: list[str] = []
    top_events: list[TopEvent] = []
    # New signal-quality fields (added in fix 6)
    has_signal: bool = False
    event_count_7d: int = 0
    event_count_14d: int = 0
    confidence: Literal["high", "medium", "low", "no_data"] = "no_data"
    # Fix J: last conviction change vs snapshot >7 days old
    last_change: dict = {}

    @model_validator(mode="after")
    def tilt_in_valid_set(self) -> "Theme":
        # Pro-rata AI-capex cap (Fix C) can produce values outside the standard steps.
        # Validate range [-3, +3] to allow pro-rata values; log if outside standard steps.
        lo, hi = TILT_RANGE
        if not (lo - 0.001 <= self.tilt_pct <= hi + 0.001):
            raise ValueError(
                f"tilt_pct {self.tilt_pct} outside allowed range [{lo}, {hi}]"
            )
        return self


class LeapProgress(BaseModel):
    current: float = Field(ge=0.0)
    target: float = Field(ge=0.0)
    candidate: str = ""


class Reserve(BaseModel):
    sgov_dry_powder: float = Field(ge=0.0)
    leap_accumulation: float = Field(ge=0.0)
    total_reserve: float = Field(ge=0.0)

    @model_validator(mode="after")
    def total_matches_parts(self) -> "Reserve":
        expected = self.sgov_dry_powder + self.leap_accumulation
        if abs(expected - self.total_reserve) > 0.01:
            raise ValueError(
                f"reserve.total_reserve {self.total_reserve} != "
                f"sgov_dry_powder {self.sgov_dry_powder} + "
                f"leap_accumulation {self.leap_accumulation} = {expected}"
            )
        return self


class Deploy(BaseModel):
    roth_total: float = Field(ge=0.0)
    taxable_total: float = Field(ge=0.0)
    reserve_total: float = Field(ge=0.0)
    roth_breakdown: list[dict] = []
    taxable_breakdown: list[dict] = []
    leap_progress: LeapProgress


class KillSwitches(BaseModel):
    max_drawdown_pct: float | None = None
    rolling_hit_rate: float | None = None
    alpha_drawdown_pct: float | None = None
    triggered: bool = False
    warnings: list[str] = []
    status: Literal["data_collecting", "live"] = "data_collecting"
    weeks_collected: int = 0
    weeks_needed: int = 30


class Metrics(BaseModel):
    total_contributed_ytd: float = 0.0
    total_value: float = 0.0
    vs_spy_ytd_pct: float | None = None
    vs_equal_weight_pct: float | None = None
    tilt_alpha_ytd_pct: float | None = None
    validation_status: Literal["collecting", "in_progress", "live"] = "collecting"
    weeks_collected: int = 0


# ── Root model ─────────────────────────────────────────────────────────────────

class StateV1(BaseModel):
    model_config = {"extra": "allow"}   # allow forward-compat fields like meta

    version: str
    generated_at: str
    next_refresh_at: str
    stale: bool = False
    stale_feeds: list[str] = []
    degraded: bool = False
    themes: list[Theme]
    deploy: Deploy
    reserve: Reserve
    kill_switches: KillSwitches
    metrics: Metrics
    meta: dict = {}   # Fix I: cost/duration telemetry — optional

    @model_validator(mode="after")
    def budget_sum_check(self) -> "StateV1":
        themes_total = sum(t.weekly_dollars for t in self.themes)
        total = themes_total + self.reserve.total_reserve
        if abs(total - BUDGET) > BUDGET_TOLERANCE:
            raise ValueError(
                f"Budget check failed: themes ${themes_total:.2f} + "
                f"reserve ${self.reserve.total_reserve:.2f} = ${total:.2f}, "
                f"expected ${BUDGET:.2f} ± ${BUDGET_TOLERANCE:.2f}"
            )
        return self


# ── Validation helper ──────────────────────────────────────────────────────────

def validate_state(state_path: Path) -> StateV1:
    """
    Parse and validate state.json. On failure, write state.invalid.json
    alongside it and re-raise so the pipeline can exit 1.
    """
    raw = state_path.read_text(encoding="utf-8")
    try:
        model = StateV1.model_validate_json(raw)
        log.info("schema_valid", path=str(state_path))
        return model
    except Exception as exc:
        invalid_path = state_path.parent / "state.invalid.json"
        invalid_path.write_text(raw, encoding="utf-8")
        log.error("schema_invalid", path=str(state_path),
                  invalid_path=str(invalid_path), err=str(exc))
        raise
