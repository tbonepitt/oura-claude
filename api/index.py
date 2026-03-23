#!/usr/bin/env python3
"""Oura Dashboard — Vercel serverless endpoint (Flask)
Token is read from the X-Oura-Token request header. Never stored server-side.
"""

import json, os, math, random, urllib.parse
from datetime import date, timedelta, datetime
from urllib.request import urlopen, Request
from urllib.error import URLError
from collections import defaultdict
from flask import Flask, jsonify, request, redirect

app = Flask(__name__)

BASE = "https://api.ouraring.com/v2/usercollection"

# ── Security headers on every API response ──────────────────────────────────
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options']        = 'DENY'
    response.headers['Referrer-Policy']        = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy']     = 'geolocation=(), microphone=(), camera=()'
    response.headers['Cache-Control']          = 'no-store'
    return response

# ── Helpers ────────────────────────────────────────────────────────────────────

def fetch(ep, start, end, token):
    url = f"{BASE}/{ep}?start_date={start}&end_date={end}" if (start and end) else f"{BASE}/{ep}"
    req = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(req, timeout=12) as r:
            body = json.loads(r.read())
            return body.get("data", body)
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

def linreg(xs, ys):
    """Return slope and intercept of least-squares line, or None if insufficient data."""
    pairs = [(x,y) for x,y in zip(xs,ys) if x is not None and y is not None]
    if len(pairs) < 8: return None
    n = len(pairs); mx = sum(p[0] for p in pairs)/n; my = sum(p[1] for p in pairs)/n
    ss_xx = sum((x-mx)**2 for x,y in pairs)
    if ss_xx == 0: return None
    slope = sum((x-mx)*(y-my) for x,y in pairs) / ss_xx
    return {"slope": slope, "intercept": my - slope * mx}

def clamp(v, lo, hi): return max(lo, min(hi, v))

# ── Upstash Vector (feedback storage) ─────────────────────────────────────────

def vector_request(path, body):
    """POST to Upstash Vector REST API."""
    url   = os.environ.get("UPSTASH_VECTOR_REST_URL", "").rstrip("/")
    token = os.environ.get("UPSTASH_VECTOR_REST_TOKEN", "")
    if not url or not token:
        return None
    try:
        req = Request(f"{url}{path}",
                      data=json.dumps(body).encode(),
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json"})
        with urlopen(req, timeout=6) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"Vector error: {e}", flush=True)
        return None

# ── Sleep Science ──────────────────────────────────────────────────────────────

