#!/usr/bin/env python3
"""Oura Dashboard - local API proxy + static file server"""

import json, os, math
from datetime import date, timedelta, datetime
from urllib.request import urlopen, Request
from urllib.error import URLError
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import defaultdict

TOKEN = os.environ["OURA_TOKEN"]
BASE  = "https://api.ouraring.com/v2/usercollection"
PORT  = 7891
DIR   = os.path.dirname(os.path.abspath(__file__))

def fetch(ep, start, end):
    url = f"{BASE}/{ep}?start_date={start}&end_date={end}"
    req = Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urlopen(req, timeout=12) as r:
            return json.loads(r.read())["data"]
    except URLError:
        return []

def mean(vals):
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), 1) if v else None

def std(vals):
    v = [x for x in vals if x is not None]
    if len(v) < 2: return 0
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m)**2 for x in v) / len(v))

def pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 5: return None
    mx = sum(p[0] for p in pairs) / len(pairs)
    my = sum(p[1] for p in pairs) / len(pairs)
    num = sum((x - mx)*(y - my) for x, y in pairs)
    den = math.sqrt(sum((x-mx)**2 for x,y in pairs) * sum((y-my)**2 for x,y in pairs))
    return round(num/den, 3) if den else None

def clamp(v, lo, hi): return max(lo, min(hi, v))

# ── 7-Day Readiness Forecast ───────────────────────────────────────────────────
def build_forecast(days, r_map, s_map, a_map, ready_scores, sleep_scores, hrv_series, act_scores):
    today = date.today()
    DOW_NAMES = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

    # Day-of-week baselines from historical data
    dow_ready = defaultdict(list)
    dow_sleep = defaultdict(list)
    for d in days:
        dow = date.fromisoformat(d).weekday()
        if r_map[d].get("score"): dow_ready[dow].append(r_map[d]["score"])
        if s_map[d].get("score"): dow_sleep[dow].append(s_map[d]["score"])
    dow_ready_avg = {dow: mean(vals) or 75 for dow, vals in dow_ready.items()}
    global_avg = mean(ready_scores) or 78

    # Recent trend: last 7 vs prior 7
    recent7  = [v for v in ready_scores[-7:]  if v]
    prior7   = [v for v in ready_scores[-14:-7] if v]
    trend_delta = (mean(recent7) or global_avg) - (mean(prior7) or global_avg)

    # HRV momentum
    hrv_recent = [v for v in hrv_series[-5:] if v]
    hrv_prior  = [v for v in hrv_series[-10:-5] if v]
    hrv_mom = ((mean(hrv_recent) or 80) - (mean(hrv_prior) or 80)) * 0.3

    # Activity load penalty: high recent activity suppresses next-day readiness
    act_recent = [v for v in act_scores[-3:] if v]
    act_avg    = mean(act_scores) or 75
    act_load   = ((mean(act_recent) or act_avg) - act_avg) * -0.15

    forecast = []
    for i in range(1, 8):
        fdate = today + timedelta(days=i)
        dow   = fdate.weekday()
        base  = dow_ready_avg.get(dow, global_avg)
        # Decay trend influence over days
        decay = 1.0 / (1 + i * 0.3)
        pred  = base + trend_delta * decay * 0.4 + hrv_mom * decay + act_load * decay
        pred  = clamp(round(pred), 55, 95)

        if pred >= 85:   rec, intensity, color = "Hard training", "💪 Push it", "#22c55e"
        elif pred >= 75: rec, intensity, color = "Moderate effort", "🟢 Go for it", "#3b82f6"
        elif pred >= 65: rec, intensity, color = "Easy day", "🟡 Take it easy", "#f59e0b"
        else:            rec, intensity, color = "Recovery", "🔴 Rest day", "#ef4444"

        forecast.append({
            "date":      str(fdate),
            "dow":       DOW_NAMES[dow],
            "month_day": fdate.strftime("%b %d"),
            "score":     pred,
            "rec":       rec,
            "intensity": intensity,
            "color":     color,
            "is_weekend": dow >= 5,
        })
    return forecast

