"""Track processing utilities for AI Playlist integration.

Pure functions for normalizing, parsing, and filtering tracks.
No Home Assistant dependencies — fully testable standalone.
"""
import json
import logging as _logging
import re

_logger = _logging.getLogger(__name__)


def normalize_track(track: str | None) -> str:
    """Normalize a track string for dedup comparison.

    Steps: lowercase, strip album (after |), remove remaster/live/version suffixes,
    replace & with and, normalize dashes and whitespace.
    """
    if not track:
        return ""

    # Strip album portion (after |) before normalizing
    if "|" in track:
        track = track.split("|", 1)[0].strip()

    normalized = track.lower()

    # Normalize non-breaking spaces
    normalized = normalized.replace("\u00a0", " ")

    # Remove quotes
    normalized = normalized.replace('"', "").replace("'", "")

    # Normalize various dash characters to simple hyphen
    normalized = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2212]", "-", normalized)

    # Normalize spacing around hyphen separator
    normalized = re.sub(r"\s*-\s*", " - ", normalized)

    # Remove common trailing parenthetical/bracketed suffixes
    normalized = re.sub(
        r"\s*[\(\[](?:\d{4}\s+)?(?:single version|single|remastered|remaster|"
        r"album version|album|original|live|mix|deluxe|edition)[^\)\]]*[\)\]]\s*$",
        "",
        normalized,
        flags=re.IGNORECASE,
    )

    # Replace & with and
    normalized = normalized.replace("&", "and")

    # Collapse whitespace
    normalized = re.sub(r"\s+", " ", normalized)

    return normalized.strip()


def strip_album(track: str) -> tuple[str, str]:
    """Strip album portion from a track string.

    Returns (track_without_album, album) where album may be empty.
    """
    if not track:
        return ("", "")
    if "|" in track:
        parts = track.split("|", 1)
        return (parts[0].strip(), parts[1].strip())
    return (track.strip(), "")


def track_dict_to_string(track: dict) -> str:
    """Convert a track dict to 'Artist - Title | Album' string format."""
    artist = track.get("artist", "").strip()
    title = track.get("title", "").strip()
    album = track.get("album", "").strip()
    base = f"{artist} - {title}"
    if album:
        return f"{base} | {album}"
    return base