STAGE_Y = {"1": 0, "2": 2, "3": 1, "4": 3}

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

    return {
        "labels": labels, "stages": stages, "colors": colors,
        "hr":  session.get("heart_rate", {}).get("items", []),
        "hrv": session.get("hrv", {}).get("items", []),
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
        if beds: optimal_bed = beds[0]

    issues = []
    best_steps = max([n.get("steps") or 0 for n in best_nights], default=8000)

    if steps < best_steps * 0.7:
        gap = best_steps - steps
        issues.append({"priority": 1, "icon": "🚶",
            "headline": f"Take a {min(30, max(10, gap//100))}-minute walk before bed",
            "body": f"You've done {steps:,} steps today. Your best deep sleep nights average {best_steps:,}.",
            "urgency": "high"})

    if optimal_bed:
        issues.append({"priority": 2, "icon": "🛏️",
            "headline": f"Be in bed by {optimal_bed}",
            "body": f"Your three best deep sleep nights all started before {optimal_bed}.",
            "urgency": "high" if datetime.now().hour >= 21 else "medium"})

    if hrv < 75:
        issues.append({"priority": 3, "icon": "💆",
            "headline": "Wind down early tonight — HRV is low",
            "body": f"Your HRV balance is {hrv}/100. Avoid screens and alcohol.",
            "urgency": "medium"})

    if debt_hrs > 3:
        issues.append({"priority": 4, "icon": "💳",
            "headline": f"You're {debt_hrs}h in sleep debt — don't cut tonight short",
            "body": "Your body needs at least 8 hours in bed tonight.",
            "urgency": "medium"})

    if any("restless" in f.get("title","").lower() for f in findings):
        issues.append({"priority": 5, "icon": "🌡️",
            "headline": "Cool your room tonight",
            "body": "Restlessness is your #1 deep sleep killer. Keep 65–68°F.",
            "urgency": "medium"})

    issues.sort(key=lambda x: x["priority"])

    if readiness >= 80 and steps >= 7000:
        verdict, verdict_msg, verdict_color = "great", "You're set up well for a good night.", "#22c55e"
    elif readiness >= 65 or steps >= 5000:
        verdict, verdict_msg, verdict_color = "ok", "Tonight is fixable. A couple of things to do before bed.", "#f59e0b"
    else:
        verdict, verdict_msg, verdict_color = "at-risk", "Today's numbers put your sleep at risk.", "#ef4444"

    return {"verdict": verdict, "verdict_msg": verdict_msg, "verdict_color": verdict_color,
            "optimal_bed": optimal_bed, "steps_today": steps, "best_steps": best_steps,
            "hrv": hrv, "debt": debt_hrs, "actions": issues[:2]}

def build_deep_sleep_decoder(sleep_detail, activity_map):
    nights = []
    for s in sleep_detail:
        phase = s.get("sleep_phase_5_min", "")
        if not phase or len(phase) < 20: continue
        deep_min = phase.count("1") * 5
        day = s.get("day", "")
        try:
            bh = datetime.fromisoformat(s.get("bedtime_start","")).hour + \
                 datetime.fromisoformat(s.get("bedtime_start","")).minute / 60
            if bh < 12: bh += 24
        except Exception:
            bh = None
        act = activity_map.get(day, {})
        nights.append({"day": day, "deep_min": deep_min, "total_min": len(phase)*5,
                       "bed_hour": bh, "steps": act.get("steps"),
                       "calories": act.get("active_calories"), "restless": s.get("restless_periods", 0)})

    if len(nights) < 10: return {}

    nights.sort(key=lambda x: x["deep_min"])
    n = len(nights)
    top_q = nights[int(n*0.75):]
    bot_q = nights[:int(n*0.25)]

    def avg(lst, key):
        vals = [x[key] for x in lst if x.get(key) is not None]
        return round(sum(vals)/len(vals), 1) if vals else None

    def fmt_hour(h):
        if h is None: return "—"
        h = h % 24; hh = int(h); mm = int((h%1)*60)
        return f"{hh if hh<=12 else hh-12}:{mm:02d}{'am' if hh<12 else 'pm'}"

    top_steps=avg(top_q,"steps"); bot_steps=avg(bot_q,"steps")
    top_bed=avg(top_q,"bed_hour"); bot_bed=avg(bot_q,"bed_hour")
    top_cal=avg(top_q,"calories"); bot_cal=avg(bot_q,"calories")
    top_rest=avg(top_q,"restless"); bot_rest=avg(bot_q,"restless")

    findings = []
    overall_avg = avg(nights,"deep_min")
    overall_std = round(std([n["deep_min"] for n in nights]), 0)

    if top_steps and bot_steps and (top_steps-bot_steps) > 500:
        diff = top_steps-bot_steps
        findings.append({"icon":"🚶","title":"Steps matter for YOU",
            "body":f"Best nights: {int(top_steps):,} steps. Worst: {int(bot_steps):,}. ({int(diff):,}-step gap)",
            "action":f"Aim for {int(top_steps):,} steps on days you want deep sleep.","impact":"high" if diff>1500 else "medium"})

    if top_bed and bot_bed and abs(bot_bed-top_bed) > 0.5:
        earlier = top_bed < bot_bed
        findings.append({"icon":"🛏️","title":f"{'Earlier' if earlier else 'Later'} bedtimes = more deep sleep",
            "body":f"Best nights: in bed ~{fmt_hour(top_bed)}. Worst: ~{fmt_hour(bot_bed)}.",
            "action":f"Target {fmt_hour(top_bed)} as your bedtime.","impact":"high"})

    if top_rest is not None and bot_rest is not None and (bot_rest-top_rest) > 50:
        findings.append({"icon":"🌀","title":"Restlessness is killing your deep sleep",
            "body":f"Bad nights: {int(bot_rest)} restless periods vs {int(top_rest)} on good nights.",
            "action":"Track alcohol, late meals, heat, or stress.","impact":"high"})

    if top_cal and bot_cal and (top_cal-bot_cal) > 100:
        findings.append({"icon":"🔥","title":"Active days → deeper sleep",
            "body":f"Best nights: {int(top_cal)} active calories. Worst: {int(bot_cal)}.",
            "action":"Light-to-moderate activity improves deep sleep quality.","impact":"medium"})

    pct_deep = round(overall_avg/(avg(nights,"total_min") or 480)*100, 0) if overall_avg else 0
    science = {
        "what_is_deep": "Deep sleep (slow-wave sleep) is your body's repair mode — growth hormone, memory consolidation, tissue repair, and brain waste clearance.",
        "your_avg": overall_avg, "your_std": overall_std, "ideal_min": 90,
        "ideal_pct": 20, "your_pct": pct_deep,
        "status": "low" if (overall_avg or 0)<60 else "fair" if (overall_avg or 0)<90 else "good",
        "status_msg": (f"Your average of {overall_avg} min is below the ideal 90+ min."
            if (overall_avg or 0)<60 else
            f"Your average of {overall_avg} min is getting there. Small tweaks could push you into the optimal zone."
            if (overall_avg or 0)<90 else
            f"Your deep sleep is solid at {overall_avg} min average."),
        "when_it_happens": "Most deep sleep happens in the first 3-4 hours of the night. Late bedtimes and alcohol cut into this window.",
        "why_variable": f"Your deep sleep swings {overall_std:.0f} min night to night — something specific is disrupting it on bad nights.",
    }

    best3  = sorted(nights, key=lambda x: -x["deep_min"])[:3]
    worst3 = sorted(nights, key=lambda x:  x["deep_min"])[:3]

    return {
        "science": science, "findings": findings,
        "best_nights":  [{"day":n["day"],"deep_min":n["deep_min"],"steps":n["steps"],"bed":fmt_hour(n["bed_hour"])} for n in best3],
        "worst_nights": [{"day":n["day"],"deep_min":n["deep_min"],"steps":n["steps"],"bed":fmt_hour(n["bed_hour"])} for n in worst3],
        "distribution": [n["deep_min"] for n in sorted(nights, key=lambda x: x["day"])],
        "distribution_days": [n["day"] for n in sorted(nights, key=lambda x: x["day"])],
        "top_avg": avg(top_q,"deep_min"), "bot_avg": avg(bot_q,"deep_min"), "overall_avg": overall_avg,
    }

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
    hrv_mom = ((mean([v for v in hrv_series[-5:] if v]) or 80) - (mean([v for v in hrv_series[-10:-5] if v]) or 80)) * 0.3
    act_avg = mean(act_scores) or 75
    act_load = ((mean([v for v in act_scores[-3:] if v]) or act_avg) - act_avg) * -0.15

    forecast = []
    for i in range(1, 8):
        fdate = today + timedelta(days=i)
        dow = fdate.weekday()
        base = dow_ready_avg.get(dow, global_avg)
        decay = 1.0 / (1 + i * 0.3)
        pred = clamp(round(base + trend_delta*decay*0.4 + hrv_mom*decay + act_load*decay), 55, 95)
        if pred >= 85:   rec, intensity, color = "Hard training", "💪 Push it", "#22c55e"
        elif pred >= 75: rec, intensity, color = "Moderate effort", "🟢 Go for it", "#3b82f6"
        elif pred >= 65: rec, intensity, color = "Easy day", "🟡 Take it easy", "#f59e0b"
        else:            rec, intensity, color = "Recovery", "🔴 Rest day", "#ef4444"
        forecast.append({"date": str(fdate), "dow": DOW_NAMES[dow],
            "month_day": fdate.strftime("%b %d"), "score": pred,
            "rec": rec, "intensity": intensity, "color": color, "is_weekend": dow >= 5})
    return forecast

def detect_anomalies(days, s_map, r_map, a_map, sleep_scores, ready_scores, act_scores):
    anomalies = []
    metrics = [("sleep",sleep_scores,s_map,"Sleep score dropped"),
               ("readiness",ready_scores,r_map,"Readiness dropped"),
               ("activity",act_scores,a_map,"Activity crashed")]
    for key, series, src_map, label in metrics:
        for i in range(14, len(days)):
            window_vals = [v for v in series[i-14:i] if v is not None]
            if len(window_vals) < 7: continue
            m = mean(window_vals); sd = std(window_vals)
            val = series[i]
            if val is None or val >= m - 1.5*sd: continue
            d = days[i]; causes = []
            entry = src_map.get(d, {}); contribs = entry.get("contributors", {})
            if key=="sleep":
                if contribs.get("deep_sleep",100)<30: causes.append("almost no deep sleep")
                if contribs.get("restfulness",100)<45: causes.append("very restless night")
                if contribs.get("efficiency",100)<65: causes.append("poor sleep efficiency")
                if contribs.get("total_sleep",100)<55: causes.append("short total sleep")
            elif key=="readiness":
                if contribs.get("hrv_balance",100)<65: causes.append("low HRV balance")
                if contribs.get("recovery_index",100)<55: causes.append("poor overnight recovery")
                temp = entry.get("temperature_deviation")
                if temp and temp>0.5: causes.append(f"elevated body temp (+{temp:.1f}°C)")
            elif key=="activity":
                steps = a_map.get(d,{}).get("steps",0)
                if steps<3000: causes.append(f"only {steps:,} steps")
            if i>0:
                prev_act = a_map.get(days[i-1],{}).get("steps",0)
                if prev_act>12000: causes.append(f"very high activity prior ({prev_act:,} steps)")
            anomalies.append({"date":d,"label":label,"metric":key,"score":val,
                "avg":round(m,0),"drop":round(m-val,0),"dow":date.fromisoformat(d).strftime("%a"),"causes":causes[:3]})
    seen = {}
    for a in sorted(anomalies, key=lambda x: -x["drop"]):
        k = f"{a['date']}-{a['metric']}"
        if k not in seen: seen[k] = a
    return sorted(seen.values(), key=lambda x: x["date"], reverse=True)[:8]

def calc_sleep_debt(sleep_detail, target_hours=8.0):
    """Calculate cumulative sleep debt, ignoring naps and partial syncs under 3h."""
    debt_hours = 0.0; log = []
    for d in sleep_detail[-30:]:
        raw = d.get("total_sleep_duration")
        actual = raw / 3600 if raw is not None else 0.0
        if raw is not None and actual < 3.0:   # skip naps / partial ring syncs
            continue
        nightly_debt = target_hours - actual
        debt_hours += nightly_debt
        log.append({"date":d.get("day",""),"actual":round(actual,2),
                    "debt":round(nightly_debt,2),"cumulative":round(debt_hours,2)})
    return round(debt_hours, 1), log

def calc_recovery_intelligence(detail, ready_data, sleep_data, hrv_series, debt_log, debt):
    """Compute personal sleep target, payback plan, debt trend, recovery rate, personal records, trajectories."""
    import math

    # 1. Personal sleep target — avg sleep on nights before top-quartile readiness
    ready_scores_all = [(r.get("day",""), r.get("score",0)) for r in ready_data if r.get("score")]
    if ready_scores_all:
        q75 = sorted([s for _,s in ready_scores_all])[int(len(ready_scores_all)*0.75)]
        top_ready_days = {day for day,score in ready_scores_all if score >= q75}
        target_nights = []
        for d in detail:
            day_after = (date.fromisoformat(d["day"]) + timedelta(days=1)).isoformat() if d.get("day") else None
            if day_after and day_after in top_ready_days:
                hrs = (d.get("total_sleep_duration") or 0) / 3600
                if hrs > 4: target_nights.append(hrs)
        personal_target = round(mean(target_nights), 1) if len(target_nights) >= 4 else 7.5
    else:
        personal_target = 7.5

    # 2. Average wake time and sleep latency from last 7 nights
    wake_times_min = []
    latency_mins   = []
    for d in detail[-7:]:
        end_str   = d.get("bedtime_end", "")
        start_str = d.get("bedtime_start", "")
        latency   = d.get("latency")  # seconds until sleep onset
        if end_str:
            try:
                wake_dt  = datetime.fromisoformat(end_str)
                wake_min = wake_dt.hour * 60 + wake_dt.minute
                wake_times_min.append(wake_min)
            except Exception:
                pass
        if latency and latency > 0:
            latency_mins.append(latency / 60)

    avg_wake_min    = round(mean(wake_times_min)) if wake_times_min else None
    avg_latency_min = round(mean(latency_mins))   if latency_mins   else 15  # default 15 min

    def mins_to_hhmm(m):
        """Convert minutes-since-midnight to HH:MM AM/PM, handling negative (previous day)."""
        m = m % (24 * 60)
        h, mn = divmod(m, 60)
        period = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{h12}:{mn:02d} {period}"

    # 3. Payback plan with bedtime (accounting for sleep latency)
    # "In bed by" = wake_time - target_sleep_hrs - avg_latency
    # This gives the time to GET INTO BED, not the time to fall asleep
    if debt > 0:
        nightly_surplus = min(1.0, round(personal_target * 0.12, 1))
        nights_to_clear = math.ceil(debt / nightly_surplus) if nightly_surplus > 0 else 99
        target_hrs = round(personal_target + nightly_surplus, 1)
        total_in_bed_min = round(target_hrs * 60) + avg_latency_min
        bedtime_str = mins_to_hhmm(avg_wake_min - total_in_bed_min) if avg_wake_min else None
        payback_plan = {
            "nights": nights_to_clear,
            "target_hrs": target_hrs,
            "surplus": nightly_surplus,
            "bedtime": bedtime_str,
            "wake_time": mins_to_hhmm(avg_wake_min) if avg_wake_min else None,
            "avg_latency_min": avg_latency_min,
        }
    else:
        total_in_bed_min = round(personal_target * 60) + avg_latency_min
        maintenance_bedtime = mins_to_hhmm(avg_wake_min - total_in_bed_min) if avg_wake_min else None
        payback_plan = None

    # 3. Debt trend over last 14 days
    if len(debt_log) >= 14:
        recent_cumul = [d["cumulative"] for d in debt_log[-14:]]
        reg = linreg(list(range(14)), recent_cumul)
        slope = reg["slope"] if reg else 0
        debt_trend = {
            "direction": "accumulating" if slope > 0.05 else "recovering" if slope < -0.05 else "stable",
            "hrs_per_week": round(abs(slope) * 7, 1)
        }
    else:
        debt_trend = {"direction": "stable", "hrs_per_week": 0}

    # 4. Last night delta
    last_night_delta = round(debt_log[-1]["debt"], 1) if debt_log else 0

    # 5. Recovery rate — nights to bounce back after readiness < 65
    if ready_scores_all:
        baseline = mean([s for _,s in ready_scores_all])
        scored_list = ready_scores_all  # list of (day, score) sorted by date
        recoveries = []
        for i, (day, score) in enumerate(scored_list):
            if score < 65:
                for j in range(i+1, min(i+8, len(scored_list))):
                    if scored_list[j][1] >= baseline:
                        recoveries.append(j - i); break
        recovery_rate = round(mean(recoveries), 1) if recoveries else None
    else:
        recovery_rate = None

    # 6. Personal records (all-time from full detail/sleep data)
    deep_records = [(d.get("day",""), (d.get("deep_sleep_duration") or 0)//60) for d in detail if d.get("deep_sleep_duration")]
    hrv_records  = [(s.get("day",""), s.get("average_hrv")) for s in sleep_data if s.get("average_hrv")]
    ready_records = ready_scores_all

    best_deep  = max(deep_records, key=lambda x: x[1]) if deep_records else None
    best_hrv   = max(hrv_records,  key=lambda x: x[1]) if hrv_records  else None
    best_ready = max(ready_records, key=lambda x: x[1]) if ready_records else None

    def fmt_record_date(d):
        try: return date.fromisoformat(d).strftime("%b %d")
        except: return d

    personal_records = {
        "best_deep":  {"value": best_deep[1],          "date": fmt_record_date(best_deep[0])}  if best_deep  else None,
        "best_hrv":   {"value": round(best_hrv[1], 0), "date": fmt_record_date(best_hrv[0])}   if best_hrv   else None,
        "best_ready": {"value": best_ready[1],          "date": fmt_record_date(best_ready[0])} if best_ready else None,
    }

    # 7. Readiness trajectory (14-day slope)
    recent_ready = [s for _,s in ready_scores_all[-14:]]
    if len(recent_ready) >= 7:
        reg_r = linreg(list(range(len(recent_ready))), recent_ready)
        slope_r = reg_r["slope"] if reg_r else 0
        total_change = round(slope_r * len(recent_ready), 0)
        ready_trajectory = {
            "direction": "improving" if slope_r > 0.3 else "declining" if slope_r < -0.3 else "stable",
            "change_pts": int(total_change)
        }
    else:
        ready_trajectory = {"direction": "stable", "change_pts": 0}

    # 8. HRV trajectory (21-day rolling comparison)
    hrv_clean = [v for v in hrv_series if v is not None]
    if len(hrv_clean) >= 28:
        hrv_recent = mean(hrv_clean[-21:])
        hrv_prior  = mean(hrv_clean[-42:-21]) if len(hrv_clean) >= 42 else mean(hrv_clean[:-21])
        pct = round((hrv_recent - hrv_prior) / hrv_prior * 100, 1) if hrv_prior else 0
        hrv_trajectory = {
            "direction": "rising" if pct > 3 else "falling" if pct < -3 else "stable",
            "pct_change": pct,
            "recent_avg": round(hrv_recent, 0),
            "prior_avg":  round(hrv_prior, 0)
        }
    else:
        hrv_trajectory = {"direction": "stable", "pct_change": 0, "recent_avg": 0, "prior_avg": 0}

    # Maintenance bedtime (no debt case)
    maintenance_bedtime = mins_to_hhmm(avg_wake_min - round(personal_target * 60)) if avg_wake_min else None
    avg_wake_str = mins_to_hhmm(avg_wake_min) if avg_wake_min else None

    return {
        "personal_target":      personal_target,
        "avg_latency_min":      avg_latency_min,
        "payback_plan":         payback_plan,
        "maintenance_bedtime":  maintenance_bedtime,
        "avg_wake_time":        mins_to_hhmm(avg_wake_min) if avg_wake_min else None,
        "debt_trend":           debt_trend,
        "last_night_delta":     last_night_delta,
        "recovery_rate":        recovery_rate,
        "personal_records":     personal_records,
        "ready_trajectory":     ready_trajectory,
        "hrv_trajectory":       hrv_trajectory,
    }

def build_data(token):
    today=date.today(); end=str(today)
    start60=str(today-timedelta(days=60)); start7=str(today-timedelta(days=7))

    sleep           = fetch("daily_sleep",              start60, end,  token)
    ready           = fetch("daily_readiness",          start60, end,  token)
    activity        = fetch("daily_activity",           start60, end,  token)
    detail          = fetch("sleep",                    start60, end,  token)
    hr_data         = fetch("heartrate",                start7,  end,  token)
    spo2            = fetch("daily_spo2",               start60, end,  token)
    stress          = fetch("daily_stress",             start60, end,  token)
    cardio          = fetch("daily_cardiovascular_age", start60, end,  token)
    vo2             = fetch("vO2_max",                  start60, end,  token)
    resilience_data = fetch("daily_resilience",         start60, end,  token)
    sleep_time_data = fetch("sleep_time",               start60, end,  token)
    rest_mode_data  = fetch("rest_mode_period",         start60, end,  token)
    workout_data    = fetch("workout",                  start60, end,  token)
    ring_config     = fetch("ring_configuration",       None,    None, token)
    user_info       = fetch("personal_info",            None,    None, token)

    # Index all endpoints by day
    spo2_map       = {d["day"]: d for d in (spo2            if isinstance(spo2,            list) else [])}
    stress_map     = {d["day"]: d for d in (stress          if isinstance(stress,          list) else [])}
    cardio_map     = {d["day"]: d for d in (cardio          if isinstance(cardio,          list) else [])}
    vo2_map        = {d["day"]: d for d in (vo2             if isinstance(vo2,             list) else [])}
    resilience_map = {d["day"]: d for d in (resilience_data if isinstance(resilience_data, list) else [])}
    sleep_time_map = {d["day"]: d for d in (sleep_time_data if isinstance(sleep_time_data, list) else [])}
    rest_periods   = rest_mode_data if isinstance(rest_mode_data, list) else []
    workout_list   = workout_data   if isinstance(workout_data,   list) else []
    ring_info      = ring_config[0] if isinstance(ring_config, list) and ring_config else {}

    s_map={d["day"]:d for d in sleep}; r_map={d["day"]:d for d in ready}; a_map={d["day"]:d for d in activity}
    # Use union of sleep+readiness days (activity often lags 1 day — don't drop today's sleep/readiness)
    days=sorted(set(s_map)|set(r_map))

    def series(src, key, sub=None):
        out=[]
        for d in days:
            val=src.get(d,{})
            if sub: val=val.get(sub,{})
            out.append(val.get(key))
        return out

    sleep_scores=series(s_map,"score"); ready_scores=series(r_map,"score"); act_scores=series(a_map,"score")
    steps_series=series(a_map,"steps"); calories=series(a_map,"active_calories")
    deep_series=series(s_map,"deep_sleep",sub="contributors"); rem_series=series(s_map,"rem_sleep",sub="contributors")
    rest_series=series(s_map,"restfulness",sub="contributors"); eff_series=series(s_map,"efficiency",sub="contributors")
    hrv_series=series(r_map,"hrv_balance",sub="contributors"); rhr_series=series(r_map,"resting_heart_rate",sub="contributors")
    temp_series=[r_map.get(d,{}).get("temperature_deviation") for d in days]

    latest_sleep=sleep[-1] if sleep else {}; latest_ready=ready[-1] if ready else {}
    latest_act=activity[-1] if activity else {}; latest_det=detail[-1] if detail else {}

    def fmt_dur(secs):
        if not secs: return "—"
        h,m=divmod(int(secs)//60,60); return f"{h}h {m:02d}m"

    arch={"total":fmt_dur(latest_det.get("total_sleep_duration")),"deep":fmt_dur(latest_det.get("deep_sleep_duration")),
          "rem":fmt_dur(latest_det.get("rem_sleep_duration")),"light":fmt_dur(latest_det.get("light_sleep_duration")),
          "deep_pct":round((latest_det.get("deep_sleep_duration") or 0)/max(latest_det.get("total_sleep_duration") or 1,1)*100,1),
          "rem_pct": round((latest_det.get("rem_sleep_duration")  or 0)/max(latest_det.get("total_sleep_duration") or 1,1)*100,1),
          "light_pct":round((latest_det.get("light_sleep_duration") or 0)/max(latest_det.get("total_sleep_duration") or 1,1)*100,1)}

    DOW=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    dow_sleep=defaultdict(list); dow_ready=defaultdict(list); dow_steps=defaultdict(list)
    for d in days:
        dow=date.fromisoformat(d).weekday()
        if s_map.get(d,{}).get("score"): dow_sleep[dow].append(s_map[d]["score"])
        if r_map.get(d,{}).get("score"): dow_ready[dow].append(r_map[d]["score"])
        if a_map.get(d,{}).get("steps"): dow_steps[dow].append(a_map[d]["steps"])

    n=len(days)

    # ── Workouts ──────────────────────────────────────────────────────────────
    workout_by_day = {}
    for w in workout_list:
        day = w.get("day", "")
        if day: workout_by_day.setdefault(day, []).append(w)

    def workout_dur_min(w):
        try:
            s = datetime.fromisoformat(w["start_datetime"].replace("Z",""))
            e = datetime.fromisoformat(w["end_datetime"].replace("Z",""))
            return max(0, int((e - s).total_seconds() // 60))
        except Exception: return 0

    workout_min_series  = [sum(workout_dur_min(w) for w in workout_by_day.get(d,[])) for d in days]
    workout_cal_series  = [round(sum((w.get("calories") or 0) for w in workout_by_day.get(d,[]))) for d in days]
    workout_active_days = [1 if workout_by_day.get(d) else 0 for d in days]

    activity_counts = {}
    for w in workout_list:
        act = w.get("activity","other")
        activity_counts[act] = activity_counts.get(act,0) + 1
    activity_counts = dict(sorted(activity_counts.items(), key=lambda x: -x[1]))

    recent_cutoff = str(today - timedelta(days=21))
    def fmt_workout(w):
        return {"day":w.get("day"),"activity":w.get("activity","other"),
                "intensity":w.get("intensity","moderate"),"duration_min":workout_dur_min(w),
                "calories":round(w.get("calories") or 0),
                "start_time":w.get("start_datetime","")[:16] if w.get("start_datetime") else ""}
    recent_workouts = [fmt_workout(w) for w in sorted(
        [w for w in workout_list if w.get("day","") >= recent_cutoff],
        key=lambda w: w.get("start_datetime",""), reverse=True
    )[:20]]

    workout_ready_corr = pearson(workout_active_days[:-1], ready_scores[1:]) if n > 5 else None

    # ── Resilience ────────────────────────────────────────────────────────────
    RESILIENCE_SCORE = {"limited":25,"adequate":45,"solid":65,"strong":80,"exceptional":95}
    RESILIENCE_COLOR = {"limited":"#ef4444","adequate":"#f59e0b","solid":"#3b82f6","strong":"#22c55e","exceptional":"#a855f7"}
    res_level_series    = [resilience_map.get(d,{}).get("level") for d in days]
    res_numeric_series  = [RESILIENCE_SCORE.get(l) for l in res_level_series]
    res_sleep_rec       = [resilience_map.get(d,{}).get("contributors",{}).get("sleep_recovery") for d in days]
    res_day_rec         = [resilience_map.get(d,{}).get("contributors",{}).get("daytime_recovery") for d in days]
    res_stress          = [resilience_map.get(d,{}).get("contributors",{}).get("stress") for d in days]
    latest_res          = resilience_map.get(days[-1],{}) if days else {}
    latest_res_level    = latest_res.get("level","")
    latest_res_contribs = latest_res.get("contributors",{})

    # ── Sleep Recommendation ──────────────────────────────────────────────────
    SLEEP_REC_MAP = {"earlier_bedtime":"Go to bed 30–60 min earlier tonight",
                     "later_bedtime":"You can stay up a bit later tonight",
                     "maintain_bedtime":"Your bedtime is dialed in — stick with it",
                     "not_enough_data":"Building your sleep profile…"}
    latest_sleep_time    = sleep_time_map.get(days[-1],{}) if days else {}
    sleep_rec_raw        = latest_sleep_time.get("recommendation","")
    sleep_recommendation = SLEEP_REC_MAP.get(sleep_rec_raw, sleep_rec_raw.replace("_"," ").title() if sleep_rec_raw else "")

    # ── Ring Configuration ────────────────────────────────────────────────────
    HW_NAMES    = {"gen1":"Ring 1","gen2":"Ring 2","gen2m":"Ring 2M","gen3":"Ring 3","gen4":"Ring 4"}
    COLOR_NAMES = {"silver":"Silver","black":"Black","stealth_black":"Stealth Black","gold":"Gold",
                   "rose_gold":"Rose Gold","matte_black":"Matte Black","sandstone":"Sandstone",
                   "brushed_titanium":"Brushed Titanium","horizon_silver":"Horizon Silver","horizon_gold":"Horizon Gold"}
    ring_display = {
        "model":    HW_NAMES.get(ring_info.get("hardware_type",""), ring_info.get("hardware_type","Unknown").replace("_"," ").title()),
        "color":    COLOR_NAMES.get(ring_info.get("color",""),       ring_info.get("color","").replace("_"," ").title()),
        "size":     ring_info.get("size"),
        "firmware": ring_info.get("firmware_version",""),
        "since":    (ring_info.get("set_up_at","") or "")[:10],
    }

    corrs={"steps_deep":pearson(steps_series[:n-1],deep_series[1:n]),
           "sleep_ready":pearson(sleep_scores[:n-1],ready_scores[1:n]),
           "cal_deep":pearson(calories[:n-1],deep_series[1:n]),
           "hrv_sleep":pearson(hrv_series,sleep_scores),
           "temp_sleep":pearson(temp_series,sleep_scores),
           "rest_ready":pearson(rest_series,ready_scores),
           "workout_ready":workout_ready_corr}

    # Vitals latest — needed by both insights block and return
    spo2_series    = [spo2_map.get(d,{}).get("spo2_percentage",{}) or {} for d in days]
    spo2_avg       = [s.get("average") for s in spo2_series]
    spo2_bdi       = [spo2_map.get(d,{}).get("breathing_disturbance_index") for d in days]
    stress_high    = [round((stress_map.get(d,{}).get("stress_high") or 0)/60) for d in days]
    recovery_high  = [round((stress_map.get(d,{}).get("recovery_high") or 0)/60) for d in days]
    stress_summary = [stress_map.get(d,{}).get("day_summary") for d in days]
    cardio_age     = [cardio_map.get(d,{}).get("vascular_age") for d in days]
    vo2_series     = [vo2_map.get(d,{}).get("vo2_max") for d in days]
    latest_spo2    = next((v for v in reversed(spo2_avg) if v is not None), None)
    latest_bdi     = next((v for v in reversed(spo2_bdi) if v is not None), None)
    latest_stress  = stress_map.get(days[-1],{}) if days else {}
    latest_cardio  = next((v for v in reversed(cardio_age) if v is not None), None)
    latest_vo2     = next((v for v in reversed(vo2_series) if v is not None), None)

    # ── Correlation Insights — translate numbers to plain English ─────────────
    STEP_THRESHOLD = 8000
    steps_deep_pairs = [(s,d) for s,d in zip(steps_series,deep_series) if s is not None and d is not None]
    high_step_deep = mean([d for s,d in steps_deep_pairs if s >= STEP_THRESHOLD])
    low_step_deep  = mean([d for s,d in steps_deep_pairs if s < STEP_THRESHOLD])
    steps_deep_reg = linreg(steps_series, deep_series)
    steps_per_1k   = round(steps_deep_reg["slope"]*1000,1) if steps_deep_reg else None
    today_steps    = latest_act.get("steps") or 0
    steps_gap      = max(0, STEP_THRESHOLD - today_steps)

    sr_reg            = linreg(sleep_scores[:-1], ready_scores[1:])
    sleep_ready_per10 = round(sr_reg["slope"]*10,1) if sr_reg else None

    hrv_reg           = linreg(hrv_series, sleep_scores)
    hrv_sleep_per10   = round(hrv_reg["slope"]*10,1) if hrv_reg else None

    wo_high = mean([r for a,r in zip(workout_active_days[:-1],ready_scores[1:]) if a==1 and r is not None])
    wo_low  = mean([r for a,r in zip(workout_active_days[:-1],ready_scores[1:]) if a==0 and r is not None])
    wo_diff = round(wo_high - wo_low, 1) if (wo_high and wo_low) else None

    temp_elev_sleep = mean([s for t,s in zip(temp_series,sleep_scores) if t is not None and t>=0.5 and s is not None])
    temp_norm_sleep = mean([s for t,s in zip(temp_series,sleep_scores) if t is not None and t< 0.5 and s is not None])
    temp_diff       = round(temp_elev_sleep - temp_norm_sleep,1) if (temp_elev_sleep and temp_norm_sleep) else None

    raw_age = user_info.get("age") if isinstance(user_info, dict) else None
    user_age = int(raw_age) if raw_age is not None else None
    cardio_gap = round(user_age - latest_cardio) if (user_age and latest_cardio) else None

    stress_streak = 0
    for d in reversed(days):
        if stress_map.get(d,{}).get("day_summary")=="stressful": stress_streak+=1
        else: break

    sh_today = latest_stress.get("stress_high") or 0
    rh_today = latest_stress.get("recovery_high") or 0
    tt = sh_today + rh_today
    stress_ratio_pct = round(rh_today/tt*100) if tt>0 else None
    typical_ratios = [rh/(sh+rh)*100 for d in days[-30:]
                      for sh,rh in [(stress_map.get(d,{}).get("stress_high") or 0,
                                     stress_map.get(d,{}).get("recovery_high") or 0)]
                      if sh+rh>0]
    typical_stress_ratio = round(mean(typical_ratios)) if typical_ratios else None

    # readiness drop on consecutive stress days
    stress_ready_impact = None
    stress_days_idx = [i for i,d in enumerate(days[:-1]) if stress_map.get(d,{}).get("day_summary")=="stressful"]
    if len(stress_days_idx) >= 3:
        stress_next = [ready_scores[i+1] for i in stress_days_idx if ready_scores[i+1] is not None]
        non_stress_idx = [i for i,d in enumerate(days[:-1]) if stress_map.get(d,{}).get("day_summary")!="stressful"]
        non_stress_next = [ready_scores[i+1] for i in non_stress_idx if ready_scores[i+1] is not None]
        if stress_next and non_stress_next:
            stress_ready_impact = round(mean(stress_next) - mean(non_stress_next), 1)

    correlation_insights = {
        "steps_deep": {
            "high_avg_min": round(high_step_deep) if high_step_deep else None,
            "low_avg_min":  round(low_step_deep)  if low_step_deep  else None,
            "threshold":    STEP_THRESHOLD,
            "diff_min":     round(high_step_deep - low_step_deep) if (high_step_deep and low_step_deep) else None,
            "per_1k":       steps_per_1k,
            "today_steps":  today_steps,
            "steps_gap":    steps_gap,
        },
        "sleep_ready": {"per_10": sleep_ready_per10, "last_sleep": latest_sleep.get("score")},
        "workout_ready": {
            "workout_avg": round(wo_high) if wo_high else None,
            "rest_avg":    round(wo_low)  if wo_low  else None,
            "diff":        wo_diff,
        },
        "hrv_sleep": {"per_10": hrv_sleep_per10, "latest_hrv": latest_ready.get("contributors",{}).get("hrv_balance")},
        "temp_sleep": {
            "elevated_avg": round(temp_elev_sleep) if temp_elev_sleep else None,
            "normal_avg":   round(temp_norm_sleep)  if temp_norm_sleep  else None,
            "diff":         temp_diff,
            "latest_temp":  latest_ready.get("temperature_deviation"),
        },
        "cardio": {"user_age": user_age, "vascular_age": latest_cardio, "gap": cardio_gap},
        "stress": {
            "streak":       stress_streak,
            "ratio_pct":    stress_ratio_pct,
            "typical_pct":  typical_stress_ratio,
            "summary":      latest_stress.get("day_summary"),
            "ready_impact": stress_ready_impact,
        },
    }

    r_s=corrs["sleep_ready"] or 0; r_h=pearson(hrv_series[:n-1],ready_scores[1:n]) or 0
    tw=abs(r_s)+abs(r_h)+0.01
    pred=round((mean(ready_scores) or 78)*0.4+latest_sleep.get("score",75)*abs(r_s)/tw*0.35+
               latest_ready.get("contributors",{}).get("hrv_balance",80)*abs(r_h)/tw*0.25)

    hrv_30=[v for v in hrv_series[-30:] if v is not None]
    hrv_delta=round((mean(hrv_30[-10:]) or 0)-(mean(hrv_30[:10]) or 0),1) if len(hrv_30)>=20 else 0

    forecast=build_forecast(days,r_map,s_map,a_map,ready_scores,sleep_scores,hrv_series,act_scores)
    anomalies=detect_anomalies(days,s_map,r_map,a_map,sleep_scores,ready_scores,act_scores)
    sleep_debt,debt_log=calc_sleep_debt(detail)
    recovery_intel=calc_recovery_intelligence(detail,ready,sleep,hrv_series,debt_log,sleep_debt)

    resting_hr_timeline=[{"t":h["timestamp"][:16],"bpm":h["bpm"]} for h in hr_data if h.get("source")=="rest" and h.get("bpm")]

    hypnogram=parse_hypnogram(detail[-1] if detail else {})
    deep_decoder=build_deep_sleep_decoder(detail,a_map)
    tonight_card=build_tonight_card(latest_act,latest_ready,latest_sleep,deep_decoder,
                                    sleep_debt,act_scores,ready_scores,sleep_scores)
    heatmap=[{"date":d,"score":r_map.get(d,{}).get("score")} for d in days]
    first_name = user_info.get("first_name","") if isinstance(user_info,dict) else ""
    email      = user_info.get("email","")      if isinstance(user_info,dict) else ""
    user_age   = user_info.get("age")           if isinstance(user_info,dict) else None
    bio_sex    = user_info.get("biological_sex","") if isinstance(user_info,dict) else ""
    weight_kg  = user_info.get("weight")        if isinstance(user_info,dict) else None
    height_m   = user_info.get("height")        if isinstance(user_info,dict) else None

    return {"generated":str(today),"user":{
        "first_name": first_name, "email": email,
        "age": user_age, "biological_sex": bio_sex,
        "weight_kg": weight_kg, "height_m": height_m,
    },"days":days,
        "ring": ring_display,
        "scores":{"sleep":sleep_scores,"ready":ready_scores,"activity":act_scores,
                  "steps":steps_series,"calories":calories,"deep":deep_series,"rem":rem_series,
                  "restfulness":rest_series,"efficiency":eff_series,"hrv":hrv_series,"rhr":rhr_series,"temp":temp_series},
        "data_dates":{
            "today":          str(today),
            "activity":       latest_act.get("day"),
            "sleep":          latest_sleep.get("day"),
            "readiness":      latest_ready.get("day"),
            "detail":         latest_det.get("day"),
            "activity_is_yesterday": latest_act.get("day") != str(today),
        },
        "latest":{"sleep":latest_sleep.get("score"),"ready":latest_ready.get("score"),
                  "activity":latest_act.get("score"),"steps":latest_act.get("steps"),
                  "calories":latest_act.get("active_calories"),"avg_hrv":latest_det.get("average_hrv"),
                  "avg_hr":latest_det.get("average_heart_rate"),
                  "hrv_bal":latest_ready.get("contributors",{}).get("hrv_balance"),
                  "rhr":latest_ready.get("contributors",{}).get("resting_heart_rate"),
                  "temp_dev":latest_ready.get("temperature_deviation"),"arch":arch,
                  "contributors":{"sleep":latest_sleep.get("contributors",{}),"ready":latest_ready.get("contributors",{})}},
        "avgs":{"sleep":mean(sleep_scores[-30:]),"ready":mean(ready_scores[-30:]),
                "activity":mean(act_scores[-30:]),"hrv":mean(hrv_series[-30:]),
                "deep":mean(deep_series[-30:]),"restfulness":mean(rest_series[-30:])},
        "prediction":pred,"hrv_delta":hrv_delta,
        "dow":{"labels":DOW,"sleep":[mean(dow_sleep[i]) for i in range(7)],
               "ready":[mean(dow_ready[i]) for i in range(7)],"steps":[mean(dow_steps[i]) for i in range(7)]},
        "correlations":corrs,"correlation_insights":correlation_insights,
        "resting_hr":resting_hr_timeline[-200:],"checkin_insights":[],
        "forecast":forecast,"anomalies":anomalies,"sleep_debt":sleep_debt,"debt_log":debt_log,
        "recovery_intel":recovery_intel,
        "heatmap":heatmap,"hypnogram":hypnogram,"deep_decoder":deep_decoder,"tonight_card":tonight_card,
        "sleep_recommendation": sleep_recommendation,
        "resilience":{
            "levels":res_level_series,"numeric":res_numeric_series,
            "sleep_recovery":res_sleep_rec,"daytime_recovery":res_day_rec,"stress":res_stress,
            "latest":{"level":latest_res_level,
                      "sleep_recovery":latest_res_contribs.get("sleep_recovery"),
                      "daytime_recovery":latest_res_contribs.get("daytime_recovery"),
                      "stress":latest_res_contribs.get("stress")}
        },
        "workouts":{
            "active_minutes":workout_min_series,"calories":workout_cal_series,
            "active_days":workout_active_days,"activity_counts":activity_counts,
            "recent":recent_workouts,
            "total_active_days":sum(workout_active_days),
            "total_active_minutes":sum(workout_min_series),
        },
        "vitals":{
            "spo2_avg":spo2_avg,"spo2_bdi":spo2_bdi,
            "stress_high":stress_high,"recovery_high":recovery_high,"stress_summary":stress_summary,
            "cardio_age":cardio_age,"vo2_max":vo2_series,
            "latest":{"spo2":latest_spo2,"bdi":latest_bdi,
                      "stress_high_min":round((latest_stress.get("stress_high") or 0)/60),
                      "recovery_high_min":round((latest_stress.get("recovery_high") or 0)/60),
                      "stress_summary":latest_stress.get("day_summary"),
                      "cardio_age":latest_cardio,"vo2_max":latest_vo2}
        }}

# ── Demo data generator ────────────────────────────────────────────────────────

def generate_demo_data():
    """Realistic demo data — seeded so it's consistent across loads."""
    rng = random.Random(42)
    today = date.today()
    DOW = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

    days, sleep_scores, ready_scores, act_scores = [], [], [], []
    steps_series, calories, deep_series, rem_series = [], [], [], []
    rest_series, eff_series, hrv_series, rhr_series, temp_series = [], [], [], [], []

    # Simulate a 60-day pattern: decent baseline with a rough patch around day 35-42
    for i in range(60, 0, -1):
        d = str(today - timedelta(days=i))
        days.append(d)
        rough = 28 <= i <= 42   # rough week
        base_sleep = rng.gauss(62 if rough else 78, 7)
        base_ready = rng.gauss(58 if rough else 76, 8)
        sleep_scores.append(max(40, min(99, round(base_sleep + rng.gauss(0, 3)))))
        ready_scores.append(max(40, min(99, round(base_ready + rng.gauss(0, 3)))))
        act_scores.append(max(30, min(99, round(rng.gauss(58 if rough else 74, 10)))))
        steps_series.append(round(rng.gauss(5200 if rough else 8400, 1800)))
        calories.append(round(rng.gauss(280 if rough else 420, 80)))
        deep_series.append(max(10, min(95, round(rng.gauss(45 if rough else 68, 12)))))
        rem_series.append(max(10, min(95, round(rng.gauss(58, 10)))))
        rest_series.append(max(20, min(95, round(rng.gauss(50 if rough else 72, 12)))))
        eff_series.append(max(50, min(95, round(rng.gauss(72, 8)))))
        hrv_series.append(max(40, min(95, round(rng.gauss(55 if rough else 74, 8)))))
        rhr_series.append(max(40, min(80, round(rng.gauss(58 if rough else 52, 4)))))
        temp_series.append(round(rng.gauss(0.3 if rough else -0.1, 0.2), 2))

    # Day-of-week avgs
    dow_sleep = defaultdict(list); dow_ready = defaultdict(list); dow_steps = defaultdict(list)
    for i, d in enumerate(days):
        dow = date.fromisoformat(d).weekday()
        dow_sleep[dow].append(sleep_scores[i]); dow_ready[dow].append(ready_scores[i]); dow_steps[dow].append(steps_series[i])

    # Correlations (realistic)
    corrs = {"steps_deep": 0.41, "sleep_ready": 0.63, "cal_deep": 0.38,
             "hrv_sleep": 0.55, "temp_sleep": -0.29, "rest_ready": 0.48, "workout_ready": 0.34}

    # Forecast
    forecast = []
    for i in range(1, 8):
        fdate = today + timedelta(days=i)
        dow = fdate.weekday()
        score = clamp(round(rng.gauss(76, 6)), 60, 92)
        if score >= 85:   rec, intensity, color = "Hard training", "💪 Push it", "#22c55e"
        elif score >= 75: rec, intensity, color = "Moderate effort", "🟢 Go for it", "#3b82f6"
        elif score >= 65: rec, intensity, color = "Easy day", "🟡 Take it easy", "#f59e0b"
        else:             rec, intensity, color = "Recovery", "🔴 Rest day", "#ef4444"
        forecast.append({"date":str(fdate),"dow":DOW[dow],"month_day":fdate.strftime("%b %d"),
                         "score":score,"rec":rec,"intensity":intensity,"color":color,"is_weekend":dow>=5})

    # Anomalies — two real-looking crash events
    anomalies = [
        {"date": str(today - timedelta(days=35)), "label": "Sleep score dropped", "metric": "sleep",
         "score": 51, "avg": 76, "drop": 25, "dow": "Mon",
         "causes": ["very restless night", "off-schedule bedtime", "low HRV balance"]},
        {"date": str(today - timedelta(days=29)), "label": "Readiness dropped", "metric": "readiness",
         "score": 48, "avg": 74, "drop": 26, "dow": "Sun",
         "causes": ["poor overnight recovery", "elevated resting HR", "elevated body temp (+0.6°C)"]},
        {"date": str(today - timedelta(days=12)), "label": "Activity crashed", "metric": "activity",
         "score": 44, "avg": 72, "drop": 28, "dow": "Thu",
         "causes": ["only 1,842 steps", "very high activity prior (14,200 steps)"]},
    ]

    # Hypnogram — realistic last-night pattern
    bedtime_dt = datetime.combine(today - timedelta(days=1), datetime.strptime("22:38", "%H:%M").time())
    phase_pattern = (
        "4444" +         # fall asleep (~20 min awake)
        "1111111111" +   # first deep block (50 min)
        "222222" +       # light (30 min)
        "333333" +       # REM (30 min)
        "44" +           # brief wake
        "11111111" +     # second deep block (40 min)
        "2222222222" +   # light (50 min)
        "33333333" +     # REM (40 min)
        "2222" +         # light (20 min)
        "3333" +         # REM (20 min)
        "44"             # wake
    )
    hyp_labels, hyp_stages, hyp_colors = [], [], []
    COLOR_MAP = {"1":"#3b82f6","2":"#6366f1","3":"#a855f7","4":"#374151"}
    for i, ch in enumerate(phase_pattern):
        t = bedtime_dt + timedelta(minutes=i*5)
        hyp_labels.append(t.strftime("%H:%M"))
        hyp_stages.append(STAGE_Y.get(ch, 3))
        hyp_colors.append(COLOR_MAP.get(ch, "#374151"))
    hr_items = [max(42, min(72, round(rng.gauss(54, 6)))) for _ in phase_pattern]
    hypnogram = {
        "labels": hyp_labels, "stages": hyp_stages, "colors": hyp_colors,
        "hr": hr_items, "hrv": [],
        "deep_min": phase_pattern.count("1")*5, "light_min": phase_pattern.count("2")*5,
        "rem_min": phase_pattern.count("3")*5, "awake_min": phase_pattern.count("4")*5,
        "total_min": len(phase_pattern)*5,
        "bedtime": bedtime_dt.isoformat()[:16],
        "wake_time": (bedtime_dt + timedelta(minutes=len(phase_pattern)*5)).isoformat()[:16],
        "efficiency": 88, "restless_periods": 12, "avg_hr": 54, "avg_hrv": 38, "lowest_hr": 46,
    }

    # Deep sleep decoder
    deep_decoder = {
        "science": {
            "what_is_deep": "Deep sleep (slow-wave sleep) is your body's repair mode — growth hormone, memory consolidation, tissue repair, and brain waste clearance.",
            "your_avg": 72.0, "your_std": 18.0, "ideal_min": 90, "ideal_pct": 20, "your_pct": 22,
            "status": "fair",
            "status_msg": "Your average of 72 min is getting there. Small tweaks could push you into the optimal zone.",
            "when_it_happens": "Most deep sleep happens in the first 3-4 hours of the night. Late bedtimes and alcohol cut into this window.",
            "why_variable": "Your deep sleep swings 18 min night to night — something specific is disrupting it on bad nights.",
        },
        "findings": [
            {"icon":"🚶","title":"Steps matter for YOU",
             "body":"Best deep sleep nights: 9,800 steps avg. Worst: 5,200. That's a 4,600-step gap.",
             "action":"Aim for 9,000+ steps on days you want deep sleep.","impact":"high"},
            {"icon":"🛏️","title":"Earlier bedtimes = more deep sleep",
             "body":"Best nights: in bed ~10:28pm. Worst: ~12:14am.",
             "action":"Target 10:30pm as your bedtime for better deep sleep.","impact":"high"},
            {"icon":"🌀","title":"Restlessness is killing your deep sleep",
             "body":"Bad nights: 28 restless periods vs 8 on good nights.",
             "action":"Track alcohol, late meals, heat, or stress.","impact":"high"},
        ],
        "best_nights":  [{"day":str(today-timedelta(days=5)), "deep_min":105,"steps":10200,"bed":"10:22pm"},
                         {"day":str(today-timedelta(days=11)),"deep_min":98, "steps":9800, "bed":"10:41pm"},
                         {"day":str(today-timedelta(days=18)),"deep_min":94, "steps":9100, "bed":"10:28pm"}],
        "worst_nights": [{"day":str(today-timedelta(days=36)),"deep_min":28, "steps":4200, "bed":"12:31am"},
                         {"day":str(today-timedelta(days=30)),"deep_min":32, "steps":5100, "bed":"12:08am"},
                         {"day":str(today-timedelta(days=22)),"deep_min":38, "steps":6200, "bed":"11:52pm"}],
        "distribution": [rng.randint(30,105) for _ in range(60)],
        "distribution_days": days,
        "top_avg": 99.0, "bot_avg": 33.0, "overall_avg": 72.0,
    }

    # Tonight card
    tonight_card = {
        "verdict": "ok", "verdict_msg": "Tonight is fixable. A couple of things to do before bed.",
        "verdict_color": "#f59e0b", "optimal_bed": "10:28pm",
        "steps_today": 6100, "best_steps": 9800, "hrv": 71, "debt": 4.2,
        "actions": [
            {"priority":1,"icon":"🚶","headline":"Take a 20-minute walk before bed",
             "body":"You've done 6,100 steps today. Your best deep sleep nights average 9,800. A short walk tonight could add 15–25 minutes of deep sleep.","urgency":"high"},
            {"priority":2,"icon":"🛏️","headline":"Be in bed by 10:28pm",
             "body":"Your three best deep sleep nights all started before 10:28pm. Every hour later costs roughly 20 minutes of deep sleep.","urgency":"medium"},
        ],
    }

    # Sleep debt
    sleep_debt = 4.2
    debt_log = [{"date":str(today-timedelta(days=30-i)),"actual":round(rng.gauss(6.8,0.8),2),
                 "debt":round(rng.gauss(1.2,0.8),2),"cumulative":round(i*0.14,2)} for i in range(30)]

    # Resting HR timeline
    resting_hr = [{"t":(today - timedelta(days=6-i//4)).isoformat()+"T0"+str(i%4+1)+":00","bpm":round(rng.gauss(53,4))} for i in range(48)]

    heatmap = [{"date":d,"score":s} for d,s in zip(days,ready_scores)]

    # Demo resilience series
    RES_LEVELS = ["limited","adequate","solid","strong","exceptional"]
    res_level_series = []
    for i in range(60):
        rough = 28 <= (60-i) <= 42
        level = rng.choice(["adequate","solid"] if rough else ["solid","solid","strong"])
        res_level_series.append(level)
    RES_SCORE = {"limited":25,"adequate":45,"solid":65,"strong":80,"exceptional":95}
    res_numeric  = [RES_SCORE.get(l) for l in res_level_series]
    res_sleep_r  = [round(rng.gauss(52 if rough else 60, 5), 1) for rough in [28<=(60-i)<=42 for i in range(60)]]
    res_day_r    = [round(rng.gauss(44 if rough else 51, 5), 1) for rough in [28<=(60-i)<=42 for i in range(60)]]
    res_stress_c = [round(rng.gauss(58 if rough else 67, 6), 1) for rough in [28<=(60-i)<=42 for i in range(60)]]

    # Demo workouts (≈4 per week, mix of activities)
    ACTIVITIES = ["walking","walking","walking","strengthTraining","cycling","basketball","yardwork","other"]
    workout_min_series = []
    workout_cal_series = []
    workout_active_days = []
    recent_workouts = []
    activity_counts = {}
    for i, d in enumerate(days):
        if rng.random() < 0.55:
            act = rng.choice(ACTIVITIES)
            dur = round(rng.gauss(35, 12))
            cal = round(dur * rng.gauss(6, 1.5))
            workout_min_series.append(dur)
            workout_cal_series.append(cal)
            workout_active_days.append(1)
            activity_counts[act] = activity_counts.get(act, 0) + 1
            if i >= 39:  # last 21 days
                recent_workouts.append({"day":d,"activity":act,"intensity":"moderate",
                                         "duration_min":dur,"calories":cal,"start_time":d+"T09:00"})
        else:
            workout_min_series.append(0)
            workout_cal_series.append(0)
            workout_active_days.append(0)
    recent_workouts = list(reversed(recent_workouts))[:15]
    activity_counts = dict(sorted(activity_counts.items(), key=lambda x: -x[1]))

    return {
        "generated": str(today), "is_demo": True,
        "user": {"first_name": "Demo"},
        "ring": {"model":"Ring 4","color":"Stealth Black","size":9,"firmware":"2.10.4","since":"2025-12-25"},
        "sleep_recommendation": "Go to bed 30–60 min earlier tonight",
        "days": days,
        "scores": {"sleep":sleep_scores,"ready":ready_scores,"activity":act_scores,
                   "steps":steps_series,"calories":calories,"deep":deep_series,"rem":rem_series,
                   "restfulness":rest_series,"efficiency":eff_series,"hrv":hrv_series,
                   "rhr":rhr_series,"temp":temp_series},
        "latest": {"sleep":78,"ready":74,"activity":71,"steps":6100,"calories":380,
                   "avg_hrv":38,"avg_hr":54,"hrv_bal":71,"rhr":52,"temp_dev":0.1,
                   "arch":{"total":"7h 10m","deep":"1h 12m","rem":"1h 35m","light":"4h 08m",
                           "deep_pct":16.8,"rem_pct":22.1,"light_pct":57.7},
                   "contributors":{"sleep":{"deep_sleep":72,"rem_sleep":78,"restfulness":65,
                                            "efficiency":82,"timing":74,"total_sleep":80},
                                   "ready":{"hrv_balance":71,"recovery_index":68,
                                            "resting_heart_rate":74,"previous_night":78}}},
        "avgs": {"sleep":mean(sleep_scores[-30:]),"ready":mean(ready_scores[-30:]),
                 "activity":mean(act_scores[-30:]),"hrv":mean(hrv_series[-30:]),
                 "deep":mean(deep_series[-30:]),"restfulness":mean(rest_series[-30:])},
        "prediction": 76, "hrv_delta": -3.2,
        "dow": {"labels":DOW,
                "sleep":[mean(dow_sleep[i]) for i in range(7)],
                "ready":[mean(dow_ready[i]) for i in range(7)],
                "steps":[mean(dow_steps[i]) for i in range(7)]},
        "correlations": corrs,
        "correlation_insights": {
            "steps_deep":   {"high_avg_min":82,"low_avg_min":56,"threshold":8000,"diff_min":26,"per_1k":5.2,"today_steps":6100,"steps_gap":1900},
            "sleep_ready":  {"per_10":3.8,"last_sleep":78},
            "workout_ready":{"workout_avg":77,"rest_avg":72,"diff":5.0},
            "hrv_sleep":    {"per_10":4.1,"latest_hrv":71},
            "temp_sleep":   {"elevated_avg":61,"normal_avg":74,"diff":-13.0,"latest_temp":0.1},
            "cardio":       {"user_age":38,"vascular_age":32,"gap":6},
            "stress":       {"streak":0,"ratio_pct":62,"typical_pct":55,"summary":"normal","ready_impact":-8.4},
        },
        "resting_hr": resting_hr, "checkin_insights": [],
        "forecast": forecast, "anomalies": anomalies,
        "sleep_debt": sleep_debt, "debt_log": debt_log,
        "recovery_intel": {
            "personal_target":     7.3,
            "avg_latency_min":     14,
            "payback_plan":        {"nights": 6, "target_hrs": 8.2, "surplus": 0.9, "bedtime": "9:51 PM", "wake_time": "6:17 AM", "avg_latency_min": 14},
            "maintenance_bedtime": "10:41 PM",
            "avg_wake_time":       "6:17 AM",
            "debt_trend":          {"direction": "accumulating", "hrs_per_week": 1.4},
            "last_night_delta":    1.3,
            "recovery_rate":       2.1,
            "personal_records": {
                "best_deep":  {"value": 102, "date": "Feb 14"},
                "best_hrv":   {"value": 89,  "date": "Jan 28"},
                "best_ready": {"value": 94,  "date": "Feb 02"},
            },
            "ready_trajectory": {"direction": "declining", "change_pts": -8},
            "hrv_trajectory":   {"direction": "falling", "pct_change": -7.2, "recent_avg": 65, "prior_avg": 70},
        },
        "heatmap": heatmap, "hypnogram": hypnogram,
        "deep_decoder": deep_decoder, "tonight_card": tonight_card,
        "vitals": {
            "spo2_avg":      [round(rng.gauss(96.8, 0.8), 1) for _ in days],
            "spo2_bdi":      [round(rng.gauss(4, 2)) for _ in days],
            "stress_high":   [round(rng.gauss(85, 30)) for _ in days],
            "recovery_high": [round(rng.gauss(110, 35)) for _ in days],
            "stress_summary":["stressful" if rng.random()<0.2 else "normal" if rng.random()<0.5 else "restored" for _ in days],
            "cardio_age":    [32 if i%7==0 else None for i,_ in enumerate(days)],
            "vo2_max":       [round(rng.gauss(44, 1.5), 1) if i%5==0 else None for i,_ in enumerate(days)],
            "latest": {
                "spo2": 96.4, "bdi": 5,
                "stress_high_min": 72, "recovery_high_min": 118,
                "stress_summary": "normal",
                "cardio_age": 32, "vo2_max": 44.2
            }
        },
        "resilience": {
            "levels": res_level_series, "numeric": res_numeric,
            "sleep_recovery": res_sleep_r, "daytime_recovery": res_day_r, "stress": res_stress_c,
            "latest": {"level": res_level_series[-1], "sleep_recovery": res_sleep_r[-1],
                       "daytime_recovery": res_day_r[-1], "stress": res_stress_c[-1]}
        },
        "workouts": {
            "active_minutes": workout_min_series, "calories": workout_cal_series,
            "active_days": workout_active_days, "activity_counts": activity_counts,
            "recent": recent_workouts,
            "total_active_days": sum(workout_active_days),
            "total_active_minutes": sum(workout_min_series),
        },
    }

# ── Flask routes ───────────────────────────────────────────────────────────────

@app.route("/api/validate")
def validate_endpoint():
    """Quick token validation — calls personal_info, returns first_name or error."""
    token = request.headers.get("X-Oura-Token", "").strip()
    if not token:
        return jsonify({"valid": False, "error": "No token provided"}), 400
    try:
        info = fetch("personal_info", None, None, token)
        if isinstance(info, dict) and ("first_name" in info or "id" in info):
            # Count unique user connections (best-effort, non-blocking)
            pass  # user count tracked via Vercel Analytics
            return jsonify({"valid": True, "first_name": info.get("first_name", "")})
        return jsonify({"valid": False, "error": "Token rejected by Oura API"}), 401
    except Exception:
        return jsonify({"valid": False, "error": "validation_failed"}), 401

@app.route("/api/stats")
def stats_endpoint():
    """Return feedback vote counts from Upstash Vector."""
    res  = vector_request("/range", {"cursor": "0", "limit": 1000, "includeMetadata": True})
    vecs = (res or {}).get("result", {}).get("vectors", [])
    up   = sum(1 for v in vecs if (v.get("metadata") or {}).get("vote") == "up")
    down = sum(1 for v in vecs if (v.get("metadata") or {}).get("vote") == "down")
    return jsonify({"thumbs_up": up, "thumbs_down": down})

@app.route("/api/feedback", methods=["POST"])
def feedback_endpoint():
    """Store feedback in Upstash Vector with auto-embedding."""
    data    = request.get_json(silent=True) or {}
    vote    = data.get("vote", "")
    comment = str(data.get("comment", ""))[:500].strip()
    if vote not in ("up", "down"):
        return jsonify({"error": "invalid vote"}), 400
    uid  = f"fb-{int(datetime.now().timestamp()*1000)}-{os.urandom(2).hex()}"
    text = comment if comment else f"Ring Edge feedback: {vote}"
    vector_request("/upsert-data", [{"id": uid, "data": text,
                                     "metadata": {"vote": vote, "comment": comment,
                                                  "ts": str(date.today())}}])
    return jsonify({"ok": True})

@app.route("/api/demo")
def demo_endpoint():
    """Returns fully realistic fictitious data for the preview/demo mode."""
    return jsonify(generate_demo_data())

@app.route("/api/data")
def data_endpoint():
    token = request.headers.get("X-Oura-Token", "").strip()
    if not token:
        return jsonify({"error": "missing_token",
                        "message": "Provide your Oura token via X-Oura-Token header."}), 401
    try:
        return jsonify(build_data(token))
    except Exception:
        return jsonify({"error": "data_fetch_failed"}), 500

@app.route("/api/oauth/authorize")
def oauth_authorize():
    """Redirect the browser to Oura's OAuth authorization page."""
    client_id = os.environ.get("OURA_CLIENT_ID", "")
    if not client_id:
        return jsonify({"error": "OAuth not configured on this server. Use a Personal Access Token instead."}), 503
    redirect_uri = request.args.get("redirect_uri", "")
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "email personal daily heartrate spo2Daily workout",
    })
    return redirect(f"https://cloud.ouraring.com/oauth/authorize?{params}")

@app.route("/api/oauth/token", methods=["POST"])
def oauth_token_endpoint():
    """Exchange an OAuth authorization code for an access token."""
    client_id     = os.environ.get("OURA_CLIENT_ID", "")
    client_secret = os.environ.get("OURA_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return jsonify({"error": "OAuth not configured on this server. Use a Personal Access Token instead."}), 503
    data         = request.get_json(silent=True) or {}
    code         = data.get("code", "").strip()
    redirect_uri = data.get("redirect_uri", "").strip()
    if not code:
        return jsonify({"error": "Missing authorization code"}), 400
    payload = urllib.parse.urlencode({
        "grant_type":   "authorization_code",
        "code":         code,
        "redirect_uri": redirect_uri,
        "client_id":    client_id,
        "client_secret": client_secret,
    }).encode()
    req = Request("https://api.ouraring.com/oauth/token", data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urlopen(req, timeout=15) as r:
            body = json.loads(r.read())
        access_token = body.get("access_token", "")
        if not access_token:
            return jsonify({"error": "oauth_failed"}), 400
        try:
            info       = fetch("personal_info", None, None, access_token)
            first_name = info.get("first_name", "") if isinstance(info, dict) else ""
        except Exception:
            first_name = ""
        return jsonify({"access_token": access_token, "first_name": first_name})
    except Exception:
        return jsonify({"error": "token_exchange_failed"}), 400

