"""Left navigation sidebar for the JARVIS dashboard shell
(jarvis.gui.app.JarvisApp). Pure UI - it holds no JARVIS state itself
and calls no JARVIS core/backend logic; it only renders nav buttons and
a status indicator, and calls back into JarvisApp (via `on_navigate`)
when a nav item is clicked. JarvisApp owns which view is currently
shown and all of the underlying data.
"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.animate import pulse

NAV_ITEMS = (
    ("home", "🏠", "Home"),
    ("chat", "💬", "Chat"),
    ("tasks", "✅", "Tasks"),
    ("instagram", "📷", "Instagram"),
    ("gmail", "✉️", "Gmail"),
    ("stripe", "💳", "Stripe"),
    ("content", "✨", "Content"),
    ("analytics", "📊", "Analytics"),
    ("automations", "⚙️", "Automations"),
    ("settings", "🛠️", "Settings"),
)

# Status -> (label text, dot color) - matches the brief's five states
# exactly. THINKING/WORKING/WAITING/ERROR reuse the same palette
# jarvis.gui.theme defines for consistency with any other status
# indicator in the app (e.g. a future per-view status chip).
STATUS_STYLES: dict[str, tuple[str, str]] = {
    "ONLINE": ("ONLINE", theme.STATUS_ONLINE),
    "THINKING": ("THINKING", theme.STATUS_THINKING),
    "WORKING": ("WORKING", theme.STATUS_WORKING),
    "WAITING": ("WAITING", theme.STATUS_WAITING),
    "ERROR": ("ERROR", theme.STATUS_ERROR),
}


class Sidebar(ctk.CTkFrame):
    def __init__(self, master, *, on_navigate: Callable[[str], None]) -> None:
        super().__init__(
            master, width=theme.SIDEBAR_WIDTH, fg_color=theme.BG_SURFACE, corner_radius=0,
        )
        self.pack_propagate(False)
        self._on_navigate = on_navigate
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._active_key = "home"
        self._stop_pulse: Callable[[], None] | None = None

        self._build_logo_and_status()
        self._build_nav()

    # --- logo + status -----------------------------------------------------------------

    def _build_logo_and_status(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=theme.SPACE_MD, pady=(theme.SPACE_LG, theme.SPACE_MD))

        ctk.CTkLabel(
            header, text="JARVIS",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_TITLE, weight="bold"),
            text_color=theme.TEXT_PRIMARY,
        ).pack(anchor="w")

        status_row = ctk.CTkFrame(header, fg_color="transparent")
        status_row.pack(anchor="w", pady=(theme.SPACE_SM, 0))

        self._status_dot = ctk.CTkLabel(
            status_row, text="●", font=ctk.CTkFont(size=12), text_color=theme.STATUS_ONLINE, width=16,
        )
        self._status_dot.pack(side="left")

        self._status_label = ctk.CTkLabel(
            status_row, text="ONLINE",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY,
        )
        self._status_label.pack(side="left", padx=(theme.SPACE_XS, 0))

        self.set_status("ONLINE")

    def set_status(self, status: str) -> None:
        """Updates the sidebar's status indicator. `status` must be one
        of ONLINE/THINKING/WORKING/WAITING/ERROR - an unrecognized value
        falls back to a plain gray dot with the raw text shown, rather
        than raising, since a status string ultimately comes from
        JarvisApp's own state transitions and a typo here should never
        crash the window."""
        label, color = STATUS_STYLES.get(status, (status, theme.TEXT_MUTED))
        self._status_label.configure(text=label)

        if self._stop_pulse is not None:
            self._stop_pulse()
            self._stop_pulse = None

        if status == "ONLINE":
            # A slow, subtle pulse only for the steady-state ONLINE
            # status - the brief asks for "JARVIS status pulse"
            # specifically, and a pulsing dot while THINKING/WORKING
            # (which already have their own visual weight elsewhere,
            # e.g. a chat "thinking" indicator) would be redundant
            # motion, against the "do not over-animate" instruction.
            self._stop_pulse = pulse(
                self._status_dot, base_color=theme.STATUS_ONLINE, pulse_color=theme.TEXT_MUTED,
                apply=lambda color: self._status_dot.configure(text_color=color),
            )
        else:
            self._status_dot.configure(text_color=color)

    # --- navigation ----------------------------------------------------------------------

    def _build_nav(self) -> None:
        nav_container = ctk.CTkFrame(self, fg_color="transparent")
        nav_container.pack(fill="both", expand=True, padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        for key, icon, label in NAV_ITEMS:
            button = ctk.CTkButton(
                nav_container,
                text=f"  {icon}   {label}",
                anchor="w",
                fg_color="transparent",
                hover_color=theme.BG_CARD_HOVER,
                text_color=theme.TEXT_SECONDARY,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY),
                corner_radius=theme.RADIUS_BUTTON,
                height=38,
                command=lambda k=key: self._handle_click(k),
            )
            button.pack(fill="x", pady=2)
            self._nav_buttons[key] = button

        self._highlight_active()

    def _handle_click(self, key: str) -> None:
        self._active_key = key
        self._highlight_active()
        self._on_navigate(key)

    def _highlight_active(self) -> None:
        for key, button in self._nav_buttons.items():
            if key == self._active_key:
                button.configure(fg_color=theme.BG_CARD, text_color=theme.TEXT_PRIMARY)
            else:
                button.configure(fg_color="transparent", text_color=theme.TEXT_SECONDARY)

    def set_active(self, key: str) -> None:
        """Lets JarvisApp programmatically change the highlighted nav
        item (e.g. a Quick Action button on Home navigating to Chat)
        without going through a simulated click."""
        if key in self._nav_buttons:
            self._active_key = key
            self._highlight_active()
