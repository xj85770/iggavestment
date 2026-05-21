"""FIX 3: schema gate tests — valid state passes, corrupted state fails."""
import json
import tempfile
from pathlib import Path

import pytest
from datetime import datetime, timezone

from iggavestment.synthesize import synthesize_state
from iggavestment.schema import StateV1, validate_state


def _good_state() -> dict:
    return synthesize_state(scores=[], dry_run=True)


def _write_tmp(data: dict) -> Path:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    )
    tmp.write(json.dumps(data, indent=2))
    tmp.flush()
    return Path(tmp.name)


# ── Valid state ────────────────────────────────────────────────────────────────

def test_valid_state_passes_schema():
    state = _good_state()
    validated = StateV1.model_validate(state)
    assert validated.version == "1.0.0"


def test_valid_state_file_passes():
    state = _good_state()
    p = _write_tmp(state)
    validated = validate_state(p)
    assert validated is not None


# ── Invalid states ────────────────────────────────────────────────────────────

def test_missing_reserve_fails():
    state = _good_state()
    del state["reserve"]
    with pytest.raises(Exception):
        StateV1.model_validate(state)


def test_bad_conviction_fails():
    state = _good_state()
    state["themes"][0]["conviction"] = 150   # out of 0-100
    with pytest.raises(Exception):
        StateV1.model_validate(state)


def test_bad_tilt_pct_fails():
    state = _good_state()
    state["themes"][0]["tilt_pct"] = 5.0   # outside allowed range [-3, +3]
    with pytest.raises(Exception):
        StateV1.model_validate(state)


def test_budget_sum_check_fails():
    state = _good_state()
    # Inflate first theme weekly_dollars to break the $250 sum
    state["themes"][0]["weekly_dollars"] = 9999.0
    with pytest.raises(Exception):
        StateV1.model_validate(state)


def test_corrupted_file_writes_invalid_json(tmp_path):
    state = _good_state()
    state["themes"][0]["conviction"] = 999   # illegal value
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state, indent=2))
    with pytest.raises(Exception):
        validate_state(p)
    # state.invalid.json must exist
    assert (tmp_path / "state.invalid.json").exists()
