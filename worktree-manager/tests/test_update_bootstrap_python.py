"""Explicit update prepares refreshed declarations before lifecycle admission."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from worktree_manager import update_bootstrap, update_bootstrap_python


def _declared(payload: Path, *, valid: bool = True) -> Path:
    scripts = payload / "scripts"
    (scripts / "_bootstrap_python").mkdir(parents=True)
    (scripts / "bootstrap-python-launcher.txt").write_bytes(b"uv\n" if valid else b"other\n")
    for name in ("select.ps1", "select.sh"):
        (scripts / "_bootstrap_python" / name).write_text("# fixture\n", encoding="utf-8")
    return payload


@pytest.mark.parametrize("windows", [True, False])
def test_preparation_uses_declared_owner_and_original_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: bool,
) -> None:
    payload = _declared(tmp_path / "payload")
    environment = {"PATH": "fixture tools", "UV_PYTHON_INSTALL_DIR": "custom store"}
    calls: list[tuple[list[str], Path]] = []
    monkeypatch.setattr(update_bootstrap_python, "os", SimpleNamespace(
        name="nt" if windows else "posix",
    ))
    monkeypatch.setattr(shutil, "which", lambda name: "/tools/" + name)

    def run(command: list[str], child: dict[str, str], cwd: Path) -> int:
        assert child is environment
        calls.append((command, cwd))
        return 23

    assert update_bootstrap_python.prepare(payload, environment, run) == 23
    command, cwd = calls[0]
    assert cwd == payload
    selector = payload / "scripts/_bootstrap_python" / ("select.ps1" if windows else "select.sh")
    assert str(selector) in command
    suffix = ["-Operation", "Prepare"] if windows else ["Prepare"]
    assert command[-len(suffix):] == suffix
    assert environment == {"PATH": "fixture tools", "UV_PYTHON_INSTALL_DIR": "custom store"}


@pytest.mark.parametrize("declaration", ["legacy", "invalid", "missing-selector"])
def test_preparation_never_falls_back_to_ambient_python(
    tmp_path: Path, declaration: str,
) -> None:
    payload = tmp_path / "payload"
    if declaration != "legacy":
        _declared(payload, valid=declaration != "invalid")
        if declaration == "missing-selector":
            (payload / "scripts/_bootstrap_python" / (
                "select.ps1" if os.name == "nt" else "select.sh"
            )).unlink()

    def run(command: list[str], environment: dict[str, str], cwd: Path) -> int:
        pytest.fail("unavailable declaration cannot launch an alternative")

    if declaration == "legacy":
        assert update_bootstrap_python.prepare(payload, {}, run) == 0
    else:
        with pytest.raises(RuntimeError, match="declaration|entry"):
            update_bootstrap_python.prepare(payload, {}, run)


@pytest.mark.parametrize("preparation_exit", [0, 29])
@pytest.mark.parametrize("separate_artifact", [False, True])
def test_engine_repair_prepares_before_lifecycle_and_stops_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, preparation_exit: int,
    separate_artifact: bool,
) -> None:
    payload = _declared(tmp_path / "payload")
    artifact = _declared(tmp_path / "artifact") if separate_artifact else None
    installation = update_bootstrap._Installation(
        root=tmp_path / "runtime", payload=payload, identity={}, sdk=tmp_path / "sdk",
        artifact=artifact,
    )
    calls: list[str] = []

    def run(command: list[str], environment: dict[str, str], cwd: Path) -> int:
        preparing = "_bootstrap_python" in " ".join(command)
        calls.append(cwd.name if preparing else "lifecycle")
        return preparation_exit if preparing else 17

    monkeypatch.setattr(update_bootstrap, "_wait", run)
    assert update_bootstrap._invoke(installation, "update", {}) == (preparation_exit or 17)
    assert calls == (
        ["payload"] if preparation_exit else
        ["payload", "artifact", "lifecycle"] if separate_artifact else ["payload", "lifecycle"]
    )