def _item_field(obj, key):
    """Read a field from an MA queue item in object or dict form."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def queue_item_to_track_string(item) -> str:
    """Convert an MA QueueItem (duck-typed object or event-payload dict) to
    'Artist - Title' format.

    Prefers media_item.artists[0].name + media_item.name; falls back to the
    queue item's display name. Returns '' if no usable name can be built.
    """
    if item is None:
        return ""
    media_item = _item_field(item, "media_item")
    if media_item is not None:
        artists = _item_field(media_item, "artists") or []
        title = _item_field(media_item, "name") or ""
        if artists and title:
            artist = _item_field(artists[0], "name") or ""
            if artist:
                return f"{artist} - {title}"
    return _item_field(item, "name") or ""


_LIVE_MARKER = re.compile(r"[\(\[][^\)\]]*\blive\b|\s-\s.*\blive\b", re.IGNORECASE)


# Live album names: "Live at Leeds", "Unplugged (Live)", "Rust Never Sleeps - Live".
# A bare leading "Live" is not matched ("Live and Let Die" is a studio album).
_LIVE_ALBUM_MARKER = re.compile(
    r"[\(\[][^\)\]]*\blive\b|\blive\s+(?:at|in|from|on)\b|\s-\s.*\blive\b|\blive\s*$",
    re.IGNORECASE,
)


def _is_live_title(title: str) -> bool:
    """Whether a raw title marks a live recording, e.g. 'Song (Live at X)'."""
    return bool(title) and bool(_LIVE_MARKER.search(title))


_LIVE_WORD = re.compile(r"\blive\b", re.IGNORECASE)


def _is_live_result(result: dict) -> bool:
    """Whether an MA search result is a live recording.

    MA's provider parsers move "(Live ...)" out of the name into `version`
    (e.g. name "Teach Your Children", version "Live"), so `version` is the
    primary signal; the name and album name are checked too.
    """
    album = result.get("album") or {}
    if not isinstance(album, dict):
        album = {}
    for version in (result.get("version"), album.get("version")):
        if version and _LIVE_WORD.search(str(version)):
            return True
    if _is_live_title(result.get("name", "")):
        return True
    album_name = album.get("name", "")
    return bool(album_name) and bool(_LIVE_ALBUM_MARKER.search(album_name))


def pick_track_uri(
    results: list[dict], track: dict, exclude_live: bool = False
) -> tuple[str | None, bool]:
    """Pick the best-matching track URI from music_assistant.search results.

    Returns (uri, confident). Preference: artist+title match (album match
    breaks ties), then artist-only, then title-only, then the first result —
    the last mirrors what MA's own name lookup would have played. Within a
    tier, a live version ranks below a studio one unless the request itself
    is live (normalize_track strips "(Live ...)", so it can't tell them
    apart). With exclude_live and a studio request, live results (by title or
    album) are dropped outright — ranking alone let a live take win whenever
    a thin search had no same-tier studio result.

    Returns (uri, cacheable): cacheable is True only when the artist matched
    and the pick's live-ness matches the request, so the shared URI cache
    never serves a live take for a studio request (or a title-only/fallback
    pick). Returns (None, False) if no usable result carries a URI.
    """
    candidates = [r for r in results if isinstance(r, dict) and r.get("uri")]
    want_live = _is_live_title(track.get("title", ""))
    if exclude_live and not want_live:
        candidates = [r for r in candidates if not _is_live_result(r)]
    if not candidates:
        return None, False

    want_artist = normalize_track(track.get("artist", ""))
    want_title = normalize_track(track.get("title", ""))
    want_album = normalize_track(track.get("album", ""))

    def fields(result: dict) -> tuple[str, str, str]:
        artists = result.get("artists") or []
        artist = artists[0].get("name", "") if artists and isinstance(artists[0], dict) else ""
        album = result.get("album") or {}
        album_name = album.get("name", "") if isinstance(album, dict) else ""
        return (
            normalize_track(artist),
            normalize_track(result.get("name", "")),
            normalize_track(album_name),
        )

    scored = []
    for index, result in enumerate(candidates):
        artist, title, album = fields(result)
        artist_ok = bool(want_artist) and artist == want_artist
        title_ok = bool(want_title) and title == want_title
        album_ok = bool(want_album) and album == want_album
        if artist_ok and title_ok:
            rank = 0 if album_ok else 1
        elif artist_ok:
            rank = 2
        elif title_ok:
            rank = 3
        else:
            rank = 4
        live_mismatch = _is_live_result(result) != want_live
        scored.append((rank, live_mismatch, index, result["uri"]))
    scored.sort()
    best_rank, best_mismatch, _, uri = scored[0]
    return uri, best_rank <= 2 and not best_mismatch


def merge_track_lists(base: list[str], extra: list[str]) -> list[str]:
    """Order-preserving union of two track lists, deduped by normalize_track.

    Entries from `base` win over normalized duplicates in `extra`.
    Entries that normalize to '' are dropped.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for track in [*base, *extra]:
        norm = normalize_track(track)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        merged.append(track)
    return merged


def split_track(track: str) -> tuple[str, str]:
    """Split a track string into (artist, title). Strips album first."""
    if not track:
        return ("", "")

    track_only, _ = strip_album(track)
    # Normalize dash variants for splitting
    normalized = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2212]", "-", track_only)

    if re.search(r"\s-\s", normalized):
        parts = re.split(r"\s*-\s*", normalized, maxsplit=1)
        return (parts[0].strip(), parts[1].strip() if len(parts) > 1 else "")
    return (track_only.strip(), "")


