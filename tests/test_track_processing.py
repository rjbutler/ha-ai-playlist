"""Tests for track_processing.py — pure functions, no HA dependencies."""
import pytest

from custom_components.ai_playlist.track_processing import (
    pick_track_uri,
    filter_tracks,
    merge_track_lists,
    normalize_track,
    parse_ai_response,
    parse_json_tracks,
    queue_item_to_track_string,
    split_track,
    strip_album,
    track_dict_to_string,
)


# ── normalize_track ──────────────────────────────────────────────


class TestNormalizeTrack:
    def test_basic(self):
        assert normalize_track("Led Zeppelin - Stairway to Heaven") == "led zeppelin - stairway to heaven"

    def test_strips_album(self):
        result = normalize_track("Pink Floyd - Comfortably Numb | The Wall")
        assert "the wall" not in result
        assert result == "pink floyd - comfortably numb"

    def test_strips_remastered_suffix(self):
        result = normalize_track("The Beatles - Come Together (2009 Remastered)")
        assert result == "the beatles - come together"

    def test_strips_live_suffix(self):
        result = normalize_track("Queen - Bohemian Rhapsody (Live)")
        assert result == "queen - bohemian rhapsody"

    def test_strips_album_version_suffix(self):
        result = normalize_track("Radiohead - Creep (Album Version)")
        assert result == "radiohead - creep"

    def test_normalizes_ampersand(self):
        result = normalize_track("Simon & Garfunkel - The Sound of Silence")
        assert "simon and garfunkel" in result

    def test_normalizes_em_dash(self):
        result = normalize_track("Artist\u2014Title")
        assert "-" in result

    def test_normalizes_en_dash(self):
        result = normalize_track("Artist \u2013 Title")
        assert result == "artist - title"

    def test_collapses_whitespace(self):
        result = normalize_track("Led   Zeppelin  -  Stairway   to Heaven")
        assert result == "led zeppelin - stairway to heaven"

    def test_removes_quotes(self):
        result = normalize_track("""The "Real" Artist - Title""")
        assert '"' not in result
        assert result == "the real artist - title"

    def test_none_input(self):
        assert normalize_track(None) == ""

    def test_empty_string(self):
        assert normalize_track("") == ""

    def test_non_breaking_space(self):
        result = normalize_track("Artist\u00a0-\u00a0Title")
        assert result == "artist - title"

    def test_single_version_suffix(self):
        result = normalize_track("Nirvana - Smells Like Teen Spirit (Single Version)")
        assert result == "nirvana - smells like teen spirit"

    def test_deluxe_edition_suffix(self):
        result = normalize_track("Daft Punk - Get Lucky (Deluxe Edition)")
        assert result == "daft punk - get lucky"


# ── strip_album ──────────────────────────────────────────────────


class TestStripAlbum:
    def test_with_album(self):
        track, album = strip_album("Pink Floyd - Comfortably Numb | The Wall")
        assert track == "Pink Floyd - Comfortably Numb"
        assert album == "The Wall"

    def test_without_album(self):
        track, album = strip_album("Pink Floyd - Comfortably Numb")
        assert track == "Pink Floyd - Comfortably Numb"
        assert album == ""

    def test_empty(self):
        assert strip_album("") == ("", "")

    def test_none(self):
        assert strip_album(None) == ("", "")

    def test_strips_whitespace(self):
        track, album = strip_album("  Artist - Title  |  Album Name  ")
        assert track == "Artist - Title"
        assert album == "Album Name"

    def test_multiple_pipes(self):
        track, album = strip_album("Artist - Title | Album | Bonus")
        assert track == "Artist - Title"
        assert album == "Album | Bonus"


# ── split_track ──────────────────────────────────────────────────


