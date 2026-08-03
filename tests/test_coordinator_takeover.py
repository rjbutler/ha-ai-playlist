"""Tests for external queue-takeover detection in _async_handle_queue_update.

Regression tests for the cache cross-contamination bug (2026-08-03): an
external music_assistant.play_media with enqueue=replace swaps N queue items
for M items without ever passing through 0, so the coordinator stayed
attached and its stale session record later poisoned resurrection, cache,
and history for the wrong playlist.

The fix: a queue update whose current item is not in enqueued_tracks ∪
history is checked against the full live queue — if none of our tracks
remain on it, the queue was taken over externally and we detach.
"""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.ai_playlist.coordinator import PlaylistCoordinator


def _make_coordinator(playlist_config=None):
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []
    hass.data = {}
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


def _current_item(artist, title):
    return {
        "media_item": {"artists": [{"name": artist}], "name": title},
        "name": f"{artist} - {title}",
    }


def _queue_event(artist, title, items=5):
    return {
        "items": items,
        "shuffle_enabled": False,
        "repeat_mode": "off",
        "current_item": _current_item(artist, title),
    }


def _prep(coord, enqueued, history, live_played=None, live_upcoming=None):
    coord.state = "playing"
    coord.enqueued_tracks = list(enqueued)
    coord._last_internal_queue_clear = 0.0
    coord.hass.async_add_executor_job = AsyncMock(return_value=list(history))
    coord._get_live_queue_tracks = AsyncMock(
        return_value=(list(live_played or []), list(live_upcoming or []))
    )
    coord._detach = AsyncMock()


class TestExternalTakeoverDetection:
    @pytest.mark.asyncio
    async def test_detaches_when_queue_fully_foreign(self):
        """Replace-takeover: current + all live items unknown → detach."""
        coord = _make_coordinator()
        _prep(
            coord,
            enqueued=["Boston - More Than a Feeling", "Kansas - Carry On"],
            history=["Foreigner - Cold as Ice"],
            live_upcoming=[
                "I Musici - Brandenburg Concerto No. 3",
                "Vivaldi - Concerto RV 149",
            ],
        )

        await coord._async_handle_queue_update(
            _queue_event("I Musici", "Brandenburg Concerto No. 3")
        )

        coord._detach.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_detach_when_current_in_enqueued(self):
        """Current track is ours — no live-queue read, no detach."""
        coord = _make_coordinator()
        _prep(coord, enqueued=["Boston - More Than a Feeling"], history=[])

        await coord._async_handle_queue_update(
            _queue_event("Boston", "More Than a Feeling")
        )

        coord._detach.assert_not_awaited()
        coord._get_live_queue_tracks.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_detach_when_current_in_history(self):
        """Current track was already played/recorded — still ours."""
        coord = _make_coordinator()
        _prep(coord, enqueued=[], history=["Foreigner - Cold as Ice"])

        await coord._async_handle_queue_update(_queue_event("Foreigner", "Cold as Ice"))

        coord._detach.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_detach_when_current_matches_after_normalization(self):
        """MA metadata differs by remaster suffix — normalization bridges it."""
        coord = _make_coordinator()
        _prep(coord, enqueued=["The Beatles - Let It Be"], history=[])

        await coord._async_handle_queue_update(
            _queue_event("The Beatles", "Let It Be (Remastered 2009)")
        )

        coord._detach.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_detach_when_live_queue_still_contains_our_tracks(self):
        """Single-track metadata mismatch: current unknown, but other queue
        items are ours → not a takeover."""
        coord = _make_coordinator()
        _prep(
            coord,
            enqueued=["Boston - More Than a Feeling", "Kansas - Carry On"],
            history=[],
            live_upcoming=[
                "Ludwig van Beethoven - Symphony No. 5 in C Minor: I. Allegro",
                "Kansas - Carry On",
            ],
        )

        await coord._async_handle_queue_update(
            _queue_event("Ludwig van Beethoven", "Symphony No. 5 in C Minor: I. Allegro")
        )

        coord._detach.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_detach_when_played_live_items_are_ours(self):
        """Last track of our queue mismatches, but played items match history."""
        coord = _make_coordinator()
        _prep(
            coord,
            enqueued=["Kansas - Carry On"],
            history=["Boston - More Than a Feeling"],
            live_played=["Boston - More Than a Feeling"],
            live_upcoming=["Kansas - Carry On My Wayward Son (Live 1978)"],
        )

        await coord._async_handle_queue_update(
            _queue_event("Kansas", "Carry On My Wayward Son (Live 1978)")
        )

        coord._detach.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_detach_when_live_queue_unreadable(self):
        """MA client hiccup (empty read) must not be mistaken for takeover."""
        coord = _make_coordinator()
        _prep(
            coord,
            enqueued=["Boston - More Than a Feeling"],
            history=[],
            live_played=[],
            live_upcoming=[],
        )

        await coord._async_handle_queue_update(
            _queue_event("I Musici", "Brandenburg Concerto No. 3")
        )

        coord._detach.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_detach_within_internal_clear_grace_window(self):
        """Right after our own replace-enqueue, skip the takeover check."""
        coord = _make_coordinator()
        _prep(
            coord,
            enqueued=["Boston - More Than a Feeling"],
            history=[],
            live_upcoming=["I Musici - Brandenburg Concerto No. 3"],
        )
        coord._last_internal_queue_clear = time.monotonic()

        await coord._async_handle_queue_update(
            _queue_event("I Musici", "Brandenburg Concerto No. 3")
        )

        coord._detach.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_detach_without_current_item(self):
        coord = _make_coordinator()
        _prep(coord, enqueued=["Boston - More Than a Feeling"], history=[])

        await coord._async_handle_queue_update(
            {"items": 5, "shuffle_enabled": False, "repeat_mode": "off"}
        )

        coord._detach.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_takeover_check_while_generating(self):
        """The _generating guard suppresses the whole handler."""
        coord = _make_coordinator()
        _prep(
            coord,
            enqueued=[],
            history=[],
            live_upcoming=["I Musici - Brandenburg Concerto No. 3"],
        )
        coord._generating = True

        await coord._async_handle_queue_update(
            _queue_event("I Musici", "Brandenburg Concerto No. 3")
        )

        coord._detach.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_shuffle_detach_still_works(self):
        """Existing shuffle detach path is unchanged."""
        coord = _make_coordinator()
        _prep(coord, enqueued=["Boston - More Than a Feeling"], history=[])

        await coord._async_handle_queue_update(
            {"items": 5, "shuffle_enabled": True, "repeat_mode": "off"}
        )

        coord._detach.assert_awaited_once()
