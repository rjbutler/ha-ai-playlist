"""Tests for resurrection reconciliation and the refill guard."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.ai_playlist.const import STATE_PLAYING
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


class TestResumeAfterRestart:
    def _prep(self, coord, cache_peek, played, upcoming):
        # get_cache_peek runs via executor job
        coord.hass.async_add_executor_job = AsyncMock(return_value=cache_peek)
        coord.store.async_add_to_history = AsyncMock()
        coord._get_live_queue_tracks = AsyncMock(return_value=(played, upcoming))
        coord._subscribe_to_queue_events = MagicMock()

    @pytest.mark.asyncio
    async def test_seeds_enqueued_from_live_queue_and_cache(self):
        coord = _make_coordinator()
        self._prep(
            coord,
            cache_peek=["Cached - Song", "C - Three"],  # C - Three also in queue
            played=["A - One"],
            upcoming=["C - Three", "D - Four"],
        )

        await coord.async_resume_after_restart()

        # Live queue first, cache extras appended, dup deduped
        assert coord.enqueued_tracks == ["C - Three", "D - Four", "Cached - Song"]

    @pytest.mark.asyncio
    async def test_played_queue_items_recorded_to_history(self):
        coord = _make_coordinator()
        self._prep(coord, cache_peek=[], played=["A - One", "B - Two"], upcoming=[])

        await coord.async_resume_after_restart()

        recorded = [c.args for c in coord.store.async_add_to_history.await_args_list]
        assert ("Test Playlist", "A - One") in recorded
        assert ("Test Playlist", "B - Two") in recorded

    @pytest.mark.asyncio
    async def test_empty_cache_with_live_queue_still_seeds(self):
        """The original bug: empty cache peek must no longer leave enqueued_tracks empty."""
        coord = _make_coordinator()
        self._prep(coord, cache_peek=[], played=[], upcoming=["C - Three", "D - Four"])

        await coord.async_resume_after_restart()

        assert coord.enqueued_tracks == ["C - Three", "D - Four"]
        # Post-resume side effects: playing state, listeners wired up
        assert coord.state == STATE_PLAYING
        coord._subscribe_to_queue_events.assert_called_once()
        assert coord._unsub_state_listener is not None

    @pytest.mark.asyncio
    async def test_empty_live_queue_falls_back_to_cache(self):
        """Queue read returned nothing — cache peek alone seeds enqueued_tracks."""
        coord = _make_coordinator()
        self._prep(coord, cache_peek=["Cached - Song"], played=[], upcoming=[])

        await coord.async_resume_after_restart()

        assert coord.enqueued_tracks == ["Cached - Song"]


def _playing_event():
    state = MagicMock()
    state.state = "playing"
    state.attributes = {}
    event = MagicMock()
    event.data = {"new_state": state}
    return event


class TestRefillGuard:
    def _prep(self, coord, queue_depth):
        coord.state = "playing"
        coord._last_queue_check = 0.0
        coord._check_queue_depth = AsyncMock(return_value=queue_depth)
        coord._refill = AsyncMock()

    @pytest.mark.asyncio
    async def test_refills_with_empty_enqueued_but_live_queue(self):
        """The original bug: resurrected session with empty enqueued_tracks
        and a draining live queue must still refill."""
        coord = _make_coordinator({"name": "Test Playlist", "refill_threshold": 2})
        self._prep(coord, queue_depth=(10, 8))  # items_after = 1 < 2
        coord.enqueued_tracks = []

        await coord._async_handle_state_change(_playing_event())

        coord._refill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_refill_without_enqueued_and_without_live_queue(self):
        """Before the initial enqueue: nothing enqueued, no live queue → block."""
        coord = _make_coordinator({"name": "Test Playlist", "refill_threshold": 2})
        self._prep(coord, queue_depth=(0, -1))
        coord.enqueued_tracks = []

        await coord._async_handle_state_change(_playing_event())

        coord._refill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refills_normally_with_enqueued_tracks(self):
        coord = _make_coordinator({"name": "Test Playlist", "refill_threshold": 2})
        self._prep(coord, queue_depth=(10, 8))
        coord.enqueued_tracks = ["A - One"]

        await coord._async_handle_state_change(_playing_event())

        coord._refill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_refill_above_threshold(self):
        coord = _make_coordinator({"name": "Test Playlist", "refill_threshold": 2})
        self._prep(coord, queue_depth=(10, 5))  # items_after = 4 >= 2
        coord.enqueued_tracks = ["A - One"]

        await coord._async_handle_state_change(_playing_event())

        coord._refill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_refill_when_not_playing(self):
        """A paused player with a draining queue must not trigger refill."""
        coord = _make_coordinator({"name": "Test Playlist", "refill_threshold": 2})
        self._prep(coord, queue_depth=(10, 8))  # items_after = 1 < 2
        coord.enqueued_tracks = ["A - One"]

        state = MagicMock()
        state.state = "paused"
        state.attributes = {}
        event = MagicMock()
        event.data = {"new_state": state}

        await coord._async_handle_state_change(event)

        coord._refill.assert_not_awaited()