class TestSplitTrack:
    def test_basic(self):
        artist, title = split_track("Led Zeppelin - Stairway to Heaven")
        assert artist == "Led Zeppelin"
        assert title == "Stairway to Heaven"

    def test_with_album(self):
        artist, title = split_track("Pink Floyd - Comfortably Numb | The Wall")
        assert artist == "Pink Floyd"
        assert title == "Comfortably Numb"

    def test_no_separator(self):
        artist, title = split_track("Just Some Text")
        assert artist == "Just Some Text"
        assert title == ""

    def test_empty(self):
        assert split_track("") == ("", "")

    def test_none(self):
        assert split_track(None) == ("", "")

    def test_en_dash(self):
        artist, title = split_track("Artist \u2013 Title")
        assert artist == "Artist"
        assert title == "Title"

    def test_em_dash(self):
        artist, title = split_track("Artist \u2014 Title")
        assert artist == "Artist"
        assert title == "Title"

    def test_hyphenated_artist(self):
        # split_track splits on " - " (space-dash-space), so "Jay-Z" stays intact
        # But the regex normalizes all dashes then splits on first " - "
        # "Jay-Z - 99 Problems" → after normalization: "Jay-Z - 99 Problems"
        # splits on first " - " → ("Jay", "Z - 99 Problems") — known limitation
        artist, title = split_track("Jay-Z - 99 Problems")
        # This is a known edge case — documenting actual behavior
        assert artist == "Jay"
        assert title == "Z - 99 Problems"


# ── track_dict_to_string ─────────────────────────────────────────


class TestTrackDictToString:
    def test_with_album(self):
        result = track_dict_to_string({"artist": "Pink Floyd", "title": "Comfortably Numb", "album": "The Wall"})
        assert result == "Pink Floyd - Comfortably Numb | The Wall"

    def test_without_album(self):
        result = track_dict_to_string({"artist": "Radiohead", "title": "Creep"})
        assert result == "Radiohead - Creep"

    def test_empty_album(self):
        result = track_dict_to_string({"artist": "Radiohead", "title": "Creep", "album": ""})
        assert result == "Radiohead - Creep"

    def test_whitespace_stripped(self):
        result = track_dict_to_string({"artist": " Jay-Z ", "title": " 99 Problems ", "album": " The Black Album "})
        assert result == "Jay-Z - 99 Problems | The Black Album"


# ── parse_json_tracks ────────────────────────────────────────────


class TestParseJsonTracks:
    def test_valid_json(self):
        raw = '[{"artist": "Miles Davis", "title": "So What", "album": "Kind of Blue"}]'
        result = parse_json_tracks(raw)
        assert len(result) == 1
        assert result[0] == {"artist": "Miles Davis", "title": "So What", "album": "Kind of Blue"}

    def test_missing_album_defaults_empty(self):
        raw = '[{"artist": "Radiohead", "title": "Creep"}]'
        result = parse_json_tracks(raw)
        assert result[0]["album"] == ""

    def test_multiple_tracks(self):
        raw = '[{"artist": "Jay-Z", "title": "99 Problems", "album": "The Black Album"}, {"artist": "Radiohead", "title": "Creep"}]'
        result = parse_json_tracks(raw)
        assert len(result) == 2
        assert result[0]["artist"] == "Jay-Z"
        assert result[1]["artist"] == "Radiohead"

    def test_code_fenced_json(self):
        raw = '```json\n[{"artist": "Miles Davis", "title": "So What"}]\n```'
        result = parse_json_tracks(raw)
        assert len(result) == 1
        assert result[0]["artist"] == "Miles Davis"

    def test_code_fenced_no_language(self):
        raw = '```\n[{"artist": "Miles Davis", "title": "So What"}]\n```'
        result = parse_json_tracks(raw)
        assert len(result) == 1

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            parse_json_tracks("not json at all")

    def test_not_a_list_raises(self):
        with pytest.raises(ValueError):
            parse_json_tracks('{"artist": "X", "title": "Y"}')

    def test_empty_array_raises(self):
        with pytest.raises(ValueError):
            parse_json_tracks("[]")

    def test_missing_artist_skipped(self):
        raw = '[{"title": "So What", "album": "Kind of Blue"}, {"artist": "Jay-Z", "title": "99 Problems"}]'
        result = parse_json_tracks(raw)
        assert len(result) == 1
        assert result[0]["artist"] == "Jay-Z"

    def test_missing_title_skipped(self):
        raw = '[{"artist": "Miles Davis", "album": "Kind of Blue"}, {"artist": "Jay-Z", "title": "99 Problems"}]'
        result = parse_json_tracks(raw)
        assert len(result) == 1

    def test_empty_artist_skipped(self):
        raw = '[{"artist": "", "title": "So What"}, {"artist": "Jay-Z", "title": "99 Problems"}]'
        result = parse_json_tracks(raw)
        assert len(result) == 1

    def test_all_entries_invalid_raises(self):
        raw = '[{"title": "So What"}, {"artist": "", "title": ""}]'
        with pytest.raises(ValueError):
            parse_json_tracks(raw)

    def test_none_raises(self):
        with pytest.raises(ValueError):
            parse_json_tracks(None)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            parse_json_tracks("")

    def test_extra_fields_ignored(self):
        raw = '[{"artist": "Miles Davis", "title": "So What", "album": "Kind of Blue", "year": 1959}]'
        result = parse_json_tracks(raw)
        assert result[0] == {"artist": "Miles Davis", "title": "So What", "album": "Kind of Blue"}

    def test_whitespace_stripped(self):
        raw = '[{"artist": " Jay-Z ", "title": " 99 Problems ", "album": " The Black Album "}]'
        result = parse_json_tracks(raw)
        assert result[0]["artist"] == "Jay-Z"
        assert result[0]["title"] == "99 Problems"
        assert result[0]["album"] == "The Black Album"

    def test_non_string_artist_skipped(self):
        raw = '[{"artist": 123, "title": "So What"}, {"artist": "Jay-Z", "title": "99 Problems"}]'
        result = parse_json_tracks(raw)
        assert len(result) == 1
        assert result[0]["artist"] == "Jay-Z"

    def test_non_string_title_skipped(self):
        raw = '[{"artist": "Miles Davis", "title": 42}, {"artist": "Jay-Z", "title": "99 Problems"}]'
        result = parse_json_tracks(raw)
        assert len(result) == 1
        assert result[0]["artist"] == "Jay-Z"

    def test_null_artist_skipped(self):
        raw = '[{"artist": null, "title": "So What"}, {"artist": "Jay-Z", "title": "99 Problems"}]'
        result = parse_json_tracks(raw)
        assert len(result) == 1

    def test_boolean_values_skipped(self):
        raw = '[{"artist": true, "title": "So What"}, {"artist": "Jay-Z", "title": "99 Problems"}]'
        result = parse_json_tracks(raw)
        assert len(result) == 1


