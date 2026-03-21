#!/usr/bin/env python3
"""
Oura Evening Check-In Logger
Logs tonight's lifestyle factors so the insights engine can correlate them
with tomorrow's sleep quality — building Tyler's personal sleep model.
"""

import json, os, sys
from datetime import date

CHECKIN_FILE = os.path.expanduser("~/.claude/oura/checkins.json")

def load():
    if os.path.exists(CHECKIN_FILE):
        with open(CHECKIN_FILE) as f:
            return json.load(f)
    return {}

def save(data):
    with open(CHECKIN_FILE, "w") as f:
        json.dump(data, f, indent=2)

def main():
    today = str(date.today())
    checkins = load()

    # Accept JSON input from stdin (for Claude to fill in)
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            try:
                entry = json.loads(raw)
                checkins[today] = entry
                save(checkins)
                print(f"✅ Check-in logged for {today}: {entry}")
                return
            except json.JSONDecodeError:
                pass

    # Default structured entry (Claude fills this in via the scheduled task prompt)
    print(f"Evening check-in for {today}")
    print("Pass JSON via stdin, e.g.:")
    print('  echo \'{"alcohol": false, "late_meal": false, "screen_time_late": true, "stress_level": 3, "exercise": true, "caffeine_after_2pm": false}\' | python3 evening_checkin.py')

if __name__ == "__main__":
    main()
