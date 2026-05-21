"""
CLI:
  iggavestment refresh           — full pipeline → writes data/state.json
  iggavestment refresh --dry-run — mock feeds, no API key needed
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

from .config import LOG_DIR, THEMES, DATA_DIR
from .db import RunAccumulator
from .fetch import fetch_all_feeds, FEED_REGISTRY
from .normalize import normalize_batch, RawDoc, Event, Score
from .score import score_events
from .synthesize import synthesize_state
from .render import write_state, write_history_snapshot, append_audit_log, load_prior_state

log = structlog.get_logger(__name__)


def _setup_logging(date_str: str) -> None:
    log_path = LOG_DIR / f"{date_str}.log"
    logging.basicConfig(
        level=logging.INFO,
        handlers=[
            logging.FileHandler(str(log_path)),
            logging.StreamHandler(sys.stdout),
        ],
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _make_mock_docs() -> list[RawDoc]:
    """Synthetic events for dry-run / testing."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    data = [
        ("bio",
         "FDA approves PDUFA drug — NDA complete response favorable",
         "The FDA issued a complete response allowing NDA approval for phase 3 oncology drug. Significant M&A premium expected for XBI constituents. ASCO June 2026 catalyst window."),
        ("tech_ai",
         "NVDA Q1 FY27 revenue $44B — hyperscaler capex record $650B forecast",
         "Blackwell ramp continues. MSFT/AMZN/META/GOOG combined FY26 capex tracking $650-700B. Supply-constrained demand signals sustained SMH outperformance."),
        ("robotics",
         "DoD awards $800M Replicator 2.0 autonomous systems contract",
         "Contract covers attritable drones and JADC2 networking. AeroVironment and Kratos leading. ABB supply chain benefits from automation demand."),
        ("energy",
         "Centrus $900M DOE HALEU task order — enrichment bottleneck easing",
         "DOE task order to expand Piketon to commercial-scale HALEU. NRC SMR licensing timeline accelerating. URA beneficiary via 4.2% Centrus position."),
        ("space",
         "RKLB wins $805M SDA Tranche 3 Tracking Layer contract",
         "First prime award for Rocket Lab. 72 satellite constellation for missile tracking. Validates proliferated-LEO architecture and ASTS commercial ramp."),
        ("rare_earth",
         "MP Materials DoD $400M convertible preferred — NdFeB plant Fort Worth online",
         "Pentagon $400M convertible at $30.03 per share, 7% PIK. $110/kg NdPr 10-year price floor. Magnet production Dec 2025. China export pause through Nov 2026."),
        ("food_ag",
         "USDA WASDE-671: potash demand record Q1 — NTR guidance reaffirmed 14.1Mt",
         "Canpotex fully committed through June. Record potash volumes. EPA RVO biofuel mandate 60% increase drives soy oil demand. NTR Q1 realized $286/tonne."),
        ("bio",
         "Phase 3 ASCO readout — oncology trial primary endpoint met in small-cap XBI name",
         "M&A premium catalyzed. Oncology readout June 2026 ASCO. XBI calendar overlay positive May-June window."),
        ("energy",
         "Uranium spot $84.50/lb — Kazatomprom value-over-volume stance reaffirmed",
         "KAP Q1 production 29,697 tU3O8 vs 32,777 target. Uncovered utility requirements at record. URA tilt justified by supply tightness thesis."),
        ("tech_ai",
         "BIS export controls tightened — H20 ban absorbed in NVDA Q2 guide $45B",
         "Federal Register BIS rules tighten AI chip exports. NVDA Q2 guide $45B absorbing H20 ban impact. SMH headwind short term; thesis intact on domestic demand."),
    ]
    docs = []
    for i, (theme, title, body) in enumerate(data):
        docs.append(RawDoc(
            source=f"mock_{theme}_{i}",
            theme=theme,
            url=f"https://example.com/mock/{i}",
            title=title,
            body=body,
            published_at=datetime.now(timezone.utc),
        ))
    return docs


