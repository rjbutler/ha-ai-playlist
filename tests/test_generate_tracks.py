"""Tests for the module-level generate_tracks helper."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.ai_playlist.coordinator import generate_tracks


def _make_hass(ai_response_data: str):
    """Return a stub hass whose services.async_call returns the given AI data."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock(return_value={"data": ai_response_data})
    return hass


@pytest.mark.asyncio
async def test_generate_tracks_happy_path():
    ai_response = (
        '[{"artist":"Miles Davis","title":"So What","album":"Kind of Blue"},'
        '{"artist":"John Coltrane","title":"Giant Steps","album":"Giant Steps"}]'
    )
    hass = _make_hass(ai_response)

    tracks = await generate_tracks(
        hass=hass,
        ai_entity_id="ai_task.my_llm",
        system_prompt="SYS",
        playlist_config={"name": "jazz", "prompt": "cool jazz", "exclude_live": False},
        history=[],
        enqueued=[],
        track_count=2,
    )

    assert len(tracks) == 2
    assert tracks[0]["artist"] == "Miles Davis"
    assert tracks[0]["title"] == "So What"
    assert tracks[0]["album"] == "Kind of Blue"

    # AI call used the supplied entity
    call_args = hass.services.async_call.await_args
    assert call_args.args[0] == "ai_task"
    assert call_args.args[1] == "generate_data"
    assert call_args.args[2]["entity_id"] == "ai_task.my_llm"
    assert "SYS" in call_args.args[2]["instructions"]
    assert "cool jazz" in call_args.args[2]["instructions"]


@pytest.mark.asyncio
async def test_generate_tracks_raises_on_ai_exception():
    from homeassistant.exceptions import HomeAssistantError

    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock(side_effect=RuntimeError("ai down"))

    with pytest.raises(HomeAssistantError, match="AI generation failed"):
        await generate_tracks(
            hass=hass,
            ai_entity_id="ai_task.x",
            system_prompt="SYS",
            playlist_config={"name": "p", "prompt": "x"},
            history=[],
            enqueued=[],
            track_count=5,
        )


@pytest.mark.asyncio
async def test_generate_tracks_raises_on_empty_response():
    from homeassistant.exceptions import HomeAssistantError

    hass = _make_hass("")

    with pytest.raises(HomeAssistantError, match="no parseable tracks"):
        await generate_tracks(
            hass=hass,
            ai_entity_id="ai_task.x",
            system_prompt="SYS",
            playlist_config={"name": "p", "prompt": "x"},
            history=[],
            enqueued=[],
            track_count=5,
        )


@pytest.mark.asyncio
async def test_generate_tracks_raises_when_all_duplicates():
    from homeassistant.exceptions import HomeAssistantError

    ai_response = '[{"artist":"A","title":"B","album":""}]'
    hass = _make_hass(ai_response)

    with pytest.raises(HomeAssistantError, match="filtered as duplicates"):
        await generate_tracks(
            hass=hass,
            ai_entity_id="ai_task.x",
            system_prompt="SYS",
            playlist_config={"name": "p", "prompt": "x"},
            history=["A - B"],
            enqueued=[],
            track_count=1,
        )


@pytest.mark.asyncio
async def test_generate_tracks_honors_history_dedup():
    ai_response = (
        '[{"artist":"A","title":"X","album":""},'
        '{"artist":"A","title":"Y","album":""}]'
    )
    hass = _make_hass(ai_response)

    tracks = await generate_tracks(
        hass=hass,
        ai_entity_id="ai_task.x",
        system_prompt="SYS",
        playlist_config={"name": "p", "prompt": "x"},
        history=["A - X"],
        enqueued=[],
        track_count=2,
    )

    titles = [t["title"] for t in tracks]
    assert "X" not in titles
    assert "Y" in titles

    # Exclusion list appears in the prompt sent to the AI
    prompt_sent = hass.services.async_call.await_args.args[2]["instructions"]
    assert "A - X" in prompt_sent
