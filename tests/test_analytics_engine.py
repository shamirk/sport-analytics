"""Unit tests for app.services.analytics_engine.AnalyticsEngine."""
from datetime import date, timedelta

import pytest

from app.services.analytics_engine import AnalyticsEngine


@pytest.fixture
def engine():
    return AnalyticsEngine()


def _make_scores(n: int, base_pct: float = 70.0, division: str = "Limited") -> list[dict]:
    """Build n classifier score dicts with incrementing dates."""
    start = date(2023, 1, 1)
    return [
        {
            "classifier_number": f"99-{i:02d}",
            "percentage": base_pct + i,
            "match_date": (start + timedelta(days=i * 30)).isoformat(),
            "division": division,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# compute_classifier_stats
# ---------------------------------------------------------------------------


class TestComputeClassifierStats:
    def test_empty_returns_empty_dict(self, engine):
        assert engine.compute_classifier_stats([]) == {}

    def test_mean_and_median(self, engine):
        scores = [
            {"percentage": 60.0, "match_date": "2024-01-01"},
            {"percentage": 80.0, "match_date": "2024-02-01"},
        ]
        result = engine.compute_classifier_stats(scores)
        assert result["mean"] == pytest.approx(70.0)
        assert result["median"] == pytest.approx(70.0)

    def test_std_single_entry_is_zero(self, engine):
        scores = [{"percentage": 75.0, "match_date": "2024-01-01"}]
        result = engine.compute_classifier_stats(scores)
        assert result["std"] == 0.0

    def test_rolling_averages_present(self, engine):
        scores = _make_scores(25)
        result = engine.compute_classifier_stats(scores)
        assert "rolling_5" in result
        assert "rolling_10" in result
        assert "rolling_20" in result

    def test_trend_slope_none_for_single_entry(self, engine):
        scores = [{"percentage": 80.0, "match_date": "2024-01-01"}]
        result = engine.compute_classifier_stats(scores)
        assert result["trend_slope"] is None

    def test_trend_slope_positive_for_improving(self, engine):
        scores = [
            {"percentage": 60.0, "match_date": "2024-01-01"},
            {"percentage": 70.0, "match_date": "2024-02-01"},
            {"percentage": 80.0, "match_date": "2024-03-01"},
        ]
        result = engine.compute_classifier_stats(scores)
        assert result["trend_slope"] > 0

    def test_trend_slope_negative_for_declining(self, engine):
        scores = [
            {"percentage": 90.0, "match_date": "2024-01-01"},
            {"percentage": 75.0, "match_date": "2024-02-01"},
            {"percentage": 60.0, "match_date": "2024-03-01"},
        ]
        result = engine.compute_classifier_stats(scores)
        assert result["trend_slope"] < 0

    def test_volatility_index_present(self, engine):
        scores = _make_scores(5)
        result = engine.compute_classifier_stats(scores)
        assert "volatility_index" in result

    def test_volatility_zero_when_all_same(self, engine):
        scores = [
            {"percentage": 70.0, "match_date": f"2024-0{i+1}-01"}
            for i in range(3)
        ]
        result = engine.compute_classifier_stats(scores)
        assert result["volatility_index"] == pytest.approx(0.0)

    def test_yoy_improvement_none_without_dates(self, engine):
        scores = [{"percentage": 70.0} for _ in range(5)]
        result = engine.compute_classifier_stats(scores)
        assert result["yoy_improvement"] is None

    def test_yoy_improvement_calculated(self, engine):
        today = date(2025, 6, 1)
        old_scores = [
            {"percentage": 60.0, "match_date": (today - timedelta(days=400 + i * 10)).isoformat()}
            for i in range(3)
        ]
        new_scores = [
            {"percentage": 80.0, "match_date": (today - timedelta(days=i * 10)).isoformat()}
            for i in range(3)
        ]
        result = engine.compute_classifier_stats(old_scores + new_scores)
        assert result["yoy_improvement"] == pytest.approx(20.0, abs=1.0)

    def test_personal_bests_with_classifier_number(self, engine):
        scores = [
            {"percentage": 70.0, "match_date": "2024-01-01", "classifier_number": "99-11"},
            {"percentage": 85.0, "match_date": "2024-02-01", "classifier_number": "99-11"},
            {"percentage": 60.0, "match_date": "2024-03-01", "classifier_number": "18-01"},
        ]
        result = engine.compute_classifier_stats(scores)
        bests = {b["classifier_number"]: b["best_percentage"] for b in result["personal_bests"]}
        assert bests["99-11"] == pytest.approx(85.0)
        assert bests["18-01"] == pytest.approx(60.0)

    def test_personal_bests_empty_without_classifier_column(self, engine):
        scores = [{"percentage": 75.0, "match_date": "2024-01-01"}]
        result = engine.compute_classifier_stats(scores)
        assert result["personal_bests"] == []

    def test_nan_percentages_are_dropped(self, engine):
        scores = [
            {"percentage": "not_a_number", "match_date": "2024-01-01"},
            {"percentage": 80.0, "match_date": "2024-02-01"},
        ]
        result = engine.compute_classifier_stats(scores)
        assert result["mean"] == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# compute_division_stats
# ---------------------------------------------------------------------------


class TestComputeDivisionStats:
    def test_empty_returns_empty_dict(self, engine):
        assert engine.compute_division_stats([]) == {}

    def test_missing_division_column_returns_empty(self, engine):
        scores = [{"percentage": 70.0}]
        assert engine.compute_division_stats(scores) == {}

    def test_division_averages_present(self, engine):
        scores = [
            {"division": "Open", "percentage": 90.0},
            {"division": "Open", "percentage": 80.0},
            {"division": "Limited", "percentage": 70.0},
        ]
        result = engine.compute_division_stats(scores)
        avgs = {row["division"]: row["avg_percentage"] for row in result["division_averages"]}
        assert avgs["Open"] == pytest.approx(85.0)
        assert avgs["Limited"] == pytest.approx(70.0)

    def test_sorted_descending_by_avg(self, engine):
        scores = [
            {"division": "Limited", "percentage": 60.0},
            {"division": "Open", "percentage": 90.0},
        ]
        result = engine.compute_division_stats(scores)
        averages = result["division_averages"]
        assert averages[0]["division"] == "Open"

    def test_ranks_assigned(self, engine):
        scores = [
            {"division": "Open", "percentage": 90.0},
            {"division": "Limited", "percentage": 70.0},
        ]
        result = engine.compute_division_stats(scores)
        ranks = {row["division"]: row["rank"] for row in result["division_averages"]}
        assert ranks["Open"] == 1
        assert ranks["Limited"] == 2

    def test_radar_chart_data_normalized_0_to_100(self, engine):
        scores = [
            {"division": "Open", "percentage": 90.0},
            {"division": "Limited", "percentage": 50.0},
        ]
        result = engine.compute_division_stats(scores)
        normalized = {row["division"]: row["normalized"] for row in result["radar_chart_data"]}
        assert normalized["Open"] == pytest.approx(100.0)
        assert normalized["Limited"] == pytest.approx(0.0)

    def test_single_division_normalized_zero(self, engine):
        # When min == max, (val - min) / denom * 100 = 0 by the normalization formula.
        scores = [{"division": "Production", "percentage": 75.0}]
        result = engine.compute_division_stats(scores)
        assert result["radar_chart_data"][0]["normalized"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# compute_match_stats
# ---------------------------------------------------------------------------


class TestComputeMatchStats:
    def test_empty_returns_empty_dict(self, engine):
        assert engine.compute_match_stats([]) == {}

    def test_avg_percent_finish(self, engine):
        matches = [
            {"percent_finish": 80.0, "match_date": "2024-01-01"},
            {"percent_finish": 60.0, "match_date": "2024-02-01"},
        ]
        result = engine.compute_match_stats(matches)
        assert result["avg_percent_finish"] == pytest.approx(70.0)

    def test_best_and_worst_placement(self, engine):
        matches = [
            {"percent_finish": 70.0, "match_date": "2024-01-01", "placement": 3},
            {"percent_finish": 80.0, "match_date": "2024-02-01", "placement": 1},
            {"percent_finish": 60.0, "match_date": "2024-03-01", "placement": 7},
        ]
        result = engine.compute_match_stats(matches)
        assert result["best_placement"] == 1
        assert result["worst_placement"] == 7

    def test_no_placement_column_skipped(self, engine):
        matches = [{"percent_finish": 75.0, "match_date": "2024-01-01"}]
        result = engine.compute_match_stats(matches)
        assert "best_placement" not in result
        assert "worst_placement" not in result

    def test_trend_is_list(self, engine):
        matches = [
            {"percent_finish": 70.0, "match_date": "2024-01-01"},
            {"percent_finish": 80.0, "match_date": "2024-02-01"},
        ]
        result = engine.compute_match_stats(matches)
        assert isinstance(result["trend"], list)
        assert len(result["trend"]) == 2

    def test_trend_sorted_by_date(self, engine):
        matches = [
            {"percent_finish": 80.0, "match_date": "2024-03-01"},
            {"percent_finish": 70.0, "match_date": "2024-01-01"},
        ]
        result = engine.compute_match_stats(matches)
        dates = [row["match_date"] for row in result["trend"]]
        assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# prepare_time_series
# ---------------------------------------------------------------------------


class TestPrepareTimeSeries:
    def test_empty_returns_empty_list(self, engine):
        assert engine.prepare_time_series([]) == []

    def test_returns_sorted_by_date(self, engine):
        scores = [
            {"percentage": 80.0, "match_date": "2024-03-01", "division": "Open"},
            {"percentage": 70.0, "match_date": "2024-01-01", "division": "Open"},
        ]
        result = engine.prepare_time_series(scores)
        dates = [r["match_date"] for r in result]
        assert dates == sorted(dates)

    def test_rolling_averages_in_output(self, engine):
        scores = _make_scores(6)
        result = engine.prepare_time_series(scores)
        assert "rolling_5" in result[0]
        assert "rolling_10" in result[0]
        assert "rolling_20" in result[0]

    def test_division_filter(self, engine):
        scores = [
            {"percentage": 80.0, "match_date": "2024-01-01", "division": "Open"},
            {"percentage": 70.0, "match_date": "2024-02-01", "division": "Limited"},
        ]
        result = engine.prepare_time_series(scores, division="Open")
        assert len(result) == 1
        assert result[0]["percentage"] == pytest.approx(80.0)

    def test_no_division_filter_returns_all(self, engine):
        scores = [
            {"percentage": 80.0, "match_date": "2024-01-01", "division": "Open"},
            {"percentage": 70.0, "match_date": "2024-02-01", "division": "Limited"},
        ]
        result = engine.prepare_time_series(scores)
        assert len(result) == 2

    def test_drops_entries_without_date_or_pct(self, engine):
        scores = [
            {"percentage": None, "match_date": "2024-01-01"},
            {"percentage": 80.0, "match_date": None},
            {"percentage": 75.0, "match_date": "2024-03-01"},
        ]
        result = engine.prepare_time_series(scores)
        assert len(result) == 1
        assert result[0]["percentage"] == pytest.approx(75.0)

    def test_date_formatted_as_yyyy_mm_dd(self, engine):
        scores = [{"percentage": 75.0, "match_date": "2024-06-15"}]
        result = engine.prepare_time_series(scores)
        assert result[0]["match_date"] == "2024-06-15"


# ---------------------------------------------------------------------------
# prepare_classifier_breakdown
# ---------------------------------------------------------------------------


class TestPrepareClassifierBreakdown:
    def test_empty_returns_empty_dict(self, engine):
        assert engine.prepare_classifier_breakdown([]) == {}

    def test_top_10_sorted_descending(self, engine):
        scores = [{"percentage": float(i), "classifier_number": f"99-{i:02d}"} for i in range(20)]
        result = engine.prepare_classifier_breakdown(scores)
        top = result["top_10"]
        assert len(top) <= 10
        percentages = [r["percentage"] for r in top]
        assert percentages == sorted(percentages, reverse=True)

    def test_bottom_10_sorted_ascending(self, engine):
        scores = [{"percentage": float(i), "classifier_number": f"99-{i:02d}"} for i in range(20)]
        result = engine.prepare_classifier_breakdown(scores)
        bottom = result["bottom_10"]
        assert len(bottom) <= 10
        percentages = [r["percentage"] for r in bottom]
        assert percentages == sorted(percentages)

    def test_most_frequent_with_classifier_column(self, engine):
        scores = (
            [{"percentage": 75.0, "classifier_number": "99-11"}] * 5
            + [{"percentage": 70.0, "classifier_number": "18-01"}] * 2
        )
        result = engine.prepare_classifier_breakdown(scores)
        freq = result["most_frequent"]
        assert freq[0]["classifier_number"] == "99-11"

    def test_most_frequent_empty_without_classifier_column(self, engine):
        scores = [{"percentage": 75.0}]
        result = engine.prepare_classifier_breakdown(scores)
        assert result["most_frequent"] == []

    def test_improvement_rate_per_classifier(self, engine):
        scores = [
            {"percentage": 60.0, "classifier_number": "99-11"},
            {"percentage": 70.0, "classifier_number": "99-11"},
            {"percentage": 80.0, "classifier_number": "99-11"},
        ]
        result = engine.prepare_classifier_breakdown(scores)
        rates = result["improvement_rate_per_classifier"]
        assert len(rates) == 1
        assert rates[0]["classifier_number"] == "99-11"
        assert rates[0]["improvement_rate"] > 0

    def test_improvement_rate_needs_at_least_2_attempts(self, engine):
        scores = [{"percentage": 75.0, "classifier_number": "99-11"}]
        result = engine.prepare_classifier_breakdown(scores)
        assert result["improvement_rate_per_classifier"] == []

    def test_drops_nan_percentages(self, engine):
        scores = [
            {"percentage": "bad", "classifier_number": "99-11"},
            {"percentage": 80.0, "classifier_number": "18-01"},
        ]
        result = engine.prepare_classifier_breakdown(scores)
        assert len(result["top_10"]) == 1
