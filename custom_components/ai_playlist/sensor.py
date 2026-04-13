"""Sensor platform for AI Playlist playback state."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, STATE_IDLE

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AI Playlist sensor entities from config entry."""
    hass.data[DOMAIN]["sensor_add_entities"] = async_add_entities
    hass.data[DOMAIN]["sensors"] = {}

    # Pre-create sensors for all players that have ever had a session,
    # so dashboards and resurrection logic can find them on startup.
    store = hass.data[DOMAIN].get("store")
    if store:
        for player_entity_id in store.get_known_players():
            get_or_create_sensor(hass, player_entity_id)


def get_or_create_sensor(
    hass: HomeAssistant, entity_id: str
) -> AiPlaylistStateSensor | None:
    """Get existing sensor or create a new one for a media player entity_id."""
    sensors = hass.data[DOMAIN].get("sensors", {})
    if entity_id in sensors:
        return sensors[entity_id]

    add_entities = hass.data[DOMAIN].get("sensor_add_entities")
    if add_entities is None:
        _LOGGER.warning("Sensor platform not ready, cannot create sensor for %s", entity_id)
        return None

    sensor = AiPlaylistStateSensor(entity_id)
    sensors[entity_id] = sensor
    add_entities([sensor])
    return sensor


def get_sensor(hass: HomeAssistant, entity_id: str) -> AiPlaylistStateSensor | None:
    """Get existing sensor for a media player entity_id (lookup only, no create)."""
    return hass.data.get(DOMAIN, {}).get("sensors", {}).get(entity_id)


class AiPlaylistStateSensor(SensorEntity, RestoreEntity):
    """Sensor tracking playback state for a media player managed by AI Playlist."""

    _attr_has_entity_name = False

    def __init__(self, media_player_entity_id: str) -> None:
        """Initialize the playback state sensor."""
        self._media_player_entity_id = media_player_entity_id
        self._registered = False
        # media_player.bobs_office -> bobs_office
        suffix = media_player_entity_id.replace("media_player.", "", 1)

        self._attr_unique_id = f"ai_playlist_{suffix}"
        self.entity_id = f"sensor.ai_playlist_{suffix}"
        self._attr_name = f"AI Playlist {suffix.replace('_', ' ').title()}"
        self._attr_native_value = STATE_IDLE
        self._attr_extra_state_attributes = {
            "list": None,
            "selected": None,
            "selected_list": None,
            "media_player": media_player_entity_id,
        }

    async def async_added_to_hass(self) -> None:
        """Restore last state from recorder, then mark entity as registered."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unavailable", "unknown"):
            self._attr_native_value = last_state.state
            attrs = dict(self._attr_extra_state_attributes)
            for key in ("list", "selected", "selected_list"):
                if key in last_state.attributes:
                    attrs[key] = last_state.attributes[key]
            self._attr_extra_state_attributes = attrs
        self._registered = True

    def _safe_write_state(self) -> None:
        """Write state only if entity is registered with HA."""
        if self._registered:
            self.async_write_ha_state()

    @callback
    def update_playback(
        self,
        playlist_name: str | None,
        list_name: str | None = None,
    ) -> None:
        """Update sensor when playback starts or changes."""
        if playlist_name:
            self._attr_native_value = playlist_name
            attrs = dict(self._attr_extra_state_attributes)
            attrs["list"] = list_name
            attrs["selected"] = playlist_name
            attrs["selected_list"] = list_name
            self._attr_extra_state_attributes = attrs
        else:
            self._attr_native_value = STATE_IDLE
            attrs = dict(self._attr_extra_state_attributes)
            attrs["list"] = None
            self._attr_extra_state_attributes = attrs
        self._safe_write_state()

    @callback
    def update_selection(
        self,
        playlist_name: str,
        list_name: str | None = None,
    ) -> None:
        """Update selected playlist without changing playback state."""
        attrs = dict(self._attr_extra_state_attributes)
        attrs["selected"] = playlist_name
        if list_name is not None:
            attrs["selected_list"] = list_name
        self._attr_extra_state_attributes = attrs
        self._safe_write_state()

    @callback
    def update_idle(self) -> None:
        """Mark sensor as idle (coordinator stopped)."""
        self._attr_native_value = STATE_IDLE
        attrs = dict(self._attr_extra_state_attributes)
        attrs["list"] = None
        self._attr_extra_state_attributes = attrs
        self._safe_write_state()
