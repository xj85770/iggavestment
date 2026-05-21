# Changelog

## [1.2.0] — 2026-05-21

Ten cheap-win scoring and telemetry fixes.

### Fix A — Force neutral when no signal
`synthesize.py`: when `event_count_7d < HAS_SIGNAL_MIN_EVENTS`, override
`conviction = 50` and `tilt_pct = 0.0` before AI-capex cap computation.
Effect: all 7 mock-feed themes drop from the uniform conviction=82 artifact
to 50 (no signal this week). Tests: `test_fix_a_*`.

### Fix B — Anchor scoring prompt to neutral baseline
`prompts/score_event.txt`: added explicit calibration note. Default score is
50 (neutral). Vague or merely-related events score 45–55, not 70+. Most
events are noise and should score 20–40. Reserve 70+ for regime-shift events.

### Fix C — Symmetric AI-capex cap
`synthesize.py apply_ai_capex_cap`: replaced tech_ai-biased branch logic with
symmetric pro-rata scaling. Both themes scale by `cap / combined_up`, so
tech_ai=3%+space=3% → both +1.5%; tech_ai=3%+space=1.5% → tech_ai≈2%+space≈1%.
Schema validator updated to accept pro-rata intermediate values within [-3, +3].
Tests: `test_fix_c_*`.

### Fix D — Real event_count_14d
`synthesize.py`: `scores_14d` (all 14-day events) and `scores_7d` (7-day
subset filtered by `scored_at`) are now separate. `event_count_14d` counts
from the full 14-day list; `event_count_7d` from the 7-day subset.
`event_count_14d >= event_count_7d` guaranteed. Tests: `test_fix_d_*`.

### Fix E — HAS_SIGNAL_MIN_EVENTS to config
`config.py`: `HAS_SIGNAL_MIN_EVENTS = 3` with env-var override
`IGG_HAS_SIGNAL_MIN_EVENTS`. Docstring explains: below 3 events is anecdote,
not cross-source signal. Tests: `test_fix_e_*`.

### Fix F — uv.lock frozen in CI
`refresh.yml`: `uv sync --extra ci` → `uv sync --frozen --extra ci`.
`uv.lock` was already committed; no `.gitignore` change needed.

### Fix G — Audit log committed by workflow
`refresh.yml` already includes `data/audit.jsonl` in the commit step
(`git add data/state.json data/history/ data/audit.jsonl || true`).
Confirmed present — no change required.

### Fix H — Sparkline placeholder copy
`index.html`: empty sparkline now renders `<em>Building history</em><br>week X
of 14` using the actual sparkline length as the week counter. Replaces the
blank "Building history" with a progress-indicating caption.

### Fix I — Cost telemetry in state.json and dashboard footer
`llm.py`: per-call token accumulator (`reset_usage` / `get_usage`). SDK
backend reads exact tokens from response.usage; CLI backend estimates from
prompt/response character length. `cli.py`: injects `state["meta"]` with
`refresh_cost_usd`, `refresh_duration_sec`, `events_processed`, `llm_calls`.
Schema: `StateV1.meta` field added. `index.html` footer now renders
"Last refresh: N events · $X.XXX · Xs · cron 5am/5pm PT" when meta is present.

### Fix J — Last-significant-change per theme
`synthesize.py _last_change_for_theme`: scans `data/history/` for the most
recent snapshot older than 7 days, computes delta vs current conviction.
Returns `{"direction": "up"|"down"|"flat"|"new", "delta": int, "since_date":
"YYYY-MM-DD"}`. Cache dict prevents re-reading snapshots per theme in the loop.
`index.html`: theme conviction row shows `↑ +12 since May 14` or `↓ −8 since
May 14` caption in teal/red/gray. Schema: `Theme.last_change` dict field added.
Tests: `test_fix_j_*`.

## [1.1.0] — 2026-05-21

Seven surgical fixes from third-party code review.

### Fix 1 — Dollar arithmetic ($210 → $250)
Reserve of $40/wk ($25 SGOV dry powder + $15 LEAP accumulation buffer) is now
surfaced explicitly in `state.json` as a top-level `reserve` block. Deploy block
adds `reserve_total`. `synthesize.py` absorbs per-theme rounding residuals into
reserve so `themes_total + reserve_total == $250.00` exactly on every run.

### Fix 2 — CI auth path (claude CLI not on ubuntu-latest)
New `iggavestment/llm.py` detects `ANTHROPIC_API_KEY` at startup and routes to
the anthropic Python SDK when present; falls back to claude CLI subprocess for
local OAuth dev sessions. `score.py` and `synthesize.py` now import from `llm`
instead of `claude_cli`. `anthropic` added as optional `[ci]` extra in
`pyproject.toml`. Workflow updated to `uv sync --extra ci`.

### Fix 3 — Schema gate before commit
`iggavestment/schema.py` adds `StateV1` pydantic model covering all fields,
dollar-sum constraint, and tilt_pct valid-set check. After `write_state`,
`cli.py` calls `validate_state()` and exits 1 on failure, writing
`data/state.invalid.json` without touching `data/state.json`. Workflow aborts
commit if `state.invalid.json` is present.

### Fix 4 — Kill switches wired to real history
`iggavestment/history.py` computes `max_drawdown_pct`, `rolling_hit_rate`, and
`alpha_drawdown_pct` from actual snapshots in `data/history/`. Below data
thresholds (4 weeks drawdown, 30 weeks hit-rate), values are `null` and
`kill_switches.status = "data_collecting"`. UI renders gray "Data collecting ·
week X of Y" chips instead of green/red when collecting.

### Fix 5 — DST drift (cron breaks 5 months/year)
`refresh.yml` now has four cron entries covering both PDT (UTC-7) and PST
(UTC-8). A recency guard in `cli.py` (`_is_too_recent`) skips runs where the
last refresh was < 5 hours ago, preventing double-runs during DST transitions.

### Fix 6 — Zero-event vs measured-neutral distinction
Each theme in state.json now carries `has_signal: bool`, `event_count_7d: int`,
`event_count_14d: int`, and `confidence: "high"|"medium"|"low"|"no_data"`.
UI theme cards render gray with "—" conviction and "n/a" tilt chip when
`confidence == "no_data"`. Low confidence shows a yellow dot; high shows green.

### Fix 7 — Sparkline cold-start + honest validation status
`build_sparkline` returns `[]` when `history_count < 4` instead of fabricating
neutral 50s. Metrics block adds `validation_status: "collecting"|"in_progress"|"live"`
and `weeks_collected`. Comparison metrics (`vs_spy_ytd_pct`, etc.) are `null`
until `validation_status == "live"`. UI shows a validation status bar when not
live and replaces sparklines with "Building history" placeholder on cold start.
