#!/usr/bin/env python3
"""Oura Dashboard — Vercel serverless function
Token is read from the X-Oura-Token request header (never stored server-side).
"""

import json, os, math
from datetime import date, timedelta, datetime
from urllib.request import urlopen, Request
from urllib.error import URLError
from http.server import BaseHTTPRequestHandler
from collections import defaultdict

BASE = "https://api.ouraring.com/v2/usercollection"

# ── Helpers ────────────────────────────────────────────────────────────────────

def fetch(ep, start, end, token):
    if start and end:
        url = f"{BASE}/{ep}?start_date={start}&end_date={end}"
    else:
        url = f"{BASE}/{ep}"
    req = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(req, timeout=12) as r:
            body = json.loads(r.read())
            return body.get("data", body)  # personal_info has no 'data' wrapper
    except URLError:
        return [] if (start and end) else {}

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

# ── Sleep Science ──────────────────────────────────────────────────────────────

STAGE_MAP = {"1": "deep", "2": "light", "3": "rem", "4": "awake"}
STAGE_Y   = {"1": 0, "2": 2, "3": 1, "4": 3}

def parse_hypnogram(session):
    phase   = session.get("sleep_phase_5_min", "")
    bedtime = session.get("bedtime_start", "")
    if not phase or not bedtime:
        return None
    try:
        start_dt = datetime.fromisoformat(bedtime)
    except Exception:
        return None

    labels, stages, colors = [], [], []
    COLOR_MAP = {"1":"#3b82f6","2":"#6366f1","3":"#a855f7","4":"#374151"}

    for i, ch in enumerate(phase):
        t = start_dt + timedelta(minutes=i*5)
        labels.append(t.strftime("%H:%M"))
        stages.append(STAGE_Y.get(ch, 3))
        colors.append(COLOR_MAP.get(ch, "#374151"))

    hr_items  = session.get("heart_rate", {}).get("items", [])
    hrv_items = session.get("hrv", {}).get("items", [])

    return {
        "labels":  labels,
        "stages":  stages,
        "colors":  colors,
        "hr":      hr_items,
        "hrv":     hrv_items,
        "deep_min":  phase.count("1") * 5,
        "light_min": phase.count("2") * 5,
        "rem_min":   phase.count("3") * 5,
        "awake_min": phase.count("4") * 5,
        "total_min": len(phase) * 5,
        "bedtime":   bedtime[:16],
        "wake_time": session.get("bedtime_end", "")[:16],
        "efficiency": session.get("efficiency"),
        "restless_periods": session.get("restless_periods", 0),
        "avg_hr":  session.get("average_heart_rate"),
        "avg_hrv": session.get("average_hrv"),
        "lowest_hr": session.get("lowest_heart_rate"),
    }