# ── parse_ai_response (dict round-trip) ─────────────────────────


class TestDictStringRoundTrip:
    """Verify the dict → string → filter → string → dict round-trip used by coordinator."""

    def test_round_trip_preserves_track(self):
        """A track converted to string and back via the filter path stays intact."""
        original = {"artist": "Jay-Z", "title": "99 Problems", "album": "The Black Album"}
        string_form = track_dict_to_string(original)
        # Simulate filter_tracks returning the string unchanged
        assert string_form == "Jay-Z - 99 Problems | The Black Album"
        # Lookup in a mapping recovers the original dict
        mapping = {track_dict_to_string(original): original}
        assert mapping[string_form] is original

    def test_duplicate_strings_first_wins(self):
        """When two dicts produce the same string, first-seen should win."""
        t1 = {"artist": "Eagles", "title": "Hotel California", "album": "Studio"}
        t2 = {"artist": "Eagles", "title": "Hotel California", "album": "Live"}
        # Both produce the same string WITHOUT album difference
        # Actually they produce different strings because albums differ:
        s1 = track_dict_to_string(t1)
        s2 = track_dict_to_string(t2)
        assert s1 == "Eagles - Hotel California | Studio"
        assert s2 == "Eagles - Hotel California | Live"
        assert s1 != s2  # Different albums = different strings = no collision

    def test_duplicate_strings_same_album_collision(self):
        """Two tracks with identical artist+title+album produce the same string."""
        t1 = {"artist": "Eagles", "title": "Hotel California", "album": ""}
        t2 = {"artist": "Eagles", "title": "Hotel California", "album": ""}
        s1 = track_dict_to_string(t1)
        s2 = track_dict_to_string(t2)
        assert s1 == s2
        # First-seen-wins mapping
        mapping: dict[str, dict] = {}
        for t in [t1, t2]:
            key = track_dict_to_string(t)
            if key not in mapping:
                mapping[key] = t
        assert mapping[s1] is t1  # First one wins

    def test_filter_tracks_returns_original_strings(self):
        """filter_tracks returns the original un-normalized strings in valid list."""
        tracks_dicts = [
            {"artist": "Jay-Z", "title": "99 Problems", "album": "The Black Album"},
            {"artist": "Pink Floyd", "title": "Comfortably Numb", "album": "The Wall"},
        ]
        track_strings = [track_dict_to_string(t) for t in tracks_dicts]
        result = filter_tracks(track_strings, history=[], enqueued=[])
        # Valid strings should be identical to input strings
        assert result["valid"] == track_strings


# ── parse_ai_response ────────────────────────────────────────────


