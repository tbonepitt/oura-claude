# Ring Edge v4.0

Sleep debt tracking, 7-day readiness forecast, and deep sleep analysis — built on your own ring data, updated daily.

**[→ Try it live](https://oura-claude.vercel.app)** — connect your account and go.

---

## What it shows you

- 📉 **Cumulative sleep debt** — 30-day running total vs your personal target, with a payback plan
- 🛏️ **Get In Bed By** — personalized bedtime including your avg sleep latency
- 📈 **7-day readiness forecast** — built from your own 60-day patterns
- 😴 **Why your deep sleep is low** — correlates your data (steps, bedtime) to find your patterns
- 🏆 **Personal records** — best deep sleep, HRV, readiness with dates
- 📅 **Weekly sleep patterns** — your best and worst days of the week
- ❤️ **Heart rate during sleep** — colored by sleep stage
- 🔍 **Anomaly detection** — flags crash days and likely causes

---

## How it works

Ring Edge uses the Oura API to fetch your personal sleep and activity data. Your API token stays in your browser — nothing is stored on our servers. Data reflects your most recent Oura sync (updated daily, not real-time).

---

## Use it online

1. Go to **[oura-claude.vercel.app](https://oura-claude.vercel.app)**
2. Connect your Oura account via OAuth or paste your [Personal Access Token](https://cloud.ouraring.com/personal-access-tokens)
3. Your dashboard loads instantly

---

## Run it locally

```bash
git clone https://github.com/tbonepitt/oura-claude.git
cd oura-claude
pip install flask
cd api && python index.py
```

Open [http://localhost:5000](http://localhost:5000) and connect your account.

---

## Deploy your own instance

1. Fork this repo on GitHub
2. Go to [vercel.com](https://vercel.com) → Add New Project → import your fork
3. Deploy — done

---

## Tech stack

- Python / Flask (Vercel serverless)
- Vanilla JS / Chart.js
- Upstash Vector (feedback storage)
- Vercel Analytics + Speed Insights
- GitHub Actions CI (101 tests)

---

## Data attribution

Data provided by [Oura](https://ouraring.com). Ring Edge is an independent third-party application and is not affiliated with or endorsed by Ōura Health Oy.

---

Built with [Claude Code](https://claude.ai) and the Oura API.
