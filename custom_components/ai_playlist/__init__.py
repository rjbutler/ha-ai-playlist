"""The AI Playlist integration."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DEFAULT_HISTORY_DEPTH,
    DEFAULT_REFILL_THRESHOLD,
    DEFAULT_TRACK_COUNT,
    DOMAIN,
    PLATFORMS,
    STATE_IDLE,
)
from .coordinator import PlaylistCoordinator
from .sensor import get_or_create_sensor, get_sensor
from .store import PlaylistStore

_LOGGER = logging.getLogger(__name__)

SERVICE_PLAY = "play"
SERVICE_STOP = "stop"
SERVICE_CLEAR_HISTORY = "clear_history"
SERVICE_LIST_PLAYLISTS = "list_playlists"
SERVICE_SELECT = "select"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the AI Playlist integration."""
    hass.data.setdefault(DOMAIN, {"coordinators": {}})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AI Playlist from a config entry."""
    hass.data.setdefault(DOMAIN, {"coordinators": {}})

    store = PlaylistStore(hass)
    await store.async_load()
    hass.data[DOMAIN]["store"] = store
    hass.data[DOMAIN]["entry"] = entry

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _resurrect_sessions() -> None:
        """Attempt to re-attach coordinators to sessions that survived HA restart."""
        active_sessions = store.get_active_sessions()
        if not active_sessions:
            return

        coordinators = hass.data[DOMAIN]["coordinators"]
        for entity_id, session in active_sessions.items():
            playlist_name = session.get("playlist_name", "")
            collection_name = session.get("collection_name")

            config = store.get_playlist(playlist_name)
            if not config:
                _LOGGER.warning(
                    "Session playlist '%s' for %s not found, clearing session",
                    playlist_name, entity_id,
                )
                await store.clear_active_session(entity_id)
                continue

            coordinator = PlaylistCoordinator(
                hass=hass,
                store=store,
                playlist_config=config,
                entity_id=entity_id,
                entry=entry,
            )

            confidence = await coordinator._assess_resurrection_confidence()
            if confidence == "none":
                _LOGGER.info(
                    "Skipping resurrection for %s — player off or queue empty", entity_id
                )
                await store.clear_active_session(entity_id)
                continue

            _LOGGER.info(
                "Resurrecting coordinator for '%s' on %s (confidence: %s)",
                playlist_name, entity_id, confidence,
            )
            await coordinator.async_resume_after_restart()
            coordinators[entity_id] = coordinator

            sensor = get_or_create_sensor(hass, entity_id)
            if sensor:
                sensor.update_playback(playlist_name, collection_name)

    from homeassistant.core import callback as ha_callback

    @ha_callback
    def _on_ha_started(_event) -> None:
        hass.async_create_task(_resurrect_sessions())

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_ha_started)

    async def async_handle_play(call: ServiceCall) -> None:
        # entity_id may come from call.data (JSON call) or call.target (UI selector)
        entity_id = call.data.get("entity_id")
        if not entity_id and hasattr(call, "target") and call.target:
            entity_ids = call.target.get("entity_id", [])
            if isinstance(entity_ids, str):
                entity_id = entity_ids
            elif isinstance(entity_ids, list) and entity_ids:
                entity_id = entity_ids[0]
        if not entity_id:
            raise HomeAssistantError("entity_id is required")

        _LOGGER.info(
            "ai_playlist.play called: entity_id=%s, playlist=%s, prompt=%s, data=%s, target=%s",
            entity_id,
            call.data.get("playlist"),
            call.data.get("prompt"),
            dict(call.data),
            getattr(call, "target", None),
        )
        playlist_name = call.data.get("playlist")
        prompt = call.data.get("prompt")
        track_count = call.data.get("track_count")
        clear_queue = call.data.get("clear_queue", True)
        collection_name = call.data.get("collection")

        # Coerce track_count from float (NumberSelector) to int
        if track_count is not None:
            track_count = int(track_count)

        # Runtime check for Music Assistant
        if not hass.services.has_service("music_assistant", "play_media"):
            raise HomeAssistantError(
                "Music Assistant integration is not installed or not loaded. "
                "AI Playlist requires Music Assistant."
            )

        if not playlist_name and not prompt:
            # No-args mode: play the currently selected playlist
            sensor = get_sensor(hass, entity_id)
            if sensor:
                playlist_name = (sensor.extra_state_attributes or {}).get("selected")
                if not collection_name:
                    collection_name = (sensor.extra_state_attributes or {}).get("selected_collection")
            if not playlist_name:
                raise HomeAssistantError(
                    "No playlist or prompt specified, and no playlist is currently selected"
                )

        # Resolve playlist config
        if playlist_name:
            config = store.get_playlist(playlist_name)
            if not config:
                raise HomeAssistantError(f"Playlist '{playlist_name}' not found")
        else:
            # Ad-hoc prompt — create temporary config
            config = {
                "name": f"adhoc_{hash(prompt) % 10000}",
                "prompt": prompt,
                "track_count": track_count or DEFAULT_TRACK_COUNT,
                "history_depth": DEFAULT_HISTORY_DEPTH,
                "refill_threshold": DEFAULT_REFILL_THRESHOLD,
                "exclude_live": False,
            }

        coordinators = hass.data[DOMAIN]["coordinators"]
        existing = coordinators.get(entity_id)

        # Same playlist already attached — resume playback instead of rebuilding
        if (
            existing
            and existing.playlist_name == config.get("name", "")
            and existing.state != STATE_IDLE
        ):
            player_state = hass.states.get(entity_id)
            if player_state and player_state.state in ("paused", "idle"):
                await hass.services.async_call(
                    "media_player",
                    "media_play",
                    {},
                    target={"entity_id": entity_id},
                    blocking=True,
                )
                _LOGGER.info(
                    "Resumed existing playlist '%s' on %s",
                    existing.playlist_name,
                    entity_id,
                )
                sensor = get_or_create_sensor(hass, entity_id)
                if sensor:
                    sensor.update_playback(existing.playlist_name, collection_name)
                return

        # Different playlist or not attached — tear down and start fresh
        if existing:
            await existing.async_stop()

        coordinator = PlaylistCoordinator(
            hass=hass,
            store=store,
            playlist_config=config,
            entity_id=entity_id,
            entry=entry,
        )
        coordinators[entity_id] = coordinator

        await coordinator.async_start(
            clear_queue=clear_queue,
            track_count=track_count,
        )

        await store.set_active_session(entity_id, config.get("name", ""), collection_name)

        # Update playback state sensor
        sensor = get_or_create_sensor(hass, entity_id)
        if sensor:
            sensor.update_playback(config.get("name", ""), collection_name)

    async def async_handle_stop(call: ServiceCall) -> None:
        entity_id = call.data.get("entity_id")
        if not entity_id and hasattr(call, "target") and call.target:
            entity_ids = call.target.get("entity_id", [])
            if isinstance(entity_ids, str):
                entity_id = entity_ids
            elif isinstance(entity_ids, list) and entity_ids:
                entity_id = entity_ids[0]
        if not entity_id:
            return
        coordinators = hass.data[DOMAIN]["coordinators"]
        coordinator = coordinators.get(entity_id)
        if coordinator:
            await coordinator.async_stop()
            sensors = hass.data[DOMAIN].get("sensors", {})
            sensor = sensors.get(entity_id)
            if sensor:
                sensor.update_idle()

    async def async_handle_clear_history(call: ServiceCall) -> None:
        playlist_name = call.data["playlist"]
        await hass.async_add_executor_job(store.clear_history, playlist_name)
        _LOGGER.info("Cleared history for playlist '%s'", playlist_name)

    async def async_handle_list_playlists(call: ServiceCall) -> dict:
        tag = call.data.get("tag")
        tags = call.data.get("tags", [])
        if tag and not tags:
            tags = [tag]

        if tags:
            playlists = store.get_playlists_by_tags(tags)
        else:
            playlists = list(store.get_all_playlists().values())
        return {
            "playlists": [
                {
                    "name": p.get("name", ""),
                    "tags": p.get("tags", []),
                    "track_count": p.get("track_count", DEFAULT_TRACK_COUNT),
                    "has_prompt": bool(p.get("prompt")),
                }
                for p in playlists
            ]
        }

    async def async_handle_select(call: ServiceCall) -> None:
        entity_id = call.data.get("entity_id")
        if not entity_id and hasattr(call, "target") and call.target:
            entity_ids = call.target.get("entity_id", [])
            if isinstance(entity_ids, str):
                entity_id = entity_ids
            elif isinstance(entity_ids, list) and entity_ids:
                entity_id = entity_ids[0]
        if not entity_id:
            raise HomeAssistantError("entity_id is required")

        playlist_name = call.data.get("playlist", "")
        collection_name = call.data.get("collection")

        if not playlist_name:
            raise HomeAssistantError("'playlist' is required")

        sensor = get_or_create_sensor(hass, entity_id)
        if not sensor:
            _LOGGER.warning(
                "Sensor platform not ready for %s, ignoring select", entity_id
            )
            return

        sensor.update_selection(playlist_name, collection_name)
        _LOGGER.debug(
            "Updated selection for %s: playlist=%s, collection=%s",
            entity_id,
            playlist_name,
            collection_name,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_PLAY):
        hass.services.async_register(DOMAIN, SERVICE_PLAY, async_handle_play)
    if not hass.services.has_service(DOMAIN, SERVICE_STOP):
        hass.services.async_register(DOMAIN, SERVICE_STOP, async_handle_stop)
    if not hass.services.has_service(DOMAIN, SERVICE_CLEAR_HISTORY):
        hass.services.async_register(
            DOMAIN, SERVICE_CLEAR_HISTORY, async_handle_clear_history
        )
    if not hass.services.has_service(DOMAIN, SERVICE_LIST_PLAYLISTS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_LIST_PLAYLISTS,
            async_handle_list_playlists,
            supports_response=SupportsResponse.OPTIONAL,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SELECT):
        hass.services.async_register(DOMAIN, SERVICE_SELECT, async_handle_select)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Shut down all active coordinators, preserving sessions for resurrection
    coordinators = hass.data[DOMAIN].get("coordinators", {})
    for coordinator in list(coordinators.values()):
        await coordinator.async_shutdown()

    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Unregister services
    hass.services.async_remove(DOMAIN, SERVICE_PLAY)
    hass.services.async_remove(DOMAIN, SERVICE_STOP)
    hass.services.async_remove(DOMAIN, SERVICE_CLEAR_HISTORY)
    hass.services.async_remove(DOMAIN, SERVICE_LIST_PLAYLISTS)
    hass.services.async_remove(DOMAIN, SERVICE_SELECT)

    hass.data.pop(DOMAIN, None)
    return True
