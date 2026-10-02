"""Analytics tab (Instagram AI Manager, section 2 of the module's
brief): Reel Performance Analysis, Best Performing Content,
Week-over-Week Comparison, Best Posting Time, and Next Week AI
Recommendations - all built on jarvis.instagram_ai_manager
.analytics_services, which itself only reads REAL data from
InstagramConnector.get_recent_media_with_insights() and
jarvis.integrations.instagram_history. This view calls no LLM and
performs no write/publish/delete action against Instagram - purely
read-only rendering of already-computed, already-tested analysis
results (see that module's own docstring for its "never fabricate a
number or an 'always best' claim" guarantees, which this view passes
through unchanged rather than re-wording into a stronger claim).

Every InstagramConnector call here (get_recent_media_with_insights(),
transitively via analyze_reels()/analyze_posting_times()) is real
network I/O and must not block the Tkinter main thread - so, exactly
like Content Studio's generation calls, every fetch here runs through
jarvis.gui.worker.run_generation_in_background() and is collected via
this view's own polled result queue. A full "Sync Instagram Data" pass
re-runs every section's fetch; each section also fetches once at
construction so the tab isn't empty on first visit.
"""

from __future__ import annotations

import queue
from datetime import datetime, timezone
from typing import Any

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.instagram_ai_manager.analytics_common import (
    BarChartCard,
    error_card,
    insufficient_data_card,
)
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background
from jarvis.instagram_ai_manager import analytics_services
from jarvis.integrations.connectors.instagram import InstagramConnector

_QUEUE_POLL_INTERVAL_MS = 100
_PERIOD_CHOICES = ("7", "14", "30", "90")

# The five fetches a "Sync Instagram Data" pass refreshes - each a
# (section key, callable) pair, called with only `connector` (or nothing,
# for compare_periods which reads local history) plus whatever the UI
# state (e.g. selected period) supplies at call time. Kept as a plain
# tuple of methods on the view itself (see _start_all_fetches()) rather
# than a lookup table, since two of the five need the currently selected
# period and the others don't - a uniform signature isn't a good fit here.


