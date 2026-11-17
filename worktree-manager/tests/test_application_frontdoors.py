"""Manager publishes every application command and its neutral setup together."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from worktree_manager import self_install as install


@pytest.fixture
def application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    slot = tmp_path / "app" / "versions" / "1.2.3"
    slot.mkdir(parents=True)
    (slot / "application.json").write_text(json.dumps({
        "name": "worktree-manager",
        "commands": ["worktree-manager", "worktree-manager-install"],
    }), encoding="utf-8")
    shutil.copyfile(
        Path(__file__).resolve().parents[2] / "libs" / "installer-engine"
        / "runtime_application_binstubs.py",
        slot / "runtime_application_binstubs.py",
    )
    global_bin = tmp_path / "global"
    monkeypatch.setattr(install, "local_bin", lambda: global_bin)
    monkeypatch.delenv("WORKTREE_MANAGER_ROOT", raising=False)
    return slot, global_bin


def test_full_publication_covers_all_commands_and_neutral_setup(
    application: tuple[Path, Path],
) -> None:
    slot, global_bin = application
    written = install._deploy_binstubs(application_slot=slot)
    app_bin = slot.parent.parent / "bin"
    variants = ("", ".sh", ".cmd", ".ps1")
    commands = ("worktree-manager", "worktree-manager-install")
    assert set(written) == {global_bin / (name + suffix) for name in commands for suffix in variants}
    assert {path.name for path in app_bin.iterdir()} == {
        name + suffix for name in (*commands, "setup") for suffix in variants
    }
    assert not install._binstubs_are_stale(application_slot=slot)
    neutral = app_bin / "setup.ps1"
    neutral.unlink()
    assert install._binstubs_are_stale(application_slot=slot)
    install._deploy_binstubs(application_slot=slot)
    assert not install._binstubs_are_stale(application_slot=slot)
    (global_bin / "worktree-manager-install.cmd").write_bytes(b"edited")
    assert install._binstubs_are_stale(application_slot=slot)


def test_selected_older_launcher_api_is_a_replacement_gap_not_a_status_error(
    application: tuple[Path, Path],
) -> None:
    slot, _ = application
    (slot / "runtime_application_binstubs.py").write_text(
        "def global_binstubs(name, root_environment, default_directory, *, root=None):\n"
        "    return {name: 'legacy selected launcher\\n'}\n", encoding="utf-8",
    )
    assert install._binstubs_are_stale(application_slot=slot)


def test_uncommitted_publication_recovers_neutral_and_global_outputs(
    application: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot, global_bin = application
    root = slot.parent.parent
    payload = tmp_path / "payload"
    references = payload / "references"
    references.mkdir(parents=True)
    (references / "control-plane-provider.json").write_text(
        '{"provider":"fixture-manager"}', encoding="utf-8",
    )
    monkeypatch.setattr(install, "control_plane_providers_dir", lambda: tmp_path / "providers")
    installer: Any = install._runtime_installer()

    def activate(_root: Path, _version: str, **options: Any) -> None:
        options["before_publish"]()

    def fail(_slot: Path) -> None:
        raise RuntimeError("fixture activation failed")

    monkeypatch.setattr(installer, "activate", activate)
    install._deploy_binstubs(application_slot=slot)
    outputs = install._expected_frontdoor_contents(slot)
    for path in outputs:
        path.write_bytes(b"previous " + path.name.encode())
    before = {path: path.read_bytes() for path in outputs}
    with pytest.raises(RuntimeError, match="fixture activation failed"):
        install._complete_slot(
            slot, payload, "1.2.3", root, previous=None, was_current=False,
            reused=False, after_activate=fail,
        )
    assert {path: path.read_bytes() for path in outputs} == before
    assert (global_bin / "worktree-manager-install.cmd").read_bytes().startswith(b"previous")
    assert not (tmp_path / "providers" / "fixture-manager.json").exists()


@pytest.mark.parametrize("command", ["worktree-manager-install", "setup"])
def test_native_neutral_command_tracks_selection_and_forwards_arguments_and_exit(
    application: tuple[Path, Path], command: str,
) -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("native PowerShell is unavailable")
    slot, _ = application
    root = slot.parent.parent
    for version in ("1.2.3", "1.2.4"):
        selected = root / "versions" / version
        directory = selected / "bin" if command == "setup" else selected
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (command + ".ps1")).write_text(
            f"Write-Output '{version}'\n"
            "ConvertTo-Json -Compress -InputObject @($args)\n"
            "exit 7\n", encoding="utf-8",
        )
    install._deploy_binstubs(application_slot=slot)
    for version in ("1.2.3", "1.2.4"):
        (root / "current-version").write_text(version + "\n", encoding="utf-8")
        result = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-File", str(root / "bin" / (command + ".ps1")),
             "--stage", "two words"],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 7, result.stderr
        selected_version, arguments = result.stdout.strip().splitlines()
        assert selected_version == version
        assert json.loads(arguments) == ["--stage", "two words"]
