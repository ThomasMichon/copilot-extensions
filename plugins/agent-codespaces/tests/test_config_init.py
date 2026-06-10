"""Tests for `config init` scaffold derivation and rendering."""

from __future__ import annotations

from unittest.mock import patch

import yaml

from agent_codespaces.__main__ import (
    _derive_codespaces_defaults,
    _render_codespaces_yaml,
)

SAMPLE = [
    {
        "name": "cs-a-abc",
        "repository": "my-org/my-codespaces-repo",
        "machineName": "largePremiumLinux256gb",
        "displayName": "feature-a",
        "state": "Shutdown",
        "lastUsedAt": "2026-06-01T10:00:00Z",
    },
    {
        "name": "cs-b-def",
        "repository": "my-org/my-codespaces-repo",
        "machineName": "largePremiumLinux256gb",
        "displayName": "feature-b",
        "state": "Available",
        "lastUsedAt": "2026-06-09T10:00:00Z",
    },
]


class TestDeriveDefaults:
    def test_empty_returns_none(self):
        assert _derive_codespaces_defaults([], None) is None

    def test_picks_most_recently_used(self):
        with patch(
            "agent_codespaces.__main__._discover_workspace_folder",
            return_value=None,
        ):
            d = _derive_codespaces_defaults(SAMPLE, None)
        assert d is not None
        # cs-b has the later lastUsedAt
        assert d["source_name"] == "feature-b"
        assert d["repository"] == "my-org/my-codespaces-repo"
        assert d["machine_type"] == "largePremiumLinux256gb"

    def test_from_codespace_selects_named(self):
        with patch(
            "agent_codespaces.__main__._discover_workspace_folder",
            return_value=None,
        ):
            d = _derive_codespaces_defaults(SAMPLE, "cs-a-abc")
        assert d is not None
        assert d["source_name"] == "feature-a"

    def test_from_codespace_unknown_returns_none(self):
        assert _derive_codespaces_defaults(SAMPLE, "does-not-exist") is None

    def test_workspace_folder_comes_from_discovery_not_repo_name(self):
        # The CodeSpaces repo is 'my-codespaces-repo' but the real checkout is
        # a different path -- must use the discovered value verbatim.
        with patch(
            "agent_codespaces.__main__._discover_workspace_folder",
            return_value="/workspaces/my-app",
        ):
            d = _derive_codespaces_defaults(SAMPLE, None)
        assert d["workspace_folder"] == "/workspaces/my-app"

    def test_workspace_folder_none_when_undiscoverable(self):
        with patch(
            "agent_codespaces.__main__._discover_workspace_folder",
            return_value=None,
        ):
            d = _derive_codespaces_defaults(SAMPLE, None)
        assert d["workspace_folder"] is None


class TestRenderYaml:
    def test_generic_template_is_valid_yaml_with_placeholders(self):
        text = _render_codespaces_yaml(None)
        data = yaml.safe_load(text)
        assert data["defaults"]["machine_type"] == "largePremiumLinux"
        assert "<your-repo>" in data["defaults"]["workspace_folder"]
        # repos block is commented out in the generic template
        assert data.get("repos") is None

    def test_derived_with_discovered_workspace(self):
        defaults = {
            "repository": "my-org/my-codespaces-repo",
            "machine_type": "largePremiumLinux256gb",
            "workspace_folder": "/workspaces/my-app",
            "source_name": "feature-b",
        }
        text = _render_codespaces_yaml(defaults)
        data = yaml.safe_load(text)
        assert data["defaults"]["workspace_folder"] == "/workspaces/my-app"
        assert data["repos"]["my-org/my-codespaces-repo"][
            "machine_type"
        ] == "largePremiumLinux256gb"

    def test_derived_without_workspace_leaves_no_active_value(self):
        defaults = {
            "repository": "my-org/my-codespaces-repo",
            "machine_type": "largePremiumLinux256gb",
            "workspace_folder": None,
            "source_name": "feature-b",
        }
        text = _render_codespaces_yaml(defaults)
        data = yaml.safe_load(text)
        # workspace_folder must NOT be set to a guessed value
        assert "workspace_folder" not in data["defaults"]
        # but a TODO comment should guide the user
        assert "WORKING_DIRECTORY" in text

    def test_no_internal_identifiers_in_output(self):
        for d in (None, {
            "repository": "my-org/my-codespaces-repo",
            "machine_type": "largePremiumLinux256gb",
            "workspace_folder": "/workspaces/my-app",
            "source_name": "x",
        }):
            text = _render_codespaces_yaml(d).lower()
            for bad in ("odsp", "onedrive", "tmichon"):
                assert bad not in text
