"""
Render: write state.json + history snapshot + JSONL audit log.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from .config import STATE_JSON, HIST_DIR, AUDIT_LOG

log = structlog.get_logger(__name__)


def write_state(state: dict[str, Any]) -> Path:
    """Write data/state.json (atomic via temp write)."""
    tmp = STATE_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_JSON)
    log.info("state_written", path=str(STATE_JSON))
    return STATE_JSON


def write_history_snapshot(state: dict[str, Any]) -> Path:
    """Write data/history/YYYY-MM-DD-HH.json."""
    import zoneinfo
    PT  = zoneinfo.ZoneInfo("America/Los_Angeles")
    now = datetime.now(timezone.utc).astimezone(PT)
    fname = now.strftime("%Y-%m-%d-%H") + ".json"
    path  = HIST_DIR / fname
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("history_written", path=str(path))
    return path


def append_audit_log(entry: dict[str, Any]) -> None:
    """Append one JSON line to data/audit.jsonl."""
    with AUDIT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_prior_state() -> dict | None:
    """Load existing state.json if it exists."""
    if STATE_JSON.exists():
        try:
            return json.loads(STATE_JSON.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None
