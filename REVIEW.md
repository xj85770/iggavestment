# Iggavestment — Third-Party Review

**Reviewer mandate:** cold review, capital at risk, no false balance.
**Repo state at review:** single commit `0925e89 init iggavestment` — never run in CI, never deployed. All conclusions below come from reading source + exercising the local server at `http://localhost:8765`.

---

## 1. One-paragraph verdict

**Shelve until the math is right, the CI path is proven, and the kill-switches are wired to real data.** This is a tasteful UI sitting on top of a pipeline whose deepest plumbing has never actually run end-to-end. The headline allocation claim is wrong out of the gate: the dashboard markets "$250/week DCA" but the configured weekly totals across the seven themes sum to **$210** before tilts and **$210.97** in the live `state.json` right now — a 15.6% silent under-deployment that compounds weekly. The kill-switch numbers (`-8.5%` drawdown, `0.52` hit-rate, `-1.2%` α-drawdown) are hardcoded literals in `synthesize.py:289-294`, not computed from any portfolio data, so the "kill switch" is decorative. 16 of 22 feeds are flagged stale right now and the system has no concept of "broken feed" vs "transiently stale," so the UI's stale banner will become wallpaper. The CI workflow has never been exercised — schedule is wrong for half the year (DST), the auth path was designed for two different mechanisms simultaneously (`ANTHROPIC_API_KEY` env vs OAuth `claude` CLI) without verifying either works on `ubuntu-latest`, and the bot has `contents: write` permission to push to `main` on every cron tick with no review gate. The single best part — the scoring + synthesis layering through a local Claude subprocess — is overshadowed by the fact that there is no measurable edge claim, no backtest, no benchmark, and no exit criterion. This is a tracker dressed up as a strategy. Don't put real money behind it until items 1, 2, 4, 6, 8 in the top-10 are fixed.

---

## 2. 25-criterion rubric

