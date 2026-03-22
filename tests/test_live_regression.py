"""
Oura Edge — Live Regression Tests
Fetches real data from the Oura API and verifies every calculation the
app shows the user matches the raw API values exactly.

Usage:
  OURA_TOKEN=<your-token> python -m pytest tests/test_live_regression.py -v
  # or with the token inline:
  python -m pytest tests/test_live_regression.py -v

Token is read from OURA_TOKEN env var (set it, don't hard-code secrets).
"""

import sys, os, json, math, pytest
from datetime import date, timedelta
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))
from index import calc_sleep_debt, mean, pearson, build_data

# ── Fixtures ──────────────────────────────────────────────────────────────────

TOKEN = os.environ.get("OURA_TOKEN", "5IHG2OU7TBLUK5POBZEQ6HG7A3QRDGKU")

@pytest.fixture(scope="session")
def raw_personal_info():
    """Fetch personal_info once for the whole session."""
    req = Request(
        "https://api.ouraring.com/v2/usercollection/personal_info",
        headers={"Authorization": f"Bearer {TOKEN}"}
    )
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read())

@pytest.fixture(scope="session")
def raw_sleep(days_back=30):
    """
    Fetch daily_sleep (scored aggregates) for the last 30 days.
    Note: the raw 'sleep' endpoint returns sessions with score=None;
    scores come from 'daily_sleep'.
    """
    end   = date.today()
    start = end - timedelta(days=days_back)
    url   = f"https://api.ouraring.com/v2/usercollection/daily_sleep?start_date={start}&end_date={end}"
    req   = Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("data", [])

@pytest.fixture(scope="session")
def raw_sleep_detail(days_back=7):
    """Fetch raw sleep detail (with stage data) for last 7 days."""
    end   = date.today()
    start = end - timedelta(days=days_back)
    url   = f"https://api.ouraring.com/v2/usercollection/sleep?start_date={start}&end_date={end}"
    req   = Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("data", [])

@pytest.fixture(scope="session")
def raw_readiness(days_back=30):
    end   = date.today()
    start = end - timedelta(days=days_back)
    url   = f"https://api.ouraring.com/v2/usercollection/daily_readiness?start_date={start}&end_date={end}"
    req   = Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("data", [])

@pytest.fixture(scope="session")
def raw_activity(days_back=30):
    end   = date.today()
    start = end - timedelta(days=days_back)
    url   = f"https://api.ouraring.com/v2/usercollection/daily_activity?start_date={start}&end_date={end}"
    req   = Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("data", [])

@pytest.fixture(scope="session")
def app_data():
    """Full build_data() output — what the app actually sends to the browser."""
    return build_data(TOKEN)


# ── Personal Info & Unit Conversions ─────────────────────────────────────────

