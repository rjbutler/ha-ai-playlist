"""Select platform for AI Playlist lists."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_LISTS, CONF_LIST_NAME, CONF_LIST_TAGS, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AI Playlist select entities from config entry."""
    store = hass.data[DOMAIN]["store"]
    lists_config = entry.options.get(CONF_LISTS, [])

    entities = []
    for list_cfg in lists_config:
        entities.append(AiPlaylistListSelect(store, list_cfg))

    async_add_entities(entities)

    # Store reference for dynamic updates
    hass.data[DOMAIN]["select_entities"] = entities


class AiPlaylistListSelect(SelectEntity):
    """A select entity backed by a tag query against the playlist store."""

    _attr_has_entity_name = False

    def __init__(self, store, list_config: dict) -> None:
        """Initialize the list select entity."""
        self._store = store
        self._list_name = list_config[CONF_LIST_NAME]
        self._tags = list_config.get(CONF_LIST_TAGS, [])

        slug = self._list_name.lower().replace("'", "").replace(" ", "_")
        slug = slug.strip("_")
        self._attr_unique_id = f"ai_playlist_list_{slug}"
        self.entity_id = f"select.ai_playlist_{slug}"
        self._attr_name = self._list_name
        self._attr_options = self._compute_options()
        self._attr_current_option = (
            self._attr_options[0] if self._attr_options else None
        )

    def _compute_options(self) -> list[str]:
        """Query store for playlists matching all tags."""
        playlists = self._store.get_playlists_by_tags(self._tags)
        return sorted(p["name"] for p in playlists)

    async def async_select_option(self, option: str) -> None:
        """Handle user selecting an option."""
        self._attr_current_option = option
        self.async_write_ha_state()

    @callback
    def refresh_options(self) -> None:
        """Refresh options from store (called after store changes)."""
        self._attr_options = self._compute_options()
        if self._attr_current_option not in self._attr_options:
            self._attr_current_option = (
                self._attr_options[0] if self._attr_options else None
            )
        self.async_write_ha_state()

    @property
    def list_name(self) -> str:
        """Return the list name."""
        return self._list_name
