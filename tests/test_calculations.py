"""
Oura Edge — Backend Calculation Unit Tests
Tests data accuracy for all core computations: sleep debt, correlations,
regression, tonight card logic, and API response parsing.

Run: cd /Users/saintlydigital-clawbot/oura-claude && python -m pytest tests/ -v
"""

import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

import pytest

# Import functions under test from api/index.py
from index import (
    calc_sleep_debt, pearson, linreg, mean, std, clamp,
    build_tonight_card,
)


# ── calc_sleep_debt ────────────────────────────────────────────────────────────

class TestCalcSleepDebt:
    """
    calc_sleep_debt(sleep_detail, target_hours=8.0) → (total_debt_hrs, log)
    Each entry in sleep_detail must have 'total_sleep_duration' (seconds) and 'day'.
    """

    def _make_nights(self, hours_list):
        """Build synthetic sleep detail records from a list of sleep hours."""
        from datetime import date, timedelta
        today = date(2025, 3, 22)
        return [
            {
                "day": str(today - timedelta(days=len(hours_list) - 1 - i)),
                "total_sleep_duration": int(h * 3600),
            }
            for i, h in enumerate(hours_list)
        ]

    def test_zero_debt_perfect_sleep(self):
        """8h every night for 7 nights → 0h debt."""
        nights = self._make_nights([8.0] * 7)
        debt, log = calc_sleep_debt(nights, target_hours=8.0)
        assert debt == 0.0
        assert len(log) == 7
        assert all(entry["debt"] == 0.0 for entry in log)

    def test_simple_one_night_short(self):
        """6h sleep one night → 2h debt."""
        nights = self._make_nights([6.0])
        debt, log = calc_sleep_debt(nights, target_hours=8.0)
        assert debt == 2.0
        assert log[0]["debt"] == 2.0
        assert log[0]["actual"] == 6.0

    def test_cumulative_debt_accumulates(self):
        """Deficit should accumulate across nights."""
        nights = self._make_nights([6.0, 6.0, 6.0])  # -2h each
        debt, log = calc_sleep_debt(nights, target_hours=8.0)
        assert debt == 6.0
        assert log[-1]["cumulative"] == 6.0

    def test_oversleep_creates_surplus(self):
        """9h sleep → negative debt (surplus of 1h)."""
        nights = self._make_nights([9.0])
        debt, log = calc_sleep_debt(nights, target_hours=8.0)
        assert debt == -1.0
        assert log[0]["debt"] == -1.0

    def test_mixed_debt_and_surplus(self):
        """Short night then long night should partially cancel."""
        nights = self._make_nights([6.0, 10.0])  # -2h then +2h
        debt, log = calc_sleep_debt(nights, target_hours=8.0)
        assert debt == 0.0

    def test_empty_sleep_detail(self):
        """Empty list → 0 debt, empty log."""
        debt, log = calc_sleep_debt([], target_hours=8.0)
        assert debt == 0.0
        assert log == []

    def test_missing_duration_field(self):
        """Entry with no total_sleep_duration treated as 0h sleep → full target as debt."""
        nights = [{"day": "2025-03-22", "total_sleep_duration": None}]
        debt, log = calc_sleep_debt(nights, target_hours=8.0)
        assert debt == 8.0

    def test_only_last_30_nights_used(self):
        """Only the last 30 entries should be counted regardless of how many are passed."""
        nights = self._make_nights([8.0] * 40)  # 40 perfect nights
        debt, log = calc_sleep_debt(nights, target_hours=8.0)
        assert len(log) == 30  # only last 30
        assert debt == 0.0

    def test_custom_target_hours(self):
        """Custom target of 7h — 6h sleep → 1h debt."""
        nights = self._make_nights([6.0])
        debt, log = calc_sleep_debt(nights, target_hours=7.0)
        assert debt == 1.0

    def test_log_entry_structure(self):
        """Each log entry must have date, actual, debt, cumulative keys."""
        nights = self._make_nights([7.0])
        _, log = calc_sleep_debt(nights, target_hours=8.0)
        entry = log[0]
        assert "date" in entry
        assert "actual" in entry
        assert "debt" in entry
        assert "cumulative" in entry

    def test_cumulative_is_running_total(self):
        """Cumulative should be the running sum at each point."""
        nights = self._make_nights([6.0, 7.0, 8.0])  # -2, -1, 0
        _, log = calc_sleep_debt(nights, target_hours=8.0)
        assert log[0]["cumulative"] == 2.0
        assert log[1]["cumulative"] == 3.0
        assert log[2]["cumulative"] == 3.0

    def test_result_rounded_to_one_decimal(self):
        """Total debt should be rounded to 1 decimal place."""
        nights = self._make_nights([6.33])
        debt, _ = calc_sleep_debt(nights, target_hours=8.0)
        assert debt == round(8.0 - 6.33, 1)


