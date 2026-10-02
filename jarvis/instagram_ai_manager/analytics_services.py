"""Read-only Instagram performance analysis, built entirely on REAL data
already available from jarvis.integrations.connectors.instagram
.InstagramConnector.get_recent_media_with_insights() (recent posts'
real like/comment/share/save/reach numbers) and jarvis.integrations
.instagram_history (locally recorded daily account-level snapshots -
reach, profile_views - kept because Meta's API doesn't retain insights
history indefinitely; see that module's own docstring). Nothing here
calls the LLM and nothing here fabricates a number, a trend, or a claim
the underlying data doesn't support - every function that can't compute
something from the data it has returns an explicit "insufficient data"
result rather than guessing, per the module's own brief ("Do NOT simply
label content 'viral' without sufficient data", "Do not claim that a
specific time is universally 'best'").

A "performance score" here is always defined as a plain, transparent
ratio against the account's own recent median (see
_calculate_performance_score()'s docstring) - never an opaque or
Instagram-algorithm-derived number, since this module has no visibility
into Instagram's actual ranking/distribution algorithm and must not
imply it does.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from jarvis.integrations import instagram_history
from jarvis.integrations.connectors.instagram import InstagramConnector

# Below this many analyzed posts, comparative claims ("this performed
# 2x the median") are not made - a median of 2-3 posts is not a
# meaningful baseline. This is the module's own transparency threshold,
# not an Instagram/Meta limitation.
_MIN_POSTS_FOR_COMPARISON = 5

# Below this many recorded daily snapshots (see instagram_history), a
# posting-time/day-of-week recommendation is not made - see
# analyze_posting_times()'s own docstring for why this number.
_MIN_DAYS_FOR_POSTING_TIME_CONFIDENCE = 14


# --- shared helpers --------------------------------------------------------------------


_ENGAGEMENT_METRICS = ("likes", "comments", "shares", "saved")


def _engagement_rate(post: dict[str, Any]) -> float | None:
    """(likes + comments + shares + saved) / reach, as a fraction - the
    only engagement-rate definition used anywhere in this module.
    Returns None if `reach` is missing or zero (rate is undefined, not
    zero, in that case) - a caller must handle None, never treat it as
    0.0."""
    reach = post.get("reach")
    if not reach:
        return None
    numerator = sum(post.get(m, 0.0) for m in _ENGAGEMENT_METRICS)
    return numerator / reach


def _median_or_none(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


# --- 4. Reel performance analysis --------------------------------------------------------


@dataclass(frozen=True)
class ReelPerformance:
    """One analyzed post's metrics plus a transparent, data-grounded
    performance_score - see _calculate_performance_score()'s
    docstring. `insufficient_data` is True when performance_score is
    None because the account doesn't yet have enough OTHER posts to
    compare this one against - the raw metrics are still shown either
    way, only the comparative score is withheld."""

    media_id: str
    caption: str
    media_type: str
    permalink: str
    timestamp: str
    views: float | None  # reach used as a views proxy - Instagram's Insights API does not expose a separate "views" metric for every media type (see module docstring)
    reach: float | None
    likes: float
    comments: float
    shares: float
    saves: float
    engagement_rate: float | None
    performance_score: float | None
    insufficient_data: bool


@dataclass(frozen=True)
class ReelAnalysisResult:
    reels: list[ReelPerformance]
    median_reach: float | None
    median_engagement_rate: float | None
    insufficient_data: bool
    message: str | None  # set only when insufficient_data is True, explaining why


def _calculate_performance_score(reach: float | None, median_reach: float | None) -> float | None:
    """performance_score = this post's reach / the account's own median
    reach across the analyzed set - e.g. 2.0 means "2x the account's
    typical reach for this batch", 0.5 means "half the typical reach".
    This is the ONLY performance metric this module computes, and it is
    always relative to the account's OWN recent history, never to any
    external/global/Instagram-wide baseline this module has no access
    to. Returns None if either input is missing/zero."""
    if reach is None or not median_reach:
        return None
    return reach / median_reach


def analyze_reels(connector: InstagramConnector, *, limit: int = 20) -> ReelAnalysisResult:
    """Analyzes the account's `limit` most recent posts using ONLY real
    data from InstagramConnector.get_recent_media_with_insights() - no
    LLM call, no estimation. If fewer than _MIN_POSTS_FOR_COMPARISON
    posts are available, still returns their raw metrics but with
    insufficient_data=True and every performance_score as None (there's
    no meaningful median to compare against yet)."""
    posts = connector.get_recent_media_with_insights(limit=limit)

    reach_values = [p["reach"] for p in posts if isinstance(p.get("reach"), (int, float))]
    median_reach = _median_or_none(reach_values)
    engagement_values = [
        r for p in posts if (r := _engagement_rate(p)) is not None
    ]
    median_engagement = _median_or_none(engagement_values)

    insufficient = len(posts) < _MIN_POSTS_FOR_COMPARISON
    message = (
        f"Only {len(posts)} post(s) available for analysis - at least "
        f"{_MIN_POSTS_FOR_COMPARISON} are needed for a meaningful "
        "performance comparison. Showing raw metrics only."
        if insufficient else None
    )

    results = []
    for post in posts:
        reach = post.get("reach")
        engagement_rate = _engagement_rate(post)
        score = None if insufficient else _calculate_performance_score(reach, median_reach)
        results.append(
            ReelPerformance(
                media_id=post["id"],
                caption=post.get("caption", ""),
                media_type=post.get("media_type", "?"),
                permalink=post.get("permalink", ""),
                timestamp=post.get("timestamp", "?"),
                views=reach,
                reach=reach,
                likes=post.get("likes", 0.0),
                comments=post.get("comments", 0.0),
                shares=post.get("shares", 0.0),
                saves=post.get("saved", 0.0),
                engagement_rate=engagement_rate,
                performance_score=score,
                insufficient_data=insufficient,
            )
        )

    return ReelAnalysisResult(
        reels=results, median_reach=median_reach, median_engagement_rate=median_engagement,
        insufficient_data=insufficient, message=message,
    )


# --- 5. Best performing content -----------------------------------------------------------


@dataclass(frozen=True)
class BestPerformingContent:
    top_reels: list[ReelPerformance]
    top_topics: list[tuple[str, float]]  # (caption excerpt, avg performance_score)
    explanation: str | None
    insufficient_data: bool
    message: str | None


def identify_best_performing_content(
    analysis: ReelAnalysisResult, *, top_n: int = 3
) -> BestPerformingContent:
    """Identifies the top `top_n` posts from an already-computed
    ReelAnalysisResult (call analyze_reels() first) by performance_score
    - never re-fetches data itself, so this function's output is always
    consistent with whatever analysis it was given. If the analysis was
    insufficient_data, this is too (there is no valid performance_score
    to rank by). `top_topics`/`top_formats`/etc. from the module's brief
    are approximated here by grouping on the post's media_type and a
    short caption excerpt - this module has no separate "topic" or
    "hook type" classification of past posts (that information isn't
    tracked anywhere in JARVIS), so it does not claim to identify a
    post's hook type or topic beyond what's visible in its own
    caption/media_type."""
    if analysis.insufficient_data:
        return BestPerformingContent(
            top_reels=[], top_topics=[], explanation=None, insufficient_data=True,
            message=analysis.message,
        )

    scored = [r for r in analysis.reels if r.performance_score is not None]
    scored.sort(key=lambda r: r.performance_score or 0.0, reverse=True)
    top_reels = scored[:top_n]

    explanation = None
    if top_reels and analysis.median_reach:
        best = top_reels[0]
        multiple = (best.performance_score or 0.0)
        explanation = (
            f"Your top post reached {multiple:.1f}x the median reach of the "
            f"last {len(analysis.reels)} analyzed posts "
            f"({best.reach:.0f} vs median {analysis.median_reach:.0f})."
            if best.reach is not None else None
        )

    # media_type as a rough stand-in for "format" grouping (Reel/
    # Carousel/Image) - the only content-type signal this module
    # actually has for past posts.
    by_type: dict[str, list[float]] = {}
    for r in scored:
        if r.performance_score is not None:
            by_type.setdefault(r.media_type, []).append(r.performance_score)
    top_topics = sorted(
        ((media_type, sum(scores) / len(scores)) for media_type, scores in by_type.items()),
        key=lambda pair: pair[1], reverse=True,
    )

    return BestPerformingContent(
        top_reels=top_reels, top_topics=top_topics, explanation=explanation,
        insufficient_data=False, message=None,
    )


# --- 6. Week-over-week comparison ----------------------------------------------------------


@dataclass(frozen=True)
class PeriodComparison:
    metric: str
    current_value: float | None
    previous_value: float | None
    percent_change: float | None
    insufficient_data: bool


@dataclass(frozen=True)
class PeriodComparisonResult:
    period_days: int
    comparisons: list[PeriodComparison]
    insufficient_data: bool
    message: str | None


def compare_periods(*, period_days: int = 7) -> PeriodComparisonResult:
    """Compares the sum of each metric over the last `period_days`
    RECORDED days (jarvis.integrations.instagram_history - populated by
    the existing record_instagram_daily_snapshot action, never by this
    function) against the `period_days` before that. Never makes a live
    Instagram API call - purely reads what has already been recorded
    locally, exactly like InstagramConnector's own compare_history
    action does (see that action's docstring for why: comparing
    RECORDED snapshots, since Meta's live API doesn't retain history).
    A metric or day missing from the recorded history is excluded from
    that metric's comparison rather than estimated - see
    PeriodComparison.insufficient_data per metric."""
    period_days = max(1, min(period_days, 90))
    history = instagram_history.load_history().entries
    today = datetime.now(timezone.utc).date()

    current_dates = [(today - timedelta(days=i)).isoformat() for i in range(period_days)]
    previous_dates = [
        (today - timedelta(days=i)).isoformat() for i in range(period_days, period_days * 2)
    ]

    current_snapshots = [history[d] for d in current_dates if d in history]
    previous_snapshots = [history[d] for d in previous_dates if d in history]

    all_metrics = sorted(
        {m for s in current_snapshots + previous_snapshots for m in s} | set(instagram_history.KNOWN_METRICS)
    )

    comparisons = []
    for metric in all_metrics:
        current_values = [s[metric] for s in current_snapshots if metric in s]
        previous_values = [s[metric] for s in previous_snapshots if metric in s]
        if not current_values or not previous_values:
            comparisons.append(
                PeriodComparison(
                    metric=metric, current_value=sum(current_values) if current_values else None,
                    previous_value=sum(previous_values) if previous_values else None,
                    percent_change=None, insufficient_data=True,
                )
            )
            continue
        current_sum = sum(current_values)
        previous_sum = sum(previous_values)
        percent_change = (
            None if previous_sum == 0 else ((current_sum - previous_sum) / previous_sum) * 100
        )
        comparisons.append(
            PeriodComparison(
                metric=metric, current_value=current_sum, previous_value=previous_sum,
                percent_change=percent_change, insufficient_data=False,
            )
        )

    overall_insufficient = not current_snapshots or not previous_snapshots
    message = (
        f"Not enough recorded history for a {period_days}-day comparison "
        f"(found {len(current_snapshots)} of {period_days} days in the current "
        f"period, {len(previous_snapshots)} of {period_days} in the previous "
        "period). Run 'record_instagram_daily_snapshot' daily to build up "
        "history for this comparison."
        if overall_insufficient else None
    )

    return PeriodComparisonResult(
        period_days=period_days, comparisons=comparisons,
        insufficient_data=overall_insufficient, message=message,
    )


# --- 7. Best posting time -----------------------------------------------------------------


@dataclass(frozen=True)
class PostingTimeAnalysis:
    best_days: list[str]
    best_hours: list[str]
    data_confidence: str  # "low" | "medium" | "high" - see analyze_posting_times()'s docstring
    days_of_data: int
    insufficient_data: bool
    message: str | None


def analyze_posting_times(connector: InstagramConnector, *, limit: int = 50) -> PostingTimeAnalysis:
    """Analyzes the account's own real post timestamps (from
    get_recent_media_with_insights()) grouped by day-of-week and hour,
    ranked by each post's reach - NEVER claims a universally "best"
    time (per the module's own brief); every result is scoped to "your
    account's available historical data" and always reports
    data_confidence so a caller can show that qualifier prominently.

    data_confidence is intentionally coarse and conservative:
      - "low": fewer than _MIN_DAYS_FOR_POSTING_TIME_CONFIDENCE distinct
        posting days of data - a recommendation is still computed (so
        the UI has something to show) but must be labeled low-confidence.
      - "medium": at least that many days, but fewer than 3 posts in the
        single best day/hour bucket.
      - "high": at least 3 posts in the best bucket AND enough distinct
        days. Still not a guarantee, only the most this module's limited
        data can support.
    """
    posts = connector.get_recent_media_with_insights(limit=limit)
    dated_posts = []
    for post in posts:
        reach = post.get("reach")
        if reach is None:
            continue
        try:
            ts = datetime.fromisoformat(post["timestamp"].replace("+0000", "+00:00"))
        except (ValueError, KeyError, AttributeError):
            continue
        dated_posts.append((ts, reach))

    distinct_days = {ts.date() for ts, _ in dated_posts}

    if not dated_posts:
        return PostingTimeAnalysis(
            best_days=[], best_hours=[], data_confidence="low", days_of_data=0,
            insufficient_data=True,
            message="No posts with both a timestamp and reach data are available yet.",
        )

    weekday_names = (
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    )
    by_weekday: dict[str, list[float]] = {}
    by_hour: dict[str, list[float]] = {}
    for ts, reach in dated_posts:
        by_weekday.setdefault(weekday_names[ts.weekday()], []).append(reach)
        hour_label = f"{ts.hour:02d}:00"
        by_hour.setdefault(hour_label, []).append(reach)

    ranked_days = sorted(by_weekday.items(), key=lambda kv: statistics.mean(kv[1]), reverse=True)
    ranked_hours = sorted(by_hour.items(), key=lambda kv: statistics.mean(kv[1]), reverse=True)

    best_days = [day for day, _ in ranked_days[:2]]
    best_hours = [hour for hour, _ in ranked_hours[:2]]

    best_bucket_size = len(ranked_days[0][1]) if ranked_days else 0
    if len(distinct_days) < _MIN_DAYS_FOR_POSTING_TIME_CONFIDENCE:
        confidence = "low"
    elif best_bucket_size < 3:
        confidence = "medium"
    else:
        confidence = "high"

    return PostingTimeAnalysis(
        best_days=best_days, best_hours=best_hours, data_confidence=confidence,
        days_of_data=len(distinct_days), insufficient_data=False,
        message=(
            f"Based on {len(dated_posts)} posts across {len(distinct_days)} distinct "
            f"days - confidence is '{confidence}'. More posting history will improve "
            "this recommendation."
        ),
    )


# --- 8. Next-week AI recommendations --------------------------------------------------------


@dataclass(frozen=True)
class Recommendation:
    text: str
    supporting_data: str


@dataclass(frozen=True)
class NextWeekRecommendations:
    recommendations: list[Recommendation]
    insufficient_data: bool
    message: str | None


def generate_next_week_recommendations(
    connector: InstagramConnector, *, limit: int = 20
) -> NextWeekRecommendations:
    """Builds data-grounded recommendations from analyze_reels() +
    identify_best_performing_content() - NOT an LLM call: every
    recommendation text here is templated directly from real computed
    numbers (median comparisons, engagement rates), so it can never
    state a number the data doesn't actually show. This deliberately
    trades away more natural-sounding phrasing for the module's own
    "every recommendation must show the data behind it" requirement -
    an LLM-phrased version of these same, already-validated numbers
    could be added later as a presentation layer without changing this
    function's job (compute what's true), if that becomes wanted."""
    analysis = analyze_reels(connector, limit=limit)
    if analysis.insufficient_data:
        return NextWeekRecommendations(
            recommendations=[], insufficient_data=True, message=analysis.message,
        )

    best = identify_best_performing_content(analysis)
    recommendations: list[Recommendation] = []

    if best.top_reels:
        top = best.top_reels[0]
        caption_excerpt = (top.caption or "(no caption)")[:60]
        recommendations.append(
            Recommendation(
                text=(
                    f"Create more content similar to your top-performing post "
                    f"(\"{caption_excerpt}\") - it reached "
                    f"{(top.performance_score or 0):.1f}x your median reach."
                ),
                supporting_data=(
                    f"Top post reach: {top.reach:.0f}, median reach across "
                    f"{len(analysis.reels)} posts: {analysis.median_reach:.0f}"
                    if top.reach is not None and analysis.median_reach else "N/A"
                ),
            )
        )

    if best.top_topics:
        best_format, avg_score = best.top_topics[0]
        recommendations.append(
            Recommendation(
                text=f"Your {best_format} content format is outperforming your other formats - consider prioritizing it next week.",
                supporting_data=f"Average performance score for {best_format}: {avg_score:.2f}x median",
            )
        )

    if analysis.median_engagement_rate is not None:
        low_engagement = [
            r for r in analysis.reels
            if r.engagement_rate is not None and r.engagement_rate < analysis.median_engagement_rate * 0.5
        ]
        if low_engagement:
            recommendations.append(
                Recommendation(
                    text=(
                        f"{len(low_engagement)} of your last {len(analysis.reels)} posts had "
                        "engagement well below your median - consider testing a stronger hook "
                        "or CTA on your next post in that style."
                    ),
                    supporting_data=f"Median engagement rate: {analysis.median_engagement_rate:.1%}",
                )
            )

    if not recommendations:
        return NextWeekRecommendations(
            recommendations=[], insufficient_data=True,
            message="Not enough distinguishing signal in the analyzed posts to generate a specific recommendation yet.",
        )

    return NextWeekRecommendations(recommendations=recommendations, insufficient_data=False, message=None)
