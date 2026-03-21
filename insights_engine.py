#!/usr/bin/env python3
"""
Oura Personal Intelligence Engine
Mines your biometric history to surface patterns unique to YOUR body.
"""

import json, os, math
from datetime import date, timedelta
from urllib.request import urlopen, Request
from collections import defaultdict

TOKEN = os.environ["OURA_TOKEN"]
BASE = "https://api.ouraring.com/v2/usercollection"
CHECKIN_FILE = os.path.expanduser("~/.claude/oura/checkins.json")

DAYS_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch(ep, start, end):
    url = f"{BASE}/{ep}?start_date={start}&end_date={end}"
    req = Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urlopen(req, timeout=10) as r:
        return json.loads(r.read())["data"]

def load_checkins():
    if os.path.exists(CHECKIN_FILE):
        with open(CHECKIN_FILE) as f:
            return json.load(f)
    return {}

# ── Stats helpers ──────────────────────────────────────────────────────────────

def mean(vals):
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None

def pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 5:
        return None
    n = len(pairs)
    mx, my = mean([p[0] for p in pairs]), mean([p[1] for p in pairs])
    num = sum((x - mx) * (y - my) for x, y in pairs)
    den = math.sqrt(sum((x - mx)**2 for x, y in pairs) * sum((y - my)**2 for x, y in pairs))
    return round(num / den, 3) if den else None

def corr_label(r):
    if r is None: return "insufficient data"
    if r > 0.6:  return f"strong positive ↑ ({r:+.2f})"
    if r > 0.3:  return f"moderate positive ↑ ({r:+.2f})"
    if r > 0.1:  return f"weak positive ({r:+.2f})"
    if r < -0.6: return f"strong negative ↓ ({r:+.2f})"
    if r < -0.3: return f"moderate negative ↓ ({r:+.2f})"
    if r < -0.1: return f"weak negative ({r:+.2f})"
    return f"no relationship ({r:+.2f})"

def bar(val, max_val=100, width=20, char="█"):
    if val is None: return "─" * width
    filled = int((val / max_val) * width)
    return char * filled + "░" * (width - filled)

def trend_arrow(vals, n=7):
    recent = [v for v in vals[-n:] if v is not None]
    older  = [v for v in vals[-n*2:-n] if v is not None]
    if not recent or not older: return "→"
    d = mean(recent) - mean(older)
    if d > 3:  return "↑"
    if d < -3: return "↓"
    return "→"

def score_emoji(s):
    if s is None: return "?"
    if s >= 85: return "💚"
    if s >= 70: return "🟢"
    if s >= 60: return "🟡"
    return "🔴"

def percentile_label(val, all_vals):
    valid = sorted(v for v in all_vals if v is not None)
    if not valid or val is None: return ""
    pct = sum(1 for v in valid if v <= val) / len(valid) * 100
    if pct >= 90: return " (personal best range)"
    if pct >= 75: return " (above average for you)"
    if pct <= 10: return " ⚠️ (personal worst range)"
    if pct <= 25: return " (below average for you)"
    return ""

# ── Prediction ─────────────────────────────────────────────────────────────────

