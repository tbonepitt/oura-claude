# 🩺 Oura Health Dashboard v3.0

A personal health intelligence dashboard for Oura Ring — works as a hosted web app or locally. No setup required.

**[→ Try it live](https://oura-claude.vercel.app)** — paste your Oura token and go.

---

## Features

### v3.0 — Web App
- **Token-based onboarding** — paste your Oura Personal Access Token in the browser, see your data instantly
- **Works for anyone** — no installation, no config files, no terminal
- **Privacy-first** — your token lives only in your browser's localStorage, sent directly to the Oura API

### v2.0 — Sleep Science
- **Sleep Cycle Explainer** — your actual hypnogram (deep/light/REM/awake) visualized with heart rate overlay
- **Deep Sleep Decoder** — compares your top 25% vs bottom 25% deep sleep nights to find *your* personal triggers
- **Tonight's Sleep Plan** — 2 specific, personalized actions you can take tonight

### v1.0 — Core Analytics
- **7-Day Readiness Forecast** — predicted readiness scores based on your 60-day personal patterns
- **Anomaly Detector** — flags crash days and auto-detects likely causes
- **Personal Correlations** — finds relationships unique to *your* biology
- **Sleep Debt Tracker** — 30-day running deficit with payback estimate
- **60-Day Heatmap** — full readiness history at a glance

---

## Use it online (recommended)

1. Go to **[oura-claude.vercel.app](https://oura-claude.vercel.app)**
2. Get a token at [cloud.ouraring.com/personal-access-tokens](https://cloud.ouraring.com/personal-access-tokens)
3. Paste it in — your dashboard loads instantly

Your token is stored only in your browser. It's never saved on any server.

---

## Run it locally

```bash
git clone https://github.com/tbonepitt/oura-claude.git
cd oura-claude
./run.sh
```

Then open [http://localhost:7891](http://localhost:7891) and paste your token in the browser.

**Optional:** Create a `.env` file to skip the token prompt:
```bash
cp .env.example .env
# Edit .env and add your token — browser prompt will be skipped
```

---

## Deploy your own instance

### Vercel (free, 1 click)
1. Fork this repo on GitHub
2. Go to [vercel.com](https://vercel.com) → **Add New Project** → import your fork
3. Deploy — done. Your own private instance is live.

---

## File structure

| File | What it does |
|------|-------------|
| `api/data.py` | Vercel serverless function — fetches Oura data, runs all analysis |
| `public/index.html` | The dashboard UI — used by both hosted and local modes |
| `dashboard/server.py` | Local HTTP server — serves `public/index.html` and proxies `/api/data` |
| `vercel.json` | Vercel routing config |
| `run.sh` | Local launcher |

---

## How the Deep Sleep Decoder works

Splits your last 60 nights into top 25% and bottom 25% by deep sleep, then compares:
- **Steps** — did you move more on your best nights?
- **Bedtime** — is there an optimal window for your chronotype?
- **Calories** — does activity drive deeper sleep?
- **Restlessness** — do restless nights cluster?

Each finding becomes a plain-English action.

---

## Privacy

- Your token is stored in `localStorage` — never on any server
- All analysis runs server-side in the serverless function, but no data is persisted
- The local version is fully offline after startup (data goes browser → local server → Oura API)

---

Built with [Claude](https://claude.ai) + the Oura API.
