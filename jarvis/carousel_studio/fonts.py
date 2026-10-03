"""Font families for carousel text. Each family maps to the Windows font
files Ieva's PC already has (all of them include the Lithuanian letters
ąčęėįšųūž), with Linux fallbacks so tests and other machines still
render. Custom .ttf/.otf files can be registered at runtime
(register_custom_font) - stage 3 adds the UI for that."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

_WIN = "C:/Windows/Fonts/"
_LIN_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_LIN_SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_LIN_SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
_LIN_SERIF_B = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
_LIB_SANS = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
_LIB_SANS_B = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
_LIB_SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"
_LIB_SERIF_B = "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"
_FREE_SANS = "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
_FREE_SANS_B = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
_MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

# key -> (label, kind, regular candidates, bold candidates)
_FAMILIES: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {
    "arial": ("Arial", "sans", (_WIN + "arial.ttf", _LIB_SANS, _LIN_SANS), (_WIN + "arialbd.ttf", _LIB_SANS_B, _LIN_SANS_B)),
    "segoe": ("Segoe UI", "sans", (_WIN + "segoeui.ttf", _FREE_SANS, _LIN_SANS), (_WIN + "segoeuib.ttf", _FREE_SANS_B, _LIN_SANS_B)),
    "calibri": ("Calibri", "sans", (_WIN + "calibri.ttf", _LIB_SANS, _LIN_SANS), (_WIN + "calibrib.ttf", _LIB_SANS_B, _LIN_SANS_B)),
    "verdana": ("Verdana", "sans", (_WIN + "verdana.ttf", _LIN_SANS), (_WIN + "verdanab.ttf", _LIN_SANS_B)),
    "trebuchet": ("Trebuchet MS", "sans", (_WIN + "trebuc.ttf", _FREE_SANS, _LIN_SANS), (_WIN + "trebucbd.ttf", _FREE_SANS_B, _LIN_SANS_B)),
    "bahnschrift": ("Bahnschrift", "sans", (_WIN + "bahnschrift.ttf", _LIN_SANS), (_WIN + "bahnschrift.ttf", _LIN_SANS_B)),
    "corbel": ("Corbel", "sans", (_WIN + "corbel.ttf", _LIB_SANS, _LIN_SANS), (_WIN + "corbelb.ttf", _LIB_SANS_B, _LIN_SANS_B)),
    "candara": ("Candara", "sans", (_WIN + "Candara.ttf", _FREE_SANS, _LIN_SANS), (_WIN + "Candarab.ttf", _FREE_SANS_B, _LIN_SANS_B)),
    "georgia": ("Georgia", "serif", (_WIN + "georgia.ttf", _LIN_SERIF), (_WIN + "georgiab.ttf", _LIN_SERIF_B)),
    "cambria": ("Cambria", "serif", (_WIN + "cambria.ttc", _LIB_SERIF, _LIN_SERIF), (_WIN + "cambriab.ttf", _LIB_SERIF_B, _LIN_SERIF_B)),
    "constantia": ("Constantia", "serif", (_WIN + "constan.ttf", _LIB_SERIF, _LIN_SERIF), (_WIN + "constanb.ttf", _LIB_SERIF_B, _LIN_SERIF_B)),
    "times": ("Times New Roman", "serif", (_WIN + "times.ttf", _LIB_SERIF, _LIN_SERIF), (_WIN + "timesbd.ttf", _LIB_SERIF_B, _LIN_SERIF_B)),
    "impact": ("Impact", "display", (_WIN + "impact.ttf", _LIN_SANS_B), (_WIN + "impact.ttf", _LIN_SANS_B)),
    "comic": ("Comic Sans MS", "display", (_WIN + "comic.ttf", _FREE_SANS), (_WIN + "comicbd.ttf", _FREE_SANS_B)),
    "courier": ("Courier New", "mono", (_WIN + "cour.ttf", _MONO), (_WIN + "courbd.ttf", _MONO_B)),
}

DEFAULT_FAMILY = "arial"
_LAST_RESORT = (_LIN_SANS, _LIB_SANS, _FREE_SANS)
_custom: dict[str, tuple[str, str]] = {}  # key -> (label, file)


def register_custom_font(key: str, label: str, path: str | Path) -> None:
    _custom[key] = (label, str(path))
    _font_file.cache_clear()
    load_font.cache_clear()


def family_keys() -> tuple[str, ...]:
    return tuple(_FAMILIES) + tuple(_custom)


def family_label(key: str) -> str:
    if key in _custom:
        return _custom[key][0]
    return _FAMILIES.get(key, (key,))[0]


def is_available(key: str) -> bool:
    """True when the family's real (Windows or custom) file is present,
    not just a fallback - the UI marks unavailable families."""
    if key in _custom:
        return Path(_custom[key][1]).is_file()
    entry = _FAMILIES.get(key)
    return bool(entry) and Path(entry[2][0]).is_file()


@lru_cache(maxsize=256)
def _font_file(family: str, bold: bool) -> str | None:
    if family in _custom and Path(_custom[family][1]).is_file():
        return _custom[family][1]
    entry = _FAMILIES.get(family) or _FAMILIES[DEFAULT_FAMILY]
    for candidate in (entry[3] if bold else entry[2]) + _LAST_RESORT:
        if Path(candidate).is_file():
            return candidate
    return None


@lru_cache(maxsize=512)
def load_font(family: str, bold: bool, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(4, int(size))
    path = _font_file(family, bold)
    if path is None:
        return ImageFont.load_default(size)
    return ImageFont.truetype(path, size)
