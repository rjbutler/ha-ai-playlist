"""Config flow for AI Playlist integration."""
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_AI_ENTITY,
    CONF_COLLECTIONS,
    CONF_COLLECTION_NAME,
    CONF_COLLECTION_TAGS,
    CONF_PLAYLIST_EXCLUDE_LIVE,
    CONF_PLAYLIST_HISTORY_DEPTH,
    CONF_PLAYLIST_NAME,
    CONF_PLAYLIST_PROMPT,
    CONF_PLAYLIST_REFILL_THRESHOLD,
    CONF_PLAYLIST_TRACK_COUNT,
    CONF_SYSTEM_PROMPT,
    DEFAULT_HISTORY_DEPTH,
    DEFAULT_REFILL_THRESHOLD,
    DEFAULT_TRACK_COUNT,
    DOMAIN,
    SYSTEM_PROMPT,
)

_LOGGER = logging.getLogger(__name__)


class AiPlaylistConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AI Playlist."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial setup step — select AI Task entity."""
        errors = {}

        if user_input is not None:
            ai_entity = user_input.get(CONF_AI_ENTITY, "")
            if not ai_entity:
                errors[CONF_AI_ENTITY] = "no_ai_entity"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="AI Playlist",
                    data={CONF_AI_ENTITY: ai_entity},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_AI_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="ai_task")
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return AiPlaylistOptionsFlow(config_entry)


class AiPlaylistOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for AI Playlist — playlist CRUD."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._edit_slug: str | None = None
        self._editing_collection_idx: int | None = None

    async def async_step_init(self, user_input=None):
        """Show the main menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_playlist",
                "edit_playlist",
                "delete_playlist",
                "import_playlists",
                "add_collection",
                "edit_collection",
                "delete_collection",
                "edit_system_prompt",
            ],
        )

    async def async_step_add_playlist(self, user_input=None):
        """Add a new playlist."""
        if user_input is not None:
            store = self.hass.data[DOMAIN]["store"]
            await store.async_save_playlist(
                user_input[CONF_PLAYLIST_NAME],
                {
                    "prompt": user_input[CONF_PLAYLIST_PROMPT],
                    "track_count": int(user_input.get(CONF_PLAYLIST_TRACK_COUNT, DEFAULT_TRACK_COUNT)),
                    "history_depth": int(user_input.get(CONF_PLAYLIST_HISTORY_DEPTH, DEFAULT_HISTORY_DEPTH)),
                    "refill_threshold": int(user_input.get(CONF_PLAYLIST_REFILL_THRESHOLD, DEFAULT_REFILL_THRESHOLD)),
                    "exclude_live": user_input.get(CONF_PLAYLIST_EXCLUDE_LIVE, False),
                },
            )
            await self._refresh_select_entities()
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="add_playlist",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PLAYLIST_NAME): selector.TextSelector(),
                    vol.Required(CONF_PLAYLIST_PROMPT): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                    vol.Optional(CONF_PLAYLIST_TRACK_COUNT, default=DEFAULT_TRACK_COUNT): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=3, max=50, mode="box")
                    ),
                    vol.Optional(CONF_PLAYLIST_HISTORY_DEPTH, default=DEFAULT_HISTORY_DEPTH): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=10, max=200, mode="box")
                    ),
                    vol.Optional(CONF_PLAYLIST_REFILL_THRESHOLD, default=DEFAULT_REFILL_THRESHOLD): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=1, max=10, mode="box")
                    ),
                    vol.Optional(CONF_PLAYLIST_EXCLUDE_LIVE, default=False): selector.BooleanSelector(),
                }
            ),
        )

    async def async_step_edit_playlist(self, user_input=None):
        """Select a playlist to edit."""
        store = self.hass.data[DOMAIN]["store"]
        playlists = store.get_all_playlists()

        if not playlists:
            return self.async_abort(reason="no_playlists")

        if user_input is not None:
            self._edit_slug = user_input["playlist"]
            return await self.async_step_edit_playlist_form()

        options = {slug: cfg["name"] for slug, cfg in playlists.items()}

        return self.async_show_form(
            step_id="edit_playlist",
            data_schema=vol.Schema(
                {
                    vol.Required("playlist"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=k, label=v)
                                for k, v in options.items()
                            ]
                        )
                    ),
                }
            ),
        )

    async def async_step_edit_playlist_form(self, user_input=None):
        """Edit the selected playlist."""
        store = self.hass.data[DOMAIN]["store"]
        playlists = store.get_all_playlists()
        current = playlists.get(self._edit_slug, {})

        if user_input is not None:
            await store.async_save_playlist(
                user_input.get(CONF_PLAYLIST_NAME, current.get("name", "")),
                {
                    "prompt": user_input[CONF_PLAYLIST_PROMPT],
                    "track_count": int(user_input.get(CONF_PLAYLIST_TRACK_COUNT, DEFAULT_TRACK_COUNT)),
                    "history_depth": int(user_input.get(CONF_PLAYLIST_HISTORY_DEPTH, DEFAULT_HISTORY_DEPTH)),
                    "refill_threshold": int(user_input.get(CONF_PLAYLIST_REFILL_THRESHOLD, DEFAULT_REFILL_THRESHOLD)),
                    "exclude_live": user_input.get(CONF_PLAYLIST_EXCLUDE_LIVE, False),
                },
            )
            # If name changed, remove old entry
            new_name = user_input.get(CONF_PLAYLIST_NAME, "")
            if new_name and store._playlist_slug(new_name) != self._edit_slug:
                await store.async_delete_playlist(current.get("name", ""))
            await self._refresh_select_entities()
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="edit_playlist_form",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PLAYLIST_NAME, default=current.get("name", "")): selector.TextSelector(),
                    vol.Required(CONF_PLAYLIST_PROMPT, default=current.get("prompt", "")): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                    vol.Optional(CONF_PLAYLIST_TRACK_COUNT, default=current.get("track_count", DEFAULT_TRACK_COUNT)): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=3, max=50, mode="box")
                    ),
                    vol.Optional(CONF_PLAYLIST_HISTORY_DEPTH, default=current.get("history_depth", DEFAULT_HISTORY_DEPTH)): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=10, max=200, mode="box")
                    ),
                    vol.Optional(CONF_PLAYLIST_REFILL_THRESHOLD, default=current.get("refill_threshold", DEFAULT_REFILL_THRESHOLD)): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=1, max=10, mode="box")
                    ),
                    vol.Optional(CONF_PLAYLIST_EXCLUDE_LIVE, default=current.get("exclude_live", False)): selector.BooleanSelector(),
                }
            ),
        )

    async def async_step_delete_playlist(self, user_input=None):
        """Delete a playlist."""
        store = self.hass.data[DOMAIN]["store"]
        playlists = store.get_all_playlists()

        if not playlists:
            return self.async_abort(reason="no_playlists")

        if user_input is not None:
            slug = user_input["playlist"]
            config = playlists.get(slug, {})
            await store.async_delete_playlist(config.get("name", slug))
            await self._refresh_select_entities()
            return self.async_create_entry(title="", data={})

        options = {slug: cfg["name"] for slug, cfg in playlists.items()}

        return self.async_show_form(
            step_id="delete_playlist",
            data_schema=vol.Schema(
                {
                    vol.Required("playlist"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=k, label=v)
                                for k, v in options.items()
                            ]
                        )
                    ),
                }
            ),
        )

    async def async_step_import_playlists(self, user_input=None):
        """Import playlists from YAML."""
        if user_input is not None:
            store = self.hass.data[DOMAIN]["store"]
            count = await store.async_import_playlists(user_input["yaml_data"])
            _LOGGER.info("Imported %d playlists", count)
            await self._refresh_select_entities()
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="import_playlists",
            data_schema=vol.Schema(
                {
                    vol.Required("yaml_data"): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                }
            ),
        )

    async def async_step_add_collection(self, user_input=None):
        """Add a new collection."""
        if user_input is not None:
            name = user_input[CONF_COLLECTION_NAME].strip()
            tags_raw = user_input.get(CONF_COLLECTION_TAGS, "")
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

            collections = list(self.config_entry.options.get(CONF_COLLECTIONS, []))
            collections.append({CONF_COLLECTION_NAME: name, CONF_COLLECTION_TAGS: tags})

            new_options = dict(self.config_entry.options)
            new_options[CONF_COLLECTIONS] = collections
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=new_options
            )

            # Reload entry to create the new select entity
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(data=new_options)

        return self.async_show_form(
            step_id="add_collection",
            data_schema=vol.Schema({
                vol.Required(CONF_COLLECTION_NAME): selector.TextSelector(),
                vol.Required(CONF_COLLECTION_TAGS): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=False)
                ),
            }),
            description_placeholders={"tag_hint": "Comma-separated, e.g.: Genre, Classical"},
        )

    async def async_step_edit_collection(self, user_input=None):
        """Select a collection to edit."""
        collections = self.config_entry.options.get(CONF_COLLECTIONS, [])
        if not collections:
            return self.async_abort(reason="no_collections")

        if user_input is not None:
            self._editing_collection_idx = int(user_input["collection_index"])
            return await self.async_step_edit_collection_form()

        collection_names = {str(i): cfg[CONF_COLLECTION_NAME] for i, cfg in enumerate(collections)}
        return self.async_show_form(
            step_id="edit_collection",
            data_schema=vol.Schema({
                vol.Required("collection_index"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=[
                        selector.SelectOptionDict(value=k, label=v) for k, v in collection_names.items()
                    ])
                ),
            }),
        )

    async def async_step_edit_collection_form(self, user_input=None):
        """Edit a selected collection."""
        collections = list(self.config_entry.options.get(CONF_COLLECTIONS, []))
        if self._editing_collection_idx is None or self._editing_collection_idx >= len(collections):
            return self.async_abort(reason="no_collections")
        current = collections[self._editing_collection_idx]

        if user_input is not None:
            name = user_input[CONF_COLLECTION_NAME].strip()
            tags_raw = user_input.get(CONF_COLLECTION_TAGS, "")
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

            collections[self._editing_collection_idx] = {CONF_COLLECTION_NAME: name, CONF_COLLECTION_TAGS: tags}

            new_options = dict(self.config_entry.options)
            new_options[CONF_COLLECTIONS] = collections
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=new_options
            )
            await self._refresh_select_entities()
            return self.async_create_entry(data=new_options)

        return self.async_show_form(
            step_id="edit_collection_form",
            data_schema=vol.Schema({
                vol.Required(CONF_COLLECTION_NAME, default=current[CONF_COLLECTION_NAME]): selector.TextSelector(),
                vol.Required(CONF_COLLECTION_TAGS, default=", ".join(current.get(CONF_COLLECTION_TAGS, []))): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=False)
                ),
            }),
        )

    async def async_step_delete_collection(self, user_input=None):
        """Delete a collection."""
        collections = list(self.config_entry.options.get(CONF_COLLECTIONS, []))
        if not collections:
            return self.async_abort(reason="no_collections")

        if user_input is not None:
            idx = int(user_input["collection_index"])
            if idx >= len(collections):
                return self.async_abort(reason="no_collections")
            collections.pop(idx)

            new_options = dict(self.config_entry.options)
            new_options[CONF_COLLECTIONS] = collections
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=new_options
            )
            # Reload entry to remove the select entity
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(data=new_options)

        collection_names = {str(i): cfg[CONF_COLLECTION_NAME] for i, cfg in enumerate(collections)}
        return self.async_show_form(
            step_id="delete_collection",
            data_schema=vol.Schema({
                vol.Required("collection_index"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=[
                        selector.SelectOptionDict(value=k, label=v) for k, v in collection_names.items()
                    ])
                ),
            }),
        )

    async def async_step_edit_system_prompt(self, user_input=None):
        """Edit the system prompt used for AI track generation."""
        if user_input is not None:
            new_options = dict(self.config_entry.options)
            new_options[CONF_SYSTEM_PROMPT] = user_input[CONF_SYSTEM_PROMPT]
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=new_options
            )
            return self.async_create_entry(data=new_options)

        current_prompt = self.config_entry.options.get(
            CONF_SYSTEM_PROMPT, SYSTEM_PROMPT
        )

        return self.async_show_form(
            step_id="edit_system_prompt",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SYSTEM_PROMPT, default=current_prompt
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                }
            ),
        )

    async def _refresh_select_entities(self):
        """Refresh options on all select entities after store/config changes."""
        select_entities = self.hass.data.get(DOMAIN, {}).get("select_entities", [])
        for entity in select_entities:
            entity.refresh_options()
