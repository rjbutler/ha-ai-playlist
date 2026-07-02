"""Tests for resurrection reconciliation and the refill guard."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.ai_playlist.coordinator import PlaylistCoordinator


def _make_coordinator(playlist_config=None):
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []  # no MA client by default
    store = MagicMock()
    entry = MagicMock()
    entry.data = {"ai_entity": "ai_task.test"}
    entry.options = {}
    return PlaylistCoordinator(
        hass=hass,
        store=store,
        playlist_config=playlist_config or {"name": "Test Playlist"},
        entity_id="media_player.test",
        entry=entry,
    )


def _queue_item(artist, title):
    media_item = SimpleNamespace(artists=[SimpleNamespace(name=artist)], name=title)
    return SimpleNamespace(media_item=media_item, name=f"{artist} - {title}")


def _make_mass(items, current_index):
    mass = MagicMock()
    mass.player_queues.get_queue_items = AsyncMock(return_value=items)
    mass.player_queues.get = MagicMock(
        return_value=SimpleNamespace(current_index=current_index)
    )
    return mass


class TestGetLiveQueueTracks:
    @pytest.mark.asyncio
    async def test_splits_played_and_upcoming_at_current_index(self):
        coord = _make_coordinator()
        items = [
            _queue_item("A", "One"),
            _queue_item("B", "Two"),
            _queue_item("C", "Three"),   # current
            _queue_item("D", "Four"),
        ]
        coord._get_mass_client = MagicMock(return_value=_make_mass(items, 2))
        coord._resolve_queue_id = MagicMock(return_value="queue_1")

        played, upcoming = await coord._get_live_queue_tracks()

        assert played == ["A - One", "B - Two"]
        assert upcoming == ["C - Three", "D - Four"]

    @pytest.mark.asyncio
    async def test_no_client_returns_empty(self):
        coord = _make_coordinator()
        coord._get_mass_client = MagicMock(return_value=None)
        assert await coord._get_live_queue_tracks() == ([], [])

    @pytest.mark.asyncio
    async def test_no_queue_id_returns_empty(self):
        coord = _make_coordinator()
        coord._get_mass_client = MagicMock(return_value=MagicMock())
        coord._resolve_queue_id = MagicMock(return_value=None)
        assert await coord._get_live_queue_tracks() == ([], [])

    @pytest.mark.asyncio
    async def test_client_error_returns_empty(self):
        coord = _make_coordinator()
        mass = MagicMock()
        mass.player_queues.get_queue_items = AsyncMock(side_effect=RuntimeError("down"))
        coord._get_mass_client = MagicMock(return_value=mass)
        coord._resolve_queue_id = MagicMock(return_value="queue_1")
        assert await coord._get_live_queue_tracks() == ([], [])

    @pytest.mark.asyncio
    async def test_missing_current_index_treats_all_as_upcoming(self):
        coord = _make_coordinator()
        items = [_queue_item("A", "One"), _queue_item("B", "Two")]
        mass = _make_mass(items, None)
        coord._get_mass_client = MagicMock(return_value=mass)
        coord._resolve_queue_id = MagicMock(return_value="queue_1")

        played, upcoming = await coord._get_live_queue_tracks()

        assert played == []
        assert upcoming == ["A - One", "B - Two"]
