"""Instagram carousel studio ("Karuselių kūrimas"): a multi-slide,
fully editable carousel editor with its own projects, themes and
exports. A separate module from jarvis.design_studio (single images)
and jarvis.video_editor (video), sharing nothing mutable with either.

Submodules:
  - model.py: the carousel document (Project -> Slide -> Element) as
    plain dataclasses that round-trip through JSON.
  - themes.py: color palettes and the carousel-wide Theme; elements
    refer to theme roles ("theme:primary") so one theme switch restyles
    every slide while keeping texts and photos.
  - fonts.py: font families with Lithuanian glyphs, resolved to files.
  - render.py: the ONE Pillow renderer used for the editor preview,
    thumbnails and every export, so exports match what is on screen.
  - editor.py: CarouselDocument - every edit operation plus undo/redo.
  - storage.py: project folders, the SQLite library index, assets.
  - export.py: PNG/JPG per slide, numbered ZIP, PDF, and the layout
    check that warns about clipped text or elements off the canvas.

No customtkinter import anywhere in this package; the UI lives in
jarvis.gui.views.carousel_studio.
"""