def parse_json_tracks(raw_text: str | None) -> list[dict]:
    """Parse a JSON array of track objects from LLM output.

    Expected format: [{"artist": "...", "title": "...", "album": "..."}]
    Album is optional. Entries missing artist or title are skipped.

    Raises ValueError if the text is not valid JSON, not a list,
    or contains no valid track entries.
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("Empty response")

    text = raw_text.strip()

    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")

    tracks = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        artist = entry.get("artist", "")
        title = entry.get("title", "")
        if not isinstance(artist, str) or not isinstance(title, str):
            continue
        artist = artist.strip()
        title = title.strip()
        if not artist or not title:
            continue
        album = entry.get("album", "")
        if isinstance(album, str):
            album = album.strip()
        tracks.append({"artist": artist, "title": title, "album": album or ""})

    if not tracks:
        raise ValueError("No valid tracks in JSON response")

    return tracks


# Regex for lines that look like chain-of-thought rather than tracks
_COT_PATTERN = re.compile(
    r"^\s*(STEP|PASS|FINAL|ANSWER|NOTE|ANALYSIS|THINKING|REASONING)\b",
    re.IGNORECASE,
)

# Regex for valid track lines: must contain "word(s) - word(s)"
_TRACK_LINE_PATTERN = re.compile(r"[A-Za-z].+\s-\s.+")

# Regex to strip leading numbering: "1.", "1)", "1:", "- "
_NUMBERING_PATTERN = re.compile(r"^\s*(?:\d+[\.\)\:]|\-)\s*")


def _parse_lines(raw_text: str) -> list[str]:
    """Parse plain-text AI response into track strings (fallback path)."""
    tracks = []
    for line in raw_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if _COT_PATTERN.match(line):
            continue
        line = _NUMBERING_PATTERN.sub("", line).strip()
        if not line:
            continue
        if _TRACK_LINE_PATTERN.match(line):
            tracks.append(line)
    return tracks


def parse_ai_response(raw_text: str | None) -> list[dict]:
    """Parse raw AI response into a list of track dicts.

    Tries JSON parsing first. On failure, falls back to line-based
    parsing and converts results to dicts via split_track/strip_album.

    Returns list of {"artist": str, "title": str, "album": str} dicts.
    """
    if not raw_text:
        return []

    # Primary path: JSON
    try:
        return parse_json_tracks(raw_text)
    except ValueError:
        pass

    # Fallback: line-based parsing
    _logger.warning("JSON parse failed, falling back to line parser")
    tracks = _parse_lines(raw_text)
    if not tracks:
        return []

    # Convert line-parsed strings to dicts
    result = []
    for track_str in tracks:
        artist, title = split_track(track_str)
        _, album = strip_album(track_str)
        if artist and title:
            result.append({"artist": artist, "title": title, "album": album})
    return result


# Regex for live recording detection
_LIVE_PATTERN = re.compile(r"\(\s*live\b", re.IGNORECASE)


def filter_tracks(
    tracks: list[str],
    history: list[str],
    enqueued: list[str],
    exclude_live: bool = False,
) -> dict[str, list]:
    """Filter tracks by removing duplicates against history, enqueued, and within-response.

    Returns {"valid": [...], "duplicates": [...]}.
    Each duplicate entry: {"track": str, "reason": str}.
    """
    # Build normalized sets from history + enqueued
    existing_normalized: set[str] = set()
    existing_titles_normalized: set[str] = set()

    for existing_track in [*history, *enqueued]:
        if not existing_track:
            continue
        norm = normalize_track(existing_track)
        if norm:
            existing_normalized.add(norm)
            _, title = split_track(existing_track)
            if title:
                title_norm = normalize_track(title)
                if title_norm and len(title_norm.split()) >= 2:
                    existing_titles_normalized.add(title_norm)

    valid: list[str] = []
    duplicates: list[dict] = []
    seen_in_response: set[str] = set()
    seen_titles_in_response: set[str] = set()

    for track in tracks:
        if not track or not isinstance(track, str):
            continue

        track_trimmed = track.strip()
        if not track_trimmed:
            continue

        track_normalized = normalize_track(track_trimmed)
        if not track_normalized:
            continue

        # Live recording check (before normalization strips the suffix)
        if exclude_live:
            _, raw_title = split_track(track_trimmed)
            if raw_title and _LIVE_PATTERN.search(raw_title):
                duplicates.append({"track": track_trimmed, "reason": "live_recording"})
                continue

        # Within-response duplicate
        if track_normalized in seen_in_response:
            duplicates.append({"track": track_trimmed, "reason": "duplicate_in_response"})
            continue

        # Against history/enqueued — full track match
        if track_normalized in existing_normalized:
            duplicates.append({"track": track_trimmed, "reason": "duplicate_in_existing"})
            continue

        # Title-level dedup (2+ word titles only)
        _, candidate_title = split_track(track_trimmed)
        candidate_title_norm = normalize_track(candidate_title) if candidate_title else ""

        if candidate_title_norm and len(candidate_title_norm.split()) >= 2:
            if candidate_title_norm in existing_titles_normalized:
                duplicates.append({"track": track_trimmed, "reason": "duplicate_title_in_existing"})
                continue
            if candidate_title_norm in seen_titles_in_response:
                duplicates.append({"track": track_trimmed, "reason": "duplicate_title_in_response"})
                continue

        # Passed all checks
        valid.append(track_trimmed)
        seen_in_response.add(track_normalized)
        if candidate_title_norm:
            seen_titles_in_response.add(candidate_title_norm)

    return {"valid": valid, "duplicates": duplicates}
