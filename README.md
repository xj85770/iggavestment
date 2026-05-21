# Iggavestment

Live investment conviction dashboard — refreshed twice daily via GitHub Actions cron, deployed as a static site on Netlify.

## What it is

Pulls free feeds (EDGAR, FDA, SDA, DOE, SpaceNews, ClinicalTrials, Fed Register, EIA), scores events per theme using Claude Haiku, synthesizes conviction scores, writes `data/state.json`. The dashboard at `index.html` fetches that JSON on load and renders. No server required.

## How it updates

GitHub Actions cron fires at 5am and 5pm Pacific Time (`0 12,0 * * *` UTC). It runs `iggavestment refresh`, commits updated `data/state.json` + `data/history/`, and pushes. Netlify auto-deploys on every push.

## Deploy to Netlify

```bash
# 1. Create GitHub repo and push
git init && git add . && git commit -m "init iggavestment"
gh repo create xavierdjones/iggavestment --public --push --source=.

# 2. Connect Netlify to your GitHub repo
#    netlify.app → New site from Git → GitHub → pick the repo
#    Build command: (none)  Publish directory: .
#    Click Deploy

# 3. Add ANTHROPIC_API_KEY secret
#    GitHub → Settings → Secrets and variables → Actions → New repository secret
#    Name: ANTHROPIC_API_KEY  Value: sk-ant-...

# 4. Manually trigger first run to verify
#    GitHub → Actions → "Refresh Iggavestment Data" → Run workflow
```

## Local dev

```bash
uv sync

# Dry-run: no API key, uses mock events
uv run iggavestment refresh --dry-run

# Start local server and open dashboard
python -m http.server 8765 &
open http://localhost:8765

# Full run (requires ANTHROPIC_API_KEY in .env)
uv run iggavestment refresh
```

## Dashboard will live at

`https://<your-netlify-subdomain>.netlify.app`