# ── Anomaly Detection ──────────────────────────────────────────────────────────
def detect_anomalies(days, s_map, r_map, a_map, sleep_scores, ready_scores, act_scores):
    anomalies = []
    window = 14

    metrics = [
        ("sleep",     sleep_scores, s_map, "score", "Sleep score dropped"),
        ("readiness", ready_scores, r_map, "score", "Readiness dropped"),
        ("activity",  act_scores,   a_map, "score", "Activity crashed"),
    ]

    for key, series, src_map, score_key, label in metrics:
        for i in range(window, len(days)):
            window_vals = [v for v in series[i-window:i] if v is not None]
            if len(window_vals) < 7: continue
            m  = mean(window_vals)
            sd = std(window_vals)
            val = series[i]
            if val is None: continue

            # Flag if more than 1.5 std below rolling mean
            if val < m - 1.5 * sd:
                d = days[i]
                # Find likely causes
                causes = []
                entry = src_map.get(d, {})
                contribs = entry.get("contributors", {})

                if key == "sleep":
                    if contribs.get("deep_sleep", 100)    < 30: causes.append("almost no deep sleep")
                    if contribs.get("restfulness", 100)   < 45: causes.append("very restless night")
                    if contribs.get("efficiency", 100)    < 65: causes.append("poor sleep efficiency")
                    if contribs.get("total_sleep", 100)   < 55: causes.append("short total sleep")
                    if contribs.get("timing", 100)        < 80: causes.append("off-schedule bedtime")
                elif key == "readiness":
                    if contribs.get("hrv_balance", 100)   < 65: causes.append("low HRV balance")
                    if contribs.get("recovery_index", 100)< 55: causes.append("poor overnight recovery")
                    if contribs.get("resting_heart_rate", 100) < 60: causes.append("elevated resting HR")
                    temp = entry.get("temperature_deviation")
                    if temp and temp > 0.5: causes.append(f"elevated body temp (+{temp:.1f}°C)")
                elif key == "activity":
                    steps = a_map.get(d, {}).get("steps", 0)
                    if steps < 3000: causes.append(f"only {steps:,} steps")

                # Also check prior day's activity
                if i > 0:
                    prev_d = days[i-1]
                    prev_act = a_map.get(prev_d, {}).get("steps", 0)
                    if prev_act > 12000:
                        causes.append(f"very high activity day prior ({prev_act:,} steps)")

                dow = date.fromisoformat(d).strftime("%a")
                anomalies.append({
                    "date":   d,
                    "label":  label,
                    "metric": key,
                    "score":  val,
                    "avg":    round(m, 0),
                    "drop":   round(m - val, 0),
                    "dow":    dow,
                    "causes": causes[:3],
                })

    # Dedupe by date+metric, keep biggest drop
    seen = {}
    for a in sorted(anomalies, key=lambda x: -x["drop"]):
        k = f"{a['date']}-{a['metric']}"
        if k not in seen:
            seen[k] = a
    result = sorted(seen.values(), key=lambda x: x["date"], reverse=True)
    return result[:8]

# ── Sleep Debt ─────────────────────────────────────────────────────────────────
def calc_sleep_debt(sleep_detail, target_hours=8.0):
    debt_hours = 0.0
    log = []
    for d in sleep_detail[-30:]:
        total_sec = d.get("total_sleep_duration") or 0
        actual    = total_sec / 3600
        nightly_debt = target_hours - actual
        debt_hours  += nightly_debt
        log.append({
            "date":   d.get("day",""),
            "actual": round(actual, 2),
            "debt":   round(nightly_debt, 2),
            "cumulative": round(debt_hours, 2),
        })
    return round(debt_hours, 1), log

