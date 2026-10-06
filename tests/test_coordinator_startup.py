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
    # URI cache hit for every track, "uri" = the title, so enqueue tests can
    # assert on titles without exercising the search path
    store.get_track_uri.side_effect = lambda track: track.split(" - ", 1)[1].split(" | ")[0]
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

    @pytest.mark.asyncio
    async def test_replace_carries_over_when_first_track_fails(self):
        """If the replace-enqueue fails, the next track must replace instead.

        2026-10-05: Apple Music 429s failed track 1; tracks 2+ were "add"ed
        onto the stale queue of an idle player, so nothing ever played.
        """
        coord = _make_coordinator()
        coord.hass.states.get = MagicMock(return_value=SimpleNamespace(state="idle"))
        modes = []

        async def fake_call(domain, service, data, target=None, blocking=False):
            modes.append((data["media_id"], data["enqueue"]))
            if data["media_id"] in ("One", "Two"):
                raise RuntimeError("rate limited")

        coord.hass.services.async_call = fake_call
        tracks = [
            {"artist": "A", "title": "One"},
            {"artist": "B", "title": "Two"},
            {"artist": "C", "title": "Three"},
            {"artist": "D", "title": "Four"},
        ]

        await coord._enqueue_tracks(tracks, clear_first=True)

        assert modes == [
            ("One", "replace"),
            ("Two", "replace"),
            ("Three", "replace"),
            ("Four", "add"),
        ]
        assert coord.enqueued_tracks == ["C - Three", "D - Four"]

    @pytest.mark.asyncio
    async def test_fallback_replace_rearms_internal_clear_grace(self, monkeypatch):
        """A late fallback replace must still be inside the items=0 grace window."""
        clock = {"t": 1000.0}
        monkeypatch.setattr(coordinator_mod.time, "monotonic", lambda: clock["t"])
        coord = _make_coordinator()
        coord.hass.states.get = MagicMock(return_value=SimpleNamespace(state="idle"))

        async def fake_call(domain, service, data, target=None, blocking=False):
            clock["t"] += 30.0  # each call burns a full enqueue timeout
            if data["media_id"] == "One":
                raise RuntimeError("rate limited")

        coord.hass.services.async_call = fake_call
        tracks = [{"artist": "A", "title": "One"}, {"artist": "B", "title": "Two"}]

        await coord._enqueue_tracks(tracks, clear_first=True)

        # Marker reflects the successful replace (t=1060), not the first try
        assert coord._last_internal_queue_clear == 1060.0


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