def predict_tomorrow_readiness(readiness_data, sleep_data):
    """Simple weighted model based on Tyler's personal correlations."""
    if not readiness_data or not sleep_data:
        return None, []

    # Features: prev_night_score, hrv_balance, sleep_score → next readiness
    features = []
    for i in range(1, len(readiness_data)):
        r = readiness_data[i]
        r_prev = readiness_data[i-1]
        # find matching sleep
        s = next((s for s in sleep_data if s["day"] == r_prev["day"]), None)
        if s and r.get("score") and s.get("score") and r_prev.get("contributors", {}).get("hrv_balance"):
            features.append({
                "sleep_score": s["score"],
                "hrv": r_prev["contributors"]["hrv_balance"],
                "readiness_next": r["score"]
            })

    if len(features) < 5:
        return None, []

    # Compute weights via correlation
    sleep_scores = [f["sleep_score"] for f in features]
    hrv_scores   = [f["hrv"] for f in features]
    targets      = [f["readiness_next"] for f in features]

    r_sleep = pearson(sleep_scores, targets) or 0
    r_hrv   = pearson(hrv_scores, targets) or 0

    # Last known sleep + hrv
    last_sleep = sleep_data[-1].get("score", 75)
    last_hrv   = readiness_data[-1].get("contributors", {}).get("hrv_balance", 80)
    baseline   = mean(targets) or 78

    # Weighted prediction
    total_w = abs(r_sleep) + abs(r_hrv) + 0.01
    pred = (
        baseline * 0.4 +
        last_sleep * abs(r_sleep) / total_w * 0.35 +
        last_hrv   * abs(r_hrv)   / total_w * 0.25
    )

    factors = []
    if last_sleep >= 78:
        factors.append(f"strong sleep score ({last_sleep}) boosts tomorrow")
    elif last_sleep < 68:
        factors.append(f"weak sleep score ({last_sleep}) will drag tomorrow down")
    if last_hrv >= 87:
        factors.append(f"high HRV balance ({last_hrv}) — nervous system well recovered")
    elif last_hrv < 70:
        factors.append(f"low HRV ({last_hrv}) — body still under stress")

    return round(pred), factors

# ── Main Report ────────────────────────────────────────────────────────────────

