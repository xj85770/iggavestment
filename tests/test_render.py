"""Tests for render.py"""
import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch


def test_write_state_creates_file():
    from iggavestment.render import write_state
    from iggavestment.config import STATE_JSON

    sample_state = {
        "version": "1.0.0",
        "generated_at": "2026-05-21T05:00:00-07:00",
        "next_refresh_at": "2026-05-21T17:00:00-07:00",
        "stale": False,
        "stale_feeds": [],
        "degraded": False,
        "themes": [],
        "deploy": {"roth_total": 135, "taxable_total": 75, "roth_breakdown": [], "taxable_breakdown": [], "leap_progress": {"current": 0, "target": 2500, "candidate": "MSFT"}},
        "kill_switches": {"triggered": False, "warnings": []},
        "metrics": {},
    }

    with patch("iggavestment.render.STATE_JSON", Path(tempfile.mktemp(suffix=".json"))) as mock_path:
        path = write_state(sample_state)
        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded["version"] == "1.0.0"
        path.unlink(missing_ok=True)


def test_load_prior_state_missing():
    from iggavestment.render import load_prior_state
    with patch("iggavestment.render.STATE_JSON", Path("/tmp/iggavestment_nonexistent_123456.json")):
        result = load_prior_state()
        assert result is None