def _pipeline(dry_run: bool = False) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    _setup_logging(today)

    run = RunAccumulator()
    prior_state = load_prior_state()

    # 1. Fetch
    if dry_run:
        click.echo("DRY RUN — using mock feeds, no API key required")
        raw_docs = _make_mock_docs()
    else:
        from .config import MAX_EVENTS
        log.info("pipeline_fetch_start", feeds=len(FEED_REGISTRY))
        raw_docs = asyncio.run(fetch_all_feeds())
        log.info("pipeline_fetched", n=len(raw_docs))

    # 2. Normalize
    events = normalize_batch(raw_docs)
    new_count = run.add_events(events)
    log.info("pipeline_normalized", total=len(events), new=new_count)

    if not dry_run:
        from .config import MAX_EVENTS
        if len(run.events) > MAX_EVENTS:
            log.warning("event_cap", n=len(run.events), cap=MAX_EVENTS)
            run.events = run.events[:MAX_EVENTS]

    # 3. Score
    client = None
    if not dry_run:
        from .config import get_anthropic_client
        client = get_anthropic_client()

    if run.events:
        if dry_run:
            # Build mock scores from mock docs (deterministic)
            from .normalize import Score as _Score
            now_iso = datetime.now(timezone.utc).isoformat()
            mock_scores = []
            for e in run.events:
                mock_scores.append(_Score(
                    event_id=e.id, theme=e.theme, scored_at=now_iso,
                    salience=65, direction=1,
                    rationale=f"Mock score: {e.title[:60]}",
                    model="mock",
                ))
            run.add_scores(mock_scores)
            log.info("pipeline_mock_scored", n=len(mock_scores))
        else:
            log.info("pipeline_scoring", n=len(run.events))
            scored = score_events(run.events, client)
            run.add_scores(scored)
            log.info("pipeline_scored", n=len(run.scores))
    else:
        log.warning("no_events_to_score")

    # 4. Synthesize
    log.info("pipeline_synth_start")
    state = synthesize_state(
        scores=run.scores,
        client=client,
        prior_state=prior_state,
        dry_run=dry_run,
    )

    # 5. Write outputs
    state_path = write_state(state)
    hist_path  = write_history_snapshot(state)

    # 6. Audit log
    append_audit_log({
        "run_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "events_fetched": len(raw_docs),
        "events_normalized": len(events),
        "events_scored": len(run.scores),
        "themes": {t["id"]: t["conviction"] for t in state["themes"]},
        "stale_feeds": state.get("stale_feeds", []),
        "degraded": state.get("degraded", False),
    })

    click.echo(f"\nstate.json written: {state_path}")
    click.echo(f"history snapshot:  {hist_path}")
    click.echo(f"themes updated:    {len(state['themes'])}")
    click.echo(f"generated_at:      {state['generated_at']}")
    click.echo(f"next_refresh_at:   {state['next_refresh_at']}")
    if state.get("stale_feeds"):
        click.echo(f"stale feeds:       {', '.join(state['stale_feeds'])}")
    if state.get("degraded"):
        click.echo("*** DEGRADED — API unavailable, using deterministic fallback ***")
    if state.get("kill_switches", {}).get("triggered"):
        click.echo("*** KILL SWITCH ACTIVE — equal-weight DCA in effect ***")


@click.group()
def cli():
    """Iggavestment — twice-daily investment conviction dashboard pipeline."""
    pass


@cli.command("refresh")
@click.option("--dry-run", is_flag=True, default=False,
              help="Use mock feeds. No ANTHROPIC_API_KEY required.")
def cmd_refresh(dry_run: bool):
    """Fetch feeds → score → synthesize → write data/state.json."""
    _pipeline(dry_run=dry_run)


@cli.command("show")
def cmd_show():
    """Print current state.json to stdout."""
    from .config import STATE_JSON
    if not STATE_JSON.exists():
        click.echo("No state.json yet. Run `iggavestment refresh` first.")
        return
    state = json.loads(STATE_JSON.read_text())
    click.echo(f"generated_at: {state['generated_at']}")
    click.echo(f"themes:")
    for t in state["themes"]:
        click.echo(f"  {t['id']:12s}  conv={t['conviction']:3d}  tilt={t['tilt_pct']:+.1f}%  ${t['weekly_dollars']:.2f}/wk")
    click.echo(f"\ndeploy roth: ${state['deploy']['roth_total']:.2f}")
    click.echo(f"deploy taxable: ${state['deploy']['taxable_total']:.2f}")
