"""Tests for core-install checkout discovery (worktree-manager #540).

`resolve_checkout()` must find a copilot-extensions checkout from **cwd** (walk up)
or the **bootstrap staging clone** (`<root>/staging`), not only from this package's
installed slot — so `setup` can drive the real agent-worktrees installer right after
a bootstrap.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import worktree_manager.core_install as ci


@pytest.fixture(autouse=True)
def _isolate_checkout_ancestors(tmp_path, monkeypatch):
    # Runner-owned storage may live inside the real checkout. Hide only its
    # ancestor markers; synthetic checkouts and the real discovery walk remain.
    markers = {
        ancestor / ".github" / "plugin" / "marketplace.json"
        for ancestor in tmp_path.parents
    }
    is_file = Path.is_file
    monkeypatch.setattr(
        Path, "is_file", lambda path: False if path in markers else is_file(path),
    )
    assert ci.find_repo_root(tmp_path) is None


def _make_checkout(root: Path) -> Path:
    """Materialize the minimal markers `_is_checkout` looks for."""
    (root / ".github" / "plugin").mkdir(parents=True, exist_ok=True)
    (root / ".github" / "plugin" / "marketplace.json").write_text("{}", "utf-8")
    scripts = root / "plugins" / "agent-worktrees" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "install.sh").write_text("#!/usr/bin/env bash\n", "utf-8")
    (scripts / "install.ps1").write_text("# ps1\n", "utf-8")
    return root


def test_is_checkout(tmp_path):
    assert not ci._is_checkout(tmp_path)
    _make_checkout(tmp_path)
    assert ci._is_checkout(tmp_path)


def test_resolve_from_cwd(tmp_path, monkeypatch):
    co = _make_checkout(tmp_path / "co")
    sub = co / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)  # cwd inside the checkout (walk up finds it)
    # No staging fallback available for this root.
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "wmroot"))
    assert ci.resolve_checkout() == co


def test_resolve_from_staging_when_cwd_has_none(tmp_path, monkeypatch):
    # cwd is NOT inside a checkout…
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    # …but the bootstrap staging clone is a full checkout.
    wmroot = tmp_path / "wmroot"
    _make_checkout(wmroot / "staging")
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(wmroot))
    assert ci.resolve_checkout() == wmroot / "staging"


def test_resolve_none_when_neither(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "empty-root"))
    assert ci.resolve_checkout() is None


def test_install_command_uses_resolved_checkout(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    wmroot = tmp_path / "wmroot"
    _make_checkout(wmroot / "staging")
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(wmroot))
    cmd = ci.install_command()  # no explicit repo_root -> resolves staging
    assert cmd is not None
    name = "install.ps1" if os.name == "nt" else "install.sh"
    assert Path(cmd[cmd.index("install") - 1]) == (
        wmroot / "staging" / "plugins" / "agent-worktrees" / "scripts" / name
    )
    if os.name == "nt":
        assert cmd[-2:] == ["-BootstrapPython", sys._base_executable]


@pytest.mark.parametrize("exit_code", [0, 7])
def test_core_install_preserves_prepared_interpreter_and_child_exit(
    tmp_path, monkeypatch, exit_code,
):
    import subprocess
    from types import SimpleNamespace

    source = _make_checkout(tmp_path / "source")
    monkeypatch.setattr(ci, "core_status", lambda home: SimpleNamespace(installed=False))
    monkeypatch.setenv("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", "stale-inherited-selection")
    environment = dict(os.environ)
    calls = []

    def run(command, **options):
        calls.append((command, options))
        return subprocess.CompletedProcess(command, exit_code)

    monkeypatch.setattr(ci.subprocess, "run", run)
    result = ci.install_core(repo_root=source, dry_run=False)
    assert result.ran and result.returncode == exit_code
    command, options = calls[0]
    assert command == result.planned_command
    assert options["env"] == {
        **environment, "COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON": sys._base_executable,
    }
    if os.name == "nt":
        assert command[-2:] == ["-BootstrapPython", sys._base_executable]
        assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert dict(os.environ) == environment


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.parametrize("exit_code", [0, 7])
def test_native_core_handoff_uses_prepared_python_with_empty_path(
    tmp_path, monkeypatch, exit_code,
):
    import json
    import shlex
    import shutil
    import subprocess
    from types import SimpleNamespace

    shell = shutil.which("pwsh" if os.name == "nt" else "bash")
    if not shell:
        pytest.skip("native installer shell is unavailable")
    receipt = tmp_path / "receipt.json"
    program = (
        "import json,os,pathlib,shutil,sys;"
        "assert not any(shutil.which(name) for name in ('python', 'python3', 'py'));"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
        "'python':sys.executable,"
        "'prepared':os.environ['COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON']}));"
        f"raise SystemExit({exit_code})"
    )
    script = tmp_path / ("install.ps1" if os.name == "nt" else "install.sh")
    if os.name == "nt":
        quoted = program.replace("'", "''")
        script.write_text(
            "param([string]$Action, [string]$BootstrapPython)\n"
            "if ($Action -ne 'install') { exit 90 }\n"
            f"& $BootstrapPython -I -S -B -c '{quoted}' "
            "(Join-Path $PSScriptRoot 'receipt.json')\nexit $LASTEXITCODE\n",
            encoding="utf-8",
        )
    else:
        script.write_text(
            'test "$1" = install || exit 90\n'
            'exec "$COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON" -I -S -B -c '
            f"{shlex.quote(program)} {shlex.quote(str(receipt))}\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(ci, "install_script", lambda root: script)
    command = ci.install_command()
    command[0] = shell
    monkeypatch.setattr(ci, "install_command", lambda root: command)
    monkeypatch.setattr(ci, "core_status", lambda home: SimpleNamespace(installed=False))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", "stale-inherited-selection")
    completed = []
    run = subprocess.run

    def observe(command, **options):
        assert options["env"]["PATH"] == ""
        result = run(command, capture_output=True, text=True, timeout=15, **options)
        completed.append(result)
        return result

    monkeypatch.setattr(ci.subprocess, "run", observe)
    result = ci.install_core(dry_run=False)
    assert result.ran and result.returncode == exit_code, (
        result.reason, [(item.stdout, item.stderr) for item in completed],
    )
    assert json.loads(receipt.read_text(encoding="utf-8")) == {
        "python": sys._base_executable, "prepared": sys._base_executable,
    }