# ── Main data builder ──────────────────────────────────────────────────────────
def build_data():
    today   = date.today()
    end     = str(today)
    start60 = str(today - timedelta(days=60))
    start7  = str(today - timedelta(days=7))

    sleep    = fetch("daily_sleep",    start60, end)
    ready    = fetch("daily_readiness",start60, end)
    activity = fetch("daily_activity", start60, end)
    detail   = fetch("sleep",          start60, end)
    hr_data  = fetch("heartrate",      start7,  end)

    s_map = {d["day"]: d for d in sleep}
    r_map = {d["day"]: d for d in ready}
    a_map = {d["day"]: d for d in activity}
    days  = sorted(set(s_map) & set(r_map) & set(a_map))

    def series(src, key, sub=None):
        out = []
        for d in days:
            val = src.get(d, {})
            if sub: val = val.get(sub, {})
            out.append(val.get(key))
        return out

    sleep_scores = series(s_map, "score")
    ready_scores = series(r_map, "score")
    act_scores   = series(a_map, "score")
    steps_series = series(a_map, "steps")
    calories     = series(a_map, "active_calories")
    deep_series  = series(s_map, "deep_sleep",   sub="contributors")
    rem_series   = series(s_map, "rem_sleep",     sub="contributors")
    rest_series  = series(s_map, "restfulness",   sub="contributors")
    eff_series   = series(s_map, "efficiency",    sub="contributors")
    hrv_series   = series(r_map, "hrv_balance",   sub="contributors")
    rhr_series   = series(r_map, "resting_heart_rate", sub="contributors")
    temp_series  = [r_map.get(d, {}).get("temperature_deviation") for d in days]

    latest_sleep = sleep[-1]  if sleep  else {}
    latest_ready = ready[-1]  if ready  else {}
    latest_act   = activity[-1] if activity else {}
    latest_det   = detail[-1]   if detail   else {}

    def fmt_dur(secs):
        if not secs: return "—"
        h, m = divmod(int(secs)//60, 60)
        return f"{h}h {m:02d}m"

    arch = {
        "total": fmt_dur(latest_det.get("total_sleep_duration")),
        "deep":  fmt_dur(latest_det.get("deep_sleep_duration")),
        "rem":   fmt_dur(latest_det.get("rem_sleep_duration")),
        "light": fmt_dur(latest_det.get("light_sleep_duration")),
        "deep_pct":  round((latest_det.get("deep_sleep_duration") or 0) / max(latest_det.get("total_sleep_duration") or 1, 1) * 100, 1),
        "rem_pct":   round((latest_det.get("rem_sleep_duration")  or 0) / max(latest_det.get("total_sleep_duration") or 1, 1) * 100, 1),
        "light_pct": round((latest_det.get("light_sleep_duration")or 0) / max(latest_det.get("total_sleep_duration") or 1, 1) * 100, 1),
    }

    # Day-of-week
    DOW = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    dow_sleep = defaultdict(list)
    dow_ready = defaultdict(list)
    dow_steps = defaultdict(list)
    for d in days:
        dow = date.fromisoformat(d).weekday()
        if s_map[d].get("score"): dow_sleep[dow].append(s_map[d]["score"])
        if r_map[d].get("score"): dow_ready[dow].append(r_map[d]["score"])
        if a_map[d].get("steps"): dow_steps[dow].append(a_map[d]["steps"])

    # Correlations
    n = len(days)
    corrs = {
        "steps_deep":  pearson(steps_series[:n-1], deep_series[1:n]),
        "sleep_ready": pearson(sleep_scores[:n-1], ready_scores[1:n]),
        "cal_deep":    pearson(calories[:n-1],     deep_series[1:n]),
        "hrv_sleep":   pearson(hrv_series,         sleep_scores),
        "temp_sleep":  pearson(temp_series,        sleep_scores),
        "rest_ready":  pearson(rest_series,        ready_scores),
    }

    # Tomorrow single prediction
    last_sleep_score = latest_sleep.get("score", 75)
    last_hrv         = latest_ready.get("contributors", {}).get("hrv_balance", 80)
    hist_ready_avg   = mean(ready_scores) or 78
    r_s = corrs["sleep_ready"] or 0
    r_h = pearson(hrv_series[:n-1], ready_scores[1:n]) or 0
    tw  = abs(r_s) + abs(r_h) + 0.01
    pred = round(hist_ready_avg*0.4 + last_sleep_score*abs(r_s)/tw*0.35 + last_hrv*abs(r_h)/tw*0.25)

    # HRV delta
    hrv_30 = [v for v in hrv_series[-30:] if v is not None]
    hrv_delta = round((mean(hrv_30[-10:]) or 0) - (mean(hrv_30[:10]) or 0), 1) if len(hrv_30) >= 20 else 0

    # Forecast
    forecast = build_forecast(days, r_map, s_map, a_map, ready_scores, sleep_scores, hrv_series, act_scores)

    # Anomalies
    anomalies = detect_anomalies(days, s_map, r_map, a_map, sleep_scores, ready_scores, act_scores)

    # Sleep debt
    sleep_debt, debt_log = calc_sleep_debt(detail)

    # Resting HR
    resting_hr_timeline = [
        {"t": h["timestamp"][:16], "bpm": h["bpm"]}
        for h in hr_data if h.get("source") == "rest" and h.get("bpm")
    ]

    # Checkins
    checkin_file = os.path.expanduser("~/.claude/oura/checkins.json")
    checkin_insights = []
    if os.path.exists(checkin_file):
        with open(checkin_file) as f:
            checkins = json.load(f)
        factor_impact = defaultdict(lambda: {"yes": [], "no": []})
        for day_str, ci in checkins.items():
            next_day = str(date.fromisoformat(day_str) + timedelta(days=1))
            ns = s_map.get(next_day, {}).get("score")
            if ns is None: continue
            for factor, val in ci.items():
                if isinstance(val, bool):
                    factor_impact[factor]["yes" if val else "no"].append(ns)
        for factor, groups in factor_impact.items():
            if len(groups["yes"]) >= 2 and len(groups["no"]) >= 2:
                diff = (mean(groups["yes"]) or 0) - (mean(groups["no"]) or 0)
                checkin_insights.append({
                    "factor": factor.replace("_", " ").title(),
                    "with":    mean(groups["yes"]),
                    "without": mean(groups["no"]),
                    "diff":    round(diff, 1)
                })
        checkin_insights.sort(key=lambda x: abs(x["diff"]), reverse=True)

    # 60-day heatmap data
    heatmap = []
    for d in days:
        rs = r_map.get(d, {}).get("score")
        heatmap.append({"date": d, "score": rs})

    return {
        "generated":   str(today),
        "days":        days,
        "scores": {
            "sleep":       sleep_scores,
            "ready":       ready_scores,
            "activity":    act_scores,
            "steps":       steps_series,
            "calories":    calories,
            "deep":        deep_series,
            "rem":         rem_series,
            "restfulness": rest_series,
            "efficiency":  eff_series,
            "hrv":         hrv_series,
            "rhr":         rhr_series,
            "temp":        temp_series,
        },
        "latest": {
            "sleep":    latest_sleep.get("score"),
            "ready":    latest_ready.get("score"),
            "activity": latest_act.get("score"),
            "steps":    latest_act.get("steps"),
            "calories": latest_act.get("active_calories"),
            "avg_hrv":  latest_det.get("average_hrv"),
            "avg_hr":   latest_det.get("average_heart_rate"),
            "hrv_bal":  latest_ready.get("contributors", {}).get("hrv_balance"),
            "rhr":      latest_ready.get("contributors", {}).get("resting_heart_rate"),
            "temp_dev": latest_ready.get("temperature_deviation"),
            "arch":     arch,
            "contributors": {
                "sleep": latest_sleep.get("contributors", {}),
                "ready": latest_ready.get("contributors", {}),
            }
        },
        "avgs": {
            "sleep":       mean(sleep_scores[-30:]),
            "ready":       mean(ready_scores[-30:]),
            "activity":    mean(act_scores[-30:]),
            "hrv":         mean(hrv_series[-30:]),
            "deep":        mean(deep_series[-30:]),
            "restfulness": mean(rest_series[-30:]),
        },
        "prediction":       pred,
        "hrv_delta":        hrv_delta,
        "dow": {
            "labels": DOW,
            "sleep":  [mean(dow_sleep[i]) for i in range(7)],
            "ready":  [mean(dow_ready[i]) for i in range(7)],
            "steps":  [mean(dow_steps[i]) for i in range(7)],
        },
        "correlations":     corrs,
        "resting_hr":       resting_hr_timeline[-200:],
        "checkin_insights": checkin_insights,
        "forecast":         forecast,
        "anomalies":        anomalies,
        "sleep_debt":       sleep_debt,
        "debt_log":         debt_log,
        "heatmap":          heatmap,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_GET(self):
        if self.path == "/api/data":
            data = build_data()
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        else:
            path = os.path.join(DIR, "index.html")
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)


if __name__ == "__main__":
    print(f"🩺 Oura Dashboard running at http://localhost:{PORT}")
    HTTPServer(("", PORT), Handler).serve_forever()