| # | Criterion | Score | Finding |
|---|---|---|---|
| **A. Data integrity** | | | |
| 1 | Source reliability | weak | Mix of authoritative (EDGAR, FederalRegister, ClinicalTrials.gov) and noisy (SpaceNews RSS, EPA newsroom). No source weighting. `fetch.py:285-307` |
| 2 | Feed dedup | weak | Dedup is `sha256(title[:256] + body[:512])` in `normalize.py:104` — same FDA approval reported by FDA RSS and FederalRegister will have different titles/bodies and be counted twice. No cross-source event clustering. |
| 3 | Time-zone handling | weak | `generated_at` is PT, `published_at` is UTC, cron is UTC, `next_refresh_pt()` (`config.py:201`) computes PT — coherent at code level but cron is hardcoded for **PDT only** (see #15). |
| 4 | Stale data detection | weak | UI sets dot to red after 24h and shows "⚠ stale feeds" badge, but ANY feed failure flips `stale: true` (`synthesize.py:140`). With 16/22 feeds already stale in committed `state.json`, the banner is permanent and conveys nothing. |
| 5 | Schema versioning | fail | `version: "1.0.0"` is written but never checked. UI has no schema guard — silently falls back when a key is missing, with no user-visible warning. `index.html:765` uses `\|\|` fallback chains. |
| **B. Scoring math** | | | |
| 6 | Conviction calculation | weak | `build_conviction_map` in `synthesize.py:96`: `50 + raw/n * 0.5`. With salience 0-100 and direction ±1, max raw/n = ±100, so output range is [0,100] with 50 neutral — mechanically correct, but a single +100/+1 event moves conviction +50 points. No outlier protection, no recency weighting, no smoothing across runs. |
| 7 | Tilt cap logic | weak | `apply_ai_capex_cap` (`synthesize.py:69`) caps **combined** tech_ai+space to 3% but the logic in branches is wrong: if tech_ai=0 and space=0.03, the branch sets space=0.03 (no-op); if both are 0.03, sum=0.06 > cap → tech_ai=min(0.03,0.03)=0.03, space=max(0,min(0.03,0.03-0.03))=0 → tech_ai keeps full tilt, space gets zeroed. That's a tech_ai bias, not a symmetric cap. |
| 8 | Kill-switch wiring | fail | Values `-8.5`, `0.52`, `-1.2` in `synthesize.py:290-292` are **hardcoded constants**, not computed. `triggered` flag is `any(kill_flags_triggered for ...)` — fires on LLM-extracted kill flags, not on actual drawdown/hit-rate. The dashboard's kill switches are theater. |
| 9 | Backfill problem | fail | `build_sparkline` (`synthesize.py:102`) pads missing history with `50` (neutral). Cold start shows 13 flat neutral points + current — looks like "thesis just inflected" when in reality nothing was measured. Misleading. |
| **C. Money safety** | | | |
| 10 | Account partition | pass | Verified: zero ticker overlap between Roth (`bio,tech_ai,robotics,energy`) and Taxable (`space,rare_earth,food_ag`) tickers across core+bench+watch. Wash-sale safe **as currently configured** — but no enforcement check exists, so future config edits could silently break this. |
| 11 | LEAP logic | fail | `synthesize.py:283-287`: candidate hardcoded "MSFT", target $2500, `current` carried from prior state. There is **no LEAP trigger mechanism** — no code computes when to buy, just a counter the UI displays. |
| 12 | Dollar arithmetic | **fail** | Configured `weekly_usd` sums: 40+40+25+30+30+25+20 = **$210**, not $250. `EQUAL_WEIGHT_USD = 250/7 = $35.71` is defined (`config.py:197`) but **never used**. Live state.json sums to $210.97. User is under-deployed by $39/wk ≈ $2,028/yr. |
| 13 | Manual-trade gap | weak | UI shows ticker + dollar amount in `breakdown` lines (`index.html:765`) but has no copy-to-clipboard, no confirmation flow, no log of what user actually executed vs what was recommended. No reconciliation. |
| **D. Deploy/Operations** | | | |
| 14 | GitHub Actions auth | **fail** | `refresh.yml:38-46` sets `ANTHROPIC_API_KEY` env, but `score.py`/`synthesize.py` invoke the local `claude` CLI via subprocess. On `ubuntu-latest` the `claude` binary is not installed — `find_claude_cli` in `claude_cli.py:126` will raise, the pipeline exits 1, the workflow opens an issue, and `data/state.json` is never updated. Workflow has never actually run successfully. |
| 15 | DST handling | fail | `cron: '0 12,0 * * *'` (`refresh.yml:7`) is 5am/5pm PT **only during PDT** (UTC-7). In PST (UTC-8, Nov-Mar), the runs fire at 4am/4pm PT. Comment in workflow even admits "PDT" but doesn't fix it. ~5 months/year of wrong-time runs. |
| 16 | Concurrency | pass | `concurrency: group: refresh, cancel-in-progress: true` is correct. workflow_dispatch + scheduled won't overlap. |
| 17 | Failure recovery | weak | `write_state` is atomic (tmp + rename, `render.py:20`), good. But on partial failure mid-pipeline (feed fetched, scoring crashed), the `state.json` from the previous run remains — UI shows old data with no indication the latest refresh failed. The "open an issue on failure" hook (`refresh.yml:60`) is a paper-tiger notification. |
| **E. UX/Trust** | | | |
| 18 | Confidence calibration | fail | A theme with zero events (`tech_ai`, `robotics`, `space` in current state.json have `top_events: []`) shows conviction=50 and tilt=0.0 with the same visual treatment as a theme with real events. No "data: none" state. Looks identical to "we measured and it's neutral." |
| 19 | Information density | weak | 7 themes × (conviction + sparkline + tilt + dollars + accounts + 3 ticker rows + thesis + kill-flags + 5 events) on a single mobile-first page is busy. The 878-line single HTML file works but will not survive feature creep. |
| 20 | Decision support vs replacement | weak | No disclaimer anywhere ("not financial advice"). UI uses imperative tone ("$40.60/wk Roth") that reads like a directive. For a single retail investor that's probably fine — for any third-party viewer that's a liability surface. |
| 21 | Cold-start UX | fail | First load before any cron has run: UI uses `FALLBACK_STATE` baked into `index.html:493-513` showing seven themes at conviction=50, tilt=0, with the **error banner** visible. New user can't distinguish "system hasn't run yet" from "everything is neutral" from "fetch broke." |
| **F. Strategy soundness** | | | |
| 22 | Edge claim | fail | The whole apparatus is "score thematic news with an LLM, tilt allocation ±3%." There is no backtest, no benchmark, no measured Sharpe, no out-of-sample test, no edge hypothesis stated as falsifiable. `vs_spy_ytd_pct` and `vs_equal_weight_pct` are hardcoded `0.0`. The "edge" is unmeasured. |
| 23 | Behavioral risk | weak | A twice-daily refreshing dashboard with sparklines, kill banners, conviction scores, and per-theme dollar adjustments **encourages** tinkering. The retail DCA win condition is "don't touch it." This UI is engineered to be touched. |
| 24 | Capacity / scaling | n/a | At $250/wk this is irrelevant for the user, but the system claims "tilt protocol" which doesn't really exist at this size — every "tilt" is rounding noise. Pass-through. |
| 25 | Exit strategy | fail | No code computes "this isn't working, stop." No alpha-drawdown trigger that's real (see #8). No "you've been doing this 2 years and lost to SPY by X%" alarm. User has no programmatic off-ramp. |

**Tally:** 1 pass, 11 weak, 12 fail, 1 n/a.

---

## 3. Top 10 needle-movers

1. **The $250 weekly is actually $210** (`config.py:55-149`, `synthesize.py:240`). The seven `weekly_usd` literals sum to 210. `EQUAL_WEIGHT_USD = 250/7` exists but is dead code. **Fix:** make weekly_usd derived from a single `WEEKLY_BUDGET=250` constant, prorated by theme weights that sum to 1.0. Verify the live state.json sum equals WEEKLY_BUDGET ± rounding cents.

2. **CI workflow has never run successfully** (`refresh.yml:38-46` + `claude_cli.py:126`). Workflow tries `ANTHROPIC_API_KEY` env, but the code path subprocesses the `claude` CLI which is not installed on `ubuntu-latest`. **Fix:** either install `@anthropic-ai/claude-code` in the workflow before running, or add a Python-SDK code path that's used when `ANTHROPIC_API_KEY` is set and `claude` is absent. Then actually run it once.

3. **Kill switches are decorative** (`synthesize.py:289-294`). The three thresholds are hardcoded literals; `triggered` only flips when LLM scoring extracts kill-flag *text*, never from real drawdown/hit-rate data. **Fix:** wire to actual portfolio P&L — either pull broker positions or have the user log fills, then compute drawdown vs cost basis. Until then, label the panel "thesis-flag panel," not "kill switches."

4. **No measurable edge** (whole project). No backtest, no benchmark numbers, no falsifiable thesis. **Fix:** before adding any feature, pick one theme, simulate 2 years of news → tilt → return vs equal-weight, post the Sharpe delta. If <0.3, the LLM scoring is decoration.

5. **Cron breaks half the year** (`refresh.yml:7`). PST shift moves runs to 4am/4pm PT. **Fix:** `cron: '0 13 * * *'` and `'0 1 * * *'` won't fix it either since UTC doesn't shift — use two cron entries with explicit DST awareness in the runner step, or accept "5am UTC-7" year-round and adjust UI labels.

6. **Sparkline backfills with neutral 50s** (`synthesize.py:106`). Cold start shows fake "just inflected from neutral" trends. **Fix:** render only the points you have. Show "N data points" under the sparkline. Don't fabricate history.

7. **Workflow auto-commits and pushes to main with no review gate** (`refresh.yml:48-58`). Any LLM hallucination → silently committed to deployed state. **Fix:** add schema validation step before commit; on schema fail, exit non-zero without committing.

8. **Feed dedup is title-hash, not event-clustering** (`normalize.py:104`). Same PDUFA decision from FDA RSS + Federal Register will appear twice with different titles → same event double-counted in conviction. **Fix:** add fuzzy clustering on (date, entity, theme) or at minimum drop near-duplicate titles via Jaccard ≥ 0.7.

9. **AI-capex cap branches asymmetrically** (`synthesize.py:69-78`). When both tech_ai and space are positive, tech_ai gets full tilt, space gets zeroed. **Fix:** pro-rata reduction: `factor = AI_CAPEX_CAP / combined_up; result[t] *= factor for t in AI_CAPEX_THEMES`.

10. **Zero-event themes look identical to neutral-measured themes** (live state.json: tech_ai/robotics/space currently have `top_events: []` but render with conviction 50). **Fix:** UI must render a distinct "no signal this week" state — gray sparkline, "—" instead of "50", explicit count.

---

## 4. Top 5 things that look bad but don't matter

1. **`logs/stale-*.flag` files committed to disk** — annoying, but they're cleared on successful fetch. Cosmetic only.
2. **`claude_cli.py:136` hardcodes `/Users/kingme/.local/bin/claude`** as fallback — only fires locally on Xavier's box. Not a real portability risk for a single-user dashboard.
3. **No SQLite / db.py is just a list wrapper** (`db.py`) — actually correct for this scale; don't add a database.
4. **`netlify.toml` 404 redirect to `/index.html`** — SPA-style fallback for a single-page app is fine.
5. **Inline 878-line HTML / no build step** — will look "bad" to a frontend reviewer. For a single-user dashboard with no users, it's the right call. Don't refactor.

---

## 5. Single biggest hole

**The weekly dollars don't add up to $250.** Live state.json right now shows `roth_total + taxable_total = $210.97`. The product's headline promise — "$250/week DCA into thematic baskets" — is silently false on every refresh. Everything else in this review is fixable in an afternoon. This one is a trust-killing arithmetic error sitting in production and the test suite (`test_synthesize.py`) has no assertion that catches it. Fix this **first**, add a regression test that fails if `sum(weekly_dollars) ∉ [248, 252]`, and only then continue work on the other 24 criteria.

---

*File: `/Users/kingme/projects/iggavestment/REVIEW.md`*
