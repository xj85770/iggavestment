"""
Synthesize conviction scores into a dashboard state via Claude Haiku.
Tilt protocol: conviction → tilt → AI-capex cap.

Fixes:
  FIX 1 — Reserve ($40/wk) surfaced explicitly; themes+reserve = $250.
  FIX 4 — Kill switches computed from real history (history.py), not literals.
  FIX 6 — Zero-event vs measured-neutral: has_signal, event_count_7d/14d, confidence.
  FIX 7 — Sparkline cold-start: empty list when <4 snapshots; honest metrics.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from .config import (
    THEMES, SYNTH_MODEL, AI_CAPEX_THEMES, AI_CAPEX_CAP,
    TILT_TABLE, EQUAL_WEIGHT_USD, LOG_DIR,
    CLAUDE_CLI_PATH, CLAUDE_CLI_TIMEOUT_SEC,
    HAS_SIGNAL_MIN_EVENTS,
)
from .normalize import Score
from .llm import call_claude, ClaudeCliError

log = structlog.get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "synthesize_state.txt"

# ── FIX 1: reserve constants ──────────────────────────────────────────────────

WEEKLY_BUDGET       = 250.0
SGOV_DRY_POWDER     = 25.00
LEAP_ACCUMULATION   = 15.00
RESERVE_TOTAL       = SGOV_DRY_POWDER + LEAP_ACCUMULATION   # 40.00

_theme_sum = sum(t.weekly_usd for t in THEMES.values())
assert abs(_theme_sum + RESERVE_TOTAL - WEEKLY_BUDGET) < 0.01, (
    f"CONFIG ERROR: theme_sum ${_theme_sum} + reserve ${RESERVE_TOTAL} "
    f"= ${_theme_sum + RESERVE_TOTAL}, expected ${WEEKLY_BUDGET}"
)


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
    """
    FIX C — Symmetric pro-rata cap.
    When combined positive tilt across AI_CAPEX_THEMES exceeds AI_CAPEX_CAP,
    scale each theme's positive tilt proportionally so the sum == AI_CAPEX_CAP.
    Negative tilts (de-risk) are unaffected.
    """
    result = dict(tilts)
    combined_up = sum(max(0.0, result.get(t, 0.0)) for t in AI_CAPEX_THEMES)
    if combined_up > AI_CAPEX_CAP:
        factor = AI_CAPEX_CAP / combined_up
        for t in AI_CAPEX_THEMES:
            if result.get(t, 0.0) > 0:
                result[t] = round(result[t] * factor, 4)
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
        conviction_map[theme_key] = max(0, min(100, int(50 + raw / n * 0.5)))
    return conviction_map


# ── Sparkline (FIX 7) ─────────────────────────────────────────────────────────

def build_sparkline(
    current: int,
    history_values: list[int],
    history_count: int = 0,
) -> list[int]:
    """
    Return sparkline ending at current conviction.
    If history_count < 4, return empty list (cold-start — no fabricated 50s).
    Otherwise pad to 14 points using available history.
    """
    if history_count < 4:
        return []
    filled = list(history_values[-13:]) if history_values else []
    while len(filled) < 13:
        filled.insert(0, 50)
    filled.append(current)
    return filled


# ── FIX 6 + FIX D: signal quality and real 14d count ─────────────────────────

def _count_events_for_theme(scores: list[Score], theme_key: str) -> int:
    """Count distinct events for a theme in the 7-day window."""
    return sum(1 for s in scores if s.theme == theme_key)


def _count_events_14d(scores: list[Score], theme_key: str, scores_14d: list[Score]) -> int:
    """
    FIX D — Count events in the full 14-day window.
    scores_14d is the superset; scores is the 7-day subset.
    Always >= event_count_7d.
    """
    return sum(1 for s in scores_14d if s.theme == theme_key)


def _derive_confidence(event_count_7d: int) -> str:
    if event_count_7d >= 10:
        return "high"
    if event_count_7d >= 4:
        return "medium"
    if event_count_7d >= 1:
        return "low"
    return "no_data"


def _last_change_for_theme(
    theme_key: str,
    current_conviction: int,
    history_dir: "Path",
    _cache: dict | None = None,
) -> dict:
    """
    Cached wrapper: loads all old snapshots once, returns per-theme last_change.
    Pass _cache={} on first call; subsequent calls reuse it.
    """
    import json as _json
    CACHE_KEY = "__loaded__"
    if _cache is None:
        _cache = {}
    if CACHE_KEY not in _cache:
        # Find the most recent snapshot > 7 days old once
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        old_snap: dict | None = None
        old_date: str = ""
        if history_dir.exists():
            for p in reversed(sorted(history_dir.glob("*.json"))):
                try:
                    data = _json.loads(p.read_text(encoding="utf-8"))
                    from dateutil.parser import parse as _parse
                    gen_at = _parse(data.get("generated_at", ""))
                    if gen_at.tzinfo is None:
                        gen_at = gen_at.replace(tzinfo=timezone.utc)
                    if gen_at < cutoff:
                        old_snap = {t["id"]: t["conviction"] for t in data.get("themes", [])}
                        old_date = gen_at.date().isoformat()
                        break
                except Exception:
                    continue
        _cache[CACHE_KEY] = (old_snap, old_date)

    old_convictions, old_date = _cache[CACHE_KEY]
    if old_convictions is None or theme_key not in old_convictions:
        return {"direction": "new", "delta": 0, "since_date": ""}
    old_conv = old_convictions[theme_key]
    delta = current_conviction - old_conv
    if delta > 2:
        direction = "up"
    elif delta < -2:
        direction = "down"
    else:
        direction = "flat"
    return {"direction": direction, "delta": delta, "since_date": old_date}


# ── FIX 7: validation status ──────────────────────────────────────────────────

WEEKS_COLLECTING  = 30
WEEKS_IN_PROGRESS = 104


def _derive_validation_status(weeks: int) -> str:
    if weeks < WEEKS_COLLECTING:
        return "collecting"
    if weeks < WEEKS_IN_PROGRESS:
        return "in_progress"
    return "live"


# ── Main synthesize ───────────────────────────────────────────────────────────

def synthesize_state(
    scores: list[Score],
    client: Any = None,          # kept for compat; ignored — routing via llm.py
    prior_state: dict | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Build the full state dict from scores.
    Returns the dict that will be written to data/state.json.
    """
    import zoneinfo
    from datetime import timedelta
    from .config import next_refresh_pt, DATA_DIR, HIST_DIR

    PT  = zoneinfo.ZoneInfo("America/Los_Angeles")
    now = datetime.now(timezone.utc)
    now_pt = now.astimezone(PT)

    conviction_map = build_conviction_map(scores)

    # FIX A: override conviction to neutral for themes below signal threshold.
    # Applied BEFORE tilt and cap so the cap arithmetic is clean.
    # We need a preliminary event count here; full 7d/14d split happens later.
    _pre_7d_counts = {key: sum(1 for s in scores if s.theme == key) for key in THEMES}
    for key in THEMES:
        if _pre_7d_counts[key] < HAS_SIGNAL_MIN_EVENTS:
            conviction_map[key] = 50

    raw_tilts  = {k: conviction_to_tilt(v) for k, v in conviction_map.items()}
    capped     = apply_ai_capex_cap(raw_tilts)

    stale_flags = [
        p.name.replace("stale-", "").replace(".flag", "")
        for p in LOG_DIR.glob("stale-*.flag")
    ]
    any_stale = len(stale_flags) > 0

    # Count history snapshots for sparkline/validation gating
    history_count = len(list(HIST_DIR.glob("*.json"))) if HIST_DIR.exists() else 0

    # Try Claude synthesis for one-liners / thesis state
    theme_details: dict[str, dict] = {}
    degraded = False

    if dry_run:
        degraded = False
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
        synth_prompt = (
            f"Synthesize conviction from this event rollup:\n"
            f"{json.dumps(rollup, indent=2)}\n\nReturn JSON only."
        )
        try:
            raw = call_claude(
                synth_prompt,
                model=SYNTH_MODEL,
                system=SYNTH_SYSTEM_PROMPT,
                max_tokens=2000,
                timeout=CLAUDE_CLI_TIMEOUT_SEC,
                cli_path=CLAUDE_CLI_PATH,
            )
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                raise ValueError("No JSON object in response")
            payload = json.loads(m.group(0))
            log.info("synth_ok")
        except (ClaudeCliError, ValueError, json.JSONDecodeError) as exc:
            log.error("synth_failed", err=str(exc))
            degraded = True

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

    # FIX D: separate 7-day and 14-day score lists
    from datetime import timedelta
    cutoff_7d = now - timedelta(days=7)
    scores_14d = scores   # fetch already limits to 14 days; all scores are ≤14d
    scores_7d  = [
        s for s in scores
        if s.scored_at >= cutoff_7d.isoformat()
    ]

    # FIX J: pre-load history once for last_change computation
    _lc_cache: dict = {}

    # ── Assemble themes ────────────────────────────────────────────────────────

    roth_breakdown  = []
    taxable_breakdown = []
    roth_total = 0.0
    taxable_total = 0.0

    themes_out = []
    for key, theme in THEMES.items():
        # FIX A: override conviction/tilt to neutral when below signal threshold
        event_count_7d  = _count_events_for_theme(scores_7d, key)
        event_count_14d = _count_events_14d(scores, key, scores_14d)
        has_signal      = event_count_7d >= HAS_SIGNAL_MIN_EVENTS

        if not has_signal:
            conv = 50
            tilt = 0.0
        else:
            conv = conviction_map[key]
            tilt = capped.get(key, 0.0)

        weekly  = round(theme.weekly_usd * (1 + tilt), 2)
        details = theme_details.get(key, {})
        sparkline = build_sparkline(conv, prior_themes.get(key, []), history_count)

        confidence      = _derive_confidence(event_count_7d)

        # FIX J: last_change vs snapshot >7 days old
        last_change = _last_change_for_theme(key, conv, HIST_DIR, _lc_cache)

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
            "has_signal": has_signal,
            "event_count_7d": event_count_7d,
            "event_count_14d": event_count_14d,
            "confidence": confidence,
            "last_change": last_change,
        })

        if theme.account == "Roth":
            roth_total += weekly
            roth_breakdown.append({"tickers": theme.core_tickers, "theme": theme.display, "amount": weekly})
        else:
            taxable_total += weekly
            taxable_breakdown.append({"tickers": theme.core_tickers, "theme": theme.display, "amount": weekly})

    kill_active = any(details.get("kill_flags_triggered") for details in theme_details.values())

    # FIX 4: compute real kill switches from history
    from .history import compute_kill_switches
    kill_switches = compute_kill_switches(HIST_DIR, {})
    # Merge LLM-extracted kill flags into the result
    if kill_active:
        kill_switches["triggered"] = True
    if kill_active and "LLM kill flags active" not in kill_switches.get("warnings", []):
        kill_switches.setdefault("warnings", []).append("LLM kill flags active")

    # FIX 7: honest metrics + validation status
    weeks_collected = history_count // 2   # ~2 snapshots/day → weekly cadence
    validation_status = _derive_validation_status(weeks_collected)
    prior_metrics = prior_state.get("metrics", {}) if prior_state else {}

    # Only expose comparison metrics when validation_status == "live"
    if validation_status == "live":
        vs_spy = prior_metrics.get("vs_spy_ytd_pct")
        vs_ew  = prior_metrics.get("vs_equal_weight_pct")
        tilt_alpha = prior_metrics.get("tilt_alpha_ytd_pct")
    else:
        vs_spy = None
        vs_ew  = None
        tilt_alpha = None

    # FIX 1: compute reserve_total tightly so schema sum-check passes.
    # weekly_dollars are rounded individually — sum may differ from _theme_sum by cents.
    themes_deployed = round(sum(t["weekly_dollars"] for t in themes_out), 2)
    # Reserve absorbs the rounding residual so themes + reserve == exactly $250.
    reserve_total_adj = round(WEEKLY_BUDGET - themes_deployed, 2)
    # Split the adjustment proportionally (SGOV takes the rounding residual).
    sgov_adj  = round(SGOV_DRY_POWDER + (reserve_total_adj - RESERVE_TOTAL), 2)
    leap_adj  = LEAP_ACCUMULATION

    state = {
        "version": "1.0.0",
        "generated_at": now_pt.isoformat(),
        "next_refresh_at": next_refresh_pt(),
        "stale": any_stale,
        "stale_feeds": stale_flags,
        "degraded": degraded,
        "themes": themes_out,
        "reserve": {
            "sgov_dry_powder": sgov_adj,
            "leap_accumulation": leap_adj,
            "total_reserve": reserve_total_adj,
        },
        "deploy": {
            "roth_total": round(roth_total, 2),
            "taxable_total": round(taxable_total, 2),
            "reserve_total": reserve_total_adj,
            "roth_breakdown": roth_breakdown,
            "taxable_breakdown": taxable_breakdown,
            "leap_progress": {
                "current": prior_state.get("deploy", {}).get("leap_progress", {}).get("current", 0) if prior_state else 0,
                "target": 2500,
                "candidate": "MSFT",
            },
        },
        "kill_switches": kill_switches,
        "metrics": {
            "total_contributed_ytd": prior_metrics.get("total_contributed_ytd", 0),
            "total_value": prior_metrics.get("total_value", 0),
            "vs_spy_ytd_pct": vs_spy,
            "vs_equal_weight_pct": vs_ew,
            "tilt_alpha_ytd_pct": tilt_alpha,
            "validation_status": validation_status,
            "weeks_collected": weeks_collected,
        },
    }

    return state
