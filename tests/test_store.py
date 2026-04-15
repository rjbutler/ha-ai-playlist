"""Tests for store.py — file I/O, slug generation, history/cache logic.

Mocks the HA Store (async persistence) but uses real filesystem for
track history and cache (JSON files in a temp directory).
"""
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.ai_playlist.store import PlaylistStore


class FakeHass:
    """Minimal hass mock for PlaylistStore."""
    def __init__(self, tmp_path):
        self.config = MagicMock()
        self.config.path = lambda *p: os.path.join(tmp_path, *p)


@pytest.fixture
def tmp_store(tmp_path):
    """Create a PlaylistStore backed by a temp directory."""
    hass = FakeHass(str(tmp_path))
    store = PlaylistStore(hass)
    # Override HA Store methods to no-op
    store._store = MagicMock()
    store._store.async_load = AsyncMock(return_value=None)
    store._store.async_save = AsyncMock()
    store._sessions_store = MagicMock()
    store._sessions_store.async_load = AsyncMock(return_value=None)
    store._sessions_store.async_save = AsyncMock()
    return store


# ── Slug generation ──────────────────────────────────────────────


class TestPlaylistSlug:
    def test_basic(self):
        assert PlaylistStore._playlist_slug("Classic Rock") == "classic_rock"

    def test_strips_special_chars(self):
        # & and ! are stripped, spaces collapse to single underscore
        assert PlaylistStore._playlist_slug("Rock & Roll!") == "rock_roll"

    def test_strips_quotes(self):
        slug = PlaylistStore._playlist_slug("Bob's Favorites")
        assert "'" not in slug

    def test_empty_string(self):
        assert PlaylistStore._playlist_slug("") == ""

    def test_all_special_chars(self):
        # After stripping all special chars and underscores, should return _unnamed
        assert PlaylistStore._playlist_slug("!!!") == "_unnamed"

    def test_leading_trailing_whitespace(self):
        assert PlaylistStore._playlist_slug("  Rock  ") == "rock"

    def test_unicode_chars_preserved(self):
        # Word chars include unicode letters
        slug = PlaylistStore._playlist_slug("Café Jazz")
        assert "caf" in slug

    def test_dots_stripped(self):
        slug = PlaylistStore._playlist_slug("...")
        assert slug == "_unnamed"


# ── Track history ────────────────────────────────────────────────


class TestTrackHistory:
    def test_add_and_get(self, tmp_store):
        tmp_store.add_to_history("Rock Playlist", "Led Zeppelin - Stairway to Heaven", max_tracks=50)
        history = tmp_store.get_history("Rock Playlist")
        assert len(history) == 1
        assert history[0] == "Led Zeppelin - Stairway to Heaven"

    def test_fifo_eviction(self, tmp_store):
        for i in range(5):
            tmp_store.add_to_history("Test", f"Artist - Track {i}", max_tracks=3)
        history = tmp_store.get_history("Test")
        assert len(history) == 3
        assert history[0] == "Artist - Track 2"
        assert history[2] == "Artist - Track 4"

    def test_dedup_keeps_latest(self, tmp_store):
        tmp_store.add_to_history("Test", "Artist - Track A", max_tracks=50)
        tmp_store.add_to_history("Test", "Artist - Track B", max_tracks=50)
        tmp_store.add_to_history("Test", "Artist - Track A", max_tracks=50)
        history = tmp_store.get_history("Test")
        assert len(history) == 2
        assert history[-1] == "Artist - Track A"

    def test_dedup_normalized(self, tmp_store):
        tmp_store.add_to_history("Test", "Led Zeppelin - Stairway to Heaven", max_tracks=50)
        tmp_store.add_to_history("Test", "led zeppelin - stairway to heaven", max_tracks=50)
        history = tmp_store.get_history("Test")
        assert len(history) == 1

    def test_clear_history(self, tmp_store):
        tmp_store.add_to_history("Test", "Artist - Track", max_tracks=50)
        tmp_store.clear_history("Test")
        assert tmp_store.get_history("Test") == []

    def test_clear_preserves_cache(self, tmp_store):
        tmp_store.add_to_history("Test", "Artist - Track", max_tracks=50)
        tmp_store.save_cache("Test", ["Cached - Track"])
        tmp_store.clear_history("Test")
        assert tmp_store.get_history("Test") == []
        assert tmp_store.get_cache_peek("Test") == ["Cached - Track"]

    def test_history_persists_to_file(self, tmp_store):
        tmp_store.add_to_history("Test", "Artist - Track", max_tracks=50)
        path = tmp_store._history_path("Test")
        assert os.path.isfile(path)
        with open(path) as f:
            data = json.load(f)
        assert "Artist - Track" in data["tracks"]


