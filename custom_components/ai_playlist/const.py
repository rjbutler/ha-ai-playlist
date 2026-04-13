"""Constants for the AI Playlist integration."""
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "ai_playlist"

# Defaults
DEFAULT_TRACK_COUNT: Final = 10
DEFAULT_HISTORY_DEPTH: Final = 50
DEFAULT_REFILL_THRESHOLD: Final = 2

# Coordinator states
STATE_IDLE: Final = "idle"
STATE_GENERATING: Final = "generating"
STATE_ENQUEUING: Final = "enqueuing"
STATE_PLAYING: Final = "playing"
STATE_REFILLING: Final = "refilling"
STATE_ERROR: Final = "error"

# Storage keys
STORAGE_KEY_PLAYLISTS: Final = "ai_playlist.playlists"
STORAGE_KEY_SESSIONS: Final = "ai_playlist.sessions"
STORAGE_VERSION: Final = 1

# Config flow keys
CONF_AI_ENTITY: Final = "ai_entity"
CONF_SYSTEM_PROMPT: Final = "system_prompt"

# Playlist config keys
CONF_PLAYLIST_NAME: Final = "name"
CONF_PLAYLIST_PROMPT: Final = "prompt"
CONF_PLAYLIST_TRACK_COUNT: Final = "track_count"
CONF_PLAYLIST_HISTORY_DEPTH: Final = "history_depth"
CONF_PLAYLIST_REFILL_THRESHOLD: Final = "refill_threshold"
CONF_PLAYLIST_EXCLUDE_LIVE: Final = "exclude_live"

# Platforms
PLATFORMS: Final = [Platform.SELECT, Platform.SENSOR]

# Config keys for collections
CONF_COLLECTIONS: Final = "collections"
CONF_COLLECTION_NAME: Final = "name"
CONF_COLLECTION_TAGS: Final = "tags"

SYSTEM_PROMPT: Final = """You are a music playlist curator. Generate a list of tracks based on the user's request.

Output format: A JSON array of objects, each with "artist", "title", and optionally "album":
[{"artist": "Artist Name", "title": "Track Title", "album": "Album Name"}]

The album is optional but preferred when you're confident of it.

Rules:
1. EXCLUSION: Never include any track from the exclusion list.
2. UNIQUENESS: Every track must be unique — no duplicate artists in a row.
3. QUALITY: Only suggest real, well-known recordings. No made-up tracks.
4. DIVERSITY: Mix across different artists. No more than 2 tracks per artist.
5. ORDERING: Vary the energy and mood — don't front-load or cluster similar tracks.
6. OUTPUT ONLY: Return only the JSON array. No commentary, numbering, or explanations."""
