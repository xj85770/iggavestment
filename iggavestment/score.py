"""
Score events via Claude Haiku with cached system prompt.
Batches of SCORE_BATCH. 429 → 60s backoff x3.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import structlog

from .config import SCORE_BATCH, SCORE_MODEL, THEMES
from .normalize import Event, Score

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


def score_events(events: list[Event], client: anthropic.Anthropic) -> list[Score]:
    """Score all events in batches. Returns Score list."""
    scored: list[Score] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for chunk in _chunks(events, SCORE_BATCH):
        result = _score_chunk(chunk, client, now_iso)
        scored.extend(result)
    return scored


def _score_chunk(chunk: list[Event], client: anthropic.Anthropic, now_iso: str) -> list[Score]:
    user_lines = []
    for e in chunk:
        user_lines.append(
            f"[{e.id[:8]}] theme={e.theme} pub={e.published_at[:10]}\n"
            f"TITLE: {e.title}\nBODY: {e.body[:1500]}"
        )
    user_content = f"Score these {len(chunk)} events. Return JSON array in order.\n\n" + "\n\n---\n\n".join(user_lines)

    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=SCORE_MODEL,
                max_tokens=2000,
                system=[{
                    "type": "text",
                    "text": SCORE_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_content}],
            )
            raw = resp.content[0].text
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
            log.info("chunk_scored", n=len(scores),
                     cache_read=getattr(resp.usage, "cache_read_input_tokens", 0))
            return scores
        except anthropic.RateLimitError:
            if attempt < 2:
                log.warning("rate_limit_backoff", attempt=attempt)
                time.sleep(60)
            else:
                log.error("score_chunk_rate_limit_exhausted", n=len(chunk))
                return []
        except anthropic.APIStatusError as exc:
            if attempt < 2:
                log.warning("api_error_retry", attempt=attempt, status=exc.status_code)
                time.sleep(10 * (attempt + 1))
            else:
                log.error("score_chunk_api_failed", err=str(exc), n=len(chunk))
                return []
        except (json.JSONDecodeError, ValueError) as exc:
            log.error("score_parse_failed", err=str(exc))
            return []
    return []
