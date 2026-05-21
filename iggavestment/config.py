"""
Config: themes, tickers, feed specs, Anthropic client factory.
All values come from env vars or .env — no hardcoded secrets.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any

from dotenv import load_dotenv

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR  = Path(__file__).parent.parent          # repo root
DATA_DIR  = BASE_DIR / "data"
HIST_DIR  = DATA_DIR / "history"
AUDIT_LOG = DATA_DIR / "audit.jsonl"
STATE_JSON = DATA_DIR / "state.json"
LOG_DIR   = BASE_DIR / "logs"

DATA_DIR.mkdir(parents=True, exist_ok=True)
HIST_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Env ───────────────────────────────────────────────────────────────────────

load_dotenv(BASE_DIR / ".env")

UA = "Iggavestment/1.0 (+https://github.com/xavierdjones/iggavestment; research-pipeline)"

# ── Theme definitions ──────────────────────────────────────────────────────────

@dataclass
class ThemeDef:
    key: str
    display: str
    account: str              # "Roth" | "Taxable"
    core_tickers: list[str]
    bench_tickers: list[str]
    watch_tickers: list[str]
    weekly_usd: float
    thesis: str
    kill_flags: list[str]

THEMES: dict[str, ThemeDef] = {
    "bio": ThemeDef(
        key="bio",
        display="Biotech (XBI)",
        account="Roth",
        core_tickers=["XBI"],
        bench_tickers=["IBB", "LLY", "VRTX", "CRSP"],
        watch_tickers=["ARKG", "MRNA"],
        weekly_usd=40.0,
        thesis="Small-cap M&A premium via equal-weight XBI; patent-cliff urgency drives structural M&A bid through 2026-2027.",
        kill_flags=[
            "FDA leadership rejection wave",
            "accelerated approval rollback",
            "XBI AUM below $400M",
        ],
    ),
    "tech_ai": ThemeDef(
        key="tech_ai",
        display="Tech/AI Semis (SMH)",
        account="Roth",
        core_tickers=["SMH"],
        bench_tickers=["SOXX", "XSD", "AVGO", "TSM"],
        watch_tickers=["MRVL", "ASML"],
        weekly_usd=40.0,
        thesis="AI accelerator + foundry buildout; hyperscaler capex record run; Blackwell/Rubin ramp.",
        kill_flags=[
            "hyperscaler capex digestion",
            "capex flat or down",
            "custom silicon inflection",
            "export controls escalation",
        ],
    ),
    "robotics": ThemeDef(
        key="robotics",
        display="Robotics (ISRG + ABB)",
        account="Roth",
        core_tickers=["ISRG", "ABB"],
        bench_tickers=["SYM", "KTOS", "ROBO"],
        watch_tickers=["TSLA"],
        weekly_usd=25.0,
        thesis="Surgical and industrial robotics direct names; ISRG da Vinci 5 ramp + ABB factory automation.",
        kill_flags=[
            "ISRG miss on da Vinci",
            "humanoid hype cycle break",
            "Japan yen strength reversal",
        ],
    ),
    "energy": ThemeDef(
        key="energy",
        display="Energy/Nuclear/LNG",
        account="Roth",
        core_tickers=["URA", "VST", "LNG"],
        bench_tickers=["URNM", "CEG", "LEU", "OKLO"],
        watch_tickers=["TLN", "NNE"],
        weekly_usd=30.0,
        thesis="AI-datacenter nuclear renaissance + LNG export tolling; uranium supply tightness; SMR optionality.",
        kill_flags=[
            "Kazakh supply surge",
            "SMR cancellation",
            "SMR cost blowout",
            "hyperscaler PPA renegotiation",
            "gas glut",
        ],
    ),
    "space": ThemeDef(
        key="space",
        display="Space (RKLB/ASTS)",
        account="Taxable",
        core_tickers=["RKLB", "ASTS"],
        bench_tickers=["PL", "LUNR", "IRDM"],
        watch_tickers=["UFO"],
        weekly_usd=30.0,
        thesis="Launch + D2D + EO + cislunar direct basket; SDA proliferated-LEO architecture; ASTS commercial service ramp.",
        kill_flags=[
            "RKLB Neutron failure",
            "RKLB Neutron 2027 slip",
            "ASTS secondary offering",
            "DoD CR SDA budget reset",
        ],
    ),
    "rare_earth": ThemeDef(
        key="rare_earth",
        display="Rare Earth (MP/LYC/USAR)",
        account="Taxable",
        core_tickers=["MP", "LYSCF", "USAR"],
        bench_tickers=["UUUU", "IPX", "REMX"],
        watch_tickers=["Centrus (LEU)", "Iluka (ILKAY)"],
        weekly_usd=25.0,
        thesis="Ex-China REE supply buildout; China export-license friction bifurcates price; DPA Title III catalyst.",
        kill_flags=[
            "China-US grand bargain critical minerals",
            "magnet substitution recycling OEM scale",
            "lithium re-crash",
        ],
    ),
    "food_ag": ThemeDef(
        key="food_ag",
        display="Food/Ag (NTR/CTVA/DBA)",
        account="Taxable",
        core_tickers=["NTR", "CTVA", "DBA"],
        bench_tickers=["MOS", "CF", "BG"],
        watch_tickers=["IPI", "YARIY"],
        weekly_usd=20.0,
        thesis="Potash structural recovery + biofuel RVO mandate; NTR record potash volumes; CTVA trait pipeline.",
        kill_flags=[
            "El Nino bumper harvest",
            "China dumping reserves",
            "Russia-Ukraine ceasefire fertilizer normalization",
        ],
    ),
}

# ── Feed registry placeholder (populated by fetch.py) ────────────────────────

@dataclass
class FeedSpec:
    source: str
    theme: str
    kind: str           # "rss" | "json" | "html"
    url: str
    json_extractor: Callable[[Any], list[dict]] | None = field(default=None, repr=False)
    html_extractor: Callable[[Any], list[dict]] | None = field(default=None, repr=False)
    description: str = ""

FEED_REGISTRY: list[FeedSpec] = []   # populated by fetch.py at import

# ── Model config ──────────────────────────────────────────────────────────────

SCORE_MODEL  = "claude-haiku-4-5"
SYNTH_MODEL  = "claude-haiku-4-5"    # cost-frugal; upgrade to sonnet if needed
SCORE_BATCH  = 20
MAX_EVENTS   = 1000

# ── Tilt table ────────────────────────────────────────────────────────────────

TILT_TABLE = [
    (0,  35, -0.03),
    (36, 55,  0.00),
    (56, 75,  0.015),
    (76, 100, 0.03),
]

AI_CAPEX_THEMES = {"tech_ai", "space"}
AI_CAPEX_CAP    = 0.03

EQUAL_WEIGHT_USD = 250.0 / len(THEMES)   # ~$35.71

# ── Next refresh helper ───────────────────────────────────────────────────────

def next_refresh_pt() -> str:
    """Return ISO8601 PT timestamp of the next 5am or 5pm PT refresh."""
    from datetime import datetime, timezone, timedelta
    import zoneinfo
    PT = zoneinfo.ZoneInfo("America/Los_Angeles")
    now_pt = datetime.now(PT)
    for h in [5, 17]:
        candidate = now_pt.replace(hour=h, minute=0, second=0, microsecond=0)
        if candidate > now_pt:
            return candidate.isoformat()
    # Tomorrow 5am
    tomorrow = now_pt.replace(hour=5, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return tomorrow.isoformat()

# ── Anthropic client factory ──────────────────────────────────────────────────

def get_anthropic_client():
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set. Add it to .env or GH Secrets.")
    return anthropic.Anthropic(api_key=key)
