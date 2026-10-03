"""Instagram carousel template library ("Šablonų biblioteka").

  - catalog.py: categories, topics and templates read from JSON
    (data/topics/*.json, data/styles.json and the person's own folder),
    search/filters, „Įkvėpti mane 🎲“ and building a carousel.
  - layouts.py: the slide layouts (first, middle and last slides).
  - styles.py: design styles (Minimal, Luxury, ...) -> carousel Theme.
  - placeholder.py: the photo-slot placeholder picture.
  - usage.py: favorites and use counts.

use_template() turns a template into an ordinary saved carousel project,
so everything the editor can do works on it unchanged."""

from __future__ import annotations

from jarvis.carousel_studio import storage
from jarvis.carousel_studio.model import Project
from jarvis.carousel_studio.templates import catalog, placeholder, usage
from jarvis.carousel_studio.templates.catalog import Library, Template


def use_template(template: Template, *, style: str | None = None, name: str | None = None,
                 lib: Library | None = None) -> Project:
    """Creates and saves a new carousel from `template` and returns it."""
    lib = lib or catalog.library()
    project = catalog.build_project(template, style=style, name=name, lib=lib)
    if catalog.uses_photo(project):
        placeholder.write_placeholder(storage.assets_dir(project.id), lib.styles.get(style or template.style)
                                      or lib.styles[template.style])
    storage.save_project(project)
    usage.record_use(template.id)
    return project
