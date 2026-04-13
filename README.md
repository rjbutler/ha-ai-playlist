# AI Playlist

AI-powered playlist generation for Home Assistant + Music Assistant.

Uses any AI Task provider (Anthropic, OpenAI, Google, Ollama, etc.) to generate tracks based on text prompts, enqueue them on Music Assistant speakers, and automatically refill as you listen.

## Prerequisites

- **Home Assistant 2025.8+** (required for AI Task integration)
- **[Music Assistant](https://music-assistant.io/)** integration installed and configured
- **An AI Task entity** — configure an AI integration (e.g., Anthropic, OpenAI, Ollama) and enable its AI Task sub-entry

## Installation

1. Open HACS in Home Assistant
2. Click the three-dot menu (top right) → **Custom repositories**
3. Add repository URL: `https://gitea.northridgetech.com/rjbutler/ha-ai-playlist`
4. Category: **Integration**
5. Click **Add**, then find "AI Playlist" in HACS and click **Download**
6. Restart Home Assistant

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "AI Playlist"
3. Select your AI Task entity (e.g., `ai_task.claude_ai_task_sonnet`)

## Configuration

Open the integration's options (three-dot menu → **Configure**) to manage:

### Playlists

Named prompt + settings combinations. Each playlist has:

| Setting | Default | Description |
|---|---|---|
| Name | — | Display name (used in service calls) |
| Prompt | — | Instructions for the AI (e.g., "Upbeat jazz fusion, heavy on the Rhodes piano") |
| Track count | 10 | Tracks generated per AI call |
| History depth | 50 | Tracks remembered to avoid repeats |
| Refill threshold | 2 | Remaining tracks before auto-refill triggers |
| Exclude live | off | Filter out live recordings |

You can also **import playlists from YAML** — paste a YAML list with `name` and `prompt` fields.

### Lists

Tag-filtered select entities. Create a list by giving it a name and comma-separated tags. The integration creates a `select.ai_playlist_*` entity populated with all playlists matching those tags. Useful for dashboards — users pick from a dropdown, then a script calls `ai_playlist.play`.

### System Prompt

The instructions sent to the AI for track generation. The default prompt enforces output format (`Artist - Title | Album`), uniqueness rules, and diversity constraints. Customize it from the options flow — but keep the output format requirement, as the integration's parser depends on it.

## Services

### `ai_playlist.play`

Start an AI-generated playlist on a media player.

| Field | Required | Description |
|---|---|---|
| `entity_id` | yes | Media player to play on |
| `playlist` | no | Name of a configured playlist |
| `prompt` | no | Ad-hoc prompt (used if no playlist specified) |
| `track_count` | no | Override tracks per generation |
| `clear_queue` | no | Clear existing queue first (default: true) |
| `list` | no | List name for state tracking |

If neither `playlist` nor `prompt` is provided, plays the currently selected playlist (set via `ai_playlist.select`).

### `ai_playlist.stop`

Stop managing a media player. Caches unplayed tracks for next session.

| Field | Required | Description |
|---|---|---|
| `entity_id` | yes | Media player to stop managing |

### `ai_playlist.select`

Set the "up next" playlist for a media player without starting playback.

| Field | Required | Description |
|---|---|---|
| `entity_id` | yes | Media player |
| `playlist` | yes | Playlist name to select |
| `list` | no | List/category name |

### `ai_playlist.clear_history`

Reset track history for a playlist (allows previously played tracks to be generated again).

| Field | Required | Description |
|---|---|---|
| `playlist` | yes | Playlist name |

### `ai_playlist.list_playlists`

Returns all configured playlists. Supports response data.

| Field | Required | Description |
|---|---|---|
| `tag` | no | Filter by single tag |
| `tags` | no | Filter by multiple tags (AND logic) |

## How It Works

1. **Generate** — Sends your prompt + exclusion list to the AI Task entity
2. **Parse** — Extracts `Artist - Title | Album` lines from the response
3. **Filter** — Removes duplicates against history and current queue
4. **Enqueue** — Sends tracks to Music Assistant via `play_media`
5. **Monitor** — Watches queue depth and auto-refills when tracks are running low
6. **Detach** — Stops managing if shuffle/repeat is enabled or queue is cleared externally

Sessions survive HA restarts — the integration resurrects active coordinators on startup.

## License

MIT
