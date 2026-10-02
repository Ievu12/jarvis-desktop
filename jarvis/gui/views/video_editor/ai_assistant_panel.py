"""AI creative assistant panel: a natural-language brief, a "✨ Propose
Style" button, and a PREVIEW of what came back (never applied
automatically) with explicit "✅ Apply" / "✖ Discard" actions - the
"peržiūros ir patvirtinimo žingsnis prieš bet kokių pakeitimų
pritaikymą" (a preview and confirmation step before applying any
changes) the user explicitly required for Stage 5 of the "professional
Reels editor" plan.

This panel never calls jarvis.video_editor.ai_assistant.propose_reel_style()
itself (a real, possibly-slow LLM call) - it only collects the brief
and hands it to the owning dashboard via `on_propose_requested`, which
runs it through jarvis.gui.worker.run_generation_in_background() like
every other possibly-slow action in this package, then calls
show_proposal()/show_error() once a real result is ready. Applying the
shown proposal (if the person clicks "✅ Apply") fires
`on_apply_requested(proposal)` - the dashboard routes it through the
EXACT SAME CaptionsPanel.apply_style()/StickersPanel.apply_presets()/
TimelinePanel.apply_timeline() paths Stage 4's "Apply Reel Template"
action already uses (see that action's own docstring), never a second
application mechanism."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import status_label
from jarvis.gui.widgets import Card
from jarvis.video_editor.ai_assistant import AiReelProposal


class AiAssistantPanel(ctk.CTkFrame):
    def __init__(
        self, master, *,
        on_propose_requested: Callable[[str], None],
        on_apply_requested: Callable[[AiReelProposal], None],
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_propose_requested = on_propose_requested
        self._on_apply_requested = on_apply_requested
        self._current_proposal: AiReelProposal | None = None

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="🤖 AI CREATIVE ASSISTANT",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            inner,
            text="Describe the video in a sentence (e.g. \"calm yoga reel with a soft affirmation\") "
                 "and get a styling suggestion to review before applying anything.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=600, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._brief_entry = ctk.CTkEntry(inner, width=400, placeholder_text="Describe your video...")
        self._brief_entry.pack(anchor="w", pady=(0, theme.SPACE_SM))
        self._brief_entry.bind("<Return>", lambda _e: self._on_propose_clicked())

        self._propose_button = ctk.CTkButton(inner, text="✨ Propose Style", width=160, command=self._on_propose_clicked)
        self._propose_button.pack(anchor="w")

        self._status = status_label(inner, "", kind="muted")
        self._preview_container = ctk.CTkFrame(inner, fg_color="transparent")

    def _on_propose_clicked(self) -> None:
        brief = self._brief_entry.get().strip()
        if not brief:
            self._status.configure(text="⚠️ Describe your video first.", text_color=theme.DANGER)
            self._status.pack(anchor="w", pady=(theme.SPACE_SM, 0))
            return
        self._current_proposal = None
        for child in self._preview_container.winfo_children():
            child.destroy()
        self._preview_container.pack_forget()
        self._propose_button.configure(state="disabled")
        self._status.configure(text="Thinking of a style for your video...", text_color=theme.ACCENT_PRIMARY)
        self._status.pack(anchor="w", pady=(theme.SPACE_SM, 0))
        self._on_propose_requested(brief)

    def show_proposal(self, proposal: AiReelProposal) -> None:
        """Renders a REAL preview of `proposal` - every field actually
        in it, not a generic "AI suggested something" placeholder, so
        the person can judge whether to apply it before anything is
        touched. Called by the owning dashboard once a real proposal
        comes back from the background LLM call."""
        self._propose_button.configure(state="normal")
        self._current_proposal = proposal
        self._status.pack_forget()

        for child in self._preview_container.winfo_children():
            child.destroy()

        lines = [
            f"Motion: {proposal.effect.motion} (intensity {proposal.effect.motion_intensity:.2f})",
            f"Fade: {proposal.effect.fade} ({proposal.effect.fade_seconds:.1f}s)",
            f"Color: brightness {proposal.effect.brightness:+.2f}, contrast {proposal.effect.contrast:.2f}, "
            f"saturation {proposal.effect.saturation:.2f}",
            f"Captions: {proposal.caption_style.position}, {proposal.caption_style.animation} style, "
            f"color {proposal.caption_style.color}",
        ]
        if proposal.text_template_name:
            lines.append(f"Text template: {proposal.text_template_name}")
        if proposal.suggested_caption_text:
            lines.append(f'Suggested line: "{proposal.suggested_caption_text}"')
        if proposal.music_mood_suggestion:
            lines.append(f"Music mood suggestion: {proposal.music_mood_suggestion} (no file chosen automatically)")
        if proposal.stickers:
            shapes = ", ".join(s.shape for s in proposal.stickers)
            lines.append(f"Stickers: {shapes}")

        ctk.CTkLabel(
            self._preview_container, text="Proposed style (nothing applied yet):",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        for line in lines:
            ctk.CTkLabel(
                self._preview_container, text=f"• {line}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_SECONDARY, anchor="w", wraplength=580, justify="left",
            ).pack(anchor="w")

        buttons_row = ctk.CTkFrame(self._preview_container, fg_color="transparent")
        buttons_row.pack(anchor="w", pady=(theme.SPACE_SM, 0))
        ctk.CTkButton(buttons_row, text="✅ Apply", width=100, command=self._on_apply_clicked).pack(
            side="left", padx=(0, theme.SPACE_XS),
        )
        ctk.CTkButton(
            buttons_row, text="✖ Discard", width=100, command=self._on_discard_clicked,
            fg_color=theme.BG_CARD, hover_color=theme.DANGER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

        self._preview_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

    def show_error(self, message: str) -> None:
        self._propose_button.configure(state="normal")
        self._status.configure(text=f"⚠️ {message}", text_color=theme.DANGER)
        self._status.pack(anchor="w", pady=(theme.SPACE_SM, 0))

    def _on_apply_clicked(self) -> None:
        if self._current_proposal is not None:
            self._on_apply_requested(self._current_proposal)

    def _on_discard_clicked(self) -> None:
        self._current_proposal = None
        for child in self._preview_container.winfo_children():
            child.destroy()
        self._preview_container.pack_forget()