# ── pearson correlation ────────────────────────────────────────────────────────

class TestPearson:
    """pearson(xs, ys) → float in [-1, 1] or None"""

    def test_perfect_positive_correlation(self):
        """Identical lists → r = 1.0."""
        xs = [1, 2, 3, 4, 5, 6]
        r = pearson(xs, xs)
        assert r == 1.0

    def test_perfect_negative_correlation(self):
        """Reversed list → r = -1.0."""
        xs = [1, 2, 3, 4, 5, 6]
        ys = [6, 5, 4, 3, 2, 1]
        r = pearson(xs, ys)
        assert r == -1.0

    def test_zero_correlation(self):
        """Uncorrelated data → r ≈ 0."""
        xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        ys = [5, 5, 5, 5, 5, 5, 5, 5, 5, 5]  # constant → correlation undefined
        r = pearson(xs, ys)
        assert r is None  # denominator = 0

    def test_minimum_pairs_required(self):
        """Fewer than 5 non-null pairs → None."""
        r = pearson([1, 2, 3, 4], [1, 2, 3, 4])
        assert r is None

    def test_exactly_five_pairs_ok(self):
        """Exactly 5 non-null pairs → returns a value."""
        r = pearson([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])
        assert r is not None
        assert r == 1.0

    def test_none_values_filtered(self):
        """None values in xs/ys should be skipped."""
        xs = [1, None, 3, 4, 5, 6]
        ys = [1, 2,    3, 4, 5, 6]
        r = pearson(xs, ys)
        assert r is not None  # 5 valid pairs → computed

    def test_result_is_between_neg1_and_1(self):
        """Real-world style data — result must be in [-1, 1]."""
        import random
        rng = random.Random(42)
        xs = [rng.randint(5000, 15000) for _ in range(30)]
        ys = [rng.randint(40, 120) for _ in range(30)]
        r = pearson(xs, ys)
        assert r is not None
        assert -1.0 <= r <= 1.0

    def test_result_rounded_to_3_decimals(self):
        """Result should be rounded to 3 decimal places."""
        xs = [1, 2, 3, 4, 5, 6, 7]
        ys = [1, 3, 2, 5, 4, 7, 6]
        r = pearson(xs, ys)
        assert r == round(r, 3)


# ── linreg ────────────────────────────────────────────────────────────────────

