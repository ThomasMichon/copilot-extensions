"""Tests for Copilot permission / folder-trust seeding (permissions.py).

Focus: folder trust must be written to the camelCase ``trustedFolders`` key
that the Copilot CLI actually reads. An earlier snake_case ``trusted_folders``
was silently ignored, so worktrees were never pre-trusted (leaving the startup
folder-trust reload firing, which feeds the extension-reload hang) and were
never cleaned up on finalize.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import permissions


@pytest.fixture
def copilot_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point permissions.py at an isolated ~/.copilot dir."""
    cop = tmp_path / ".copilot"
    cop.mkdir()
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: cop)
    return cop


def _write_config(cop: Path, data: dict) -> Path:
    p = cop / "config.json"
    p.write_text(json.dumps(data, indent=2))
    return p


# ── folder trust: correct key ────────────────────────────────────────────

def test_add_trusted_folder_uses_camelcase_key(copilot_home: Path) -> None:
    cfg = _write_config(copilot_home, {"trustedFolders": []})

    assert permissions.add_trusted_folder(r"D:\wt\a") is True

    data = json.loads(cfg.read_text())
    assert data["trustedFolders"] == [r"D:\wt\a"]
    # The dead snake_case key must never be created.
    assert "trusted_folders" not in data


def test_add_trusted_folder_initializes_missing_key(copilot_home: Path) -> None:
    # config.json exists but has no trustedFolders yet (fresh CLI install).
    cfg = _write_config(copilot_home, {"firstLaunchAt": "2026-01-01"})

    assert permissions.add_trusted_folder(r"D:\wt\a") is True

    data = json.loads(cfg.read_text())
    assert data["trustedFolders"] == [r"D:\wt\a"]
    assert data["firstLaunchAt"] == "2026-01-01"  # unrelated keys preserved


def test_add_trusted_folder_idempotent(copilot_home: Path) -> None:
    _write_config(copilot_home, {"trustedFolders": [r"D:\wt\a"]})

    assert permissions.add_trusted_folder(r"D:\wt\a") is False


def test_add_trusted_folder_preserves_existing_entries(copilot_home: Path) -> None:
    cfg = _write_config(copilot_home, {"trustedFolders": [r"D:\wt\existing"]})

    assert permissions.add_trusted_folder(r"D:\wt\new") is True

    data = json.loads(cfg.read_text())
    assert data["trustedFolders"] == [r"D:\wt\existing", r"D:\wt\new"]


def test_add_trusted_folder_no_config(copilot_home: Path) -> None:
    # No config.json at all -> no-op (CLI creates it on first run).
    assert permissions.add_trusted_folder(r"D:\wt\a") is False


# ── folder trust: removal (finalize cleanup) ─────────────────────────────

def test_remove_trusted_folder_uses_camelcase_key(copilot_home: Path) -> None:
    cfg = _write_config(
        copilot_home, {"trustedFolders": [r"D:\wt\a", r"D:\wt\b"]}
    )

    assert permissions.remove_trusted_folder(r"D:\wt\a") is True

    data = json.loads(cfg.read_text())
    assert data["trustedFolders"] == [r"D:\wt\b"]


def test_remove_trusted_folder_absent(copilot_home: Path) -> None:
    _write_config(copilot_home, {"trustedFolders": [r"D:\wt\b"]})

    assert permissions.remove_trusted_folder(r"D:\wt\a") is False


def test_add_then_remove_roundtrip(copilot_home: Path) -> None:
    cfg = _write_config(copilot_home, {"trustedFolders": []})

    assert permissions.add_trusted_folder(r"D:\wt\a") is True
    assert permissions.remove_trusted_folder(r"D:\wt\a") is True

    data = json.loads(cfg.read_text())
    assert data["trustedFolders"] == []
    assert "trusted_folders" not in data


# ── JSONC: config.json has a managed leading // comment header ────────────

_JSONC_HEADER = (
    "// User settings belong in settings.json.\n"
    "// This file is managed automatically.\n"
)


def _write_jsonc_config(cop: Path, data: dict) -> Path:
    p = cop / "config.json"
    p.write_text(_JSONC_HEADER + json.dumps(data, indent=2), encoding="utf-8")
    return p


def test_add_trusted_folder_parses_jsonc(copilot_home: Path) -> None:
    # Real Copilot config.json is JSONC (stdlib json.loads would choke on the
    # // header) -- the seed must still succeed.
    cfg = _write_jsonc_config(copilot_home, {"trustedFolders": []})

    assert permissions.add_trusted_folder(r"D:\wt\a") is True

    text = cfg.read_text(encoding="utf-8")
    # Header preserved verbatim...
    assert text.startswith(_JSONC_HEADER)
    # ...and the JSON body (after the header) parses with the new entry.
    body = json.loads(text[len(_JSONC_HEADER):])
    assert body["trustedFolders"] == [r"D:\wt\a"]


