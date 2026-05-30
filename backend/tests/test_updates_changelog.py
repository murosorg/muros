# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Tests for the changelog parser feeding the dashboard version card.

The parser reads the CHANGELOG.md shipped with the package and exposes the
notes for the latest version through get_muros_status. We point
CHANGELOG_PATH at a temporary file so the tests stay deterministic and do
not depend on an installed package.
"""
import pytest

from app import updates

SAMPLE = """# Changelog

## [Unreleased]

## [v0.9.0-rc70] - 2026-05-30

### Added
- Version card on the dashboard.
- Changelog read from the shipped file.

### Changed
- Dashboard reorganized.

## [v0.9.0-rc53] - 2026-05-29

### Fixed
- Installer ISO apt exit 100.
"""


@pytest.fixture
def changelog(tmp_path, monkeypatch):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(updates, "CHANGELOG_PATH", path)
    return path


def test_sections_are_parsed_in_order(changelog):
    sections = updates._read_changelog_sections()
    versions = [s["version"] for s in sections]
    assert versions == ["Unreleased", "v0.9.0-rc70", "v0.9.0-rc53"]
    assert sections[1]["date"] == "2026-05-30"
    assert "Version card on the dashboard." in sections[1]["body"]
    # The body stops at the next header.
    assert "Installer ISO" not in sections[1]["body"]


def test_exact_version_match_ignores_v_prefix(changelog):
    notes, date = updates._changelog_for("0.9.0-rc70")
    assert date == "2026-05-30"
    assert "Dashboard reorganized." in notes


def test_unknown_version_falls_back_to_latest_release(changelog):
    # rc999 is not in the changelog: fall back to the newest released
    # section, i.e. rc70 (Unreleased is skipped).
    notes, date = updates._changelog_for("v0.9.0-rc999")
    assert date == "2026-05-30"
    assert "Version card on the dashboard." in notes


def test_missing_changelog_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "CHANGELOG_PATH", tmp_path / "absent.md")
    assert updates._changelog_for("v0.9.0-rc70") == (None, None)
    assert updates._read_changelog_sections() == []


def test_only_unreleased_is_used_as_last_resort(tmp_path, monkeypatch):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n- WIP feature.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(updates, "CHANGELOG_PATH", path)
    notes, _ = updates._changelog_for("v0.9.0-rc70")
    assert "WIP feature." in notes


def test_get_muros_status_exposes_release_notes(changelog, monkeypatch):
    monkeypatch.setattr(updates, "_load_state", lambda: {"last_check_at": None, "packages": []})
    monkeypatch.setattr(updates, "_dpkg_installed_version", lambda pkg: "0.9.0-rc70")
    monkeypatch.setattr(updates, "_apt_candidate_version", lambda pkg: "0.9.0-rc70")
    monkeypatch.setattr(updates, "_apt_available", lambda: True)
    status = updates.get_muros_status()
    assert status["release_published_at"] == "2026-05-30"
    assert "Version card on the dashboard." in status["release_notes"]
    assert status["upgrade_available"] is False
