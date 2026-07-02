"""Tests for select entity state restoration across HA restarts.

Regression test for the 2026-07-02 incident: collection selects reset to
their first (alphabetical) option on every restart, so dashboard play
buttons silently played 'Afrobeat & World Grooves' instead of the playlist
the user had selected before the restart.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.ai_playlist.select import AiPlaylistCollectionSelect


def _make_select(options):
    store = MagicMock()
    store.get_playlists_by_tags.return_value = [{"name": n} for n in options]
    return AiPlaylistCollectionSelect(
        store, {"name": "Genre Playlists", "tags": ["genre"]}
    )


class TestSelectRestore:
    @pytest.mark.asyncio
    async def test_restores_last_selected_option_after_restart(self):
        entity = _make_select(["Afrobeat & World Grooves", "Folk Rock"])
        entity.async_get_last_state = AsyncMock(
            return_value=SimpleNamespace(state="Folk Rock")
        )

        await entity.async_added_to_hass()

        assert entity._attr_current_option == "Folk Rock"

    @pytest.mark.asyncio
    async def test_no_previous_state_keeps_first_option(self):
        entity = _make_select(["Afrobeat & World Grooves", "Folk Rock"])
        entity.async_get_last_state = AsyncMock(return_value=None)

        await entity.async_added_to_hass()

        assert entity._attr_current_option == "Afrobeat & World Grooves"

    @pytest.mark.asyncio
    async def test_stale_option_no_longer_in_collection_keeps_first_option(self):
        entity = _make_select(["Afrobeat & World Grooves", "Folk Rock"])
        entity.async_get_last_state = AsyncMock(
            return_value=SimpleNamespace(state="Christmas Hits")
        )

        await entity.async_added_to_hass()

        assert entity._attr_current_option == "Afrobeat & World Grooves"
