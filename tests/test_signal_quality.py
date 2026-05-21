"""FIX 6: zero-event vs measured-neutral signal quality tests."""
import pytest
from datetime import datetime, timezone

from iggavestment.normalize import Score
from iggavestment.synthesize import synthesize_state, _derive_confidence


def _make_score(theme: str, n: int = 1, salience: int = 60, direction: int = 1) -> list[Score]:
    now = datetime.now(timezone.utc).isoformat()
    return [
        Score(
            event_id=f"ev{i:04d}{theme[:4]}",
            theme=theme,
            scored_at=now,
            salience=salience,
            direction=direction,
            rationale="test",
            model="mock",
        )
        for i in range(n)
    ]


# ── confidence derivation ──────────────────────────────────────────────────────

def test_confidence_no_data():
    assert _derive_confidence(0) == "no_data"

def test_confidence_low():
    assert _derive_confidence(2) == "low"

def test_confidence_medium():
    assert _derive_confidence(6) == "medium"

def test_confidence_high():
    assert _derive_confidence(12) == "high"


# ── state fields ──────────────────────────────────────────────────────────────

def test_zero_events_no_data_confidence():
    state = synthesize_state(scores=[], dry_run=True)
    for t in state["themes"]:
        assert t["confidence"] == "no_data"
        assert t["has_signal"] is False
        assert t["event_count_7d"] == 0


def test_twelve_events_high_confidence():
    scores = _make_score("bio", n=12)
    state = synthesize_state(scores=scores, dry_run=True)
    bio = next(t for t in state["themes"] if t["id"] == "bio")
    assert bio["confidence"] == "high"
    assert bio["has_signal"] is True
    assert bio["event_count_7d"] == 12


def test_other_themes_no_data_when_only_bio_scored():
    scores = _make_score("bio", n=12)
    state = synthesize_state(scores=scores, dry_run=True)
    for t in state["themes"]:
        if t["id"] != "bio":
            assert t["confidence"] == "no_data"