class TestLinreg:
    """linreg(xs, ys) → {"slope": float, "intercept": float} or None"""

    def test_perfect_line_slope_1(self):
        """y = x → slope=1, intercept=0."""
        xs = list(range(10))
        ys = list(range(10))
        result = linreg(xs, ys)
        assert result is not None
        assert abs(result["slope"] - 1.0) < 1e-9
        assert abs(result["intercept"]) < 1e-9

    def test_perfect_line_slope_2(self):
        """y = 2x + 5 → slope=2, intercept=5."""
        xs = list(range(10))
        ys = [2 * x + 5 for x in xs]
        result = linreg(xs, ys)
        assert result is not None
        assert abs(result["slope"] - 2.0) < 1e-9
        assert abs(result["intercept"] - 5.0) < 1e-9

    def test_negative_slope(self):
        """y = -x → slope=-1."""
        xs = list(range(10))
        ys = [-x for x in xs]
        result = linreg(xs, ys)
        assert result is not None
        assert abs(result["slope"] - (-1.0)) < 1e-9

    def test_minimum_pairs_required(self):
        """Fewer than 8 non-null pairs → None."""
        result = linreg([1, 2, 3, 4, 5, 6, 7], [1, 2, 3, 4, 5, 6, 7])
        assert result is None

    def test_exactly_eight_pairs_ok(self):
        """Exactly 8 non-null pairs → returns result."""
        xs = list(range(8))
        ys = list(range(8))
        result = linreg(xs, ys)
        assert result is not None

    def test_constant_x_returns_none(self):
        """All x values identical → ss_xx=0 → None."""
        xs = [5] * 10
        ys = list(range(10))
        result = linreg(xs, ys)
        assert result is None

    def test_none_values_filtered(self):
        """None values should be excluded from computation."""
        xs = [1, None, 3, 4, 5, 6, 7, 8, 9, 10]
        ys = [1, 2,    3, 4, 5, 6, 7, 8, 9, 10]
        result = linreg(xs, ys)
        assert result is not None  # 9 valid pairs

    def test_slope_units(self):
        """Steps→deep sleep: slope should be positive for realistic data."""
        # Simulate: more steps → more deep sleep
        steps = [4000, 5000, 6000, 7000, 8000, 9000, 10000, 11000, 12000]
        deep  = [ 45,   52,   58,   65,   72,   78,    85,    90,    95]
        result = linreg(steps, deep)
        assert result is not None
        assert result["slope"] > 0  # positive relationship


# ── mean / std helpers ─────────────────────────────────────────────────────────

class TestMeanStd:
    def test_mean_basic(self):
        assert mean([1, 2, 3, 4, 5]) == 3.0

    def test_mean_ignores_none(self):
        assert mean([1, None, 3]) == 2.0

    def test_mean_empty(self):
        assert mean([]) is None

    def test_mean_all_none(self):
        assert mean([None, None]) is None

    def test_std_basic(self):
        vals = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        assert abs(std(vals) - 2.0) < 0.01

    def test_std_single_value(self):
        assert std([5.0]) == 0

    def test_std_empty(self):
        assert std([]) == 0


# ── clamp ──────────────────────────────────────────────────────────────────────

class TestClamp:
    def test_within_range(self):
        assert clamp(50, 0, 100) == 50

    def test_below_min(self):
        assert clamp(-5, 0, 100) == 0

    def test_above_max(self):
        assert clamp(150, 0, 100) == 100

    def test_exact_bounds(self):
        assert clamp(0, 0, 100) == 0
        assert clamp(100, 0, 100) == 100


# ── build_tonight_card ─────────────────────────────────────────────────────────

