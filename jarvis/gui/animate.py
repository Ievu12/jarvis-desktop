"""Small, subtle animation helpers shared by the dashboard's views -
all built on Tkinter's own `.after()` scheduler (there is no other
animation primitive available in this stack), driving simple linear
color interpolation over a short duration. Deliberately minimal, per
the brief's own "do not over-animate the application" instruction:
these are short (150-900ms), low-amplitude effects (a status pulse, a
fade-in, a hover color shift), not decorative motion.

Every animation here is defensive against the widget it's animating
having been destroyed mid-animation (e.g. the person switched views or
closed the window) - each step checks winfo_exists() before touching
the widget and simply stops if it's gone, rather than raising a
TclError from a stale .after() callback.
"""

from __future__ import annotations

from typing import Callable


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, c)) for c in rgb))


def _lerp_color(start: str, end: str, t: float) -> str:
    sr, sg, sb = _hex_to_rgb(start)
    er, eg, eb = _hex_to_rgb(end)
    return _rgb_to_hex((
        round(sr + (er - sr) * t),
        round(sg + (eg - sg) * t),
        round(sb + (eb - sb) * t),
    ))


def animate_color(
    widget,
    *,
    from_color: str,
    to_color: str,
    duration_ms: int = 220,
    steps: int = 12,
    apply: Callable[[str], None] | None = None,
    on_complete: Callable[[], None] | None = None,
) -> None:
    """Animates a color from `from_color` to `to_color` over
    `duration_ms`, calling `apply(color)` at each step - `apply`
    defaults to `widget.configure(fg_color=color)`, but callers can pass
    their own (e.g. to animate `text_color` or a canvas item's fill)
    without this helper needing to know every widget's configure
    signature. `widget` is only used for the winfo_exists()/`.after()`
    liveness check, never configured directly unless `apply` is None.
    """
    if apply is None:
        apply = lambda color: widget.configure(fg_color=color)  # noqa: E731

    step_delay = max(1, duration_ms // steps)

    def _step(i: int) -> None:
        if not widget.winfo_exists():
            return
        t = i / steps
        apply(_lerp_color(from_color, to_color, t))
        if i < steps:
            widget.after(step_delay, lambda: _step(i + 1))
        elif on_complete is not None:
            on_complete()

    _step(0)


def pulse(
    widget,
    *,
    base_color: str,
    pulse_color: str,
    apply: Callable[[str], None] | None = None,
    half_cycle_ms: int = 900,
) -> Callable[[], None]:
    """Starts a slow, continuous back-and-forth color pulse (e.g. the
    sidebar's ONLINE status dot) - subtle by design (a single soft
    color shift per ~0.9s half-cycle, not a flashing/strobing effect).
    Returns a `stop()` callable; the caller MUST call it before the
    widget might be destroyed (e.g. in the view's own teardown), since
    an un-stopped pulse otherwise keeps rescheduling itself via
    `.after()` (harmless once winfo_exists() starts returning False,
    since the loop then stops itself, but stop() avoids even scheduling
    that one extra wasted tick)."""
    stopped = {"value": False}

    def _cycle(forward: bool) -> None:
        if stopped["value"] or not widget.winfo_exists():
            return
        start, end = (base_color, pulse_color) if forward else (pulse_color, base_color)
        animate_color(
            widget, from_color=start, to_color=end, duration_ms=half_cycle_ms, apply=apply,
            on_complete=lambda: _cycle(not forward),
        )

    _cycle(True)

    def stop() -> None:
        stopped["value"] = True

    return stop


def fade_in(widget, *, text_color: str, duration_ms: int = 200) -> None:
    """A simple 'fade in' approximation for a text widget appearing
    (e.g. a new activity card) - Tkinter has no real alpha compositing
    for arbitrary widgets, so this interpolates the text color from the
    background color toward its real color, which reads as a soft
    appear rather than an abrupt pop-in."""
    try:
        bg = widget.cget("fg_color")
        if isinstance(bg, (tuple, list)):
            bg = bg[-1]
    except Exception:
        bg = text_color
    animate_color(
        widget, from_color=bg if isinstance(bg, str) and bg.startswith("#") else text_color,
        to_color=text_color, duration_ms=duration_ms,
        apply=lambda color: widget.configure(text_color=color),
    )