def build_tonight_card(act, ready, sleep, decoder, debt, act_scores, ready_scores, sleep_scores):
    steps     = act.get("steps", 0) or 0
    hrv       = ready.get("contributors", {}).get("hrv_balance", 80) or 80
    readiness = ready.get("score", 75) or 75
    debt_hrs  = debt if isinstance(debt, (int, float)) else 0

    findings    = decoder.get("findings", []) if decoder else []
    best_nights = decoder.get("best_nights", []) if decoder else []

    optimal_bed = None
    if best_nights:
        beds = [n.get("bed") for n in best_nights if n.get("bed")]
        if beds:
            optimal_bed = beds[0]

    issues = []

    best_steps = max([n.get("steps") or 0 for n in best_nights], default=8000)
    if steps < best_steps * 0.7:
        gap = best_steps - steps
        issues.append({
            "priority": 1, "icon": "🚶",
            "headline": f"Take a {min(30, max(10, gap//100))}-minute walk before bed",
            "body": f"You've done {steps:,} steps today. Your best deep sleep nights average {best_steps:,}. A short walk tonight could add 15–25 minutes of deep sleep.",
            "urgency": "high"
        })

    if optimal_bed:
        now_hour = datetime.now().hour
        issues.append({
            "priority": 2, "icon": "🛏️",
            "headline": f"Be in bed by {optimal_bed}",
            "body": f"Your three best deep sleep nights all started before {optimal_bed}. Every hour later you go to bed costs roughly 20 minutes of deep sleep.",
            "urgency": "high" if now_hour >= 21 else "medium"
        })

    if hrv < 75:
        issues.append({
            "priority": 3, "icon": "💆",
            "headline": "Wind down early tonight — HRV is low",
            "body": f"Your HRV balance is {hrv}/100. Avoid screens and alcohol. Try 10 minutes of slow breathing.",
            "urgency": "medium"
        })

    if debt_hrs > 3:
        issues.append({
            "priority": 4, "icon": "💳",
            "headline": f"You're {debt_hrs}h in sleep debt — don't cut tonight short",
            "body": "Your body needs at least 8 hours in bed tonight. Deep sleep increases during recovery nights.",
            "urgency": "medium"
        })

    restless_finding = next((f for f in findings if "restless" in f.get("title","").lower()), None)
    if restless_finding:
        issues.append({
            "priority": 5, "icon": "🌡️",
            "headline": "Cool your room tonight",
            "body": "Restlessness is your #1 deep sleep killer. Keep 65–68°F. Your body needs to drop 1–2°F to enter deep sleep.",
            "urgency": "medium"
        })

    issues.sort(key=lambda x: x["priority"])
    top = issues[:2]

    if readiness >= 80 and steps >= 7000:
        verdict, verdict_msg, verdict_color = "great", "You're set up well for a good night. Lock in the details below.", "#22c55e"
    elif readiness >= 65 or steps >= 5000:
        verdict, verdict_msg, verdict_color = "ok", "Tonight is fixable. A couple of things to do before bed.", "#f59e0b"
    else:
        verdict, verdict_msg, verdict_color = "at-risk", "Today's numbers put your sleep at risk. Follow the plan below.", "#ef4444"

    return {
        "verdict": verdict, "verdict_msg": verdict_msg, "verdict_color": verdict_color,
        "optimal_bed": optimal_bed, "steps_today": steps, "best_steps": best_steps,
        "hrv": hrv, "debt": debt_hrs, "actions": top,
    }

