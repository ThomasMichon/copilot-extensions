"""The installed profile consumer reaches Vault's real read-only provider."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_worktrees.update_runtime_target import TARGET_ENV, application_profile

VAULT = Path(__file__).resolve().parents[2] / "agent-vault"


@pytest.mark.parametrize(("desired", "extras"), [
    (None, []),
    ("base", []),
    ("ssh", ["ssh"]),
    ("federated-relay", ["federated-relay"]),
    ("ssh-federated-relay", ["federated-relay", "ssh"]),
])
def test_vault_profile_consumer_preserves_installed_state(
    tmp_path: Path, desired: str | None, extras: list[str],
) -> None:
    root = tmp_path / "runtime"
    before: dict[str, tuple[bytes, int]] = {}
    if desired is not None:
        root.mkdir()
        (root / "runtime-profile.json").write_text(json.dumps({
            "schema": "agent-vault.runtime-profile", "version": 1, "profile": desired,
        }), encoding="utf-8")
        (root / "current-version").write_text("retained-generation", encoding="utf-8")
        (root / "database.kdbx").write_bytes(b"untouched database fixture")
        before = {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.iterdir()
        }
    environment = {**os.environ, TARGET_ENV: "not-a-source-target"}

    actual = application_profile("agent-vault", VAULT, root, environment)

    assert actual == {
        "schema": "copilot-extensions.runtime-application-profile", "version": 1,
        "plugin": "agent-vault", "runtime_root": str(root.resolve()),
        "profile": desired or "base", "extras": extras,
    }
    if desired is None:
        assert not root.exists()
    else:
        assert {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.iterdir()
        } == before


def test_vault_profile_consumer_refuses_malformed_selection(tmp_path: Path) -> None:
    selection = tmp_path / "runtime-profile.json"
    selection.write_text(
        '{"schema":"agent-vault.runtime-profile","version":1,"profile":"unknown"}',
        encoding="utf-8",
    )
    before = selection.read_bytes(), selection.stat().st_mtime_ns

    with pytest.raises(ValueError, match="Unsupported Vault runtime profile"):
        application_profile("agent-vault", VAULT, tmp_path, dict(os.environ))

    assert sorted(path.name for path in tmp_path.iterdir()) == [selection.name]
    assert (selection.read_bytes(), selection.stat().st_mtime_ns) == before
