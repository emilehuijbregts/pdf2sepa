"""Tests for logic/auto_update.py version helpers."""

from __future__ import annotations

from logic.auto_update import is_newer_version, version_tuple


def test_version_tuple_parses_semver() -> None:
    assert version_tuple("1.0.2") == (1, 0, 2)


def test_version_tuple_strips_prerelease_suffix() -> None:
    assert version_tuple("1.0.1-test2") == (1, 0, 1)


def test_is_newer_version() -> None:
    assert is_newer_version("1.0.2", "1.0.1")
    assert not is_newer_version("1.0.1", "1.0.2")
    assert not is_newer_version("1.0.1", "1.0.1")