class TestResolveTrackUri:
    def _coord_with_search(self, tracks_response):
        coord = _make_coordinator()
        coord.store.get_track_uri.side_effect = None
        coord.store.get_track_uri.return_value = None
        coord._get_mass_entry_id = MagicMock(return_value="ma_entry")
        coord.hass.services.async_call = AsyncMock(return_value={"tracks": tracks_response})
        return coord

    @pytest.mark.asyncio
    async def test_cache_hit_skips_search(self):
        coord = _make_coordinator()
        coord.store.get_track_uri.side_effect = None
        coord.store.get_track_uri.return_value = "apple_music://track/1"
        coord.hass.services.async_call = AsyncMock()

        uri = await coord._resolve_track_uri({"artist": "The Jam", "title": "Town Called Malice"})

        assert uri == "apple_music://track/1"
        coord.hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_result_is_cached(self):
        coord = self._coord_with_search([
            {"uri": "apple_music://track/9", "name": "Town Called Malice",
             "artists": [{"name": "The Jam"}], "album": {"name": "The Gift"}},
        ])

        uri = await coord._resolve_track_uri({"artist": "The Jam", "title": "Town Called Malice"})

        assert uri == "apple_music://track/9"
        coord.store.set_track_uri.assert_called_once_with(
            "The Jam - Town Called Malice", "apple_music://track/9"
        )
        # library hit: one call, no provider (Apple) search
        coord.hass.services.async_call.assert_called_once()
        data = coord.hass.services.async_call.call_args.args[2]
        assert data["media_type"] == ["track"]
        assert data["config_entry_id"] == "ma_entry"
        assert data["library_only"] is True
        assert "album" not in data

    @pytest.mark.asyncio
    async def test_library_miss_falls_back_to_full_search(self):
        coord = _make_coordinator()
        coord.store.get_track_uri.side_effect = None
        coord.store.get_track_uri.return_value = None
        coord._get_mass_entry_id = MagicMock(return_value="ma_entry")
        hit = {"uri": "apple_music://track/9", "name": "Town Called Malice",
               "artists": [{"name": "The Jam"}]}
        coord.hass.services.async_call = AsyncMock(side_effect=[{"tracks": []}, {"tracks": [hit]}])

        uri = await coord._resolve_track_uri({"artist": "The Jam", "title": "Town Called Malice"})

        assert uri == "apple_music://track/9"
        calls = coord.hass.services.async_call.call_args_list
        assert [c.args[2]["library_only"] for c in calls] == [True, False]
        coord.store.set_track_uri.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_search_failure_keeps_uncached_library_pick(self):
        coord = _make_coordinator()
        coord.store.get_track_uri.side_effect = None
        coord.store.get_track_uri.return_value = None
        coord._get_mass_entry_id = MagicMock(return_value="ma_entry")
        cover = {"uri": "library://track/3", "name": "Town Called Malice",
                 "artists": [{"name": "Cover Band"}]}
        coord.hass.services.async_call = AsyncMock(
            side_effect=[{"tracks": [cover]}, TimeoutError()]
        )

        uri = await coord._resolve_track_uri({"artist": "The Jam", "title": "Town Called Malice"})

        assert uri == "library://track/3"
        coord.store.set_track_uri.assert_not_called()

    @pytest.mark.asyncio
    async def test_exclude_live_skips_live_only_library_and_finds_studio(self):
        coord = _make_coordinator()
        coord.store.get_track_uri.side_effect = None
        coord.store.get_track_uri.return_value = None
        coord._get_mass_entry_id = MagicMock(return_value="ma_entry")
        coord.playlist_config = {**(coord.playlist_config or {}), "exclude_live": True}
        live = {"uri": "library://track/1", "name": "My Generation", "version": "Live",
                "artists": [{"name": "The Who"}]}
        studio = {"uri": "apple_music://track/2", "name": "My Generation", "version": "",
                  "artists": [{"name": "The Who"}]}
        coord.hass.services.async_call = AsyncMock(
            side_effect=[{"tracks": [live]}, {"tracks": [live, studio]}]
        )

        uri = await coord._resolve_track_uri({"artist": "The Who", "title": "My Generation"})

        assert uri == "apple_music://track/2"
        coord.store.set_track_uri.assert_called_once_with(
            "The Who - My Generation", "apple_music://track/2"
        )

    @pytest.mark.asyncio
    async def test_unconfident_pick_plays_but_is_not_cached(self):
        coord = self._coord_with_search([
            {"uri": "apple_music://track/7", "name": "Something Else",
             "artists": [{"name": "Unrelated"}]},
        ])

        uri = await coord._resolve_track_uri({"artist": "AI", "title": "Invented Song"})

        assert uri == "apple_music://track/7"
        coord.store.set_track_uri.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_search_returns_none_and_caches_nothing(self):
        coord = self._coord_with_search([])

        assert await coord._resolve_track_uri({"artist": "A", "title": "B"}) is None
        coord.store.set_track_uri.assert_not_called()


class TestUnresolvedRetry:
    @pytest.mark.asyncio
    async def test_unresolved_track_retried_once_and_never_touches_queue(self, monkeypatch):
        monkeypatch.setattr(coordinator_mod, "URI_RETRY_DELAY", 0)
        coord = _make_coordinator()
        attempts = {"Two": 0, "Never": 0}

        async def fake_resolve(track):
            title = track["title"]
            if title in attempts:
                attempts[title] += 1
                if title == "Two" and attempts[title] == 2:
                    return "uri:Two"
                return None
            return f"uri:{title}"

        coord._resolve_track_uri = fake_resolve
        played = []

        async def fake_call(domain, service, data, target=None, blocking=False):
            played.append((data["media_id"], data["enqueue"]))

        coord.hass.services.async_call = fake_call
        tracks = [
            {"artist": "A", "title": "Two"},
            {"artist": "B", "title": "Never"},
            {"artist": "C", "title": "Three"},
        ]

        await coord._enqueue_tracks(tracks, clear_first=True)

        # Unresolved first track must not consume the replace
        assert played == [("uri:Three", "replace"), ("uri:Two", "add")]
        assert attempts == {"Two": 2, "Never": 2}
        assert coord.enqueued_tracks == ["C - Three", "A - Two"]

    @pytest.mark.asyncio
    async def test_failed_enqueue_forgets_cached_uri(self):
        coord = _make_coordinator()

        async def fake_call(domain, service, data, target=None, blocking=False):
            raise RuntimeError("gone")

        coord.hass.services.async_call = fake_call

        await coord._enqueue_tracks([{"artist": "A", "title": "One"}], clear_first=True)

        coord.store.forget_track_uri.assert_called_once_with("A - One")