class TestBuildTonightCard:
    """
    build_tonight_card(act, ready, sleep, decoder, debt, act_scores, ready_scores, sleep_scores)
    """

    def _defaults(self):
        act   = {"steps": 8000}
        ready = {"score": 78, "contributors": {"hrv_balance": 80}}
        sleep = {"score": 75}
        decoder = {"findings": [], "best_nights": []}
        debt  = 0
        return act, ready, sleep, decoder, debt, [], [], []

    def test_great_verdict_high_readiness_high_steps(self):
        act, ready, sleep, decoder, debt, a, r, s = self._defaults()
        act["steps"] = 9000
        ready["score"] = 85
        card = build_tonight_card(act, ready, sleep, decoder, debt, a, r, s)
        assert card["verdict"] == "great"

    def test_at_risk_verdict_low_readiness_low_steps(self):
        act, ready, sleep, decoder, debt, a, r, s = self._defaults()
        act["steps"] = 1000
        ready["score"] = 55
        card = build_tonight_card(act, ready, sleep, decoder, debt, a, r, s)
        assert card["verdict"] == "at-risk"

    def test_ok_verdict_moderate(self):
        act, ready, sleep, decoder, debt, a, r, s = self._defaults()
        act["steps"] = 6000
        ready["score"] = 70
        card = build_tonight_card(act, ready, sleep, decoder, debt, a, r, s)
        assert card["verdict"] == "ok"

    def test_walk_action_added_when_steps_low(self):
        act, ready, sleep, decoder, debt, a, r, s = self._defaults()
        act["steps"] = 1000
        decoder["best_nights"] = [{"steps": 9000, "bed": "22:00"}]
        card = build_tonight_card(act, ready, sleep, decoder, debt, a, r, s)
        icons = [action["icon"] for action in card["actions"]]
        assert "🚶" in icons

    def test_debt_action_added_when_debt_high(self):
        act, ready, sleep, decoder, debt, a, r, s = self._defaults()
        debt = 5.0
        act["steps"] = 9000
        ready["score"] = 85
        card = build_tonight_card(act, ready, sleep, decoder, debt, a, r, s)
        icons = [action["icon"] for action in card["actions"]]
        assert "💳" in icons

    def test_hrv_action_added_when_hrv_low(self):
        act, ready, sleep, decoder, debt, a, r, s = self._defaults()
        ready["contributors"]["hrv_balance"] = 50
        card = build_tonight_card(act, ready, sleep, decoder, debt, a, r, s)
        icons = [action["icon"] for action in card["actions"]]
        assert "💆" in icons

    def test_max_two_actions_returned(self):
        """Card should never return more than 2 actions (top 2 priorities)."""
        act, ready, sleep, decoder, debt, a, r, s = self._defaults()
        act["steps"] = 500
        debt = 6.0
        ready["contributors"]["hrv_balance"] = 40
        decoder["best_nights"] = [{"steps": 9000, "bed": "22:00"}]
        card = build_tonight_card(act, ready, sleep, decoder, debt, a, r, s)
        assert len(card["actions"]) <= 2

    def test_card_has_required_keys(self):
        act, ready, sleep, decoder, debt, a, r, s = self._defaults()
        card = build_tonight_card(act, ready, sleep, decoder, debt, a, r, s)
        for key in ("verdict", "verdict_msg", "verdict_color", "actions", "steps_today", "best_steps"):
            assert key in card, f"Missing key: {key}"

    def test_none_steps_doesnt_crash(self):
        """Missing steps field should not crash."""
        act, ready, sleep, decoder, debt, a, r, s = self._defaults()
        act["steps"] = None
        card = build_tonight_card(act, ready, sleep, decoder, debt, a, r, s)
        assert "verdict" in card

    def test_optimal_bed_extracted_from_best_nights(self):
        act, ready, sleep, decoder, debt, a, r, s = self._defaults()
        decoder["best_nights"] = [{"steps": 9000, "bed": "22:30"}, {"steps": 8500, "bed": "23:00"}]
        card = build_tonight_card(act, ready, sleep, decoder, debt, a, r, s)
        assert card["optimal_bed"] == "22:30"


# ── sleep debt integration: data accuracy cross-check ─────────────────────────

class TestSleepDebtAccuracy:
    """
    Cross-check that sleep debt matches what you'd compute by hand
    from realistic Oura-style data.
    """

    def test_real_world_scenario(self):
        """
        Week of sleep: 6.5, 7.0, 5.5, 8.0, 7.5, 6.0, 7.0 hours
        vs 8h target:  -1.5, -1.0, -2.5, 0, -0.5, -2.0, -1.0
        Total debt:    8.5h
        """
        from datetime import date, timedelta
        base = date(2025, 3, 15)
        hours = [6.5, 7.0, 5.5, 8.0, 7.5, 6.0, 7.0]
        nights = [
            {"day": str(base + timedelta(days=i)),
             "total_sleep_duration": int(h * 3600)}
            for i, h in enumerate(hours)
        ]
        debt, log = calc_sleep_debt(nights, target_hours=8.0)
        expected = round(sum(8.0 - h for h in hours), 1)
        assert debt == expected, f"Expected {expected}h debt, got {debt}h"

    def test_debt_log_actual_matches_input(self):
        """Each log entry's 'actual' should match the input sleep hours."""
        from datetime import date, timedelta
        hours = [6.0, 7.5, 8.25, 5.5, 9.0, 7.0, 6.5, 8.0]
        base = date(2025, 3, 14)
        nights = [
            {"day": str(base + timedelta(days=i)),
             "total_sleep_duration": int(h * 3600)}
            for i, h in enumerate(hours)
        ]
        _, log = calc_sleep_debt(nights, target_hours=8.0)
        for i, entry in enumerate(log):
            expected_actual = round(hours[i], 2)
            assert abs(entry["actual"] - expected_actual) < 0.01, \
                f"Night {i}: expected {expected_actual}h, got {entry['actual']}h"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
