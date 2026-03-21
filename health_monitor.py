#!/usr/bin/env python3
"""
Oura Ring Health Monitor for Claude
Fetches and summarizes daily health data from the Oura API.
"""

import os
import json
import sys
from datetime import date, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError

OURA_TOKEN = os.environ["OURA_TOKEN"]
BASE_URL = "https://api.ouraring.com/v2/usercollection"

SCORE_LABELS = {
    range(0, 60): "Poor 🔴",
    range(60, 70): "Fair 🟡",
    range(70, 85): "Good 🟢",
    range(85, 101): "Optimal 💚",
}

def score_label(score):
    if score is None:
        return "N/A"
    for r, label in SCORE_LABELS.items():
        if score in r:
            return f"{score} — {label}"
    return str(score)

def fetch(endpoint, start_date, end_date):
    url = f"{BASE_URL}/{endpoint}?start_date={start_date}&end_date={end_date}"
    req = Request(url, headers={"Authorization": f"Bearer {OURA_TOKEN}"})
    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except URLError as e:
        print(f"Error fetching {endpoint}: {e}", file=sys.stderr)
        return {"data": []}

def latest(data_list):
    return data_list[-1] if data_list else {}

def trend(data_list, key="score"):
    scores = [d.get(key) for d in data_list if d.get(key) is not None]
    if len(scores) < 2:
        return "—"
    delta = scores[-1] - scores[0]
    arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
    return f"{arrow} {abs(delta):+.0f} over {len(scores)} days"

def fmt_minutes(minutes):
    if minutes is None:
        return "N/A"
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m}m"

def main():
    today = date.today()
    week_ago = today - timedelta(days=7)
    start = str(week_ago)
    end = str(today)

    print(f"\n{'='*55}")
    print(f"  🩺 OURA HEALTH REPORT — {today.strftime('%B %d, %Y')}")
    print(f"{'='*55}\n")

    # --- READINESS ---
    readiness_data = fetch("daily_readiness", start, end)["data"]
    r = latest(readiness_data)
    print("📊 READINESS")
    print(f"  Score:        {score_label(r.get('score'))}")
    c = r.get("contributors", {})
    print(f"  HRV Balance:  {c.get('hrv_balance', 'N/A')}")
    print(f"  Resting HR:   {c.get('resting_heart_rate', 'N/A')}")
    print(f"  Body Temp:    {c.get('body_temperature', 'N/A')}")
    print(f"  Recovery Idx: {c.get('recovery_index', 'N/A')}")
    print(f"  7-day trend:  {trend(readiness_data)}")
    temp_dev = r.get("temperature_deviation")
    if temp_dev is not None:
        flag = " ⚠️ Elevated" if temp_dev > 0.5 else (" ❄️ Below normal" if temp_dev < -0.5 else " Normal")
        print(f"  Temp Dev:    {temp_dev:+.2f}°C{flag}")

    # --- SLEEP ---
    sleep_data = fetch("daily_sleep", start, end)["data"]
    s = latest(sleep_data)
    print(f"\n😴 SLEEP")
    print(f"  Score:        {score_label(s.get('score'))}")
    sc = s.get("contributors", {})
    print(f"  Deep Sleep:   {sc.get('deep_sleep', 'N/A')}")
    print(f"  REM Sleep:    {sc.get('rem_sleep', 'N/A')}")
    print(f"  Efficiency:   {sc.get('efficiency', 'N/A')}")
    print(f"  Restfulness:  {sc.get('restfulness', 'N/A')}")
    print(f"  Timing:       {sc.get('timing', 'N/A')}")
    print(f"  7-day trend:  {trend(sleep_data)}")

    # --- SLEEP DETAILS ---
    sleep_detail = fetch("sleep", start, end)["data"]
    if sleep_detail:
        sd = sleep_detail[-1]
        total = sd.get("total_sleep_duration")
        deep = sd.get("deep_sleep_duration")
        rem = sd.get("rem_sleep_duration")
        light = sd.get("light_sleep_duration")
        print(f"  Total Sleep:  {fmt_minutes((total or 0)/60)}")
        print(f"  Deep:         {fmt_minutes((deep or 0)/60)}")
        print(f"  REM:          {fmt_minutes((rem or 0)/60)}")
        print(f"  Light:        {fmt_minutes((light or 0)/60)}")
        avg_hr = sd.get("average_heart_rate")
        avg_hrv = sd.get("average_hrv")
        if avg_hr:
            print(f"  Avg HR:       {avg_hr:.0f} bpm")
        if avg_hrv:
            print(f"  Avg HRV:      {avg_hrv:.0f} ms")

    # --- ACTIVITY ---
    activity_data = fetch("daily_activity", start, end)["data"]
    a = latest(activity_data)
    print(f"\n🏃 ACTIVITY")
    print(f"  Score:        {score_label(a.get('score'))}")
    ac = a.get("contributors", {})
    print(f"  Daily Target: {ac.get('meet_daily_targets', 'N/A')}")
    print(f"  Move/Hour:    {ac.get('move_every_hour', 'N/A')}")
    print(f"  Stay Active:  {ac.get('stay_active', 'N/A')}")
    cal = a.get("active_calories")
    steps = a.get("steps")
    dist = a.get("equivalent_walking_distance")
    if cal:
        print(f"  Active Cal:   {cal} kcal")
    if steps:
        print(f"  Steps:        {steps:,}")
    if dist:
        print(f"  Distance:     {dist/1000:.1f} km")
    print(f"  7-day trend:  {trend(activity_data)}")

    # --- HRV ---
    hrv_data = fetch("heartrate", str(today - timedelta(days=1)), end)["data"]
    if hrv_data:
        hr_vals = [h["bpm"] for h in hrv_data if h.get("source") == "rest" and h.get("bpm")]
        if hr_vals:
            print(f"\n❤️  HEART RATE (resting)")
            print(f"  Min:          {min(hr_vals)} bpm")
            print(f"  Max:          {max(hr_vals)} bpm")
            print(f"  Avg:          {sum(hr_vals)/len(hr_vals):.0f} bpm")

    # --- ALERTS ---
    alerts = []
    r_score = r.get("score", 100)
    s_score = s.get("score", 100)
    a_score = a.get("score", 100)
    temp_dev = r.get("temperature_deviation", 0) or 0

    if r_score < 60:
        alerts.append("⚠️  Low readiness — consider rest or light activity today")
    if s_score < 60:
        alerts.append("⚠️  Poor sleep last night — prioritize recovery")
    if a_score < 60:
        alerts.append("⚠️  Low activity score — try to move more today")
    if temp_dev > 0.5:
        alerts.append(f"🌡️  Body temp elevated (+{temp_dev:.2f}°C) — possible illness or stress")
    if temp_dev < -0.5:
        alerts.append(f"❄️  Body temp below normal ({temp_dev:.2f}°C) — monitor closely")

    readiness_scores = [d.get("score", 0) for d in readiness_data if d.get("score")]
    if len(readiness_scores) >= 3 and all(s < 70 for s in readiness_scores[-3:]):
        alerts.append("📉 Readiness has been below 70 for 3+ days — consider reducing training load")

    if alerts:
        print(f"\n🚨 ALERTS & RECOMMENDATIONS")
        for alert in alerts:
            print(f"  {alert}")
    else:
        print(f"\n✅ All metrics look healthy — keep it up!")

    print(f"\n{'='*55}\n")

if __name__ == "__main__":
    main()
