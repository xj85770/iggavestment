"""
Score events via Claude Haiku using the local claude CLI.
Batches of SCORE_BATCH. Non-zero exit → deterministic fallback per event.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import structlog

from .config import SCORE_BATCH, SCORE_MODEL, THEMES, CLAUDE_CLI_PATH, CLAUDE_CLI_TIMEOUT_SEC
from .normalize import Event, Score
from .llm import call_claude, ClaudeCliError

log = structlog.get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "score_event.txt"


def _load_score_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text()
    kill_json = {k: v.kill_flags for k, v in THEMES.items()}
    return f"""You are a sell-side analyst scoring news events for thematic ETF tilts.

Output a JSON array of objects, one per event, IN ORDER matching input.
Each object: {{"salience": 0-100, "direction": -1|0|1, "rationale": "<=240 chars"}}

Salience scale:
0-20   noise / already priced / irrelevant
21-50  soft directional (small contract, minor policy update)
51-80  material (FDA AdCom, >$500M DoD contract, export-control rule)
81-100 regime-shift (approval/rejection, prime contract, Section 232)

Direction:
+1 raises forward FCF / multiple for theme's median constituent
-1 lowers forward FCF / multiple
 0 ambiguous / mixed

THEMES:
bio       : XBI — late-stage FDA decisions, M&A, PDUFA, clinical readouts
tech_ai   : SMH — hyperscaler capex, export controls, TSMC/NVDA/AMD
robotics  : ISRG+ABB — surgical robotics deployments, DoD autonomy, factory automation
energy    : URA+VST+LNG — uranium supply, NRC/SMR, nuclear PPAs, LNG export
space     : RKLB+ASTS — SDA awards, launch milestones, D2D commercial
rare_earth: MP+LYC+USAR — DPA Title III, China export controls, REE magnet supply
food_ag   : NTR+CTVA+DBA — WASDE, fertilizer cycle, RVO/biofuels, crop prices

KILL_FLAGS (if triggered, note in rationale):
{json.dumps(kill_json, indent=2)}

Return ONLY valid JSON array. No prose before or after.
"""


SCORE_SYSTEM_PROMPT = _load_score_prompt()


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i: i + n]


def _extract_json(text: str) -> str:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        return m.group(0)
    raise ValueError(f"No JSON array found in: {text[:200]}")


def _mock_score(e: Event, now_iso: str) -> Score:
    """Deterministic fallback when CLI call fails for an event."""
    return Score(
        event_id=e.id, theme=e.theme, scored_at=now_iso,
        salience=30, direction=0,
        rationale="[fallback] CLI scoring failed; neutral placeholder.",
        model="fallback",
    )


def score_events(events: list[Event]) -> list[Score]:
    """Score all events in batches via claude CLI. Returns Score list."""
    scored: list[Score] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for chunk in _chunks(events, SCORE_BATCH):
        result = _score_chunk(chunk, now_iso)
        scored.extend(result)
    return scored


def _score_chunk(chunk: list[Event], now_iso: str) -> list[Score]:
    user_lines = []
    for e in chunk:
        user_lines.append(
            f"[{e.id[:8]}] theme={e.theme} pub={e.published_at[:10]}\n"
            f"TITLE: {e.title}\nBODY: {e.body[:1500]}"
        )
    user_content = (
        f"Score these {len(chunk)} events. Return JSON array in order.\n\n"
        + "\n\n---\n\n".join(user_lines)
    )

    try:
        raw = call_claude(
            user_content,
            model=SCORE_MODEL,
            system=SCORE_SYSTEM_PROMPT,
            max_tokens=2000,
            timeout=CLAUDE_CLI_TIMEOUT_SEC,
            cli_path=CLAUDE_CLI_PATH,
        )
        arr = json.loads(_extract_json(raw))
        scores = []
        for e, s in zip(chunk, arr):
            try:
                scores.append(Score(
                    event_id=e.id, theme=e.theme, scored_at=now_iso,
                    salience=int(s.get("salience", 0)),
                    direction=int(s.get("direction", 0)),
                    rationale=str(s.get("rationale", ""))[:240],
                    model=SCORE_MODEL,
                ))
            except Exception as exc:
                log.warning("score_parse_row_failed", event_id=e.id[:8], err=str(exc))
                scores.append(_mock_score(e, now_iso))
        log.info("chunk_scored", n=len(scores))
        return scores
    except ClaudeCliError as exc:
        log.warning("score_chunk_cli_failed", err=str(exc), n=len(chunk))
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("score_chunk_parse_failed", err=str(exc), n=len(chunk))
    except Exception as exc:
        log.error("score_chunk_unexpected", err=str(exc), n=len(chunk))

    # Graceful degrade: return fallback scores for every event in chunk
    log.warning("score_chunk_fallback", n=len(chunk))
    return [_mock_score(e, now_iso) for e in chunk]
