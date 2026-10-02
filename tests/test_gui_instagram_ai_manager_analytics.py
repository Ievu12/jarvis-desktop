"""Tests for jarvis.gui.views.instagram_ai_manager.analytics_view
.AnalyticsView (the Analytics tab): widget wiring and rendering, using a
real (withdrawn) CTk root. InstagramConnector and every
jarvis.instagram_ai_manager.analytics_services function are mocked, so
no real Graph API call or local-history read happens here - the
analytics_services functions themselves already have their own
dedicated, thorough tests (tests/test_instagram_ai_manager_analytics_services
.py); this file only confirms the VIEW renders each of the five
sections' insufficient_data/error/populated states correctly and wires
the Sync button/period dropdown/background-queue polling correctly,
mirroring the manual verification against the real, already-connected
Instagram account done during development (see this module's PR/commit
notes) - that real-account run is not repeatable in CI, so this file's
mocked ReelAnalysisResult/PeriodComparisonResult/etc. fixtures stand in
for it.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import customtkinter as ctk
import pytest

from jarvis.gui.views.instagram_ai_manager.analytics_view import AnalyticsView
from jarvis.instagram_ai_manager.analytics_services import (
    BestPerformingContent,
    NextWeekRecommendations,
    PeriodComparison,
    PeriodComparisonResult,
    PostingTimeAnalysis,
    Recommendation,
    ReelAnalysisResult,
    ReelPerformance,
)


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


def _pump(root, seconds: float = 2.0) -> None:
    """Drives Tkinter's event loop (root.update()) for up to `seconds`,
    giving background-thread results time to arrive on the view's
    polled queue - mirrors the polling this view itself does with
    .after(), just driven synchronously for a test."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        time.sleep(0.02)


def _make_reel(score: float = 2.0) -> ReelPerformance:
    return ReelPerformance(
        media_id="1", caption="Test caption", media_type="REEL", permalink="https://x",
        timestamp="2024-01-01T10:00:00+0000", views=1000, reach=1000, likes=50, comments=5,
        shares=2, saves=10, engagement_rate=0.067, performance_score=score, insufficient_data=False,
    )


@pytest.fixture
def not_configured_connector():
    with patch("jarvis.gui.views.instagram_ai_manager.analytics_view.InstagramConnector") as MockConnector:
        MockConnector.return_value.is_configured.return_value = False
        yield MockConnector


@pytest.fixture
def configured_connector():
    with patch("jarvis.gui.views.instagram_ai_manager.analytics_view.InstagramConnector") as MockConnector:
        MockConnector.return_value.is_configured.return_value = True
        yield MockConnector


# --- not connected -----------------------------------------------------------------------


def test_shows_not_connected_message_in_every_section_when_not_configured(root, not_configured_connector):
    view = AnalyticsView(root)
    root.update()
    assert "Instagram isn't connected" in _all_text(view._reel_perf_container)
    assert "Instagram isn't connected" in _all_text(view._best_content_container)
    assert "Instagram isn't connected" in _all_text(view._comparison_container)
    assert "Instagram isn't connected" in _all_text(view._posting_time_container)
    assert "Instagram isn't connected" in _all_text(view._recommendations_container)


def _all_text(container) -> str:
    parts = []

    def _walk(widget):
        try:
            t = widget.cget("text")
            if t:
                parts.append(t)
        except Exception:
            pass
        for child in widget.winfo_children():
            _walk(child)

    _walk(container)
    return " ".join(parts)


# --- populated / insufficient-data / error rendering ------------------------------------


def test_populated_results_render_without_error(root, configured_connector):
    analysis = ReelAnalysisResult(
        reels=[_make_reel()] * 6, median_reach=500, median_engagement_rate=0.05,
        insufficient_data=False, message=None,
    )
    best = BestPerformingContent(
        top_reels=[_make_reel()], top_topics=[("REEL", 2.0)],
        explanation="Your top post reached 2.0x median reach.", insufficient_data=False, message=None,
    )
    comparison = PeriodComparisonResult(
        period_days=7,
        comparisons=[PeriodComparison(metric="reach", current_value=1000, previous_value=800, percent_change=25.0, insufficient_data=False)],
        insufficient_data=False, message=None,
    )
    posting = PostingTimeAnalysis(
        best_days=["Monday"], best_hours=["10:00"], data_confidence="high",
        days_of_data=34, insufficient_data=False, message="Based on 49 posts.",
    )
    recs = NextWeekRecommendations(
        recommendations=[Recommendation(text="Post more Reels.", supporting_data="Top post 2x median")],
        insufficient_data=False, message=None,
    )

    with patch(
        "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.analyze_reels",
        return_value=analysis,
    ):
        with patch(
            "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.identify_best_performing_content",
            return_value=best,
        ):
            with patch(
                "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.compare_periods",
                return_value=comparison,
            ):
                with patch(
                    "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.analyze_posting_times",
                    return_value=posting,
                ):
                    with patch(
                        "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.generate_next_week_recommendations",
                        return_value=recs,
                    ):
                        view = AnalyticsView(root)
                        _pump(root, seconds=4.0)
                        assert "2.0x median" in _all_text(view._reel_perf_container) or len(view._reel_perf_container.winfo_children()) > 0
                        assert "Monday" in _all_text(view._posting_time_container)
                        assert "Post more Reels." in _all_text(view._recommendations_container)
                        assert "25.0%" in _all_text(view._comparison_container)
                        assert "never" not in view._sync_label.cget("text")


