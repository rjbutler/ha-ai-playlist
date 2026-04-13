"""Tests for configuration file consistency and validity.

Validates that strings.json, translations/en.json, services.yaml,
and manifest.json are well-formed and internally consistent.
"""
import json
from pathlib import Path

import yaml
import pytest

COMPONENT_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "ai_playlist"


@pytest.fixture
def strings():
    with open(COMPONENT_DIR / "strings.json") as f:
        return json.load(f)


@pytest.fixture
def translations():
    with open(COMPONENT_DIR / "translations" / "en.json") as f:
        return json.load(f)


@pytest.fixture
def services():
    with open(COMPONENT_DIR / "services.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def manifest():
    with open(COMPONENT_DIR / "manifest.json") as f:
        return json.load(f)


# ── strings.json / translations sync ────────────────────────────


class TestStringTranslationSync:
    def test_strings_equals_translations(self, strings, translations):
        """strings.json and translations/en.json must be identical."""
        assert strings == translations

    def test_all_step_ids_have_titles(self, strings):
        """Every step in the options flow should have a title."""
        steps = strings["options"]["step"]
        for step_id, step_data in steps.items():
            if step_id == "init":
                continue  # init is a menu, not a form
            assert "title" in step_data, f"Step '{step_id}' missing title"

    def test_menu_options_have_matching_steps(self, strings):
        """Every menu option in init should have a corresponding step method."""
        menu_options = strings["options"]["step"]["init"]["menu_options"]
        steps = strings["options"]["step"]
        for option_key in menu_options:
            assert option_key in steps, (
                f"Menu option '{option_key}' has no corresponding step in strings.json"
            )


# ── No stale "list" references ───────────────────────────────────


class TestNoStaleListReferences:
    def test_no_list_keys_in_strings(self, strings):
        """Ensure no leftover 'list' keys from the rename (excluding 'playlist')."""
        menu_options = strings["options"]["step"]["init"]["menu_options"]
        for key in menu_options:
            # "add_playlist" contains "list" but that's "playlist", not the old "list" concept
            key_without_playlist = key.replace("playlist", "")
            assert "list" not in key_without_playlist, f"Stale 'list' reference in menu: {key}"

    def test_no_list_steps_in_strings(self, strings):
        steps = strings["options"]["step"]
        for step_id in steps:
            step_without_playlist = step_id.replace("playlist", "")
            assert "list" not in step_without_playlist, f"Stale 'list' step: {step_id}"

    def test_no_list_abort_reasons(self, strings):
        abort = strings["options"].get("abort", {})
        for key in abort:
            key_without_playlist = key.replace("playlist", "")
            assert "list" not in key_without_playlist, f"Stale 'list' abort reason: {key}"

    def test_no_list_field_in_services(self, services):
        """Service fields should use 'collection', not 'list'."""
        for service_name, service_def in services.items():
            fields = service_def.get("fields", {})
            assert "list" not in fields, (
                f"Service '{service_name}' has stale 'list' field"
            )


# ── services.yaml validation ────────────────────────────────────


class TestServicesYaml:
    def test_all_services_have_name(self, services):
        for service_name, service_def in services.items():
            assert "name" in service_def, f"Service '{service_name}' missing name"

    def test_all_services_have_description(self, services):
        for service_name, service_def in services.items():
            assert "description" in service_def, f"Service '{service_name}' missing description"

    def test_required_fields_marked(self, services):
        """Services with required fields should have 'required: true'."""
        # play requires entity_id
        play_fields = services["play"]["fields"]
        assert play_fields["entity_id"].get("required") is True

        # stop requires entity_id
        stop_fields = services["stop"]["fields"]
        assert stop_fields["entity_id"].get("required") is True

    def test_collection_field_in_play(self, services):
        assert "collection" in services["play"]["fields"]

    def test_collection_field_in_select(self, services):
        assert "collection" in services["select"]["fields"]

    def test_expected_services_present(self, services):
        expected = ["play", "stop", "clear_history", "list_playlists", "select"]
        for name in expected:
            assert name in services, f"Missing service: {name}"


# ── manifest.json validation ────────────────────────────────────


class TestManifest:
    def test_required_keys(self, manifest):
        required = ["domain", "name", "version", "config_flow", "documentation"]
        for key in required:
            assert key in manifest, f"Missing required key: {key}"

    def test_domain(self, manifest):
        assert manifest["domain"] == "ai_playlist"

    def test_version_format(self, manifest):
        """Version should be PEP 440 compliant."""
        import re
        # Basic PEP 440 pattern
        pattern = r"^\d+\.\d+\.\d+([ab]\d+)?$"
        assert re.match(pattern, manifest["version"]), (
            f"Version '{manifest['version']}' doesn't match PEP 440"
        )

    def test_ai_task_dependency(self, manifest):
        assert "ai_task" in manifest.get("dependencies", [])

    def test_music_assistant_after_dep(self, manifest):
        """music_assistant should be an after_dependency, not a hard dependency."""
        assert "music_assistant" not in manifest.get("dependencies", [])
        assert "music_assistant" in manifest.get("after_dependencies", [])


# ── const.py consistency ─────────────────────────────────────────


class TestConstConsistency:
    def test_collection_constants_use_correct_values(self):
        from custom_components.ai_playlist.const import (
            CONF_COLLECTIONS,
            CONF_COLLECTION_NAME,
            CONF_COLLECTION_TAGS,
        )
        assert CONF_COLLECTIONS == "collections"
        assert CONF_COLLECTION_NAME == "name"
        assert CONF_COLLECTION_TAGS == "tags"

    def test_platforms_use_platform_constants(self):
        from custom_components.ai_playlist.const import PLATFORMS
        from homeassistant.const import Platform
        assert Platform.SELECT in PLATFORMS
        assert Platform.SENSOR in PLATFORMS
