"""
Synthesize conviction scores into a dashboard state via Claude Haiku.
Tilt protocol: conviction → tilt → AI-capex cap.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
import structlog

from .config import (
    THEMES, SYNTH_MODEL, AI_CAPEX_THEMES, AI_CAPEX_CAP,
    TILT_TABLE, EQUAL_WEIGHT_USD, LOG_DIR,
)
from .normalize import Score

log = structlog.get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "synthesize_state.txt"


def _load_synth_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text()
    return """You are a buy-side portfolio analyst synthesizing weekly event scores into conviction ratings.

Given a JSON rollup of top scored events per theme, return a JSON object:

{
  "themes": {
    "<theme_key>": {
      "conviction": <0-100>,
      "thesis_state": "intact|weakening|threatened",
      "kill_flags_triggered": ["..."],
      "one_liner": "<=120 chars",
      "top_events": [{"title":"...","url":"...","salience":N,"direction":N,"rationale":"..."}]
    }
  },
  "stale": false,
  "degraded": false,
  "notes": "<=300 chars"
}

Rules:
- conviction = weighted mean of (salience × direction), mapped to 0-100 (50=neutral)
- Kill flags: match event text against theme's kill_flags
- Return ONLY valid JSON. No prose.
"""


SYNTH_SYSTEM_PROMPT = _load_synth_prompt()


# ── Tilt calculation ──────────────────────────────────────────────────────────

def conviction_to_tilt(conviction: int) -> float:
    for lo, hi, tilt in TILT_TABLE:
        if lo <= conviction <= hi:
            return tilt
    return 0.0


def apply_ai_capex_cap(tilts: dict[str, float]) -> dict[str, float]:
    result = dict(tilts)
    combined_up = sum(max(0, result.get(t, 0)) for t in AI_CAPEX_THEMES)
    if combined_up > AI_CAPEX_CAP:
        if result.get("tech_ai", 0) > 0:
            result["tech_ai"] = min(result.get("tech_ai", 0), AI_CAPEX_CAP)
            result["space"] = max(0, min(result.get("space", 0), AI_CAPEX_CAP - result["tech_ai"]))
        else:
            result["space"] = min(result.get("space", 0), AI_CAPEX_CAP)
    return result


def build_conviction_map(scores: list[Score]) -> dict[str, int]:
    """Aggregate raw event scores into per-theme conviction 0-100."""
    by_theme: dict[str, list[Score]] = {}
    for s in scores:
        by_theme.setdefault(s.theme, []).append(s)

    conviction_map: dict[str, int] = {}
    for theme_key in THEMES:
        theme_scores = by_theme.get(theme_key, [])
        if not theme_scores:
            conviction_map[theme_key] = 50  # neutral default
            continue
        raw = sum(s.salience * s.direction for s in theme_scores)
        n   = max(1, len(theme_scores))
        # Map [-100n, +100n] → [0, 100] with 50 as neutral
        conviction_map[theme_key] = max(0, min(100, int(50 + raw / n * 0.5)))
    return conviction_map


# ── Sparkline ─────────────────────────────────────────────────────────────────

def build_sparkline(current: int, history_values: list[int]) -> list[int]:
    """Return 14-point sparkline ending at current conviction."""
    filled = list(history_values[-13:]) if history_values else []
    while len(filled) < 13:
        filled.insert(0, 50)
    filled.append(current)
    return filled


# ── Main synthesize ───────────────────────────────────────────────────────────

def synthesize_state(
    scores: list[Score],
    client: anthropic.Anthropic | None,
    prior_state: dict | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Build the full state dict from scores.
    Returns the dict that will be written to data/state.json.
    """
    import zoneinfo
    from datetime import timedelta
    from .config import next_refresh_pt, DATA_DIR

    PT  = zoneinfo.ZoneInfo("America/Los_Angeles")
    now = datetime.now(timezone.utc)
    now_pt = now.astimezone(PT)

    conviction_map = build_conviction_map(scores)

    raw_tilts  = {k: conviction_to_tilt(v) for k, v in conviction_map.items()}
    capped     = apply_ai_capex_cap(raw_tilts)

    stale_flags = [
        p.name.replace("stale-", "").replace(".flag", "")
        for p in LOG_DIR.glob("stale-*.flag")
    ]
    any_stale = len(stale_flags) > 0

    # Try Claude synthesis for one-liners / thesis state
    theme_details: dict[str, dict] = {}
    degraded = False

    if dry_run or client is None:
        # Deterministic fallback for dry-run
        degraded = (client is None and not dry_run)
        for key, theme in THEMES.items():
            conv = conviction_map[key]
            theme_details[key] = {
                "conviction": conv,
                "thesis_state": "intact" if conv >= 55 else ("weakening" if conv >= 35 else "threatened"),
                "kill_flags_triggered": [],
                "one_liner": f"Dry-run fallback. Conviction {conv}/100 based on {len([s for s in scores if s.theme == key])} events.",
                "top_events": [
                    {
                        "title": s.rationale[:80] or "Event",
                        "url": "",
                        "salience": s.salience,
                        "direction": s.direction,
                        "rationale": s.rationale,
                    }
                    for s in sorted(
                        [s for s in scores if s.theme == key],
                        key=lambda x: x.salience * abs(x.direction),
                        reverse=True,
                    )[:3]
                ],
            }
    else:
        # Build rollup for Claude
        by_theme: dict[str, list[Score]] = {}
        for s in scores:
            by_theme.setdefault(s.theme, []).append(s)

        rollup = {
            key: {
                "conviction": conviction_map[key],
                "top_events": [
                    {"title": s.rationale[:120], "salience": s.salience, "direction": s.direction}
                    for s in sorted(by_theme.get(key, []), key=lambda x: x.salience, reverse=True)[:5]
                ],
                "kill_flags": THEMES[key].kill_flags,
                "thesis": THEMES[key].thesis,
            }
            for key in THEMES
        }

        payload: dict = {}
        for attempt in range(3):
            try:
                resp = client.messages.create(
                    model=SYNTH_MODEL,
                    max_tokens=2000,
                    system=[{"type": "text", "text": SYNTH_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content":
                        f"Synthesize conviction from this event rollup:\n{json.dumps(rollup, indent=2)}\n\nReturn JSON only."}],
                )
                raw = resp.content[0].text
                m   = re.search(r"\{.*\}", raw, re.DOTALL)
                if not m:
                    raise ValueError("No JSON object in response")
                payload = json.loads(m.group(0))
                log.info("synth_ok", cache_read=getattr(resp.usage, "cache_read_input_tokens", 0))
                break
            except anthropic.RateLimitError:
                if attempt < 2:
                    time.sleep(60)
                else:
                    degraded = True
            except Exception as exc:
                log.error("synth_failed", attempt=attempt, err=str(exc))
                if attempt == 2:
                    degraded = True
                else:
                    time.sleep(5)

        for key in THEMES:
            td = (payload.get("themes") or {}).get(key, {})
            theme_details[key] = {
                "conviction": conviction_map[key],
                "thesis_state": td.get("thesis_state", "intact"),
                "kill_flags_triggered": td.get("kill_flags_triggered", []),
                "one_liner": td.get("one_liner", ""),
                "top_events": td.get("top_events", [])[:5],
            }

    # Build sparklines from prior state history
    prior_themes = {}
    if prior_state:
        for t in prior_state.get("themes", []):
            prior_themes[t["id"]] = t.get("sparkline", [])

    # ── Assemble state.json ────────────────────────────────────────────────────

    roth_breakdown  = []
    taxable_breakdown = []
    roth_total = 0.0
    taxable_total = 0.0

    themes_out = []
    for key, theme in THEMES.items():
        conv    = conviction_map[key]
        tilt    = capped.get(key, 0.0)
        weekly  = round(theme.weekly_usd * (1 + tilt), 2)
        details = theme_details.get(key, {})
        sparkline = build_sparkline(conv, prior_themes.get(key, []))

        themes_out.append({
            "id": key,
            "name": theme.display,
            "conviction": conv,
            "tilt_pct": round(tilt * 100, 1),
            "weekly_dollars": weekly,
            "account": theme.account,
            "core_tickers": theme.core_tickers,
            "bench_tickers": theme.bench_tickers,
            "watch_tickers": theme.watch_tickers,
            "sparkline": sparkline,
            "why": details.get("one_liner") or theme.thesis[:120],
            "thesis_state": details.get("thesis_state", "intact"),
            "kill_flags_triggered": details.get("kill_flags_triggered", []),
            "top_events": details.get("top_events", []),
        })

        if theme.account == "Roth":
            roth_total += weekly
            roth_breakdown.append({"tickers": theme.core_tickers, "theme": theme.display, "amount": weekly})
        else:
            taxable_total += weekly
            taxable_breakdown.append({"tickers": theme.core_tickers, "theme": theme.display, "amount": weekly})

    kill_active = any(details.get("kill_flags_triggered") for details in theme_details.values())

    state = {
        "version": "1.0.0",
        "generated_at": now_pt.isoformat(),
        "next_refresh_at": next_refresh_pt(),
        "stale": any_stale,
        "stale_feeds": stale_flags,
        "degraded": degraded,
        "themes": themes_out,
        "deploy": {
            "roth_total": round(roth_total, 2),
            "taxable_total": round(taxable_total, 2),
            "roth_breakdown": roth_breakdown,
            "taxable_breakdown": taxable_breakdown,
            "leap_progress": {
                "current": prior_state.get("deploy", {}).get("leap_progress", {}).get("current", 0) if prior_state else 0,
                "target": 2500,
                "candidate": "MSFT",
            },
        },
        "kill_switches": {
            "max_drawdown_pct": -8.5,
            "rolling_hit_rate": 0.52,
            "alpha_drawdown_pct": -1.2,
            "triggered": kill_active,
            "warnings": [],
        },
        "metrics": {
            "total_contributed_ytd": prior_state.get("metrics", {}).get("total_contributed_ytd", 0) if prior_state else 0,
            "total_value": prior_state.get("metrics", {}).get("total_value", 0) if prior_state else 0,
            "vs_spy_ytd_pct": 0.0,
            "vs_equal_weight_pct": 0.0,
            "tilt_alpha_ytd_pct": 0.0,
        },
    }

    return state