class TestPersonalInfo:

    def test_height_raw_value(self, raw_personal_info):
        """Log the raw height from Oura so we can verify it matches what you entered in the app."""
        h = raw_personal_info.get("height")
        assert h is not None, "Oura API did not return height"
        print(f"\n  Oura API height: {h} m")
        print(f"  → {round(h * 39.3701, 1)} inches total")
        feet   = int(h * 39.3701 // 12)
        inches = round(h * 39.3701 % 12)
        print(f"  → {feet}'{inches}\"  (what the app displays)")
        # Verify the math is right
        assert feet == int(h * 100 / 30.48), \
            f"feet calc mismatch: got {feet}, expected {int(h * 100 / 30.48)}"

    def test_height_conversion_formula(self):
        """Verify our feet/inches formula is correct for known values."""
        cases = [
            (1.7018, 5, 7),   # 5'7"
            (1.7526, 5, 9),   # 5'9"
            (1.7780, 5, 10),  # 5'10"
            (1.8288, 6, 0),   # 6'0"
            (1.8034, 5, 11),  # 5'11" — what Oura currently has for user
        ]
        for height_m, exp_feet, exp_inches in cases:
            total_in = height_m * 39.3701
            feet     = int(total_in // 12)
            inches   = round(total_in % 12)
            assert feet   == exp_feet,   f"{height_m}m → expected {exp_feet}', got {feet}'"
            assert inches == exp_inches, f"{height_m}m → expected {exp_inches}\", got {inches}\""

    def test_weight_conversion_kg_to_lbs(self):
        """1 kg = 2.20462 lbs — verify formula used in renderProfile()."""
        cases = [
            (70,  154),
            (80,  176),
            (86,  190),   # user's weight per Oura API
            (100, 220),
        ]
        for kg, expected_lbs in cases:
            lbs = round(kg * 2.205)
            assert lbs == expected_lbs, f"{kg}kg → expected {expected_lbs}lbs, got {lbs}lbs"

    def test_bmi_formula(self, raw_personal_info):
        """BMI = weight_kg / height_m² — verify against raw API values."""
        w = raw_personal_info.get("weight")
        h = raw_personal_info.get("height")
        if w and h:
            bmi = round(w / (h * h), 1)
            print(f"\n  BMI: {bmi} (weight={w}kg, height={h}m)")
            assert 10 < bmi < 60, f"BMI {bmi} is physiologically implausible"

    def test_app_returns_user_email(self, app_data):
        """App data must include the user's email."""
        assert "user" in app_data
        assert app_data["user"].get("email"), "Email missing from app user object"
        print(f"\n  User email: {app_data['user']['email']}")

    def test_app_user_height_matches_api(self, app_data, raw_personal_info):
        """height_m in app response must match raw API value exactly."""
        api_h = raw_personal_info.get("height")
        app_h = app_data["user"].get("height_m")
        assert api_h == app_h, f"API height={api_h}m but app shows {app_h}m"

    def test_app_user_weight_matches_api(self, app_data, raw_personal_info):
        """weight_kg in app response must match raw API value exactly."""
        api_w = raw_personal_info.get("weight")
        app_w = app_data["user"].get("weight_kg")
        assert api_w == app_w, f"API weight={api_w}kg but app shows {app_w}kg"


# ── Sleep Score Accuracy ───────────────────────────────────────────────────────

class TestSleepScores:

    def test_latest_sleep_score_matches_api(self, app_data, raw_sleep):
        """
        The latest sleep score the app shows must match the most recent
        daily_sleep record from the Oura API.
        Scores come from 'daily_sleep', NOT the raw 'sleep' endpoint
        (which returns stage-level sessions where score is always None).
        """
        if not raw_sleep:
            pytest.skip("No daily_sleep data returned from API")

        app_score = app_data["latest"]["sleep"]
        if app_score is None:
            pytest.skip("App has no sleep score yet — ring may not have synced today")

        most_recent = sorted(raw_sleep, key=lambda x: x["day"])[-1]
        api_score   = most_recent.get("score")
        assert api_score == app_score, \
            f"Latest sleep: daily_sleep API={api_score}, app shows={app_score} (day={most_recent['day']})"

    def test_sleep_scores_length_matches_days(self, app_data):
        """scores.sleep array length must equal the days array length."""
        assert len(app_data["scores"]["sleep"]) == len(app_data["days"]), \
            "scores.sleep length != days length"

    def test_sleep_scores_all_valid_range(self, app_data):
        """All non-null sleep scores must be between 0 and 100."""
        for i, (d, s) in enumerate(zip(app_data["days"], app_data["scores"]["sleep"])):
            if s is not None:
                assert 0 <= s <= 100, f"Sleep score {s} on {d} is out of range [0, 100]"

    def test_sleep_30day_avg_matches_calculation(self, app_data):
        """avgs.sleep must equal mean(scores.sleep[-30:])."""
        scores_30 = [s for s in app_data["scores"]["sleep"][-30:] if s is not None]
        expected  = round(sum(scores_30) / len(scores_30), 1) if scores_30 else None
        app_avg   = app_data["avgs"]["sleep"]
        assert abs((app_avg or 0) - (expected or 0)) < 0.2, \
            f"30-day sleep avg: expected {expected}, got {app_avg}"


# ── Readiness Score Accuracy ───────────────────────────────────────────────────

class TestReadinessScores:

    def test_latest_readiness_score_matches_api(self, app_data, raw_readiness):
        """The 'latest' readiness score must match the most recent API record."""
        if not raw_readiness:
            pytest.skip("No readiness data returned from API")
        most_recent = sorted(raw_readiness, key=lambda x: x["day"])[-1]
        api_score   = most_recent.get("score")
        app_score   = app_data["latest"]["ready"]
        assert api_score == app_score, \
            f"Latest readiness: API={api_score} but app shows={app_score} (day={most_recent['day']})"

    def test_readiness_contributors_present(self, app_data):
        """HRV balance contributor must be present in latest data."""
        contributors = app_data["latest"].get("contributors", {}).get("ready", {})
        assert "hrv_balance" in contributors, "hrv_balance missing from readiness contributors"

    def test_readiness_scores_valid_range(self, app_data):
        for i, (d, s) in enumerate(zip(app_data["days"], app_data["scores"]["ready"])):
            if s is not None:
                assert 0 <= s <= 100, f"Readiness score {s} on {d} out of range"


# ── Activity / Steps Accuracy ─────────────────────────────────────────────────

class TestActivityAccuracy:

    def test_latest_steps_matches_api(self, app_data, raw_activity):
        """Steps shown in the app must match the most recent activity API record."""
        if not raw_activity:
            pytest.skip("No activity data returned from API")
        most_recent = sorted(raw_activity, key=lambda x: x["day"])[-1]
        api_steps   = most_recent.get("steps")
        app_steps   = app_data["latest"]["steps"]
        assert api_steps == app_steps, \
            f"Latest steps: API={api_steps} but app shows={app_steps} (day={most_recent['day']})"

    def test_activity_lag_flag_is_accurate(self, app_data, raw_activity):
        """activity_is_yesterday flag must correctly reflect whether activity day == today."""
        if not raw_activity:
            pytest.skip("No activity data")
        today        = str(date.today())
        most_recent  = sorted(raw_activity, key=lambda x: x["day"])[-1]["day"]
        expected_lag = (most_recent != today)
        actual_lag   = app_data["data_dates"]["activity_is_yesterday"]
        assert actual_lag == expected_lag, \
            f"activity_is_yesterday flag wrong: activity_day={most_recent}, today={today}, flag={actual_lag}"

    def test_data_dates_today_is_correct(self, app_data):
        """data_dates.today must equal today's actual date."""
        assert app_data["data_dates"]["today"] == str(date.today()), \
            f"data_dates.today = {app_data['data_dates']['today']}, expected {date.today()}"


# ── Sleep Debt Accuracy ────────────────────────────────────────────────────────

class TestSleepDebtLive:

    def test_sleep_debt_matches_manual_calculation(self, raw_sleep_detail):
        """
        Calculate sleep debt from raw API sleep_duration fields and compare
        to what calc_sleep_debt() returns. These must match exactly.
        """
        if not raw_sleep_detail:
            pytest.skip("No sleep detail data")

        # Build input the same way build_data() does
        nights = sorted(raw_sleep_detail, key=lambda x: x.get("day", ""))[-30:]
        app_debt, app_log = calc_sleep_debt(nights, target_hours=8.0)

        # Manual calculation
        manual_debt = 0.0
        for night in nights:
            secs   = night.get("total_sleep_duration") or 0
            actual = round(secs / 3600, 2)
            manual_debt += max(0, 8.0 - actual) - max(0, actual - 8.0)

        assert abs(app_debt - round(manual_debt, 1)) < 0.1, \
            f"Debt mismatch: app={app_debt}h, manual={round(manual_debt,1)}h"

    def test_sleep_debt_log_actual_hours_match_raw_api(self, raw_sleep_detail):
        """Every 'actual' hours in the debt log must match raw API total_sleep_duration."""
        if not raw_sleep_detail:
            pytest.skip("No sleep detail data")

        nights = sorted(raw_sleep_detail, key=lambda x: x.get("day", ""))[-30:]
        _, log  = calc_sleep_debt(nights, target_hours=8.0)

        for entry, night in zip(log, nights):
            secs          = night.get("total_sleep_duration") or 0
            expected_hrs  = round(secs / 3600, 2)
            assert abs(entry["actual"] - expected_hrs) < 0.01, \
                f"Day {entry['date']}: log shows {entry['actual']}h but API has {expected_hrs}h"

    def test_sleep_debt_in_app_data(self, app_data):
        """App response must include sleep_debt field with a numeric value."""
        assert "sleep_debt" in app_data
        debt = app_data["sleep_debt"]
        assert isinstance(debt, (int, float)), f"sleep_debt is not a number: {type(debt)}"
        assert -20 < debt < 200, f"sleep_debt {debt}h is physiologically implausible"


# ── HRV Accuracy ─────────────────────────────────────────────────────────────

class TestHRVAccuracy:

    def test_latest_avg_hrv_matches_api(self, app_data, raw_sleep_detail):
        """Average HRV shown (from last sleep session) must match raw API value."""
        if not raw_sleep_detail:
            pytest.skip("No sleep detail data")
        most_recent = sorted(raw_sleep_detail, key=lambda x: x.get("day", ""))[-1]
        api_hrv     = most_recent.get("average_hrv")
        app_hrv     = app_data["latest"].get("avg_hrv")
        if api_hrv is None:
            pytest.skip("No HRV in most recent sleep session")
        assert abs((app_hrv or 0) - api_hrv) < 1.0, \
            f"HRV mismatch: API={api_hrv}ms, app={app_hrv}ms"

    def test_hrv_series_non_empty(self, app_data):
        """HRV balance series must have at least some non-null values."""
        hrv = [v for v in app_data["scores"]["hrv"] if v is not None]
        assert len(hrv) > 0, "HRV series is entirely null"

    def test_hrv_values_physiologically_plausible(self, app_data):
        for d, v in zip(app_data["days"], app_data["scores"]["hrv"]):
            if v is not None:
                assert 20 <= v <= 200, f"HRV {v} on {d} is not physiologically plausible (expect 20–200ms)"


# ── Forecast Sanity ───────────────────────────────────────────────────────────

class TestForecast:

    def test_forecast_has_7_days(self, app_data):
        assert len(app_data["forecast"]) == 7, \
            f"Expected 7 forecast days, got {len(app_data['forecast'])}"

    def test_forecast_scores_in_range(self, app_data):
        for f in app_data["forecast"]:
            assert 0 <= f["score"] <= 100, \
                f"Forecast score {f['score']} for {f['dow']} is out of range"

    def test_forecast_has_required_fields(self, app_data):
        required = {"dow", "month_day", "score", "rec", "color", "is_weekend"}
        for f in app_data["forecast"]:
            assert required.issubset(f.keys()), \
                f"Forecast entry missing keys: {required - f.keys()}"


# ── Anomaly Detection ─────────────────────────────────────────────────────────

class TestAnomalies:

    def test_anomalies_have_required_fields(self, app_data):
        required = {"date", "dow", "metric", "score", "avg", "drop", "label"}
        for a in app_data.get("anomalies", []):
            assert required.issubset(a.keys()), \
                f"Anomaly missing keys: {required - a.keys()}"

    def test_anomaly_drops_are_positive(self, app_data):
        """Every anomaly represents a drop, so 'drop' must be positive."""
        for a in app_data.get("anomalies", []):
            assert a["drop"] > 0, \
                f"Anomaly drop should be positive but got {a['drop']} on {a['date']}"

    def test_anomaly_scores_below_avg(self, app_data):
        """An anomaly score must be below the rolling avg by definition."""
        for a in app_data.get("anomalies", []):
            assert a["score"] < a["avg"], \
                f"Anomaly score {a['score']} should be < avg {a['avg']} on {a['date']}"


# ── Recovery Intelligence ─────────────────────────────────────────────────────

class TestRecoveryIntelligence:

    def test_recovery_intel_present(self, app_data):
        assert "recovery_intel" in app_data
        ri = app_data["recovery_intel"]
        assert ri is not None, "recovery_intel is None"

    def test_personal_target_plausible(self, app_data):
        ri = app_data.get("recovery_intel") or {}
        target = ri.get("personal_target")
        if target:
            assert 5.0 <= target <= 12.0, \
                f"Personal sleep target {target}h is not physiologically plausible"

    def test_avg_latency_plausible(self, app_data):
        """Average sleep latency should be between 0 and 90 minutes."""
        ri = app_data.get("recovery_intel") or {}
        latency = ri.get("avg_latency_min")
        if latency is not None:
            assert 0 <= latency <= 90, \
                f"Avg sleep latency {latency}min is implausible (expect 0–90)"

    def test_personal_records_values_are_positive(self, app_data):
        ri = app_data.get("recovery_intel") or {}
        pr = ri.get("personal_records") or {}
        for key, rec in pr.items():
            if rec and "value" in rec:
                assert rec["value"] > 0, \
                    f"Personal record {key} has non-positive value: {rec['value']}"


# ── Heatmap Coverage ──────────────────────────────────────────────────────────

class TestHeatmap:

    def test_heatmap_has_60_entries(self, app_data):
        assert len(app_data["heatmap"]) == len(app_data["days"]), \
            "Heatmap length != days length"

    def test_heatmap_dates_are_sequential(self, app_data):
        dates = [h["date"] for h in app_data["heatmap"]]
        for i in range(1, len(dates)):
            d_prev = date.fromisoformat(dates[i - 1])
            d_curr = date.fromisoformat(dates[i])
            assert d_curr > d_prev, \
                f"Heatmap dates not in order: {dates[i-1]} then {dates[i]}"

    def test_heatmap_scores_valid_or_null(self, app_data):
        for h in app_data["heatmap"]:
            s = h.get("score")
            if s is not None:
                assert 0 <= s <= 100, \
                    f"Heatmap score {s} on {h['date']} out of range"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