def test_remove_trusted_folder_parses_jsonc(copilot_home: Path) -> None:
    cfg = _write_jsonc_config(
        copilot_home, {"trustedFolders": [r"D:\wt\a", r"D:\wt\b"]}
    )

    assert permissions.remove_trusted_folder(r"D:\wt\a") is True

    text = cfg.read_text(encoding="utf-8")
    assert text.startswith(_JSONC_HEADER)
    body = json.loads(text[len(_JSONC_HEADER):])
    assert body["trustedFolders"] == [r"D:\wt\b"]


def test_jsonc_preserves_unrelated_keys(copilot_home: Path) -> None:
    cfg = _write_jsonc_config(
        copilot_home,
        {"firstLaunchAt": "2026-01-01", "trustedFolders": []},
    )

    assert permissions.add_trusted_folder(r"D:\wt\a") is True

    body = json.loads(cfg.read_text(encoding="utf-8")[len(_JSONC_HEADER):])
    assert body["firstLaunchAt"] == "2026-01-01"
    assert body["trustedFolders"] == [r"D:\wt\a"]


def test_jsonc_slashes_in_values_not_stripped(copilot_home: Path) -> None:
    # A // inside a string value (e.g. a URL) must NOT be treated as a comment.
    cfg = _write_jsonc_config(
        copilot_home,
        {"homepage": "https://example.com/x", "trustedFolders": []},
    )

    assert permissions.add_trusted_folder(r"D:\wt\a") is True

    body = json.loads(cfg.read_text(encoding="utf-8")[len(_JSONC_HEADER):])
    assert body["homepage"] == "https://example.com/x"
    assert body["trustedFolders"] == [r"D:\wt\a"]


# ── extension-permission-access pre-seed (github/copilot-agent-runtime#22266) ──

def _write_permissions(cop: Path, data: dict) -> Path:
    p = cop / "permissions-config.json"
    p.write_text(json.dumps(data, indent=2))
    return p


def test_ensure_extension_permission_approvals_fresh_file(copilot_home: Path) -> None:
    # No permissions-config.json at all yet (brand new install/location).
    assert permissions.ensure_extension_permission_approvals(r"D:\wt\a") is True

    perm_file = copilot_home / "permissions-config.json"
    data = json.loads(perm_file.read_text())
    approvals = data["locations"][r"D:\wt\a"]["tool_approvals"]
    names = {a["extensionName"] for a in approvals}
    assert names == {
        "plugin:agent-bridge:agent-bridge",
        "plugin:context-handoff:context-handoff",
        "plugin:agent-worktrees:agent-worktrees",
    }
    assert all(a["kind"] == "extension-permission-access" for a in approvals)


def test_ensure_extension_permission_approvals_idempotent(copilot_home: Path) -> None:
    permissions.ensure_extension_permission_approvals(r"D:\wt\a")

    # A second call with nothing new to add reports no change.
    assert permissions.ensure_extension_permission_approvals(r"D:\wt\a") is False


def test_ensure_extension_permission_approvals_preserves_existing_entries(
    copilot_home: Path,
) -> None:
    perm_file = _write_permissions(
        copilot_home,
        {
            "locations": {
                r"D:\wt\a": {
                    "tool_approvals": [
                        {"kind": "read"},
                        {
                            "kind": "extension-permission-access",
                            "extensionName": "plugin:context-handoff:context-handoff",
                        },
                    ]
                }
            }
        },
    )

    assert permissions.ensure_extension_permission_approvals(r"D:\wt\a") is True

    data = json.loads(perm_file.read_text())
    approvals = data["locations"][r"D:\wt\a"]["tool_approvals"]
    # The pre-existing unrelated rule and the already-approved extension
    # survive untouched; only the two missing extensions are appended.
    assert {"kind": "read"} in approvals
    names = {
        a["extensionName"]
        for a in approvals
        if a.get("kind") == "extension-permission-access"
    }
    assert names == {
        "plugin:agent-bridge:agent-bridge",
        "plugin:context-handoff:context-handoff",
        "plugin:agent-worktrees:agent-worktrees",
    }
    assert len(approvals) == 4  # read + 3 extension approvals, no duplicate


def test_ensure_extension_permission_approvals_preserves_other_locations(
    copilot_home: Path,
) -> None:
    perm_file = _write_permissions(
        copilot_home,
        {"locations": {r"D:\wt\other": {"tool_approvals": [{"kind": "read"}]}}},
    )

    assert permissions.ensure_extension_permission_approvals(r"D:\wt\a") is True

    data = json.loads(perm_file.read_text())
    assert data["locations"][r"D:\wt\other"]["tool_approvals"] == [{"kind": "read"}]
    assert r"D:\wt\a" in data["locations"]
