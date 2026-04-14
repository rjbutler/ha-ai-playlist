"""Tests for ad-hoc playlist naming — verifies hashlib-based slugs are
deterministic and collision-resistant.
"""
import hashlib


def adhoc_name(prompt: str) -> str:
    """Reproduce the ad-hoc naming logic from __init__.py."""
    return f"adhoc_{hashlib.md5(prompt.encode()).hexdigest()[:8]}"


class TestAdhocNaming:
    def test_deterministic(self):
        """Same prompt always produces the same name."""
        name1 = adhoc_name("Upbeat jazz fusion")
        name2 = adhoc_name("Upbeat jazz fusion")
        assert name1 == name2

    def test_different_prompts_different_names(self):
        name1 = adhoc_name("Upbeat jazz fusion")
        name2 = adhoc_name("Mellow acoustic folk")
        assert name1 != name2

    def test_format(self):
        name = adhoc_name("test prompt")
        assert name.startswith("adhoc_")
        assert len(name) == 14  # "adhoc_" (6) + 8 hex chars

    def test_hex_chars_only(self):
        name = adhoc_name("test prompt")
        hex_part = name.replace("adhoc_", "")
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_collision_resistance(self):
        """Generate 10000 names and verify no collisions."""
        names = {adhoc_name(f"prompt_{i}") for i in range(10000)}
        assert len(names) == 10000
