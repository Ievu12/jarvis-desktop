"""Central visual language for the JARVIS desktop dashboard: colors,
fonts, spacing. Every view module (jarvis/gui/views/*.py) and the
sidebar/shell import from here rather than hardcoding a color/font -
this is the one place the "premium futuristic" look is tuned.

Honest about the medium: this is customtkinter (Tkinter), which has no
native blur/backdrop-filter or CSS-grade easing. "Glassmorphism" here
means layered near-opaque dark panels with a soft border and a
consistent accent-color glow on focus/active states, not a literal
frosted-glass blur - Tkinter cannot render that. Animations are
.after()-driven color/geometry interpolation (see animate.py), kept
subtle per the brief's "do not over-animate" instruction.
"""

from __future__ import annotations

# --- palette ---------------------------------------------------------------------------
# A deep graphite/near-black base (not pure #000, which reads as harsh
# on most displays) with a restrained blue-violet accent family - one
# accent hue, varied by tint/shade, rather than "many colors" per the
# brief's explicit "avoid too many colors" instruction.

BG_PRIMARY = "#0B0D12"      # main window background
BG_SURFACE = "#12151C"      # sidebar / header background
BG_CARD = "#161A23"         # panel/card background
BG_CARD_HOVER = "#1B2030"   # card hover state
BORDER_SUBTLE = "#242A38"   # card/panel borders

ACCENT_PRIMARY = "#6C8CFF"   # primary blue-violet accent (buttons, active nav, links)
ACCENT_PRIMARY_HOVER = "#8AA3FF"
ACCENT_GLOW = "#4A5FD9"      # a darker accent shade used for subtle glow/shadow tints
ACCENT_VIOLET = "#9B7CFF"    # secondary accent hue, used sparingly (e.g. voice/AI states)

TEXT_PRIMARY = "#EDEFF5"
TEXT_SECONDARY = "#9AA1B4"
TEXT_MUTED = "#5C6377"

STATUS_ONLINE = "#3DDC97"
STATUS_THINKING = "#6C8CFF"
STATUS_WORKING = "#9B7CFF"
STATUS_WAITING = "#E8B44A"
STATUS_ERROR = "#E8556A"

DANGER = "#E8556A"
SUCCESS = "#3DDC97"

# --- typography --------------------------------------------------------------------------
# customtkinter renders CTkFont via Tkinter's font system - "Segoe UI
# Variable" is the modern Windows 11 system font (falls back to Segoe
# UI on Windows 10 automatically if unavailable, which is an acceptable
# degradation, not a broken font).

FONT_FAMILY = "Segoe UI Variable Display"
FONT_FAMILY_BODY = "Segoe UI Variable Text"
FONT_FAMILY_MONO = "Cascadia Code"

FONT_SIZE_HERO = 28
FONT_SIZE_TITLE = 20
FONT_SIZE_SUBTITLE = 15
FONT_SIZE_BODY = 13
FONT_SIZE_SMALL = 11
FONT_SIZE_CAPTION = 10

# --- spacing / radii -----------------------------------------------------------------------

RADIUS_CARD = 14
RADIUS_BUTTON = 10
RADIUS_PILL = 999  # effectively fully rounded for small status pills

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 16
SPACE_LG = 24
SPACE_XL = 32

SIDEBAR_WIDTH = 220