# ── Cache ────────────────────────────────────────────────────────


class TestCache:
    def test_save_and_peek(self, tmp_store):
        tracks = ["A - 1", "B - 2", "C - 3"]
        tmp_store.save_cache("Test", tracks)
        assert tmp_store.get_cache_peek("Test") == tracks

    def test_get_cache_clears_after_read(self, tmp_store):
        tmp_store.save_cache("Test", ["A - 1"])
        result = tmp_store.get_cache("Test")
        assert result == ["A - 1"]
        # Cache should be empty after get_cache
        assert tmp_store.get_cache("Test") == []

    def test_get_cache_filters_against_history(self, tmp_store):
        tmp_store.add_to_history("Test", "A - 1", max_tracks=50)
        tmp_store.save_cache("Test", ["A - 1", "B - 2"])
        result = tmp_store.get_cache("Test")
        assert result == ["B - 2"]

    def test_empty_cache(self, tmp_store):
        assert tmp_store.get_cache("Nonexistent") == []
        assert tmp_store.get_cache_peek("Nonexistent") == []


# ── Playlist CRUD ────────────────────────────────────────────────


class TestPlaylistCrud:
    @pytest.mark.asyncio
    async def test_save_and_get(self, tmp_store):
        await tmp_store.async_save_playlist("Rock Mix", {
            "prompt": "Classic rock hits",
            "track_count": 15,
        })
        config = tmp_store.get_playlist("Rock Mix")
        assert config is not None
        assert config["name"] == "Rock Mix"
        assert config["prompt"] == "Classic rock hits"
        assert config["track_count"] == 15

    @pytest.mark.asyncio
    async def test_get_all(self, tmp_store):
        await tmp_store.async_save_playlist("A", {"prompt": "a"})
        await tmp_store.async_save_playlist("B", {"prompt": "b"})
        all_playlists = tmp_store.get_all_playlists()
        assert len(all_playlists) == 2

    @pytest.mark.asyncio
    async def test_delete(self, tmp_store):
        await tmp_store.async_save_playlist("Delete Me", {"prompt": "x"})
        assert tmp_store.get_playlist("Delete Me") is not None
        await tmp_store.async_delete_playlist("Delete Me")
        assert tmp_store.get_playlist("Delete Me") is None

    @pytest.mark.asyncio
    async def test_defaults_applied(self, tmp_store):
        await tmp_store.async_save_playlist("Test", {"prompt": "x"})
        config = tmp_store.get_playlist("Test")
        assert config["track_count"] == 10  # DEFAULT_TRACK_COUNT
        assert config["history_depth"] == 50  # DEFAULT_HISTORY_DEPTH
        assert config["refill_threshold"] == 2  # DEFAULT_REFILL_THRESHOLD
        assert config["exclude_live"] is False

    @pytest.mark.asyncio
    async def test_tags(self, tmp_store):
        await tmp_store.async_save_playlist("Rock", {
            "prompt": "rock",
            "tags": ["Genre", "Rock"],
        })
        await tmp_store.async_save_playlist("Jazz", {
            "prompt": "jazz",
            "tags": ["Genre", "Jazz"],
        })
        genre_playlists = tmp_store.get_playlists_by_tag("Genre")
        assert len(genre_playlists) == 2

        rock_only = tmp_store.get_playlists_by_tags(["Genre", "Rock"])
        assert len(rock_only) == 1
        assert rock_only[0]["name"] == "Rock"

    @pytest.mark.asyncio
    async def test_tags_case_insensitive(self, tmp_store):
        await tmp_store.async_save_playlist("Test", {
            "prompt": "x",
            "tags": ["Genre"],
        })
        assert len(tmp_store.get_playlists_by_tag("genre")) == 1
        assert len(tmp_store.get_playlists_by_tags(["GENRE"])) == 1

    @pytest.mark.asyncio
    async def test_tags_empty_returns_all(self, tmp_store):
        await tmp_store.async_save_playlist("A", {"prompt": "a"})
        await tmp_store.async_save_playlist("B", {"prompt": "b"})
        assert len(tmp_store.get_playlists_by_tags([])) == 2


