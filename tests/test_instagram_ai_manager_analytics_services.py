"""Tests for jarvis.instagram_ai_manager.analytics_services: read-only
performance analysis built entirely on real, already-fetched data (no
LLM call, no fabricated numbers). InstagramConnector is mocked (its own
network behavior is already covered by tests/test_instagram_connector.py);
jarvis.integrations.instagram_history is redirected to a per-test
tmp_path file. Confirms: every function reports insufficient_data
plainly rather than guessing when there isn't enough real data, computed
numbers (median, performance_score, percent_change) are exactly what the
math says given the mocked inputs, and no function ever fabricates a
metric name/value not present in its input data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from jarvis.instagram_ai_manager import analytics_services as az


@pytest.fixture(autouse=True)
def _isolated_instagram_history(tmp_path, monkeypatch):
    from jarvis.integrations import instagram_history

    history_file = tmp_path / "instagram_insights_history.json"
    monkeypatch.setattr(instagram_history, "INSTAGRAM_INSIGHTS_HISTORY_FILE", history_file)
    return history_file


def _fake_post(post_id: str, *, reach: float, likes=0.0, comments=0.0, shares=0.0, saved=0.0, **extra):
    post = {
        "id": post_id, "caption": f"caption {post_id}", "media_type": "IMAGE",
        "permalink": f"https://instagram.com/p/{post_id}",
        "timestamp": "2026-01-01T10:00:00+0000",
        "reach": reach, "likes": likes, "comments": comments, "shares": shares, "saved": saved,
    }
    post.update(extra)
    return post


def _connector_with_posts(posts: list[dict]) -> MagicMock:
    connector = MagicMock()
    connector.get_recent_media_with_insights.return_value = posts
    return connector


# --- analyze_reels ---------------------------------------------------------------------


def test_analyze_reels_insufficient_data_below_minimum_posts():
    connector = _connector_with_posts([_fake_post("1", reach=100)])
    result = az.analyze_reels(connector)
    assert result.insufficient_data is True
    assert result.message is not None
    assert "1" in result.message  # states how many posts it actually had


def test_analyze_reels_still_shows_raw_metrics_when_insufficient():
    connector = _connector_with_posts([_fake_post("1", reach=100, likes=5)])
    result = az.analyze_reels(connector)
    assert result.reels[0].likes == 5
    assert result.reels[0].performance_score is None  # no comparative claim made


def test_analyze_reels_computes_median_reach_correctly():
    posts = [_fake_post(str(i), reach=r) for i, r in enumerate([100, 200, 300, 400, 500])]
    connector = _connector_with_posts(posts)
    result = az.analyze_reels(connector)
    assert result.insufficient_data is False
    assert result.median_reach == 300


def test_analyze_reels_performance_score_is_reach_over_median():
    posts = [_fake_post(str(i), reach=r) for i, r in enumerate([100, 100, 100, 100, 400])]
    connector = _connector_with_posts(posts)
    result = az.analyze_reels(connector)
    # median of [100,100,100,100,400] = 100
    top_post = next(r for r in result.reels if r.reach == 400)
    assert top_post.performance_score == 4.0


def test_analyze_reels_engagement_rate_computed_correctly():
    posts = [_fake_post(str(i), reach=1000) for i in range(5)]
    posts[0]["likes"] = 50
    posts[0]["comments"] = 10
    connector = _connector_with_posts(posts)
    result = az.analyze_reels(connector)
    # (50 + 10 + 0 + 0) / 1000 = 0.06
    assert result.reels[0].engagement_rate == pytest.approx(0.06)


def test_analyze_reels_missing_reach_gives_none_engagement_rate():
    posts = [_fake_post(str(i), reach=1000) for i in range(4)]
    posts.append({"id": "no_reach", "caption": "", "media_type": "IMAGE", "permalink": "", "timestamp": "x", "likes": 5.0})
    connector = _connector_with_posts(posts)
    result = az.analyze_reels(connector)
    missing_reach_post = next(r for r in result.reels if r.media_id == "no_reach")
    assert missing_reach_post.engagement_rate is None


def test_analyze_reels_never_calls_llm():
    # Confirms this module has no LLM dependency at all - a MagicMock
    # connector with no llm-related attribute set still works correctly.
    connector = _connector_with_posts([_fake_post(str(i), reach=100) for i in range(5)])
    az.analyze_reels(connector)  # must not raise / must not need any LLM mock


# --- identify_best_performing_content ---------------------------------------------------


def test_identify_best_performing_propagates_insufficient_data():
    connector = _connector_with_posts([_fake_post("1", reach=100)])
    analysis = az.analyze_reels(connector)
    best = az.identify_best_performing_content(analysis)
    assert best.insufficient_data is True
    assert best.top_reels == []


def test_identify_best_performing_ranks_by_performance_score():
    posts = [_fake_post(str(i), reach=r) for i, r in enumerate([100, 100, 100, 100, 500])]
    connector = _connector_with_posts(posts)
    analysis = az.analyze_reels(connector)
    best = az.identify_best_performing_content(analysis, top_n=1)
    assert best.top_reels[0].reach == 500


def test_identify_best_performing_explanation_cites_real_numbers():
    posts = [_fake_post(str(i), reach=r) for i, r in enumerate([100, 100, 100, 100, 900])]
    connector = _connector_with_posts(posts)
    analysis = az.analyze_reels(connector)
    best = az.identify_best_performing_content(analysis, top_n=1)
    assert "900" in best.explanation
    assert "x" in best.explanation  # states a multiple, e.g. "9.0x"


def test_identify_best_performing_groups_by_media_type():
    posts = [_fake_post(str(i), reach=100 + i * 10, media_type="VIDEO") for i in range(5)]
    connector = _connector_with_posts(posts)
    analysis = az.analyze_reels(connector)
    best = az.identify_best_performing_content(analysis)
    assert any(media_type == "VIDEO" for media_type, _ in best.top_topics)


# --- compare_periods ---------------------------------------------------------------------


def test_compare_periods_insufficient_when_no_history():
    result = az.compare_periods(period_days=7)
    assert result.insufficient_data is True
    assert result.message is not None


def test_compare_periods_computes_percent_change_correctly():
    from jarvis.integrations import instagram_history

    today = datetime.now(timezone.utc).date()
    # Current period (today): reach 200. Previous period (8 days ago): reach 100.
    instagram_history.upsert_snapshot(today.isoformat(), {"reach": 200})
    instagram_history.upsert_snapshot((today - timedelta(days=8)).isoformat(), {"reach": 100})

    result = az.compare_periods(period_days=7)
    reach_comparison = next(c for c in result.comparisons if c.metric == "reach")
    assert reach_comparison.current_value == 200
    assert reach_comparison.previous_value == 100
    assert reach_comparison.percent_change == 100.0
    assert reach_comparison.insufficient_data is False


def test_compare_periods_metric_missing_from_one_period_is_marked_insufficient():
    from jarvis.integrations import instagram_history

    today = datetime.now(timezone.utc).date()
    instagram_history.upsert_snapshot(today.isoformat(), {"reach": 200})
    instagram_history.upsert_snapshot((today - timedelta(days=8)).isoformat(), {"likes": 5})  # no reach here

    result = az.compare_periods(period_days=7)
    reach_comparison = next(c for c in result.comparisons if c.metric == "reach")
    assert reach_comparison.insufficient_data is True
    assert reach_comparison.percent_change is None


def test_compare_periods_clamps_period_days_to_sane_range():
    result = az.compare_periods(period_days=99999)
    assert result.period_days == 90


def test_compare_periods_never_fabricates_a_metric_not_in_known_metrics_or_history():
    from jarvis.integrations import instagram_history

    today = datetime.now(timezone.utc).date()
    instagram_history.upsert_snapshot(today.isoformat(), {"reach": 200, "made_up_metric": 5})
    result = az.compare_periods(period_days=7)
    metric_names = {c.metric for c in result.comparisons}
    # made_up_metric IS included because it was actually recorded (real
    # data) - the point is compare_periods never ADDS a metric that
    # wasn't either in the recorded data or the known metrics list.
    assert metric_names <= (set(instagram_history.KNOWN_METRICS) | {"made_up_metric"})


# --- analyze_posting_times ----------------------------------------------------------------


def test_analyze_posting_times_insufficient_when_no_posts():
    connector = _connector_with_posts([])
    result = az.analyze_posting_times(connector)
    assert result.insufficient_data is True


def test_analyze_posting_times_low_confidence_with_few_distinct_days():
    posts = [_fake_post(str(i), reach=100, timestamp="2026-01-01T10:00:00+0000") for i in range(3)]
    connector = _connector_with_posts(posts)
    result = az.analyze_posting_times(connector)
    assert result.data_confidence == "low"
    assert result.insufficient_data is False


def test_analyze_posting_times_never_claims_universal_best():
    posts = [_fake_post(str(i), reach=100, timestamp="2026-01-01T10:00:00+0000") for i in range(3)]
    connector = _connector_with_posts(posts)
    result = az.analyze_posting_times(connector)
    assert "your account" in result.message.lower() or "posts across" in result.message.lower()
    assert "universal" not in result.message.lower()


def test_analyze_posting_times_ranks_days_by_mean_reach():
    posts = []
    # Monday 2026-01-05: low reach. Tuesday 2026-01-06: high reach.
    for i in range(3):
        posts.append(_fake_post(f"mon{i}", reach=50, timestamp="2026-01-05T10:00:00+0000"))
    for i in range(3):
        posts.append(_fake_post(f"tue{i}", reach=500, timestamp="2026-01-06T10:00:00+0000"))
    connector = _connector_with_posts(posts)
    result = az.analyze_posting_times(connector)
    assert result.best_days[0] == "Tuesday"


def test_analyze_posting_times_skips_posts_without_reach():
    posts = [_fake_post("1", reach=100, timestamp="2026-01-01T10:00:00+0000")]
    posts.append({"id": "2", "timestamp": "2026-01-02T10:00:00+0000"})  # no reach key
    connector = _connector_with_posts(posts)
    result = az.analyze_posting_times(connector)  # must not raise
    assert result.days_of_data == 1


def test_analyze_posting_times_skips_posts_with_malformed_timestamp():
    posts = [_fake_post("1", reach=100, timestamp="not-a-timestamp")]
    connector = _connector_with_posts(posts)
    result = az.analyze_posting_times(connector)  # must not raise
    assert result.insufficient_data is True


# --- generate_next_week_recommendations ----------------------------------------------------


def test_recommendations_insufficient_when_analysis_insufficient():
    connector = _connector_with_posts([_fake_post("1", reach=100)])
    result = az.generate_next_week_recommendations(connector)
    assert result.insufficient_data is True
    assert result.recommendations == []


def test_recommendations_include_supporting_data_for_every_item():
    posts = [_fake_post(str(i), reach=r, likes=10, comments=2) for i, r in enumerate([100, 100, 100, 100, 900])]
    connector = _connector_with_posts(posts)
    result = az.generate_next_week_recommendations(connector)
    assert result.insufficient_data is False
    assert len(result.recommendations) > 0
    for rec in result.recommendations:
        assert rec.supporting_data  # never an empty/missing justification


def test_recommendations_never_calls_llm():
    # generate_next_week_recommendations is explicitly NOT an LLM call
    # (see its own docstring) - a bare MagicMock connector with no LLM
    # wiring at all must still work.
    posts = [_fake_post(str(i), reach=r) for i, r in enumerate([100, 100, 100, 100, 900])]
    connector = _connector_with_posts(posts)
    az.generate_next_week_recommendations(connector)  # must not raise
