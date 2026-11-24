"""Source-application compatibility publication and private setup dispatch."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import sys
from typing import Any

import pytest

from agent_worktrees import _shutdown_exit, application_setup, current_roster_entry, installer

PLUGIN = Path(__file__).resolve().parents[1]
RESOLVERS = ("resolve-runtime.ps1", "resolve-runtime.sh")


@pytest.fixture
def application(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "application"
    slot = root / "versions" / "1.2.3"
    slot.mkdir(parents=True)
    (slot / "application.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(slot / ".venv"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    commands = tmp_path / "commands"
    commands.mkdir()
    monkeypatch.setattr(installer, "local_bin", lambda: commands)
    monkeypatch.setattr(installer, "scripts_dir", lambda: PLUGIN / "scripts")
    snapshot: dict[str, Any] = {"projects": {"demo": {}}}
    registry = installer.projects_yaml_path()
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps(snapshot), encoding="utf-8")

    def read_registry(*, strict: bool = False) -> dict[str, Any]:
        return snapshot

    monkeypatch.setattr(installer, "read_projects_registry", read_registry)
    return root, commands


def test_setup_publishes_readers_and_preserves_commands(
    application: tuple[Path, Path],
) -> None:
    root, commands = application
    registry = installer.projects_yaml_path().read_bytes()
    stale = commands / "prior.cmd"
    stale.write_bytes(b"retained old command\n")

    assert application_setup.setup_application() == 0
    for name in RESOLVERS:
        assert (root / "bin" / name).read_bytes() == (PLUGIN / "scripts" / name).read_bytes()
    first = {path: path.read_bytes() for path, _ in installer._project_binstub_specs("demo")}
    assert application_setup.setup_application() == 0
    assert {path: path.read_bytes() for path in first} == first
    assert installer.projects_yaml_path().read_bytes() == registry
    assert stale.read_bytes() == b"retained old command\n"
    receipt = installer._read_receipt("demo")
    assert receipt is not None
    assert receipt["owner"]["payload_root"] == root.as_posix()


@pytest.mark.parametrize("missing", RESOLVERS)
def test_setup_requires_both_readers_before_publication(
    application: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, missing: str,
) -> None:
    root, commands = application
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in RESOLVERS:
        if name != missing:
            (assets / name).write_bytes((PLUGIN / "scripts" / name).read_bytes())
    monkeypatch.setattr(installer, "scripts_dir", lambda: assets)
    previous = root / "bin" / "resolve-runtime.ps1"
    previous.parent.mkdir()
    previous.write_bytes(b"retained resolver\n")
    registry = installer.projects_yaml_path().read_bytes()

    assert application_setup.setup_application() == 1
    assert previous.read_bytes() == b"retained resolver\n"
    assert not (root / "bin" / "resolve-runtime.sh").exists()
    assert not list(commands.iterdir())
    assert installer.projects_yaml_path().read_bytes() == registry


def test_readers_do_not_follow_an_indirect_command_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root = tmp_path / "application"
    root.mkdir()
    outside = tmp_path / "other-installation"
    outside.mkdir()
    try:
        (root / "bin").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable")
    retained = outside / "resolve-runtime.ps1"
    retained.write_bytes(b"other installation\n")
    monkeypatch.setattr(installer, "scripts_dir", lambda: PLUGIN / "scripts")

    assert not application_setup._deploy_runtime_resolvers(root)
    assert retained.read_bytes() == b"other installation\n"
    assert not (outside / "resolve-runtime.sh").exists()


@pytest.mark.parametrize(
    ("arguments", "source_setup"),
    [
        (["__source-setup"], True),
        (["__source-start"], False),
        (["__source-setup", "--unknown"], False),
    ],
)
def test_setup_dispatch_retains_the_existing_shutdown_owner(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str], source_setup: bool,
) -> None:
    captured: list[Callable[[], int]] = []

    def legacy() -> int:
        return 17

    def capture(handler: Callable[[], int]) -> None:
        captured.append(handler)

    monkeypatch.setattr(sys, "argv", ["agent-worktrees", *arguments])
    monkeypatch.setattr(_shutdown_exit, "run_and_exit", capture)
    current_roster_entry.run_legacy_console(legacy)
    assert captured == [application_setup.setup_application if source_setup else legacy]