def build_deep_sleep_decoder(sleep_detail, activity_map):
    nights = []
    for s in sleep_detail:
        phase = s.get("sleep_phase_5_min", "")
        if not phase or len(phase) < 20:
            continue
        deep_min = phase.count("1") * 5
        day      = s.get("day", "")
        bedtime  = s.get("bedtime_start", "")
        try:
            bed_hour = datetime.fromisoformat(bedtime).hour + \
                       datetime.fromisoformat(bedtime).minute / 60
            if bed_hour < 12:
                bed_hour += 24
        except Exception:
            bed_hour = None

        act = activity_map.get(day, {})
        nights.append({
            "day": day, "deep_min": deep_min, "total_min": len(phase) * 5,
            "bed_hour": bed_hour, "steps": act.get("steps"),
            "calories": act.get("active_calories"), "act_score": act.get("score"),
            "restless": s.get("restless_periods", 0),
        })

    if len(nights) < 10:
        return {}

    nights.sort(key=lambda x: x["deep_min"])
    n      = len(nights)
    top_q  = nights[int(n * 0.75):]
    bot_q  = nights[:int(n * 0.25)]

    def avg(lst, key):
        vals = [x[key] for x in lst if x.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    top_steps = avg(top_q, "steps");  bot_steps = avg(bot_q, "steps")
    top_bed   = avg(top_q, "bed_hour"); bot_bed = avg(bot_q, "bed_hour")
    top_cal   = avg(top_q, "calories"); bot_cal = avg(bot_q, "calories")
    top_rest  = avg(top_q, "restless"); bot_rest = avg(bot_q, "restless")

    def fmt_hour(h):
        if h is None: return "—"
        h = h % 24
        hh = int(h); mm = int((h % 1) * 60)
        suffix = "am" if hh < 12 else "pm"
        hh12 = hh if hh <= 12 else hh - 12
        return f"{hh12}:{mm:02d}{suffix}"

    findings = []
    overall_avg = avg(nights, "deep_min")
    overall_std = round(std([n["deep_min"] for n in nights]), 0)

    if top_steps and bot_steps:
        diff = top_steps - bot_steps
        if diff > 1500:
            findings.append({"icon":"🚶","title":"Steps matter for YOU",
                "body":f"Best deep sleep nights: {int(top_steps):,} steps avg. Worst: {int(bot_steps):,}. That's a {int(diff):,}-step difference.",
                "action":f"Aim for {int(top_steps):,} steps on days you want deep sleep.","impact":"high"})
        elif diff > 500:
            findings.append({"icon":"🚶","title":"More steps, more deep sleep",
                "body":f"Best nights: {int(top_steps):,} steps. Worst: {int(bot_steps):,}.",
                "action":"A 20-minute walk can make a difference.","impact":"medium"})

    if top_bed and bot_bed:
        diff = bot_bed - top_bed
        if abs(diff) > 0.5:
            earlier = top_bed < bot_bed
            findings.append({"icon":"🛏️","title":f"{'Earlier' if earlier else 'Later'} bedtimes = more deep sleep",
                "body":f"Best nights in bed around {fmt_hour(top_bed)}. Worst around {fmt_hour(bot_bed)}.",
                "action":f"Target {fmt_hour(top_bed)} as your bedtime for better deep sleep.","impact":"high"})

    if top_rest is not None and bot_rest is not None and (bot_rest - top_rest) > 50:
        findings.append({"icon":"🌀","title":"Restlessness is killing your deep sleep",
            "body":f"Bad nights: {int(bot_rest)} restless periods vs {int(top_rest)} on good nights.",
            "action":"Restlessness is caused by alcohol, late meals, heat, or stress. Track which applies.","impact":"high"})

    if top_cal and bot_cal and (top_cal - bot_cal) > 100:
        findings.append({"icon":"🔥","title":"Active days → deeper sleep",
            "body":f"Best nights followed {int(top_cal)} active calories burned. Worst: {int(bot_cal)}.",
            "action":"Light-to-moderate activity during the day improves deep sleep quality.","impact":"medium"})

    pct_deep = round(overall_avg / (avg(nights, "total_min") or 480) * 100, 0) if overall_avg else 0
    science  = {
        "what_is_deep": "Deep sleep (slow-wave sleep) is your body's repair mode — it releases growth hormone, consolidates memories, repairs tissue, and clears metabolic waste from your brain.",
        "your_avg": overall_avg, "your_std": overall_std, "ideal_min": 90,
        "ideal_pct": 20, "your_pct": pct_deep,
        "status": "low" if (overall_avg or 0) < 60 else "fair" if (overall_avg or 0) < 90 else "good",
        "status_msg": (
            f"Your average of {overall_avg} min is below the ideal 90+ min. This is your #1 sleep improvement opportunity."
            if (overall_avg or 0) < 60 else
            f"Your average of {overall_avg} min is getting there. Small tweaks could push you into the optimal zone."
            if (overall_avg or 0) < 90 else
            f"Your deep sleep is solid at {overall_avg} min average. Focus on consistency."
        ),
        "when_it_happens": "Most deep sleep happens in the first 3-4 hours of the night. If you're going to bed late or drinking alcohol, you're cutting into your best deep sleep window.",
        "why_variable": f"Your deep sleep swings {overall_std:.0f} points night to night — something specific is disrupting it on bad nights.",
    }

    best3  = sorted(nights, key=lambda x: -x["deep_min"])[:3]
    worst3 = sorted(nights, key=lambda x:  x["deep_min"])[:3]

    return {
        "science": science, "findings": findings,
        "best_nights":  [{"day":n["day"],"deep_min":n["deep_min"],"steps":n["steps"],"bed":fmt_hour(n["bed_hour"])} for n in best3],
        "worst_nights": [{"day":n["day"],"deep_min":n["deep_min"],"steps":n["steps"],"bed":fmt_hour(n["bed_hour"])} for n in worst3],
        "distribution": [n["deep_min"] for n in sorted(nights, key=lambda x: x["day"])],
        "distribution_days": [n["day"] for n in sorted(nights, key=lambda x: x["day"])],
        "top_avg": avg(top_q, "deep_min"), "bot_avg": avg(bot_q, "deep_min"), "overall_avg": overall_avg,
    }

# ── Forecast ───────────────────────────────────────────────────────────────────

def build_forecast(days, r_map, s_map, a_map, ready_scores, sleep_scores, hrv_series, act_scores):
    today = date.today()
    DOW_NAMES = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

    dow_ready = defaultdict(list)
    for d in days:
        dow = date.fromisoformat(d).weekday()
        if r_map[d].get("score"): dow_ready[dow].append(r_map[d]["score"])
    dow_ready_avg = {dow: mean(vals) or 75 for dow, vals in dow_ready.items()}
    global_avg = mean(ready_scores) or 78

    recent7 = [v for v in ready_scores[-7:]  if v]
    prior7  = [v for v in ready_scores[-14:-7] if v]
    trend_delta = (mean(recent7) or global_avg) - (mean(prior7) or global_avg)

    hrv_recent = [v for v in hrv_series[-5:] if v]
    hrv_prior  = [v for v in hrv_series[-10:-5] if v]
    hrv_mom = ((mean(hrv_recent) or 80) - (mean(hrv_prior) or 80)) * 0.3

    act_recent = [v for v in act_scores[-3:] if v]
    act_avg    = mean(act_scores) or 75
    act_load   = ((mean(act_recent) or act_avg) - act_avg) * -0.15

    forecast = []
    for i in range(1, 8):
        fdate = today + timedelta(days=i)
        dow   = fdate.weekday()
        base  = dow_ready_avg.get(dow, global_avg)
        decay = 1.0 / (1 + i * 0.3)
        pred  = base + trend_delta * decay * 0.4 + hrv_mom * decay + act_load * decay
        pred  = clamp(round(pred), 55, 95)

        if pred >= 85:   rec, intensity, color = "Hard training", "💪 Push it", "#22c55e"
        elif pred >= 75: rec, intensity, color = "Moderate effort", "🟢 Go for it", "#3b82f6"
        elif pred >= 65: rec, intensity, color = "Easy day", "🟡 Take it easy", "#f59e0b"
        else:            rec, intensity, color = "Recovery", "🔴 Rest day", "#ef4444"

        forecast.append({
            "date": str(fdate), "dow": DOW_NAMES[dow],
            "month_day": fdate.strftime("%b %d"), "score": pred,
            "rec": rec, "intensity": intensity, "color": color,
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
            m = mean(window_vals); sd = std(window_vals)
            val = series[i]
            if val is None or val >= m - 1.5 * sd: continue

            d = days[i]; causes = []
            entry = src_map.get(d, {}); contribs = entry.get("contributors", {})

            if key == "sleep":
                if contribs.get("deep_sleep", 100)  < 30: causes.append("almost no deep sleep")
                if contribs.get("restfulness", 100) < 45: causes.append("very restless night")
                if contribs.get("efficiency", 100)  < 65: causes.append("poor sleep efficiency")
                if contribs.get("total_sleep", 100) < 55: causes.append("short total sleep")
                if contribs.get("timing", 100)      < 80: causes.append("off-schedule bedtime")
            elif key == "readiness":
                if contribs.get("hrv_balance", 100)      < 65: causes.append("low HRV balance")
                if contribs.get("recovery_index", 100)   < 55: causes.append("poor overnight recovery")
                if contribs.get("resting_heart_rate",100)< 60: causes.append("elevated resting HR")
                temp = entry.get("temperature_deviation")
                if temp and temp > 0.5: causes.append(f"elevated body temp (+{temp:.1f}°C)")
            elif key == "activity":
                steps = a_map.get(d, {}).get("steps", 0)
                if steps < 3000: causes.append(f"only {steps:,} steps")

            if i > 0:
                prev_act = a_map.get(days[i-1], {}).get("steps", 0)
                if prev_act > 12000: causes.append(f"very high activity prior ({prev_act:,} steps)")

            dow = date.fromisoformat(d).strftime("%a")
            anomalies.append({"date": d, "label": label, "metric": key, "score": val,
                               "avg": round(m, 0), "drop": round(m - val, 0),
                               "dow": dow, "causes": causes[:3]})

    seen = {}
    for a in sorted(anomalies, key=lambda x: -x["drop"]):
        k = f"{a['date']}-{a['metric']}"
        if k not in seen: seen[k] = a
    return sorted(seen.values(), key=lambda x: x["date"], reverse=True)[:8]

# ── Sleep Debt ─────────────────────────────────────────────────────────────────

def calc_sleep_debt(sleep_detail, target_hours=8.0):
    debt_hours = 0.0; log = []
    for d in sleep_detail[-30:]:
        actual = (d.get("total_sleep_duration") or 0) / 3600
        nightly_debt = target_hours - actual
        debt_hours  += nightly_debt
        log.append({"date": d.get("day",""), "actual": round(actual, 2),
                    "debt": round(nightly_debt, 2), "cumulative": round(debt_hours, 2)})
    return round(debt_hours, 1), log

# ── Main data builder ──────────────────────────────────────────────────────────

def build_data(token):
    today   = date.today()
    end     = str(today)
    start60 = str(today - timedelta(days=60))
    start7  = str(today - timedelta(days=7))

    sleep    = fetch("daily_sleep",     start60, end,  token)
    ready    = fetch("daily_readiness", start60, end,  token)
    activity = fetch("daily_activity",  start60, end,  token)
    detail   = fetch("sleep",           start60, end,  token)
    hr_data  = fetch("heartrate",       start7,  end,  token)
    user_info= fetch("personal_info",   None,    None, token)

    activity_map = {a["day"]: a for a in activity}
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

    latest_sleep = sleep[-1]    if sleep    else {}
    latest_ready = ready[-1]    if ready    else {}
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
        "deep_pct":  round((latest_det.get("deep_sleep_duration")  or 0) / max(latest_det.get("total_sleep_duration") or 1, 1) * 100, 1),
        "rem_pct":   round((latest_det.get("rem_sleep_duration")   or 0) / max(latest_det.get("total_sleep_duration") or 1, 1) * 100, 1),
        "light_pct": round((latest_det.get("light_sleep_duration") or 0) / max(latest_det.get("total_sleep_duration") or 1, 1) * 100, 1),
    }

    DOW = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    dow_sleep = defaultdict(list); dow_ready = defaultdict(list); dow_steps = defaultdict(list)
    for d in days:
        dow = date.fromisoformat(d).weekday()
        if s_map[d].get("score"): dow_sleep[dow].append(s_map[d]["score"])
        if r_map[d].get("score"): dow_ready[dow].append(r_map[d]["score"])
        if a_map[d].get("steps"): dow_steps[dow].append(a_map[d]["steps"])

    n = len(days)
    corrs = {
        "steps_deep":  pearson(steps_series[:n-1], deep_series[1:n]),
        "sleep_ready": pearson(sleep_scores[:n-1], ready_scores[1:n]),
        "cal_deep":    pearson(calories[:n-1],     deep_series[1:n]),
        "hrv_sleep":   pearson(hrv_series,         sleep_scores),
        "temp_sleep":  pearson(temp_series,        sleep_scores),
        "rest_ready":  pearson(rest_series,        ready_scores),
    }

    last_sleep_score = latest_sleep.get("score", 75)
    last_hrv         = latest_ready.get("contributors", {}).get("hrv_balance", 80)
    hist_ready_avg   = mean(ready_scores) or 78
    r_s = corrs["sleep_ready"] or 0
    r_h = pearson(hrv_series[:n-1], ready_scores[1:n]) or 0
    tw  = abs(r_s) + abs(r_h) + 0.01
    pred = round(hist_ready_avg*0.4 + last_sleep_score*abs(r_s)/tw*0.35 + last_hrv*abs(r_h)/tw*0.25)

    hrv_30    = [v for v in hrv_series[-30:] if v is not None]
    hrv_delta = round((mean(hrv_30[-10:]) or 0) - (mean(hrv_30[:10]) or 0), 1) if len(hrv_30) >= 20 else 0

    forecast  = build_forecast(days, r_map, s_map, a_map, ready_scores, sleep_scores, hrv_series, act_scores)
    anomalies = detect_anomalies(days, s_map, r_map, a_map, sleep_scores, ready_scores, act_scores)
    sleep_debt, debt_log = calc_sleep_debt(detail)

    resting_hr_timeline = [
        {"t": h["timestamp"][:16], "bpm": h["bpm"]}
        for h in hr_data if h.get("source") == "rest" and h.get("bpm")
    ]

    last_sleep_session = detail[-1] if detail else {}
    hypnogram    = parse_hypnogram(last_sleep_session)
    deep_decoder = build_deep_sleep_decoder(detail, activity_map)
    tonight_card = build_tonight_card(
        latest_act, latest_ready, latest_sleep, deep_decoder,
        sleep_debt, act_scores, ready_scores, sleep_scores
    )

    heatmap = [{"date": d, "score": r_map.get(d, {}).get("score")} for d in days]

    first_name = user_info.get("first_name", "") if isinstance(user_info, dict) else ""

    return {
        "generated": str(today),
        "user": {"first_name": first_name},
        "days": days,
        "scores": {
            "sleep": sleep_scores, "ready": ready_scores, "activity": act_scores,
            "steps": steps_series, "calories": calories, "deep": deep_series,
            "rem": rem_series, "restfulness": rest_series, "efficiency": eff_series,
            "hrv": hrv_series, "rhr": rhr_series, "temp": temp_series,
        },
        "latest": {
            "sleep": latest_sleep.get("score"), "ready": latest_ready.get("score"),
            "activity": latest_act.get("score"), "steps": latest_act.get("steps"),
            "calories": latest_act.get("active_calories"), "avg_hrv": latest_det.get("average_hrv"),
            "avg_hr": latest_det.get("average_heart_rate"),
            "hrv_bal": latest_ready.get("contributors", {}).get("hrv_balance"),
            "rhr": latest_ready.get("contributors", {}).get("resting_heart_rate"),
            "temp_dev": latest_ready.get("temperature_deviation"),
            "arch": arch,
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
        "prediction": pred, "hrv_delta": hrv_delta,
        "dow": {
            "labels": DOW,
            "sleep":  [mean(dow_sleep[i]) for i in range(7)],
            "ready":  [mean(dow_ready[i]) for i in range(7)],
            "steps":  [mean(dow_steps[i]) for i in range(7)],
        },
        "correlations": corrs,
        "resting_hr": resting_hr_timeline[-200:],
        "checkin_insights": [],
        "forecast": forecast, "anomalies": anomalies,
        "sleep_debt": sleep_debt, "debt_log": debt_log,
        "heatmap": heatmap, "hypnogram": hypnogram,
        "deep_decoder": deep_decoder, "tonight_card": tonight_card,
    }


# ── Vercel handler ─────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass  # silence access logs

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "X-Oura-Token, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        token = self.headers.get("X-Oura-Token", "").strip() or \
                os.environ.get("OURA_TOKEN", "")

        if not token:
            body = json.dumps({"error": "missing_token",
                               "message": "Provide your Oura token via the X-Oura-Token header."}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
            return

        try:
            data = build_data(token)
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = json.dumps({"error": "fetch_failed", "message": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
