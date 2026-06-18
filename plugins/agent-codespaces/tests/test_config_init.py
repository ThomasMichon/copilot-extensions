"""Tests for `config init` scaffold derivation and rendering."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import yaml

from agent_codespaces.__main__ import (
    _derive_codespaces_defaults,
    _render_codespaces_yaml,
)

# Local-only denylist of internal identifiers that must never be baked into a
# generated codespaces.yaml. The list is sourced **privately** so this public
# repo carries none of the strings itself:
#   1. env ``COPILOT_EXTENSIONS_FORBIDDEN_IDS`` (comma-separated), then
#   2. ``~/.agent-codespaces/forbidden-identifiers.txt`` (one per line; blank
#      lines and ``#`` comments ignored).
# In a fresh clone / CI neither is present -> the identifier check is a no-op
# while the generic structural assertion still runs. Maintain the real list on
# your own machine (see the agent-codespaces README, "Local identifier guard")
# so the guard enforces it locally and nothing leaks into the repo. The same two
# sources drive the repo-wide ``tools/check-no-internal-identifiers.py``.
def _forbidden_identifiers() -> list[str]:
    ids: list[str] = []
    env = os.environ.get("COPILOT_EXTENSIONS_FORBIDDEN_IDS", "")
    ids += [s for s in (part.strip() for part in env.split(",")) if s]
    local_file = Path.home() / ".agent-codespaces" / "forbidden-identifiers.txt"
    try:
        for raw in local_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                ids.append(line)
    except OSError:
        pass
    return [i.lower() for i in ids]


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
        forbidden = _forbidden_identifiers()
        for d in (None, {
            "repository": "my-org/my-codespaces-repo",
            "machine_type": "largePremiumLinux256gb",
            "workspace_folder": "/workspaces/my-app",
            "source_name": "x",
        }):
            text = _render_codespaces_yaml(d).lower()
            # Generic invariant (always checked): the scaffold documents that
            # org/account/URL values belong in the user's repo, not the plugin.
            assert "never in" in text
            # Private denylist (checked when configured locally / in CI).
            for bad in forbidden:
                assert bad not in text, (
                    f"internal identifier {bad!r} leaked into the generated "
                    "codespaces.yaml scaffold"
                )
