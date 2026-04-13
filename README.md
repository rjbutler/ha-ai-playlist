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

### Playlist Collections

A collection is a named group of playlists filtered by tags, exposed as a Home Assistant `select` entity. Use collections to organize playlists into categories for dashboards, physical controls, or automations.

**Why use collections?** If you have dozens of playlists, you probably don't want a single massive dropdown. Collections let you group playlists by tag (e.g., "Genre", "Mood", "Classical") so each dropdown shows only the relevant subset.

**How they work:** Create a collection in the options flow by giving it a name and comma-separated tags. The integration creates a `select.ai_playlist_*` entity whose options are all playlists matching those tags. When a user picks a playlist from the dropdown, it stages that selection — call `ai_playlist.play` (without a `playlist` argument) to start playback.

**Important:** Changing the dropdown selection only stages the playlist. It does not automatically start playback. You need a separate trigger (button press, automation, script) that calls `ai_playlist.play`.

| Setting | Description |
|---|---|
| Name | Display name (becomes the entity name) |
| Tags | Comma-separated tags — the dropdown shows playlists matching ALL tags |

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
| `collection` | no | Collection name for state tracking |

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
| `collection` | no | Collection name |

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

## Examples

### Play a playlist on a speaker

The simplest use — play a named playlist on a specific media player:

```yaml
# Script: Morning music
sequence:
  - action: ai_playlist.play
    data:
      entity_id: media_player.kitchen_speaker
      playlist: "Baroque Instrumental"
      clear_queue: true
```

### Dashboard: collection dropdown + play button

Use a collection select entity as a dashboard dropdown, paired with a script that plays whatever is selected:

```yaml
# Script: Play selected playlist
sequence:
  - variables:
      playlist_name: "{{ states('select.ai_playlist_genre_playlists') }}"
  - action: ai_playlist.play
    data:
      entity_id: media_player.office_speaker
      playlist: "{{ playlist_name }}"
      collection: "Genre Playlists"
      clear_queue: true
```

Add the select entity and a button to your dashboard:

```yaml
# Dashboard card (minimal example)
type: entities
entities:
  - entity: select.ai_playlist_genre_playlists
  - type: button
    name: Play
    tap_action:
      action: perform-action
      perform_action: script.play_selected_playlist
```

### Random pick from a collection

Pick a random playlist from a collection and play it — useful for physical buttons or "surprise me" automations:

```yaml
# Script: Play random playlist from a collection
sequence:
  - variables:
      options: "{{ state_attr('select.ai_playlist_genre_playlists', 'options') | list }}"
      random_pick: "{{ options | random }}"
  - action: ai_playlist.select
    data:
      entity_id: media_player.office_speaker
      playlist: "{{ random_pick }}"
      collection: "Genre Playlists"
  - action: ai_playlist.play
    data:
      entity_id: media_player.office_speaker
      clear_queue: true
```

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