class TestParseAiResponse:
    """parse_ai_response now returns list[dict], trying JSON first then line fallback."""

    def test_json_input_returns_dicts(self):
        raw = '[{"artist": "Led Zeppelin", "title": "Stairway to Heaven", "album": "Led Zeppelin IV"}]'
        result = parse_ai_response(raw)
        assert len(result) == 1
        assert result[0] == {"artist": "Led Zeppelin", "title": "Stairway to Heaven", "album": "Led Zeppelin IV"}

    def test_json_code_fenced(self):
        raw = '```json\n[{"artist": "Miles Davis", "title": "So What"}]\n```'
        result = parse_ai_response(raw)
        assert len(result) == 1
        assert result[0]["artist"] == "Miles Davis"

    def test_plain_text_fallback_returns_dicts(self):
        raw = "Led Zeppelin - Stairway to Heaven\nPink Floyd - Comfortably Numb"
        result = parse_ai_response(raw)
        assert len(result) == 2
        assert result[0]["artist"] == "Led Zeppelin"
        assert result[0]["title"] == "Stairway to Heaven"
        assert result[0]["album"] == ""

    def test_plain_text_with_album_fallback(self):
        raw = "Pink Floyd - Comfortably Numb | The Wall"
        result = parse_ai_response(raw)
        assert len(result) == 1
        assert result[0]["artist"] == "Pink Floyd"
        assert result[0]["title"] == "Comfortably Numb"
        assert result[0]["album"] == "The Wall"

    def test_plain_text_strips_numbering(self):
        raw = "1. Led Zeppelin - Stairway to Heaven\n2. Pink Floyd - Comfortably Numb"
        result = parse_ai_response(raw)
        assert len(result) == 2
        assert result[0]["artist"] == "Led Zeppelin"

    def test_plain_text_rejects_cot(self):
        raw = "STEP 1: Think about rock\nLed Zeppelin - Stairway to Heaven\nNOTE: classic"
        result = parse_ai_response(raw)
        assert len(result) == 1

    def test_malformed_json_falls_back_to_lines(self):
        raw = '[{"artist": "Miles Davis"  BROKEN\nLed Zeppelin - Stairway to Heaven'
        result = parse_ai_response(raw)
        assert len(result) == 1
        assert result[0]["artist"] == "Led Zeppelin"

    def test_empty_input(self):
        assert parse_ai_response("") == []
        assert parse_ai_response(None) == []

    def test_no_valid_tracks_returns_empty(self):
        raw = "Here are some tracks:\nEnjoy the music!"
        result = parse_ai_response(raw)
        assert result == []

    def test_jay_z_json_works(self):
        """JSON path correctly handles hyphenated artist names."""
        raw = '[{"artist": "Jay-Z", "title": "99 Problems", "album": "The Black Album"}]'
        result = parse_ai_response(raw)
        assert result[0]["artist"] == "Jay-Z"
        assert result[0]["title"] == "99 Problems"


# ── filter_tracks ────────────────────────────────────────────────