# ── YAML import ──────────────────────────────────────────────────


class TestYamlImport:
    @pytest.mark.asyncio
    async def test_import_basic(self, tmp_store):
        yaml_text = """
- name: Rock Mix
  prompt: Classic rock hits
  track_count: 15
- name: Jazz Evening
  prompt: Smooth jazz
"""
        imported, skipped = await tmp_store.async_import_playlists(yaml_text)
        assert imported == 2
        assert skipped == 0
        assert tmp_store.get_playlist("Rock Mix") is not None
        assert tmp_store.get_playlist("Jazz Evening") is not None

    @pytest.mark.asyncio
    async def test_import_invalid_yaml(self, tmp_store):
        imported, skipped = await tmp_store.async_import_playlists("{{invalid yaml")
        assert imported == 0
        assert skipped == 0

    @pytest.mark.asyncio
    async def test_import_not_list(self, tmp_store):
        imported, skipped = await tmp_store.async_import_playlists("just a string")
        assert imported == 0
        assert skipped == 0

    @pytest.mark.asyncio
    async def test_import_skips_incomplete(self, tmp_store):
        yaml_text = """
- name: Valid
  prompt: Has both fields
- name: Missing Prompt
- prompt: Missing Name
"""
        imported, skipped = await tmp_store.async_import_playlists(yaml_text)
        assert imported == 1
        assert skipped == 2


# ── Session persistence ──────────────────────────────────────────


class TestSessions:
    @pytest.mark.asyncio
    async def test_set_and_get(self, tmp_store):
        await tmp_store.set_active_session(
            "media_player.office", "Rock Mix", "Genre Playlists"
        )
        sessions = tmp_store.get_active_sessions()
        assert "media_player.office" in sessions
        assert sessions["media_player.office"]["playlist_name"] == "Rock Mix"
        assert sessions["media_player.office"]["collection_name"] == "Genre Playlists"

    @pytest.mark.asyncio
    async def test_clear(self, tmp_store):
        await tmp_store.set_active_session("media_player.office", "Rock Mix")
        await tmp_store.clear_active_session("media_player.office")
        assert "media_player.office" not in tmp_store.get_active_sessions()

    @pytest.mark.asyncio
    async def test_known_players(self, tmp_store):
        await tmp_store.set_active_session("media_player.office", "Rock Mix")
        await tmp_store.set_active_session("media_player.kitchen", "Jazz")
        known = tmp_store.get_known_players()
        assert "media_player.office" in known
        assert "media_player.kitchen" in known

    @pytest.mark.asyncio
    async def test_known_players_no_duplicates(self, tmp_store):
        await tmp_store.set_active_session("media_player.office", "A")
        await tmp_store.set_active_session("media_player.office", "B")
        known = tmp_store.get_known_players()
        assert known.count("media_player.office") == 1
