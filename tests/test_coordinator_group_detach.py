"""Tests for detaching a session whose player is synced into a foreign group.

Regression tests for the 2026-07-15 "classical tracks bleeding into the classic
rock playlist" incident. Two coordinators (main_floor -> Classic Rock, sonos_3 ->
Baroque) ended up managing one physical stream because sonos_3 was synced into
the "Main Floor" MA sync group. Both refilled into the shared group queue, so
Baroque tracks interleaved into the Classic Rock playback.

Live state at the time showed the tell-tale shape: the *member* (sonos_3) had an
empty `group_members` but its `active_queue` pointed at the group's queue
(`syncgroup_flyq9gjx`), while the *group* player (main_floor) listed sonos_3 in
its `group_members`. So subsumption is detected from the group's side.

The fix: a coordinator whose player is a synced group member detaches cleanly
(state-change path) and is skipped at resurrection, leaving the group leader's
session as the sole manager of the shared queue.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.ai_playlist.coordinator import PlaylistCoordinator
from custom_components.ai_playlist.const import STATE_PLAYING

GROUP_QUEUE = "syncgroup_flyq9gjx"


def _make_coordinator(entity_id="media_player.sonos_3", playlist_config=None):
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
        playlist_config=playlist_config or {"name": "Baroque Instrumental"},
        entity_id=entity_id,
        entry=entry,
    )


def _mp_state(entity_id, *, active_queue, group_members=None, state="playing", attrs=None):
    a = {"active_queue": active_queue, "group_members": group_members or []}
    if attrs:
        a.update(attrs)
    return SimpleNamespace(entity_id=entity_id, state=state, attributes=a)


def _wire_states(coord, states):
    """Point hass.states.get / async_all at a fixed set of media_player states."""
    by_id = {s.entity_id: s for s in states}
    coord.hass.states.get = MagicMock(side_effect=lambda eid: by_id.get(eid))
    coord.hass.states.async_all = MagicMock(return_value=list(states))


def _state_event(entity_state):
    return SimpleNamespace(data={"new_state": entity_state})


class TestSubsumingGroupDetection:
    def test_member_detects_subsuming_group(self):
        """Member's empty group_members but shared active_queue -> detected via group."""
        coord = _make_coordinator("media_player.sonos_3")
        member = _mp_state("media_player.sonos_3", active_queue=GROUP_QUEUE, group_members=[])
        group = _mp_state(
            "media_player.main_floor",
            active_queue=GROUP_QUEUE,
            group_members=["media_player.play_3", "media_player.sonos_3"],
        )
        _wire_states(coord, [member, group])
        assert coord._subsuming_group_entity() == "media_player.main_floor"

    def test_group_leader_not_flagged(self):
        """The group player itself owns the queue — no one lists it as a member."""
        coord = _make_coordinator("media_player.main_floor")
        member = _mp_state("media_player.sonos_3", active_queue=GROUP_QUEUE, group_members=[])
        group = _mp_state(
            "media_player.main_floor",
            active_queue=GROUP_QUEUE,
            group_members=["media_player.play_3", "media_player.sonos_3"],
        )
        _wire_states(coord, [member, group])
        assert coord._subsuming_group_entity() is None

    def test_standalone_player_not_flagged(self):
        """Standalone player on its own queue -> not subsumed."""
        coord = _make_coordinator("media_player.sonos_3")
        me = _mp_state("media_player.sonos_3", active_queue="RINCON_own", group_members=[])
        other = _mp_state("media_player.bobs_office", active_queue="RINCON_other")
        _wire_states(coord, [me, other])
        assert coord._subsuming_group_entity() is None


class TestStateChangeDetach:
    @pytest.mark.asyncio
    async def test_detaches_when_synced_into_group(self):
        """Synced member detaches on state change; no history record, no refill."""
        coord = _make_coordinator("media_player.sonos_3")
        coord.state = STATE_PLAYING
        coord.enqueued_tracks = ["Virginia Black - Les cyclopes"]
        member = _mp_state(
            "media_player.sonos_3", active_queue=GROUP_QUEUE, group_members=[],
            attrs={"media_title": "Les cyclopes", "media_artist": "Virginia Black"},
        )
        group = _mp_state(
            "media_player.main_floor", active_queue=GROUP_QUEUE,
            group_members=["media_player.sonos_3"],
        )
        _wire_states(coord, [member, group])
        coord._detach = AsyncMock()
        coord._check_queue_depth = AsyncMock(return_value=(20, 1))
        coord.store.async_add_to_history = AsyncMock()

        await coord._async_handle_state_change(_state_event(member))

        coord._detach.assert_awaited_once()
        coord._check_queue_depth.assert_not_called()
        coord.store.async_add_to_history.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_detach_when_standalone(self):
        """Standalone player is not detached and proceeds normally."""
        coord = _make_coordinator("media_player.sonos_3")
        coord.state = STATE_PLAYING
        coord.enqueued_tracks = []  # empty -> refill check returns early
        me = _mp_state(
            "media_player.sonos_3", active_queue="RINCON_own", group_members=[],
            attrs={"media_title": "Baba O'Riley", "media_artist": "The Who"},
        )
        _wire_states(coord, [me])
        coord._detach = AsyncMock()
        coord._check_queue_depth = AsyncMock(return_value=(0, -1))
        coord.store.async_add_to_history = AsyncMock()

        await coord._async_handle_state_change(_state_event(me))

        coord._detach.assert_not_called()