class AnalyticsView(ctk.CTkFrame):
    def __init__(self, master) -> None:
        super().__init__(master, fg_color="transparent")
        self._connector = InstagramConnector()
        self._result_queue: "queue.Queue[Any]" = queue.Queue()
        self._last_sync: datetime | None = None
        self._period_days = 7

        header_row = ctk.CTkFrame(self, fg_color="transparent")
        header_row.pack(fill="x", padx=theme.SPACE_LG, pady=(theme.SPACE_LG, theme.SPACE_SM))
        SectionHeader(header_row, "Analytics").pack(side="left")
        self._sync_button = ctk.CTkButton(
            header_row, text="🔄 Sync Instagram Data", command=self._start_all_fetches, width=170,
        )
        self._sync_button.pack(side="right")
        self._sync_label = ctk.CTkLabel(
            header_row, text="Last data sync: never",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_MUTED,
        )
        self._sync_label.pack(side="right", padx=(0, theme.SPACE_MD))

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=(0, theme.SPACE_LG))

        self._reel_perf_container = self._build_section("Reel Performance Analysis")
        self._best_content_container = self._build_section("Best Performing Content")
        self._comparison_container = self._build_comparison_section()
        self._posting_time_container = self._build_section("Best Posting Time")
        self._recommendations_container = self._build_section("Next Week AI Recommendations")

        if not self._connector.is_configured():
            self._show_not_connected_everywhere()
        else:
            self._start_all_fetches()

        self._poll_queue()

    # --- layout helpers --------------------------------------------------------------

    def _build_section(self, title: str) -> ctk.CTkFrame:
        ctk.CTkLabel(
            self._scroll, text=title,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(theme.SPACE_MD, theme.SPACE_SM))
        container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        container.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._set_loading(container)
        return container

    def _build_comparison_section(self) -> ctk.CTkFrame:
        # Week-over-Week Comparison gets its own header row (title +
        # the 7/14/30/90-day period dropdown from the module's brief)
        # instead of reusing _build_section()'s plain title.
        header_row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        header_row.pack(fill="x", pady=(theme.SPACE_MD, theme.SPACE_SM))
        ctk.CTkLabel(
            header_row, text="Week-over-Week Comparison",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(side="left")
        period_var = ctk.StringVar(value="7")
        self._period_dropdown = ctk.CTkOptionMenu(
            header_row, values=list(_PERIOD_CHOICES), variable=period_var, width=90,
            fg_color=theme.BG_CARD, button_color=theme.ACCENT_PRIMARY,
            button_hover_color=theme.ACCENT_PRIMARY_HOVER,
            command=self._on_period_changed,
        )
        self._period_dropdown.pack(side="right")
        ctk.CTkLabel(
            header_row, text="Period:",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY,
        ).pack(side="right", padx=(0, theme.SPACE_SM))

        container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        container.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._set_loading(container)
        return container

    def _clear(self, container: ctk.CTkFrame) -> None:
        for widget in container.winfo_children():
            widget.destroy()

    def _set_loading(self, container: ctk.CTkFrame) -> None:
        self._clear(container)
        ctk.CTkLabel(
            container, text="Loading...",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w")

    def _show_not_connected_everywhere(self) -> None:
        message = (
            "Instagram isn't connected yet. Connect your Instagram account "
            "(Settings, or the existing Instagram integration) to see real "
            "performance analytics here."
        )
        for container in (
            self._reel_perf_container, self._best_content_container,
            self._comparison_container, self._posting_time_container,
            self._recommendations_container,
        ):
            self._clear(container)
            error_card(container, message).pack(fill="x")

    def _on_period_changed(self, value: str) -> None:
        self._period_days = int(value)
        self._set_loading(self._comparison_container)
        run_generation_in_background(
            lambda: analytics_services.compare_periods(period_days=self._period_days),
            self._result_queue, source=("comparison", self),
        )

    # --- fetching ----------------------------------------------------------------------

    def refresh(self) -> None:
        """Matches the refresh() contract jarvis.gui.app._navigate()
        calls on every panel it shows. Deliberately does NOT re-fetch on
        every visit (unlike some other views) - a live Instagram API
        call on every tab switch would be surprising/wasteful; a person
        explicitly re-syncs via the Sync button, matching the module's
        own "Last data sync: DATE/TIME" + [Sync Instagram Data] brief,
        which implies sync is a deliberate action, not an implicit
        side effect of navigation."""
        pass

    def _start_all_fetches(self) -> None:
        if not self._connector.is_configured():
            self._show_not_connected_everywhere()
            return

        self._sync_button.configure(state="disabled")
        for container in (
            self._reel_perf_container, self._best_content_container,
            self._posting_time_container, self._recommendations_container,
        ):
            self._set_loading(container)
        self._set_loading(self._comparison_container)

        connector = self._connector
        run_generation_in_background(
            lambda: analytics_services.analyze_reels(connector),
            self._result_queue, source=("reel_analysis", self),
        )
        run_generation_in_background(
            lambda: analytics_services.compare_periods(period_days=self._period_days),
            self._result_queue, source=("comparison", self),
        )
        run_generation_in_background(
            lambda: analytics_services.analyze_posting_times(connector),
            self._result_queue, source=("posting_time", self),
        )
        run_generation_in_background(
            lambda: analytics_services.generate_next_week_recommendations(connector),
            self._result_queue, source=("recommendations", self),
        )
        # Best Performing Content is derived from analyze_reels()'s
        # result rather than its own connector fetch - see
        # _handle_result()'s "reel_analysis" branch, which computes it
        # from the same ReelAnalysisResult instead of a second, redundant
        # get_recent_media_with_insights() call.

    # --- queue polling -------------------------------------------------------------------

    def _poll_queue(self) -> None:
        try:
            while True:
                result = self._result_queue.get_nowait()
                self._handle_result(result)
        except queue.Empty:
            pass
        self.after(_QUEUE_POLL_INTERVAL_MS, self._poll_queue)

    def _handle_result(self, result: Any) -> None:
        if not isinstance(result, GenerationTaskResult) or not isinstance(result.source, tuple):
            return
        section_key, owner = result.source
        if owner is not self:
            return

        self._maybe_finish_sync()

        if section_key == "reel_analysis":
            self._render_reel_analysis(result)
        elif section_key == "comparison":
            self._render_comparison(result)
        elif section_key == "posting_time":
            self._render_posting_time(result)
        elif section_key == "recommendations":
            self._render_recommendations(result)

    def _maybe_finish_sync(self) -> None:
        self._last_sync = datetime.now(timezone.utc)
        self._sync_label.configure(
            text=f"Last data sync: {self._last_sync.strftime('%Y-%m-%d %H:%M UTC')}"
        )
        self._sync_button.configure(state="normal")

    # --- rendering: Reel Performance Analysis + Best Performing Content ------------------

    def _render_reel_analysis(self, result: GenerationTaskResult) -> None:
        self._clear(self._reel_perf_container)
        self._clear(self._best_content_container)

        if result.error:
            error_card(self._reel_perf_container, result.error).pack(fill="x")
            error_card(self._best_content_container, result.error).pack(fill="x")
            return

        analysis = result.value
        if analysis.insufficient_data:
            insufficient_data_card(self._reel_perf_container, analysis.message).pack(fill="x")
            insufficient_data_card(self._best_content_container, analysis.message).pack(fill="x")
            return

        rows = [
            (
                (r.caption or "(no caption)")[:40] or r.media_id,
                r.performance_score or 0.0,
                f"{(r.performance_score or 0.0):.1f}x median"
                + (f" · {r.engagement_rate:.1%} eng." if r.engagement_rate is not None else ""),
            )
            for r in analysis.reels
        ]
        BarChartCard(
            self._reel_perf_container, f"Performance score (vs. median reach of {len(analysis.reels)} posts)",
            rows,
        ).pack(fill="x")

        best = analytics_services.identify_best_performing_content(analysis)
        if best.insufficient_data:
            insufficient_data_card(self._best_content_container, best.message).pack(fill="x")
            return

        if best.explanation:
            explanation_card = Card(self._best_content_container)
            explanation_card.pack(fill="x", pady=(0, theme.SPACE_SM))
            ctk.CTkLabel(
                explanation_card, text=best.explanation,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_SECONDARY, anchor="w", justify="left", wraplength=560,
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        top_rows = [
            (
                (r.caption or "(no caption)")[:40] or r.media_id,
                r.performance_score or 0.0,
                f"{(r.performance_score or 0.0):.1f}x median",
            )
            for r in best.top_reels
        ]
        BarChartCard(self._best_content_container, "Top Reels", top_rows).pack(fill="x", pady=(0, theme.SPACE_SM))

        format_rows = [(fmt, score, f"{score:.2f}x median avg") for fmt, score in best.top_topics]
        BarChartCard(
            self._best_content_container, "Top Formats (by avg. performance score)", format_rows,
        ).pack(fill="x")

    # --- rendering: Week-over-Week Comparison -----------------------------------------------

    def _render_comparison(self, result: GenerationTaskResult) -> None:
        self._clear(self._comparison_container)
        if result.error:
            error_card(self._comparison_container, result.error).pack(fill="x")
            return

        comparison = result.value
        if comparison.insufficient_data:
            insufficient_data_card(self._comparison_container, comparison.message).pack(fill="x")
            return

        card = Card(self._comparison_container)
        card.pack(fill="x")
        ctk.CTkLabel(
            card, text=f"Last {comparison.period_days} days vs. the {comparison.period_days} days before",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_SM))

        for metric_comparison in comparison.comparisons:
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=theme.SPACE_MD, pady=(0, theme.SPACE_SM))
            ctk.CTkLabel(
                row, text=metric_comparison.metric.replace("_", " ").title(),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_SECONDARY, anchor="w", width=150,
            ).pack(side="left")

            if metric_comparison.insufficient_data or metric_comparison.percent_change is None:
                ctk.CTkLabel(
                    row, text="Not enough data", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                    text_color=theme.TEXT_MUTED, anchor="w",
                ).pack(side="left")
                continue

            change = metric_comparison.percent_change
            color = theme.SUCCESS if change >= 0 else theme.DANGER
            arrow = "▲" if change >= 0 else "▼"
            ctk.CTkLabel(
                row, text=f"{arrow} {abs(change):.1f}%",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
                text_color=color, anchor="w", width=80,
            ).pack(side="left")
            ctk.CTkLabel(
                row,
                text=f"{metric_comparison.current_value:.0f} (was {metric_comparison.previous_value:.0f})",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(side="left")
        ctk.CTkFrame(card, fg_color="transparent", height=theme.SPACE_XS).pack()

    # --- rendering: Best Posting Time --------------------------------------------------------

    def _render_posting_time(self, result: GenerationTaskResult) -> None:
        self._clear(self._posting_time_container)
        if result.error:
            error_card(self._posting_time_container, result.error).pack(fill="x")
            return

        analysis = result.value
        if analysis.insufficient_data:
            insufficient_data_card(self._posting_time_container, analysis.message).pack(fill="x")
            return

        confidence_colors = {"low": theme.STATUS_WAITING, "medium": theme.ACCENT_PRIMARY, "high": theme.SUCCESS}
        confidence_card = Card(self._posting_time_container)
        confidence_card.pack(fill="x", pady=(0, theme.SPACE_SM))
        confidence_row = ctk.CTkFrame(confidence_card, fg_color="transparent")
        confidence_row.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            confidence_row, text="DATA CONFIDENCE:",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_SECONDARY,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            confidence_row, text=analysis.data_confidence.upper(),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=confidence_colors.get(analysis.data_confidence, theme.TEXT_MUTED),
        ).pack(side="left")
        if analysis.message:
            ctk.CTkLabel(
                confidence_card, text=analysis.message,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=560,
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))

        row = ctk.CTkFrame(self._posting_time_container, fg_color="transparent")
        row.pack(fill="x")
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)

        days_card = Card(row)
        days_card.grid(row=0, column=0, sticky="nsew", padx=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            days_card, text="BEST DAYS",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_XS))
        for day in analysis.best_days:
            ctk.CTkLabel(
                days_card, text=day, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY),
                text_color=theme.ACCENT_PRIMARY, anchor="w",
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_XS))
        ctk.CTkFrame(days_card, fg_color="transparent", height=theme.SPACE_SM).pack()

        hours_card = Card(row)
        hours_card.grid(row=0, column=1, sticky="nsew", padx=(theme.SPACE_SM, 0))
        ctk.CTkLabel(
            hours_card, text="BEST HOURS",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_XS))
        for hour in analysis.best_hours:
            ctk.CTkLabel(
                hours_card, text=hour, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY),
                text_color=theme.ACCENT_PRIMARY, anchor="w",
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_XS))
        ctk.CTkFrame(hours_card, fg_color="transparent", height=theme.SPACE_SM).pack()

    # --- rendering: Next Week AI Recommendations ------------------------------------------

    def _render_recommendations(self, result: GenerationTaskResult) -> None:
        self._clear(self._recommendations_container)
        if result.error:
            error_card(self._recommendations_container, result.error).pack(fill="x")
            return

        recommendations = result.value
        if recommendations.insufficient_data:
            insufficient_data_card(self._recommendations_container, recommendations.message).pack(fill="x")
            return

        for rec in recommendations.recommendations:
            card = Card(self._recommendations_container)
            card.pack(fill="x", pady=(0, theme.SPACE_SM))
            ctk.CTkLabel(
                card, text=rec.text,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY),
                text_color=theme.TEXT_PRIMARY, anchor="w", justify="left", wraplength=560,
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_XS))
            ctk.CTkLabel(
                card, text=f"Based on: {rec.supporting_data}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=560,
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))
