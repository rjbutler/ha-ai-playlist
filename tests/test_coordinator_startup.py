"""Tests for startup robustness: enqueue timeouts, detach guards, resume checks.

Regression tests for the 2026-07-02 zombie-coordinator incident: a blocking
music_assistant.play_media call hung during MA sync-group flapping, freezing
async_start forever — no queue subscription, no state listener, no refill,
and every subsequent play call hit the resume branch on a dead queue.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.ai_playlist.coordinator as coordinator_mod
from custom_components.ai_playlist.const import (
    STATE_ENQUEUING,
    STATE_IDLE,
    STATE_PLAYING,
)
from custom_components.ai_playlist.coordinator import PlaylistCoordinator


def _make_coordinator(playlist_config=None):
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []  # no MA client by default
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


class TestEnqueueTimeout:
    @pytest.mark.asyncio
    async def test_hung_play_media_call_times_out_and_skips_track(self, monkeypatch):
        """A play_media call that never returns must not freeze the session."""
        monkeypatch.setattr(coordinator_mod, "ENQUEUE_TIMEOUT", 0.05, raising=False)
        coord = _make_coordinator()
        coord.hass.states.get = MagicMock(return_value=SimpleNamespace(state="idle"))

        async def fake_call(domain, service, data, target=None, blocking=False):
            if data["media_id"] == "Hang":
                await asyncio.Event().wait()  # never completes

        coord.hass.services.async_call = fake_call
        tracks = [
            {"artist": "A", "title": "One"},
            {"artist": "B", "title": "Hang"},
            {"artist": "C", "title": "Three"},
        ]

        # Must finish promptly despite the hung call (1s >> 0.05s timeout)
        await asyncio.wait_for(coord._enqueue_tracks(tracks, clear_first=True), timeout=1)

        assert coord.enqueued_tracks == ["A - One", "C - Three"]


class TestEnqueueDetachGuard:
    @pytest.mark.asyncio
    async def test_enqueue_stops_when_session_detached_mid_loop(self):
        """If the coordinator is detached mid-enqueue, remaining tracks are skipped."""
        coord = _make_coordinator()
        coord.hass.states.get = MagicMock(return_value=SimpleNamespace(state="idle"))
        calls = []

        async def fake_call(domain, service, data, target=None, blocking=False):
            calls.append(data["media_id"])
            coord.state = STATE_IDLE  # simulate detach while the call is in flight

        coord.hass.services.async_call = fake_call
        tracks = [
            {"artist": "A", "title": "One"},
            {"artist": "B", "title": "Two"},
            {"artist": "C", "title": "Three"},
        ]

        await coord._enqueue_tracks(tracks, clear_first=True)

        assert calls == ["One"]


class TestStartTaskLifecycle:
    @pytest.mark.asyncio
    async def test_async_start_records_then_clears_start_task(self):
        coord = _make_coordinator()
        coord.store.async_get_cache = AsyncMock(return_value=[])
        coord._subscribe_to_queue_events = MagicMock()
        seen = {}

        async def fake_generate(count, clear_first):
            seen["task_during_start"] = coord._start_task

        coord._generate_and_enqueue = fake_generate

        await coord.async_start()

        assert seen["task_during_start"] is asyncio.current_task()
        assert coord._start_task is None

    @pytest.mark.asyncio
    async def test_detach_cancels_inflight_start_task(self):
        """Tearing down a coordinator stuck in startup must cancel the hung task."""
        coord = _make_coordinator()
        coord.store.async_save_cache = AsyncMock()
        coord.store.clear_active_session = AsyncMock()
        started = asyncio.Event()

        async def hung_start():
            coord._start_task = asyncio.current_task()
            started.set()
            await asyncio.Event().wait()  # hung forever, like a wedged play_media

        task = asyncio.create_task(hung_start())
        await started.wait()

        await coord._detach()
        await asyncio.sleep(0)

        assert task.cancelled()


class TestCanResume:
    @pytest.mark.asyncio
    async def test_not_resumable_when_startup_never_finished(self):
        coord = _make_coordinator()
        coord.state = STATE_ENQUEUING  # zombie: stuck mid-startup
        coord._check_queue_depth = AsyncMock(return_value=(5, 1))
        assert await coord.async_can_resume() is False

    @pytest.mark.asyncio
    async def test_not_resumable_when_queue_empty(self):
        coord = _make_coordinator()
        coord.state = STATE_PLAYING
        coord._check_queue_depth = AsyncMock(return_value=(0, -1))
        assert await coord.async_can_resume() is False

    @pytest.mark.asyncio
    async def test_resumable_when_playing_with_queue_items(self):
        coord = _make_coordinator()
        coord.state = STATE_PLAYING
        coord._check_queue_depth = AsyncMock(return_value=(5, 1))
        assert await coord.async_can_resume() is True
