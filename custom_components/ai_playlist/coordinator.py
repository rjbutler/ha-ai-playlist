"""Playlist coordinator — manages the lifecycle of one active playlist session."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_AI_ENTITY,
    CONF_SYSTEM_PROMPT,
    DEFAULT_REFILL_THRESHOLD,
    DEFAULT_TRACK_COUNT,
    DOMAIN,
    STATE_ENQUEUING,
    STATE_ERROR,
    STATE_GENERATING,
    STATE_IDLE,
    STATE_PLAYING,
    STATE_REFILLING,
    SYSTEM_PROMPT,
)
from .sensor import get_sensor
from .store import PlaylistStore
from .track_processing import (
    filter_tracks,
    parse_ai_response,
    split_track,
    strip_album,
    track_dict_to_string,
)

_LOGGER = logging.getLogger(__name__)


async def generate_tracks(
    hass: HomeAssistant,
    ai_entity_id: str,
    system_prompt: str,
    playlist_config: dict,
    history: list[str],
    enqueued: list[str],
    track_count: int,
) -> list[dict]:
    """Call the AI task entity, parse the response, and return filtered tracks.

    Pure query — no side effects on coordinator state, history, or queues.
    Raises HomeAssistantError on AI failure, unparseable response, or if all
    tracks are filtered out as duplicates.

    Returns: list of {"artist": str, "title": str, "album": str} dicts.
    """
    from homeassistant.exceptions import HomeAssistantError

    playlist_name = playlist_config.get("name", "")
    exclude_live = playlist_config.get("exclude_live", False)

    # Build user prompt (same structure as _build_user_prompt)
    parts = [playlist_config.get("prompt", ""), f"\nGenerate {track_count} tracks."]
    exclusion = [*history, *enqueued]
    if exclusion:
        parts.append("\nDo not include any of these tracks:\n" + "\n".join(exclusion))
    user_prompt = "\n".join(parts)

    try:
        response = await hass.services.async_call(
            "ai_task",
            "generate_data",
            {
                "task_name": "ai_playlist_generate",
                "instructions": f"{system_prompt}\n\n{user_prompt}",
                "entity_id": ai_entity_id,
            },
            blocking=True,
            return_response=True,
        )
    except Exception as err:
        _LOGGER.exception("AI generation failed for '%s'", playlist_name)
        raise HomeAssistantError(f"AI generation failed: {err}") from err

    raw_text = ""
    if isinstance(response, dict):
        raw_text = response.get("data", "")
    elif hasattr(response, "data"):
        raw_text = response.data or ""

    parsed = parse_ai_response(raw_text)
    if not parsed:
        raise HomeAssistantError(
            f"AI returned no parseable tracks for '{playlist_name}'"
        )

    # Map filtered strings back to dicts
    string_to_dict: dict[str, dict] = {}
    for t in parsed:
        key = track_dict_to_string(t)
        if key not in string_to_dict:
            string_to_dict[key] = t

    filtered = filter_tracks(
        [track_dict_to_string(t) for t in parsed],
        history=history,
        enqueued=enqueued,
        exclude_live=exclude_live,
    )

    valid = [string_to_dict[s] for s in filtered["valid"]]
    if not valid:
        raise HomeAssistantError(
            f"All {len(parsed)} tracks filtered as duplicates for '{playlist_name}'"
        )

    _LOGGER.info(
        "generate_tracks: %d valid, %d filtered for '%s'",
        len(valid), len(filtered["duplicates"]), playlist_name,
    )
    return valid


class PlaylistCoordinator:
    """Manages one active AI playlist session on a single media player."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: PlaylistStore,
        playlist_config: dict,
        entity_id: str,
        entry: ConfigEntry,
    ) -> None:
        self.hass = hass
        self.store = store
        self.playlist_config = playlist_config
        self.entity_id = entity_id
        self.entry = entry
        self.ai_entity_id: str = entry.data.get(CONF_AI_ENTITY, "")

        self.state: str = STATE_IDLE
        self.enqueued_tracks: list[str] = []
        self.playlist_name: str = playlist_config.get("name", "")
        self._unsub_state_listener: CALLBACK_TYPE | None = None
        self._unsub_queue_listener: CALLBACK_TYPE | None = None
        self._generating: bool = False
        self._last_queue_check: float = 0.0
        self._initial_enqueue_count: int = 0
        self._queue_id: str | None = None

    @property
    def track_count(self) -> int:
        return self.playlist_config.get("track_count", DEFAULT_TRACK_COUNT)

    @property
    def refill_threshold(self) -> int:
        return self.playlist_config.get("refill_threshold", DEFAULT_REFILL_THRESHOLD)

    @property
    def exclude_live(self) -> bool:
        return self.playlist_config.get("exclude_live", False)

    @property
    def system_prompt(self) -> str:
        return self.entry.options.get(CONF_SYSTEM_PROMPT, SYSTEM_PROMPT)

    def _get_mass_client(self):
        """Get the Music Assistant client from the core integration."""
        for entry in self.hass.config_entries.async_entries("music_assistant"):
            if entry.state is ConfigEntryState.LOADED and hasattr(entry, "runtime_data"):
                return entry.runtime_data.mass
        return None

    def _resolve_queue_id(self) -> str | None:
        """Resolve the MA queue_id for our media player entity."""
        state = self.hass.states.get(self.entity_id)
        if state:
            # MA stores queue_id in the active_queue attribute
            queue_id = state.attributes.get("active_queue")
            if queue_id:
                return queue_id
        return None

    async def async_start(
        self, clear_queue: bool = True, track_count: int | None = None
    ) -> None:
        """Start the playlist session: generate/restore, enqueue, monitor."""
        count = track_count or self.track_count

        # Suppress state change handling during initial setup
        self._generating = True
        try:
            # Check for cached tracks first
            cached = await self.hass.async_add_executor_job(
                self.store.get_cache, self.playlist_name
            )
            if cached:
                _LOGGER.info(
                    "Restoring %d cached tracks for '%s'", len(cached), self.playlist_name
                )
                # Cache stores strings — convert to dicts for _enqueue_tracks
                cached_dicts = []
                for s in cached:
                    artist, title = split_track(s)
                    _, album = strip_album(s)
                    if artist and title:
                        cached_dicts.append({"artist": artist, "title": title, "album": album})
                if cached_dicts:
                    await self._enqueue_tracks(cached_dicts, clear_first=clear_queue)
            else:
                await self._generate_and_enqueue(count, clear_first=clear_queue)
        finally:
            self._generating = False

        self._initial_enqueue_count = len(self.enqueued_tracks)

        # Attach state listener for refill checks and history recording
        self.state = STATE_PLAYING
        self._unsub_state_listener = async_track_state_change_event(
            self.hass, self.entity_id, self._on_state_change
        )

        # Subscribe to MA queue events for detach detection
        self._subscribe_to_queue_events()

    def _subscribe_to_queue_events(self) -> None:
        """Subscribe to Music Assistant queue events via the MA client."""
        mass = self._get_mass_client()
        if not mass:
            _LOGGER.warning(
                "Music Assistant client not available — "
                "falling back to state-based detach for '%s'",
                self.playlist_name,
            )
            return

        self._queue_id = self._resolve_queue_id()
        if not self._queue_id:
            _LOGGER.warning(
                "Could not resolve queue_id for %s — "
                "falling back to state-based detach",
                self.entity_id,
            )
            return

        from music_assistant_models.enums import EventType

        def _on_queue_updated(event) -> None:
            """Handle MA QUEUE_UPDATED events."""
            data = event.data
            # event.data may be a PlayerQueue object or dict
            if hasattr(data, "to_dict"):
                data = data.to_dict()
            elif hasattr(data, "__dict__") and not isinstance(data, dict):
                data = vars(data)
            data = data or {}
            queue_id = data.get("queue_id", event.object_id)
            if queue_id != self._queue_id:
                return
            self.hass.async_create_task(self._async_handle_queue_update(data))

        self._unsub_queue_listener = mass.subscribe(
            _on_queue_updated, EventType.QUEUE_UPDATED
        )
        _LOGGER.info(
            "Subscribed to MA queue events for %s (queue_id=%s)",
            self.entity_id,
            self._queue_id,
        )

    async def _async_handle_queue_update(self, data: dict) -> None:
        """Process a queue update from Music Assistant."""
        if self._generating or self.state in (STATE_GENERATING, STATE_ENQUEUING):
            return

        shuffle = data.get("shuffle_enabled", False)
        repeat = data.get("repeat_mode", "off")
        if isinstance(repeat, str):
            repeat_on = repeat not in ("off",)
        else:
            # Enum — check .value or truthiness
            repeat_on = str(repeat) not in ("off", "RepeatMode.OFF")

        if shuffle or repeat_on:
            _LOGGER.info(
                "Detaching from %s — shuffle=%s, repeat=%s (queue event)",
                self.entity_id,
                shuffle,
                repeat,
            )
            await self._detach()
            return

        # Queue cleared to 0 = genuine stop or external takeover
        items = data.get("items", -1)
        if items == 0:
            _LOGGER.info(
                "Detaching from %s — queue cleared to 0 (queue event)",
                self.entity_id,
            )
            await self._detach()

    async def _generate_and_enqueue(
        self, track_count: int, clear_first: bool
    ) -> None:
        """Generate tracks via AI and enqueue them."""
        from homeassistant.exceptions import HomeAssistantError

        self.state = STATE_GENERATING

        history = await self.hass.async_add_executor_job(
            self.store.get_history, self.playlist_name
        )

        try:
            valid_dicts = await generate_tracks(
                hass=self.hass,
                ai_entity_id=self.ai_entity_id,
                system_prompt=self.system_prompt,
                playlist_config=self.playlist_config,
                history=history,
                enqueued=self.enqueued_tracks,
                track_count=track_count,
            )
        except HomeAssistantError:
            self.state = STATE_ERROR
            return

        await self._enqueue_tracks(valid_dicts, clear_first=clear_first)

    async def _enqueue_tracks(self, tracks: list[dict], clear_first: bool) -> None:
        """Enqueue tracks to Music Assistant."""
        self.state = STATE_ENQUEUING

        player_state = self.hass.states.get(self.entity_id)
        is_playing = player_state and player_state.state == "playing"

        for i, track in enumerate(tracks):
            artist = track["artist"]
            title = track["title"]
            album = track.get("album", "")

            if clear_first and i == 0:
                enqueue = "replace"
            else:
                enqueue = "add"

            try:
                await self.hass.services.async_call(
                    "music_assistant",
                    "play_media",
                    {
                        "media_id": title,
                        "media_type": "track",
                        "artist": artist,
                        "album": album,
                        "enqueue": enqueue,
                    },
                    target={"entity_id": self.entity_id},
                    blocking=True,
                )
                self.enqueued_tracks.append(track_dict_to_string(track))
            except Exception:
                _LOGGER.warning("Failed to enqueue track: %s - %s", artist, title)

    def _build_user_prompt(
        self, history: list[str], enqueued: list[str]
    ) -> str:
        """Build the user prompt with exclusion list."""
        parts = [self.playlist_config.get("prompt", "")]
        parts.append(f"\nGenerate {self.track_count} tracks.")

        exclusion = [*history, *enqueued]
        if exclusion:
            exclusion_text = "\n".join(exclusion)
            parts.append(
                f"\nDo not include any of these tracks:\n{exclusion_text}"
            )

        return "\n".join(parts)

    async def _check_queue_depth(self) -> tuple[int, int]:
        """Get queue item count and current index from Music Assistant."""
        try:
            response = await self.hass.services.async_call(
                "music_assistant",
                "get_queue",
                {},
                target={"entity_id": self.entity_id},
                blocking=True,
                return_response=True,
            )
        except Exception:
            _LOGGER.warning("Failed to get queue for %s", self.entity_id)
            return (0, -1)

        if not response or not isinstance(response, dict):
            return (0, -1)

        queue_data = response.get(self.entity_id, {})
        if isinstance(queue_data, dict):
            items = queue_data.get("items", 0)
            current_index = queue_data.get("current_index", -1)
            if current_index is None:
                current_index = -1
        else:
            # Fallback: try attribute access on queue object
            items = getattr(queue_data, "items", 0)
            current_index = getattr(queue_data, "current_index", -1)
            if items is None:
                items = 0
            if current_index is None:
                current_index = -1
            if not isinstance(items, int) or not isinstance(current_index, int):
                _LOGGER.warning(
                    "Unexpected queue data type for %s: %s", self.entity_id, type(queue_data)
                )
                return (0, -1)

        return (items, current_index)

    @callback
    def _on_state_change(self, event: Event) -> None:
        """Handle media player state changes."""
        self.hass.async_create_task(self._async_handle_state_change(event))

    async def _async_handle_state_change(self, event: Event) -> None:
        """Process state change — history recording and refill checks only.

        Detach detection is handled by MA queue event subscription.
        """
        if self._generating or self.state in (STATE_GENERATING, STATE_ENQUEUING):
            return

        new_state = event.data.get("new_state")
        if not new_state:
            return

        player_state = new_state.state
        attrs = new_state.attributes
        media_title = attrs.get("media_title")

        # Record track to history
        media_artist = attrs.get("media_artist", "")
        if media_title and media_artist and player_state == "playing":
            track_str = f"{media_artist} - {media_title}"
            await self.hass.async_add_executor_job(
                self.store.add_to_history, self.playlist_name, track_str
            )

        # Debounce queue depth checks — at most once per 5 seconds
        import time
        now = time.monotonic()
        if now - self._last_queue_check < 5.0:
            return
        self._last_queue_check = now

        # Check if refill needed
        if self._generating:
            return
        queue_items, current_index = await self._check_queue_depth()
        if player_state == "playing" and queue_items > 0 and current_index >= 0:
            items_after = queue_items - current_index - 1
            if items_after < self.refill_threshold and current_index > 0:
                if self._generating:
                    return
                self._generating = True
                try:
                    await self._refill()
                finally:
                    self._generating = False

    async def _refill(self) -> None:
        """Generate and enqueue more tracks. Caller must set _generating=True."""
        self.state = STATE_REFILLING
        await self._generate_and_enqueue(self.track_count, clear_first=False)
        self.state = STATE_PLAYING

    async def _assess_resurrection_confidence(self) -> str:
        """Check whether an active session on this player can be resurrected.

        Returns 'high' (current track matches history/cache), 'low' (queue has
        items but can't verify), or 'none' (player off or queue empty).
        """
        state = self.hass.states.get(self.entity_id)
        if not state or state.state in ("off", "unavailable", "unknown"):
            return "none"

        attrs = state.attributes

        # Must have an active MA queue
        if not attrs.get("active_queue"):
            return "none"

        # Queue must have items
        queue_items, _ = await self._check_queue_depth()
        if queue_items == 0:
            return "none"

        # Check whether the current track appears in our records
        media_title = attrs.get("media_title", "")
        media_artist = attrs.get("media_artist", "")
        if not (media_title and media_artist):
            return "low"

        from .track_processing import normalize_track

        current_norm = normalize_track(f"{media_artist} - {media_title}")
        if not current_norm:
            return "low"

        history = await self.hass.async_add_executor_job(
            self.store.get_history, self.playlist_name
        )
        history_norms = {normalize_track(t) for t in history if normalize_track(t)}
        if current_norm in history_norms:
            return "high"

        cache = await self.hass.async_add_executor_job(
            self.store.get_cache_peek, self.playlist_name
        )
        cache_norms = {normalize_track(t) for t in cache if normalize_track(t)}
        if current_norm in cache_norms:
            return "high"

        return "low"

    async def async_resume_after_restart(self) -> None:
        """Attach to an already-playing MA queue after HA restart.

        Skips track generation — just wires up listeners so refill and history
        recording work as if the coordinator had been running all along.
        """
        # Pre-populate enqueued_tracks from cache so refill exclusion works
        cached = await self.hass.async_add_executor_job(
            self.store.get_cache_peek, self.playlist_name
        )
        self.enqueued_tracks = list(cached) if cached else []

        self.state = STATE_PLAYING

        self._unsub_state_listener = async_track_state_change_event(
            self.hass, self.entity_id, self._on_state_change
        )
        self._subscribe_to_queue_events()

    async def _detach(self, preserve_session: bool = False) -> None:
        """Detach from the media player, caching enqueued tracks.

        Saves all enqueued tracks to cache. On restore, get_cache() filters
        out anything already in history, so only truly unplayed tracks survive.

        preserve_session: if True, don't clear the active session record (used
        during HA shutdown so the session can be resurrected on next start).
        """
        if self._unsub_state_listener:
            self._unsub_state_listener()
            self._unsub_state_listener = None
        if self._unsub_queue_listener:
            self._unsub_queue_listener()
            self._unsub_queue_listener = None

        if self.enqueued_tracks:
            await self.hass.async_add_executor_job(
                self.store.save_cache, self.playlist_name, list(self.enqueued_tracks)
            )
            _LOGGER.info(
                "Cached %d enqueued tracks for '%s'",
                len(self.enqueued_tracks),
                self.playlist_name,
            )

        self.enqueued_tracks = []

        if not preserve_session:
            await self.store.clear_active_session(self.entity_id)

        sensor = get_sensor(self.hass, self.entity_id)
        if sensor:
            sensor.update_idle()

        self.state = STATE_IDLE

        # Remove from coordinator registry
        coordinators = self.hass.data.get(DOMAIN, {}).get("coordinators", {})
        if coordinators.get(self.entity_id) is self:
            del coordinators[self.entity_id]

    async def async_stop(self) -> None:
        """Stop the coordinator (called by service or unload)."""
        await self._detach(preserve_session=False)

    async def async_shutdown(self) -> None:
        """Shut down coordinator during HA unload, preserving session for resurrection."""
        await self._detach(preserve_session=True)