class TestFilterTracks:
    def test_no_duplicates(self):
        tracks = ["Led Zeppelin - Stairway to Heaven", "Pink Floyd - Comfortably Numb"]
        result = filter_tracks(tracks, history=[], enqueued=[])
        assert len(result["valid"]) == 2
        assert len(result["duplicates"]) == 0

    def test_dedup_against_history(self):
        tracks = ["Led Zeppelin - Stairway to Heaven"]
        result = filter_tracks(
            tracks,
            history=["Led Zeppelin - Stairway to Heaven"],
            enqueued=[],
        )
        assert len(result["valid"]) == 0
        assert result["duplicates"][0]["reason"] == "duplicate_in_existing"

    def test_dedup_against_enqueued(self):
        tracks = ["Led Zeppelin - Stairway to Heaven"]
        result = filter_tracks(
            tracks,
            history=[],
            enqueued=["Led Zeppelin - Stairway to Heaven"],
        )
        assert len(result["valid"]) == 0

    def test_dedup_within_response(self):
        tracks = [
            "Led Zeppelin - Stairway to Heaven",
            "Led Zeppelin - Stairway to Heaven",
        ]
        result = filter_tracks(tracks, history=[], enqueued=[])
        assert len(result["valid"]) == 1
        assert result["duplicates"][0]["reason"] == "duplicate_in_response"

    def test_dedup_normalized(self):
        """Same track with different casing/spacing should be deduped."""
        tracks = ["led zeppelin - stairway to heaven"]
        result = filter_tracks(
            tracks,
            history=["Led Zeppelin - Stairway to Heaven"],
            enqueued=[],
        )
        assert len(result["valid"]) == 0

    def test_title_level_dedup(self):
        """Same title by different artist should be caught (2+ word titles)."""
        tracks = ["Artist B - Stairway to Heaven"]
        result = filter_tracks(
            tracks,
            history=["Led Zeppelin - Stairway to Heaven"],
            enqueued=[],
        )
        assert len(result["valid"]) == 0
        assert result["duplicates"][0]["reason"] == "duplicate_title_in_existing"

    def test_single_word_title_not_deduped(self):
        """Single-word titles should NOT trigger title-level dedup."""
        tracks = ["Artist B - Dreams"]
        result = filter_tracks(
            tracks,
            history=["Fleetwood Mac - Dreams"],
            enqueued=[],
        )
        # Single-word title — full track match won't match, title dedup skipped
        assert len(result["valid"]) == 1

    def test_exclude_live(self):
        tracks = ["Queen - Bohemian Rhapsody (Live at Wembley)"]
        result = filter_tracks(tracks, history=[], enqueued=[], exclude_live=True)
        assert len(result["valid"]) == 0
        assert result["duplicates"][0]["reason"] == "live_recording"

    def test_exclude_live_disabled(self):
        tracks = ["Queen - Bohemian Rhapsody (Live at Wembley)"]
        result = filter_tracks(tracks, history=[], enqueued=[], exclude_live=False)
        assert len(result["valid"]) == 1

    def test_empty_tracks(self):
        result = filter_tracks([], history=[], enqueued=[])
        assert result["valid"] == []
        assert result["duplicates"] == []

    def test_remastered_dedup(self):
        """Remastered version should match non-remastered in history."""
        tracks = ["The Beatles - Come Together (2009 Remastered)"]
        result = filter_tracks(
            tracks,
            history=["The Beatles - Come Together"],
            enqueued=[],
        )
        assert len(result["valid"]) == 0

    def test_album_stripped_for_dedup(self):
        """Album portion should not affect dedup."""
        tracks = ["Pink Floyd - Comfortably Numb | The Wall"]
        result = filter_tracks(
            tracks,
            history=["Pink Floyd - Comfortably Numb | Pulse"],
            enqueued=[],
        )
        assert len(result["valid"]) == 0

    def test_title_dedup_within_response(self):
        tracks = [
            "Artist A - Stairway to Heaven",
            "Artist B - Stairway to Heaven",
        ]
        result = filter_tracks(tracks, history=[], enqueued=[])
        assert len(result["valid"]) == 1
        assert result["duplicates"][0]["reason"] == "duplicate_title_in_response"


# ── Queue reconciliation helpers ─────────────────────────────────


class TestQueueItemToTrackString:
    def _item(self, artist=None, title=None, display_name=""):
        """Duck-typed stand-in for an MA QueueItem."""
        from types import SimpleNamespace

        media_item = None
        if artist is not None or title is not None:
            artists = [SimpleNamespace(name=artist)] if artist else []
            media_item = SimpleNamespace(artists=artists, name=title or "")
        return SimpleNamespace(media_item=media_item, name=display_name)

    def test_uses_media_item_artist_and_title(self):
        item = self._item(artist="Jackson Browne", title="Running on Empty")
        assert queue_item_to_track_string(item) == "Jackson Browne - Running on Empty"

    def test_falls_back_to_display_name_without_media_item(self):
        item = self._item(display_name="Damien Rice - The Blower's Daughter")
        assert queue_item_to_track_string(item) == "Damien Rice - The Blower's Daughter"

    def test_falls_back_to_display_name_when_artists_empty(self):
        item = self._item(artist=None, title="Orphan Title", display_name="X - Orphan Title")
        assert queue_item_to_track_string(item) == "X - Orphan Title"

    def test_returns_empty_string_when_nothing_usable(self):
        item = self._item()
        assert queue_item_to_track_string(item) == ""