def main():
    today = date.today()
    end   = str(today)
    start = str(today - timedelta(days=60))

    sleep      = fetch("daily_sleep",     start, end)
    readiness  = fetch("daily_readiness", start, end)
    activity   = fetch("daily_activity",  start, end)
    sleep_det  = fetch("sleep",           start, end)
    checkins   = load_checkins()

    # Index by day
    s_by_day = {d["day"]: d for d in sleep}
    r_by_day = {d["day"]: d for d in readiness}
    a_by_day = {d["day"]: d for d in activity}

    days = sorted(set(s_by_day) & set(r_by_day) & set(a_by_day))

    # Build aligned series
    sleep_scores     = [s_by_day[d].get("score") for d in days]
    ready_scores     = [r_by_day[d].get("score") for d in days]
    act_scores       = [a_by_day[d].get("score") for d in days]
    steps_series     = [a_by_day[d].get("steps") for d in days]
    deep_series      = [s_by_day[d].get("contributors", {}).get("deep_sleep") for d in days]
    rem_series       = [s_by_day[d].get("contributors", {}).get("rem_sleep") for d in days]
    rest_series      = [s_by_day[d].get("contributors", {}).get("restfulness") for d in days]
    hrv_series       = [r_by_day[d].get("contributors", {}).get("hrv_balance") for d in days]
    temp_series      = [r_by_day[d].get("temperature_deviation") for d in days]
    cal_series       = [a_by_day[d].get("active_calories") for d in days]

    # Shift for "today's activity → next night's sleep" correlation
    next_sleep_deep  = deep_series[1:] + [None]
    next_ready       = ready_scores[1:] + [None]

    # ── Day-of-week breakdown ──────────────────────────────────────────────────
    dow_sleep = defaultdict(list)
    dow_ready = defaultdict(list)
    dow_steps = defaultdict(list)
    for d in days:
        dow = date.fromisoformat(d).weekday()
        dow_sleep[dow].append(s_by_day[d].get("score"))
        dow_ready[dow].append(r_by_day[d].get("score"))
        dow_steps[dow].append(a_by_day[d].get("steps"))

    # ── Prediction ────────────────────────────────────────────────────────────
    pred_score, pred_factors = predict_tomorrow_readiness(readiness, sleep)

    # ── Latest values ─────────────────────────────────────────────────────────
    latest_sleep   = sleep[-1]   if sleep   else {}
    latest_ready   = readiness[-1] if readiness else {}
    latest_act     = activity[-1]  if activity  else {}
    latest_det     = sleep_det[-1] if sleep_det else {}

    ls  = latest_sleep.get("score")
    lr  = latest_ready.get("score")
    la  = latest_act.get("score")
    lsc = latest_sleep.get("contributors", {})
    lrc = latest_ready.get("contributors", {})
    lac = latest_act.get("contributors", {})

    total_sleep_sec = latest_det.get("total_sleep_duration", 0)
    deep_sec        = latest_det.get("deep_sleep_duration", 0)
    rem_sec         = latest_det.get("rem_sleep_duration", 0)
    light_sec       = latest_det.get("light_sleep_duration", 0)
    avg_hrv         = latest_det.get("average_hrv")
    avg_hr_sleep    = latest_det.get("average_heart_rate")

    def fmt(secs):
        h, m = divmod(int(secs or 0) // 60, 60)
        return f"{h}h{m:02d}m"

    W = 58
    print()
    print("╔" + "═"*W + "╗")
    print("║" + "  🧠 OURA PERSONAL INTELLIGENCE ENGINE".center(W) + "║")
    print("║" + f"  {today.strftime('%A, %B %d %Y')}".center(W) + "║")
    print("╚" + "═"*W + "╝")

    # ── TODAY'S SNAPSHOT ──────────────────────────────────────────────────────
    print(f"\n{'─'*W}")
    print("  TODAY'S SNAPSHOT")
    print(f"{'─'*W}")
    print(f"  Sleep     {bar(ls)}  {ls} {score_emoji(ls)}{percentile_label(ls, sleep_scores)}")
    print(f"  Readiness {bar(lr)}  {lr} {score_emoji(lr)}{percentile_label(lr, ready_scores)}")
    print(f"  Activity  {bar(la)}  {la} {score_emoji(la)}{percentile_label(la, act_scores)}")
    print()
    print(f"  {'Sleep architecture':20}  {'HRV':>6}   {'RHR':>6}")
    print(f"  {'─'*20}  {'─'*6}   {'─'*6}")
    print(f"  Total  {fmt(total_sleep_sec):>8}         {avg_hrv or '—':>6}ms   {avg_hr_sleep or '—':>5} bpm")
    print(f"  Deep   {fmt(deep_sec):>8}")
    print(f"  REM    {fmt(rem_sec):>8}")
    print(f"  Light  {fmt(light_sec):>8}")

    # ── 7-DAY TREND SPARKLINE ─────────────────────────────────────────────────
    print(f"\n{'─'*W}")
    print("  7-DAY TRENDS  (each block = 1 day, left=oldest)")
    print(f"{'─'*W}")
    def sparkline(vals, lo=50, hi=100):
        bars = " ▁▂▃▄▅▆▇█"
        recent = [v for v in vals[-7:] if v is not None]
        out = ""
        for v in recent:
            idx = int((v - lo) / (hi - lo) * 8)
            idx = max(0, min(8, idx))
            out += bars[idx]
        return out

    print(f"  Sleep     {sparkline(sleep_scores)}  avg {mean(sleep_scores[-7:]):.0f}  {trend_arrow(sleep_scores)}")
    print(f"  Readiness {sparkline(ready_scores)}  avg {mean(ready_scores[-7:]):.0f}  {trend_arrow(ready_scores)}")
    print(f"  Activity  {sparkline(act_scores)}   avg {mean(act_scores[-7:]):.0f}  {trend_arrow(act_scores)}")
    print(f"  HRV Bal   {sparkline(hrv_series, 60, 100)}  avg {mean(hrv_series[-7:]):.0f}  {trend_arrow(hrv_series)}")

    # ── TOMORROW'S PREDICTION ─────────────────────────────────────────────────
    print(f"\n{'─'*W}")
    print("  🔮 TOMORROW'S READINESS PREDICTION")
    print(f"{'─'*W}")
    if pred_score:
        emoji = score_emoji(pred_score)
        conf = "High" if len([v for v in ready_scores if v]) > 20 else "Moderate"
        print(f"  Predicted score:  {pred_score}  {emoji}   (confidence: {conf})")
        print(f"  Based on 60-day personal model:")
        for f in pred_factors:
            print(f"    • {f}")
        if pred_score >= 85:
            print(f"  → 💪 Green light for hard training tomorrow")
        elif pred_score >= 75:
            print(f"  → 🟢 Good day for moderate effort")
        elif pred_score >= 65:
            print(f"  → 🟡 Keep it easy — walk, stretch, or rest")
        else:
            print(f"  → 🔴 Recovery day — protect your sleep tonight")
    else:
        print("  Not enough data yet for reliable prediction.")

    # ── YOUR PERSONAL CORRELATIONS ────────────────────────────────────────────
    print(f"\n{'─'*W}")
    print("  🧬 YOUR PERSONAL CORRELATIONS  (unique to your biology)")
    print(f"{'─'*W}")

    corrs = [
        ("Steps → next night deep sleep",    pearson(steps_series[:-1], deep_series[1:])),
        ("Steps → next night sleep score",   pearson(steps_series[:-1], sleep_scores[1:])),
        ("Sleep score → next day readiness", pearson(sleep_scores[:-1], ready_scores[1:])),
        ("Activity score → sleep score",     pearson(act_scores[:-1],   sleep_scores[1:])),
        ("HRV balance → sleep quality",      pearson(hrv_series,        sleep_scores)),
        ("Restfulness → readiness",          pearson(rest_series,       ready_scores)),
        ("Active calories → deep sleep",     pearson(cal_series[:-1],   deep_series[1:])),
        ("Temp deviation → sleep score",     pearson(temp_series,       sleep_scores)),
    ]
    for label, r in corrs:
        print(f"  {label:<38} {corr_label(r)}")

    # ── DAY-OF-WEEK PATTERNS ──────────────────────────────────────────────────
    print(f"\n{'─'*W}")
    print("  📅 YOUR DAY-OF-WEEK PATTERNS")
    print(f"{'─'*W}")
    print(f"  {'Day':<6} {'Sleep':>6} {'Ready':>6} {'Steps':>7}  {'Sleep bar'}")
    print(f"  {'─'*5} {'─'*6} {'─'*6} {'─'*7}  {'─'*15}")
    for dow in range(7):
        s_avg = mean(dow_sleep[dow])
        r_avg = mean(dow_ready[dow])
        st_avg = mean(dow_steps[dow])
        if s_avg:
            b = bar(s_avg, width=15)
            print(f"  {DAYS_LABELS[dow]:<6} {s_avg:>6.0f} {r_avg:>6.0f} {st_avg:>7.0f}  {b}")

    best_sleep_dow  = max((d for d in range(7) if dow_sleep[d]), key=lambda d: mean(dow_sleep[d]) or 0)
    worst_sleep_dow = min((d for d in range(7) if dow_sleep[d]), key=lambda d: mean(dow_sleep[d]) or 99)
    best_ready_dow  = max((d for d in range(7) if dow_ready[d]), key=lambda d: mean(dow_ready[d]) or 0)
    print(f"\n  Best sleep night:   {DAYS_LABELS[best_sleep_dow]}")
    print(f"  Worst sleep night:  {DAYS_LABELS[worst_sleep_dow]}")
    print(f"  Highest readiness:  {DAYS_LABELS[best_ready_dow]}")

    # ── CHRONIC WEAK SPOTS ────────────────────────────────────────────────────
    print(f"\n{'─'*W}")
    print("  ⚠️  CHRONIC WEAK SPOTS  (bottom 3 sleep contributors, all time)")
    print(f"{'─'*W}")
    contrib_keys = ["deep_sleep", "rem_sleep", "restfulness", "efficiency", "latency", "timing", "total_sleep"]
    contrib_avgs = {}
    for k in contrib_keys:
        vals = [s.get("contributors", {}).get(k) for s in sleep]
        contrib_avgs[k] = mean(vals)

    sorted_contribs = sorted(contrib_avgs.items(), key=lambda x: x[1] or 100)
    for k, v in sorted_contribs[:3]:
        label = k.replace("_", " ").title()
        print(f"  {label:<20} avg={v:.0f}  {bar(v, width=25)}")
    print()
    # Highlight restfulness specifically
    rest_avg = contrib_avgs.get("restfulness", 0)
    if rest_avg < 60:
        print(f"  ⚠️  Restfulness ({rest_avg:.0f}/100) has been your #1 limiter for 60 days.")
        print(f"      This means you're waking briefly or moving excessively during sleep.")
        print(f"      Typical causes: alcohol, late meals, noise, temperature, or stress.")

    # ── DEEP SLEEP ANALYSIS ───────────────────────────────────────────────────
    print(f"\n{'─'*W}")
    print("  💤 DEEP SLEEP DETECTIVE  (your most volatile metric)")
    print(f"{'─'*W}")
    deep_vals = [v for v in deep_series if v is not None]
    if deep_vals:
        print(f"  Range:  {min(deep_vals)} – {max(deep_vals)}  (a {max(deep_vals)-min(deep_vals)}-point swing!)")
        print(f"  Avg:    {mean(deep_vals):.0f}")
        print(f"  Std:    ±{math.sqrt(mean([(v - mean(deep_vals))**2 for v in deep_vals])):.0f} points")

        # Best deep sleep days
        deep_by_day = [(days[i], deep_series[i]) for i in range(len(days)) if deep_series[i] is not None]
        top3 = sorted(deep_by_day, key=lambda x: x[1], reverse=True)[:3]
        bot3 = sorted(deep_by_day, key=lambda x: x[1])[:3]
        print(f"\n  Your best deep sleep nights:")
        for d, v in top3:
            dow = DAYS_LABELS[date.fromisoformat(d).weekday()]
            steps = a_by_day.get(d, {}).get("steps", "?")
            print(f"    {d} ({dow})  deep={v}  steps={steps:,}" if isinstance(steps, int) else f"    {d} ({dow})  deep={v}")
        print(f"\n  Your worst deep sleep nights:")
        for d, v in bot3:
            dow = DAYS_LABELS[date.fromisoformat(d).weekday()]
            steps = a_by_day.get(d, {}).get("steps", "?")
            print(f"    {d} ({dow})  deep={v}  steps={steps:,}" if isinstance(steps, int) else f"    {d} ({dow})  deep={v}")

    # ── HRV TRAJECTORY ────────────────────────────────────────────────────────
    print(f"\n{'─'*W}")
    print("  ❤️  HRV TRAJECTORY  (your cardiovascular fingerprint)")
    print(f"{'─'*W}")
    hrv_vals = [v for v in hrv_series if v is not None]
    if len(hrv_vals) >= 14:
        first_half = mean(hrv_vals[:len(hrv_vals)//2])
        second_half = mean(hrv_vals[len(hrv_vals)//2:])
        delta = second_half - first_half
        last7  = mean(hrv_vals[-7:])
        last30 = mean(hrv_vals[-30:]) if len(hrv_vals) >= 30 else mean(hrv_vals)
        print(f"  60-day avg:    {mean(hrv_vals):.0f}")
        print(f"  Last 7 days:   {last7:.0f}  {trend_arrow(hrv_series)}")
        print(f"  First 30 days: {first_half:.0f}  →  Last 30 days: {second_half:.0f}  (Δ {delta:+.0f})")
        if delta > 5:
            print(f"  ✅ Your cardiovascular fitness has genuinely improved over 60 days.")
            print(f"     A +{delta:.0f} point HRV rise is meaningful — your heart is adapting.")
        elif delta < -5:
            print(f"  ⚠️  HRV has declined — possible overtraining, stress, or poor recovery.")
        else:
            print(f"  → HRV is stable. Consistent, but room to grow.")

    # ── CHECKIN INSIGHTS ──────────────────────────────────────────────────────
    if checkins:
        print(f"\n{'─'*W}")
        print("  📝 LIFESTYLE EXPERIMENT RESULTS")
        print(f"{'─'*W}")
        # Group by factor
        factor_impact = defaultdict(lambda: {"yes": [], "no": []})
        for day_str, data in checkins.items():
            next_day = str(date.fromisoformat(day_str) + timedelta(days=1))
            next_sleep = s_by_day.get(next_day, {}).get("score")
            next_rest  = s_by_day.get(next_day, {}).get("contributors", {}).get("restfulness")
            if next_sleep is None:
                continue
            for factor, val in data.items():
                if isinstance(val, bool):
                    key = "yes" if val else "no"
                    factor_impact[factor][key].append(next_sleep)
        for factor, groups in factor_impact.items():
            if len(groups["yes"]) >= 2 and len(groups["no"]) >= 2:
                yes_avg = mean(groups["yes"])
                no_avg  = mean(groups["no"])
                diff = yes_avg - no_avg
                label = factor.replace("_", " ").title()
                icon = "✅" if diff > 2 else ("❌" if diff < -2 else "~")
                print(f"  {icon} {label:<25} with: {yes_avg:.0f}  without: {no_avg:.0f}  (Δ{diff:+.0f})")
        if not any(len(g["yes"]) >= 2 and len(g["no"]) >= 2 for g in factor_impact.values()):
            print("  Keep logging nightly check-ins — insights appear after ~2 weeks.")

    # ── PRESCRIPTIONS ─────────────────────────────────────────────────────────
    print(f"\n{'─'*W}")
    print("  🎯 PERSONALIZED PRESCRIPTIONS  (based on YOUR patterns)")
    print(f"{'─'*W}")

    prescriptions = []

    # Activity → deep sleep
    r_steps_deep = pearson(steps_series[:-1], deep_series[1:])
    if r_steps_deep and r_steps_deep > 0.2:
        avg_steps = mean(steps_series)
        prescriptions.append(
            f"Walk more: your data shows steps correlate with better deep sleep (r={r_steps_deep:.2f}). "
            f"Your avg is {avg_steps:.0f} steps — try hitting 8,000."
        )

    # Activity decline
    recent_act = [v for v in act_scores[-7:] if v]
    older_act  = [v for v in act_scores[-21:-7] if v]
    if recent_act and older_act and mean(recent_act) < mean(older_act) - 10:
        prescriptions.append(
            f"Activity slump detected: avg dropped from {mean(older_act):.0f} to {mean(recent_act):.0f} "
            f"in the last 7 days. Even a 20-min walk today will protect tomorrow's score."
        )

    # Restfulness
    rest_avg = mean(rest_series)
    if rest_avg and rest_avg < 60:
        prescriptions.append(
            f"Fix restfulness ({rest_avg:.0f}/100): this is your #1 sleep limiter. "
            f"Try: no food 3hrs before bed, cooler room, and track via the nightly check-in."
        )

    # HRV trend
    if len(hrv_vals) >= 14 and mean(hrv_vals[-7:]) > mean(hrv_vals[:14]):
        prescriptions.append(
            "Your HRV improvement over 60 days is real — protect it. "
            "Avoid consecutive hard days; your body is in a positive adaptation phase."
        )

    # Deep sleep instability
    if deep_vals and math.sqrt(mean([(v - mean(deep_vals))**2 for v in deep_vals])) > 15:
        prescriptions.append(
            "Deep sleep is highly variable — this usually means inconsistent bedtimes or "
            "alcohol/stimulants on some nights. Consistent sleep schedule = more deep sleep."
        )

    for i, p in enumerate(prescriptions, 1):
        words = p.split()
        lines = []
        line = f"  {i}. "
        for w in words:
            if len(line) + len(w) + 1 > W:
                lines.append(line)
                line = "     " + w + " "
            else:
                line += w + " "
        lines.append(line)
        print("\n".join(lines))

    print(f"\n{'═'*W}")
    print(f"  Report generated: {date.today()}  |  Based on 60 days of biometric data")
    print(f"{'═'*W}\n")

if __name__ == "__main__":
    main()
