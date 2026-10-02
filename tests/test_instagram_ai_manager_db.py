"""Tests for jarvis.instagram_ai_manager.db: SQLite storage for
generated Instagram content (Reel ideas, hooks, captions, CTAs, hashtag
sets, Story sequences, weekly plans, AI recommendations). Redirected to
a per-test tmp_path database file - no test touches the real
.jarvis/instagram_ai_manager.db. Confirms: schema creation is
idempotent, save/list round-trips preserve the JSON data exactly, list
functions return newest-first, and each table is independent of the
others."""

from __future__ import annotations

import pytest

from jarvis.instagram_ai_manager import db


@pytest.fixture(autouse=True)
def _isolated_db_file(tmp_path, monkeypatch):
    db_file = tmp_path / "instagram_ai_manager.db"
    monkeypatch.setattr(db, "INSTAGRAM_AI_MANAGER_DB_FILE", db_file)
    return db_file


# --- schema creation ------------------------------------------------------------------


def test_db_file_is_created_on_first_use(_isolated_db_file):
    assert not _isolated_db_file.exists()
    db.save_reel_idea_set("skincare", [{"title": "x"}])
    assert _isolated_db_file.exists()


def test_schema_creation_is_idempotent_across_multiple_connections():
    # Every db.py function opens its own connection and re-runs the
    # CREATE TABLE IF NOT EXISTS schema - this confirms that doesn't
    # error out on a database that already has the tables.
    db.save_reel_idea_set("a", [{"title": "1"}])
    db.save_reel_idea_set("b", [{"title": "2"}])  # must not raise on existing schema
    assert len(db.list_reel_idea_sets()) == 2


# --- reel_idea_sets ---------------------------------------------------------------------


def test_save_and_list_reel_idea_set_round_trips():
    ideas = [{"title": "Idea 1", "hook": "Hook text"}, {"title": "Idea 2", "hook": "Another hook"}]
    db.save_reel_idea_set("skincare", ideas)
    records = db.list_reel_idea_sets()
    assert len(records) == 1
    assert records[0].data == ideas


def test_list_reel_idea_sets_returns_newest_first():
    db.save_reel_idea_set("first", [{"title": "1"}])
    db.save_reel_idea_set("second", [{"title": "2"}])
    records = db.list_reel_idea_sets()
    assert records[0].data == [{"title": "2"}]
    assert records[1].data == [{"title": "1"}]


def test_list_reel_idea_sets_respects_limit():
    for i in range(5):
        db.save_reel_idea_set(f"topic{i}", [{"title": str(i)}])
    records = db.list_reel_idea_sets(limit=2)
    assert len(records) == 2


def test_save_reel_idea_set_returns_an_id():
    record_id = db.save_reel_idea_set("skincare", [{"title": "x"}])
    assert isinstance(record_id, int)
    assert record_id > 0


def test_reel_idea_set_records_have_created_at_timestamp():
    db.save_reel_idea_set("skincare", [{"title": "x"}])
    records = db.list_reel_idea_sets()
    assert records[0].created_at  # non-empty ISO timestamp string


# --- hook_sets -----------------------------------------------------------------------


def test_save_and_list_hook_set_round_trips():
    hooks = {"curiosity": ["Hook A", "Hook B"], "question": ["Hook C"]}
    db.save_hook_set("yoga", hooks)
    records = db.list_hook_sets()
    assert records[0].data == hooks


# --- captions ---------------------------------------------------------------------------


def test_save_and_list_caption_round_trips():
    caption = {"short_caption": "Short", "medium_caption": "Medium", "long_caption": "Long"}
    db.save_caption("skincare", "friendly", caption)
    records = db.list_captions()
    assert records[0].data == caption


# --- cta_sets ---------------------------------------------------------------------------


def test_save_and_list_cta_set_round_trips():
    ctas = {"comments": ["Comment below!"], "saves": ["Save this for later"]}
    db.save_cta_set("skincare", ctas)
    records = db.list_cta_sets()
    assert records[0].data == ctas


# --- hashtag_sets -----------------------------------------------------------------------


def test_save_and_list_hashtag_set_round_trips():
    hashtags = {"niche": ["#skincaretips"], "broader": ["#skincare"]}
    db.save_hashtag_set("skincare", hashtags)
    records = db.list_hashtag_sets()
    assert records[0].data == hashtags


# --- story_sequences ----------------------------------------------------------------------


def test_save_and_list_story_sequence_round_trips():
    sequence = [{"story_number": "1", "stage": "Hook", "text": "Hi!"}]
    db.save_story_sequence("yoga", sequence)
    records = db.list_story_sequences()
    assert records[0].data == sequence


# --- weekly_plans -------------------------------------------------------------------------


def test_save_and_list_weekly_plan_round_trips():
    plan = {"monday": [{"format": "Reel", "topic": "yoga"}], "tuesday": []}
    db.save_weekly_plan("2026-09-28", plan)
    records = db.list_weekly_plans()
    assert records[0].data == plan


# --- recommendations ----------------------------------------------------------------------


def test_save_and_list_recommendations_round_trips():
    recommendations = {"text": "Do more Reels", "supporting_data": "2x median reach"}
    db.save_recommendations("2026-W39", recommendations)
    records = db.list_recommendations()
    assert records[0].data == recommendations


# --- table independence ---------------------------------------------------------------------


def test_tables_are_independent_of_each_other():
    db.save_reel_idea_set("a", [{"title": "1"}])
    db.save_hook_set("b", {"curiosity": ["x"]})
    assert len(db.list_reel_idea_sets()) == 1
    assert len(db.list_hook_sets()) == 1


def test_unicode_content_round_trips_correctly():
    # Lithuanian content (this project's primary language for
    # user-facing text elsewhere) must survive the JSON round trip
    # intact, not escaped/mangled.
    ideas = [{"title": "Kaip pradėti jogą namuose", "hook": "Ar žinojai, kad..."}]
    db.save_reel_idea_set("joga", ideas)
    records = db.list_reel_idea_sets()
    assert records[0].data == ideas