class TestQueueItemToTrackStringDictForm:
    """MA QUEUE_UPDATED event payloads arrive as nested dicts (via to_dict())."""

    def test_uses_media_item_artist_and_title(self):
        item = {
            "media_item": {
                "artists": [{"name": "I Musici"}],
                "name": "Brandenburg Concerto No. 3",
            },
            "name": "fallback",
        }
        assert queue_item_to_track_string(item) == "I Musici - Brandenburg Concerto No. 3"

    def test_falls_back_to_display_name_without_media_item(self):
        item = {"name": "Handel - Concerto Grosso Op. 3"}
        assert queue_item_to_track_string(item) == "Handel - Concerto Grosso Op. 3"

    def test_falls_back_to_display_name_when_artists_empty(self):
        item = {
            "media_item": {"artists": [], "name": "Orphan Title"},
            "name": "X - Orphan Title",
        }
        assert queue_item_to_track_string(item) == "X - Orphan Title"

    def test_returns_empty_string_for_none(self):
        assert queue_item_to_track_string(None) == ""

    def test_returns_empty_string_for_empty_dict(self):
        assert queue_item_to_track_string({}) == ""


class TestMergeTrackLists:
    def test_union_preserves_order_base_first(self):
        merged = merge_track_lists(["A - 1", "B - 2"], ["C - 3"])
        assert merged == ["A - 1", "B - 2", "C - 3"]

    def test_dedups_by_normalized_form(self):
        # Same track, different case + remaster suffix → one entry, base wins
        merged = merge_track_lists(
            ["Jackson Browne - Running on Empty"],
            ["jackson browne - running on empty (remastered)"],
        )
        assert merged == ["Jackson Browne - Running on Empty"]

    def test_drops_unnormalizable_entries(self):
        assert merge_track_lists(["", "A - 1"], [""]) == ["A - 1"]

    def test_both_empty(self):
        assert merge_track_lists([], []) == []


def _result(uri, artist, title, album=""):
    return {"uri": uri, "name": title, "artists": [{"name": artist}], "album": {"name": album}}


class TestPickTrackUri:
    def test_prefers_artist_and_title_over_first_result(self):
        results = [
            _result("u:cover", "Some Cover Band", "Town Called Malice"),
            _result("u:jam", "The Jam", "Town Called Malice"),
        ]
        track = {"artist": "The Jam", "title": "Town Called Malice"}
        assert pick_track_uri(results, track) == ("u:jam", True)

    def test_album_breaks_ties(self):
        results = [
            _result("u:comp", "The Jam", "Town Called Malice", "Greatest Hits"),
            _result("u:gift", "The Jam", "Town Called Malice", "The Gift"),
        ]
        track = {"artist": "The Jam", "title": "Town Called Malice", "album": "The Gift"}
        assert pick_track_uri(results, track) == ("u:gift", True)

    def test_normalizes_remaster_suffix_and_ampersand(self):
        results = [
            _result("u:other", "Other", "Something"),
            _result("u:hit", "Simon & Garfunkel", "The Boxer (Remastered)"),
        ]
        track = {"artist": "Simon and Garfunkel", "title": "The Boxer"}
        assert pick_track_uri(results, track) == ("u:hit", True)

    def test_studio_request_prefers_studio_over_live(self):
        results = [
            _result("u:live", "The Who", "My Generation (Live at Leeds)"),
            _result("u:studio", "The Who", "My Generation"),
        ]
        track = {"artist": "The Who", "title": "My Generation"}
        assert pick_track_uri(results, track) == ("u:studio", True)

    def test_live_request_prefers_live(self):
        results = [
            _result("u:studio", "The Who", "My Generation"),
            _result("u:live", "The Who", "My Generation (Live at Leeds)"),
        ]
        track = {"artist": "The Who", "title": "My Generation (Live at Leeds)"}
        assert pick_track_uri(results, track) == ("u:live", True)

    def test_live_word_in_studio_title_is_not_a_live_marker(self):
        results = [_result("u:wings", "Wings", "Live and Let Die")]
        track = {"artist": "Wings", "title": "Live and Let Die"}
        assert pick_track_uri(results, track) == ("u:wings", True)

    def test_title_only_match_is_not_confident(self):
        results = [_result("u:cover", "Cover Band", "Blitzkrieg Bop")]
        track = {"artist": "Ramones", "title": "Blitzkrieg Bop"}
        assert pick_track_uri(results, track) == ("u:cover", False)

    def test_falls_back_to_first_result_without_confidence(self):
        results = [_result("u:first", "X", "Y"), _result("u:second", "Z", "W")]
        assert pick_track_uri(results, {"artist": "A", "title": "B"}) == ("u:first", False)

    def test_no_results_or_no_uri(self):
        assert pick_track_uri([], {"artist": "A", "title": "B"}) == (None, False)
        assert pick_track_uri([{"name": "B"}], {"artist": "A", "title": "B"}) == (None, False)