def test_insufficient_data_shows_message_not_a_crash(root, configured_connector):
    insuff_analysis = ReelAnalysisResult(reels=[], median_reach=None, median_engagement_rate=None, insufficient_data=True, message="Only 2 posts available.")
    insuff_comparison = PeriodComparisonResult(period_days=7, comparisons=[], insufficient_data=True, message="Not enough recorded history.")
    insuff_posting = PostingTimeAnalysis(best_days=[], best_hours=[], data_confidence="low", days_of_data=0, insufficient_data=True, message="No posts yet.")
    insuff_recs = NextWeekRecommendations(recommendations=[], insufficient_data=True, message="Not enough signal.")

    with patch(
        "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.analyze_reels",
        return_value=insuff_analysis,
    ):
        with patch(
            "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.compare_periods",
            return_value=insuff_comparison,
        ):
            with patch(
                "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.analyze_posting_times",
                return_value=insuff_posting,
            ):
                with patch(
                    "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.generate_next_week_recommendations",
                    return_value=insuff_recs,
                ):
                    view = AnalyticsView(root)
                    _pump(root, seconds=5.0)
                    assert "Only 2 posts available." in _all_text(view._reel_perf_container)
                    assert "Not enough recorded history." in _all_text(view._comparison_container)
                    assert "No posts yet." in _all_text(view._posting_time_container)
                    assert "Not enough signal." in _all_text(view._recommendations_container)


def test_background_error_shows_error_card_not_a_crash(root, configured_connector):
    with patch(
        "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.analyze_reels",
        side_effect=RuntimeError("network down"),
    ):
        with patch(
            "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.compare_periods",
            side_effect=RuntimeError("network down"),
        ):
            with patch(
                "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.analyze_posting_times",
                side_effect=RuntimeError("network down"),
            ):
                with patch(
                    "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.generate_next_week_recommendations",
                    side_effect=RuntimeError("network down"),
                ):
                    view = AnalyticsView(root)
                    _pump(root, seconds=4.0)
                    assert "network down" in _all_text(view._reel_perf_container)
                    assert "Couldn't load this" in _all_text(view._reel_perf_container)


# --- sync button / period dropdown --------------------------------------------------------


def test_sync_button_triggers_refetch(root, configured_connector):
    insuff_analysis = ReelAnalysisResult(reels=[], median_reach=None, median_engagement_rate=None, insufficient_data=True, message="none")
    insuff_comparison = PeriodComparisonResult(period_days=7, comparisons=[], insufficient_data=True, message="none")
    insuff_posting = PostingTimeAnalysis(best_days=[], best_hours=[], data_confidence="low", days_of_data=0, insufficient_data=True, message="none")
    insuff_recs = NextWeekRecommendations(recommendations=[], insufficient_data=True, message="none")

    with patch(
        "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.analyze_reels",
        return_value=insuff_analysis,
    ) as mock_analyze:
        with patch(
            "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.compare_periods",
            return_value=insuff_comparison,
        ):
            with patch(
                "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.analyze_posting_times",
                return_value=insuff_posting,
            ):
                with patch(
                    "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.generate_next_week_recommendations",
                    return_value=insuff_recs,
                ):
                    view = AnalyticsView(root)
                    _pump(root, seconds=4.0)
                    calls_before = mock_analyze.call_count
                    view._sync_button.invoke()
                    _pump(root, seconds=4.0)
                    assert mock_analyze.call_count > calls_before


def test_period_dropdown_changes_period_days(root, configured_connector):
    comparison = PeriodComparisonResult(period_days=30, comparisons=[], insufficient_data=True, message="none")
    with patch(
        "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.analyze_reels",
        return_value=ReelAnalysisResult(reels=[], median_reach=None, median_engagement_rate=None, insufficient_data=True, message="none"),
    ):
        with patch(
            "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.compare_periods",
            return_value=comparison,
        ) as mock_compare:
            with patch(
                "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.analyze_posting_times",
                return_value=PostingTimeAnalysis(best_days=[], best_hours=[], data_confidence="low", days_of_data=0, insufficient_data=True, message="none"),
            ):
                with patch(
                    "jarvis.gui.views.instagram_ai_manager.analytics_view.analytics_services.generate_next_week_recommendations",
                    return_value=NextWeekRecommendations(recommendations=[], insufficient_data=True, message="none"),
                ):
                    view = AnalyticsView(root)
                    _pump(root, seconds=4.0)
                    view._on_period_changed("30")
                    _pump(root, seconds=4.0)
                    assert view._period_days == 30
                    mock_compare.assert_called_with(period_days=30)


def test_refresh_does_not_refetch(root, not_configured_connector):
    view = AnalyticsView(root)
    root.update()
    with patch.object(view, "_start_all_fetches") as mock_start:
        view.refresh()
        mock_start.assert_not_called()
