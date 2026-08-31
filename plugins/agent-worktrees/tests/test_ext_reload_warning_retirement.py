"""Retirement guards for the obsolete extension-reload startup warning."""

from __future__ import annotations

import json
from pathlib import Path

from agent_worktrees import __main__ as m
from agent_worktrees import installer


PLUGIN = Path(__file__).resolve().parents[1]
RETIRED_ASSETS = (
    "session-ext-reload.ps1",
    "session-ext-reload.sh",
    "ext-reload-hang.md",
)


def _warning_file(proj_dir: Path) -> Path:
    return proj_dir / ".github" / "instructions" / "ext-reload-hang.instructions.md"


def test_warning_payload_and_hook_are_removed() -> None:
    scripts = PLUGIN / "scripts"
    for name in RETIRED_ASSETS:
        assert not (scripts / name).exists()

    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    assert "session-ext-reload" not in json.dumps(hooks)

    aggregator = (scripts / "emit_session_context.py").read_text(encoding="utf-8")
    assert "session-ext-reload" not in aggregator
    assert '"extension"' not in aggregator


def test_installer_retires_previously_deployed_assets(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    assets = repo / "plugins" / "agent-worktrees" / "bin"
    assets.mkdir(parents=True)
    for name in ("launch-session.cmd", "launch-session.ps1", "pane-wrapper.ps1"):
        (assets / name).write_text("wrapper\n", encoding="utf-8")

    install = tmp_path / "install"
    deployed = install / "bin"
    deployed.mkdir(parents=True)
    for name in RETIRED_ASSETS:
        (deployed / name).write_text("stale\n", encoding="utf-8")

    monkeypatch.setattr(installer, "install_dir", lambda: install)
    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")

    assert installer.deploy_wrappers(repo)
    for name in RETIRED_ASSETS:
        assert not (deployed / name).exists()


def test_locked_retired_asset_warns_without_failing_update(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    repo = tmp_path / "repo"
    assets = repo / "plugins" / "agent-worktrees" / "bin"
    assets.mkdir(parents=True)
    for name in ("launch-session.cmd", "launch-session.ps1", "pane-wrapper.ps1"):
        (assets / name).write_text("wrapper\n", encoding="utf-8")

    install = tmp_path / "install"
    deployed = install / "bin"
    deployed.mkdir(parents=True)
    locked = deployed / RETIRED_ASSETS[0]
    locked.write_text("stale\n", encoding="utf-8")

    original_unlink = Path.unlink

    def unlink(path: Path, *args, **kwargs) -> None:
        if path == locked:
            raise PermissionError("temporarily locked")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(installer, "install_dir", lambda: install)
    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    monkeypatch.setattr(Path, "unlink", unlink)

    assert installer.deploy_wrappers(repo)
    assert locked.exists()
    assert "Could not retire obsolete" in capsys.readouterr().out


def test_remove_managed_instruction_retires_marked(tmp_path: Path) -> None:
    proj = tmp_path / ".proj"
    path = _warning_file(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{m._INSTRUCTION_MARKER}\n# stale managed warning\n")

    m._remove_managed_instruction(proj, "ext-reload-hang.instructions.md")
    assert not path.exists()


def test_remove_managed_instruction_leaves_unmarked_user_file(
    tmp_path: Path,
) -> None:
    proj = tmp_path / ".proj"
    path = _warning_file(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# user's own instructions, not ours\n")

    m._remove_managed_instruction(proj, "ext-reload-hang.instructions.md")
    assert path.exists()


def test_cleanup_still_sweeps_stale_warning(tmp_path: Path) -> None:
    proj = tmp_path / ".proj"
    path = _warning_file(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{m._INSTRUCTION_MARKER}\n# stale\n")

    m._cleanup_stale_instructions(proj)
    assert not path.exists()