class TestStartSession:
    """ai_playlist.play returns once the first track is queued (2026-10-05)."""

    def _setup(self):
        loop = asyncio.get_running_loop()
        coord = _make_coordinator()
        coord.hass.async_create_task = lambda coro, name=None: loop.create_task(coro)
        store = MagicMock()
        store.set_active_session = AsyncMock()
        return coord, store

    async def _run(self, coord, store):
        from custom_components.ai_playlist import _start_session

        await asyncio.wait_for(
            _start_session(coord.hass, store, coord, True, None, "Genre"), timeout=1
        )

    @pytest.mark.asyncio
    async def test_records_session_after_first_track_while_rest_keeps_enqueuing(self, monkeypatch):
        coord, store = self._setup()
        monkeypatch.setattr("custom_components.ai_playlist.get_or_create_sensor", MagicMock())
        release = asyncio.Event()

        async def slow_start(clear_queue, track_count):
            coord.first_track_queued.set()
            await release.wait()  # rest of the batch still resolving

        coord.async_start = slow_start

        await self._run(coord, store)

        store.set_active_session.assert_awaited_once_with(
            "media_player.test", "Test Playlist", "Genre"
        )
        release.set()

    @pytest.mark.asyncio
    async def test_start_failure_before_first_track_raises(self):
        coord, store = self._setup()

        async def failing_start(clear_queue, track_count):
            raise RuntimeError("boom")

        coord.async_start = failing_start

        with pytest.raises(RuntimeError, match="boom"):
            await self._run(coord, store)
        store.set_active_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_superseded_start_returns_quietly_without_recording(self):
        """A newer play cancels our start: no CancelledError, no session overwrite."""
        coord, store = self._setup()
        started = asyncio.Event()

        async def hung_start(clear_queue, track_count):
            started.set()
            await asyncio.Event().wait()

        coord.async_start = hung_start
        from custom_components.ai_playlist import _start_session

        session = asyncio.ensure_future(
            _start_session(coord.hass, store, coord, True, None, "Genre")
        )
        await started.wait()
        for task in asyncio.all_tasks():
            if task.get_coro().__name__ == "hung_start":
                task.cancel()

        await asyncio.wait_for(session, timeout=1)  # must not raise
        store.set_active_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_background_failure_after_first_track_stops_session(self, monkeypatch):
        coord, store = self._setup()
        monkeypatch.setattr("custom_components.ai_playlist.get_or_create_sensor", MagicMock())
        coord.async_stop = AsyncMock()
        release = asyncio.Event()

        async def late_failing_start(clear_queue, track_count):
            coord.first_track_queued.set()
            await release.wait()
            raise RuntimeError("late")

        coord.async_start = late_failing_start

        await self._run(coord, store)
        release.set()
        for _ in range(5):
            await asyncio.sleep(0)

        coord.async_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_first_successful_enqueue_sets_event(self):
        coord = _make_coordinator()
        calls = []

        async def fake_call(domain, service, data, target=None, blocking=False):
            calls.append(data["media_id"])
            if data["media_id"] == "One":
                raise RuntimeError("fail")
            assert coord.first_track_queued.is_set() is False

        coord.hass.services.async_call = fake_call
        await coord._enqueue_tracks(
            [{"artist": "A", "title": "One"}, {"artist": "B", "title": "Two"}],
            clear_first=True,
        )
        assert coord.first_track_queued.is_set()


class TestDetachDuringRefill:
    @pytest.mark.asyncio
    async def test_detach_during_generation_discards_batch(self, monkeypatch):
        """A refill generating when the session is torn down must not enqueue."""
        coord = _make_coordinator()
        coord.hass.async_add_executor_job = AsyncMock(return_value=[])

        async def fake_generate(**kwargs):
            coord._detached = True  # new play/stop tore us down meanwhile
            coord.state = STATE_IDLE
            return [{"artist": "A", "title": "One"}]

        monkeypatch.setattr(coordinator_mod, "generate_tracks", fake_generate)
        coord._enqueue_tracks = AsyncMock()

        await coord._refill()

        coord._enqueue_tracks.assert_not_called()
        assert coord.state == STATE_IDLE

    @pytest.mark.asyncio
    async def test_fresh_coordinator_is_not_treated_as_detached(self):
        """A not-yet-started coordinator is STATE_IDLE but must still enqueue."""
        coord = _make_coordinator()
        assert coord.state == STATE_IDLE
        calls = []

        async def fake_call(domain, service, data, target=None, blocking=False):
            calls.append(data["media_id"])

        coord.hass.services.async_call = fake_call
        await coord._enqueue_tracks([{"artist": "A", "title": "One"}], clear_first=True)

        assert calls == ["One"]
