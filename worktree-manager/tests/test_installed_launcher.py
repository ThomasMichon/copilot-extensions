"""Native generated-launcher contracts, without touching an installed Manager."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import venv
from contextlib import contextmanager
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager import self_install as si


def _run(argv, *, env, cwd):
    return subprocess.run(
        argv, env=env, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", timeout=20, **no_window_kwargs(),
    )


@pytest.fixture(params=["cmd", "ps1", "sh"] if os.name == "nt" else ["sh"])
def launcher(request, tmp_path):
    kind = request.param
    path = tmp_path / f"manager.{kind}"
    path.write_text(getattr(si, f"_{kind}_binstub")(), encoding="utf-8", newline="")
    if kind == "cmd":
        argv = [os.environ["COMSPEC"], "/d", "/c", str(path)]
    elif kind == "ps1":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        assert shell, "native PowerShell is required on Windows"
        argv = [shell, "-NoProfile", "-NonInteractive", "-File", str(path)]
    else:
        shell = "bash"
        if os.name == "nt":
            # Never invoke Windows' bash.exe alias (which launches WSL).
            git = shutil.which("git")
            bash = Path(git).parent.parent / "bin" / "bash.exe" if git else None
            if bash is None or not bash.is_file():
                pytest.skip("native Git Bash is unavailable")
            shell = str(bash)
        argv = [shell, str(path)]
    root = tmp_path / "manager root"
    slot = root / "versions" / "1.2.3"
    slot.mkdir(parents=True)
    (root / "current-version").write_text("1.2.3\n", encoding="utf-8")
    env = dict(os.environ, WORKTREE_MANAGER_ROOT=str(root))
    for key in ("PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
        env.pop(key, None)
    return argv, env, slot


def _payload(slot, version="1.2.3"):
    pkg = slot / "src" / "worktree_manager"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (pkg / "__main__.py").write_text(
        "import json, sys, worktree_manager\n"
        "print(json.dumps(dict(version=worktree_manager.__version__, "
        "file=worktree_manager.__file__, args=sys.argv[1:])))\n"
        "raise SystemExit(7 if '--fail' in sys.argv else 0)\n",
        encoding="utf-8",
    )
    (slot / ".install-complete").touch()
    (slot / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n'
        '[project]\nname = "copilot-extensions-worktree-manager"\n'
        f'version = "{version}"\nrequires-python = ">=3.10"\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
        '[tool.uv]\nexclude-newer = "7 days"\n',
        encoding="utf-8",
    )


def test_constructed_slot_runs_installed_package_without_resolution(launcher, tmp_path):
    argv, env, slot = launcher
    _payload(slot)
    runtime = slot / ".venv"
    venv.EnvBuilder(with_pip=False).create(runtime)
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    assert uv, "the contained runner requires uv"
    # Install the actual src-layout distribution, not a PYTHONPATH injection.
    installed = _run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", str(slot)],
        env=env, cwd=slot,
    )
    assert installed.returncode == 0, installed.stderr
    # Any project resolution must now refuse, including offline resolution.
    # No such dependency belongs to the already-built environment.
    with (slot / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            '[dependency-groups]\n'
            'dev = ["manager-launcher-resolution-must-not-run==0.0.0"]\n'
        )
    env["UV_OFFLINE"] = "1"
    env["ERRORLEVEL"] = "0"
    _payload(tmp_path)
    env["PYTHONPATH"] = str(tmp_path / "src")
    env["UV_PROJECT_ENVIRONMENT"] = str(tmp_path / "foreign-env")
    result = _run(argv + ["argument with spaces", "\u2713"], env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["version"] == "1.2.3"
    assert Path(output["file"]).is_relative_to(runtime)
    assert output["args"] == ["argument with spaces", "\u2713"]
    assert not Path(env["UV_PROJECT_ENVIRONMENT"]).exists()
    failed = _run(argv + ["--fail"], env=env, cwd=tmp_path)
    assert failed.returncode == 7, failed.stderr


def test_selected_payload_only_slot_is_not_an_installed_runtime(launcher, tmp_path, monkeypatch):
    """Failed canonical construction cannot bless an interpreter-only slot."""
    argv, env, previous_slot = launcher
    root = previous_slot.parents[1]
    monkeypatch.setattr(si, "local_bin", lambda: tmp_path / "local-bin")
    monkeypatch.setattr(si, "control_plane_providers_dir", lambda: tmp_path / "providers")
    payload = si.running_payload_dir()
    version = si.payload_version(payload)
    slot = si.version_slot(version, root)
    si._copy_payload(payload, slot)
    (root / si.MARKER).write_text(version, encoding="utf-8")
    before = (root / si.MARKER).read_bytes()
    assert not si._slot_is_complete(slot)
    assert not (slot / ".venv").exists()

    # A failed resolving first use can leave only the interpreter in this
    # already-selected payload. It is not the constructed-runtime fast path.
    venv.EnvBuilder(with_pip=False).create(slot / ".venv")
    python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    imported = _run(
        [str(python), "-I", "-c", "import worktree_manager"],
        env=env, cwd=tmp_path,
    )
    assert imported.returncode != 0
    assert "No module named 'worktree_manager'" in imported.stderr
    @contextmanager
    def refused(*args, **kwargs):
        raise RuntimeError("fixture construction refused")
        yield
    monkeypatch.setattr(si._runtime_installer(), "candidate", refused)
    result = si.self_install(payload, root=root, dry_run=False)
    assert result.action == "error" and "construction refused" in result.reason
    assert (root / si.MARKER).read_bytes() == before
    env["UV_OFFLINE"] = "1"
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode != 0
    assert "invalid constructed worktree-manager runtime" in result.stderr
    assert not result.stdout.strip()


def test_marker_identity_is_not_a_normalized_distribution_version(launcher, tmp_path):
    argv, env, previous_slot = launcher
    slot = previous_slot.with_name("1.2.3.dev1")
    slot.mkdir()
    (slot.parents[1] / si.MARKER).write_text(slot.name, encoding="utf-8")
    _payload(slot, version="1.2.3-dev1")
    runtime = slot / ".venv"
    venv.EnvBuilder(with_pip=False).create(runtime)
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    assert uv
    installed = _run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", str(slot)],
        env=env, cwd=slot,
    )
    assert installed.returncode == 0, installed.stderr
    metadata = _run(
        [
            str(python), "-I", "-c",
            "import importlib.metadata as m; print(m.version('copilot-extensions-worktree-manager'))",
        ],
        env=env, cwd=tmp_path,
    )
    assert metadata.returncode == 0, metadata.stderr
    assert metadata.stdout.strip() == "1.2.3.dev1"
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["version"] == "1.2.3-dev1"
    assert Path(output["file"]).is_relative_to(runtime)


def test_missing_runtime_does_not_attempt_project_resolution(launcher, tmp_path):
    argv, env, slot = launcher
    _payload(slot)
    with (slot / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            '[dependency-groups]\n'
            'dev = ["manager-launcher-resolution-must-not-run==0.0.0"]\n'
        )
    env["UV_OFFLINE"] = "1"
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode != 0
    assert "invalid constructed worktree-manager runtime" in result.stderr
    assert not result.stdout.strip()


@pytest.mark.parametrize("damage", ["missing", "unexecutable"])
def test_retained_launcher_cannot_report_success_without_starting_interpreter(launcher, tmp_path, damage):
    argv, env, slot = launcher
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir()
    bootstrap.write_text("raise SystemExit(0)\n", encoding="utf-8")
    if damage == "unexecutable":
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir()
        python.write_bytes(b"not an executable")
        python.chmod(0o755)
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode != 0
    assert result.stderr.strip()


@pytest.mark.parametrize("exit_code", [0, 7])
def test_retained_launcher_preserves_executed_child_status(launcher, tmp_path, exit_code):
    argv, env, slot = launcher
    venv.EnvBuilder(with_pip=False).create(slot)
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir()
    bootstrap.write_text(
        f"import sys\nsys.stderr.write('fixture diagnostic\\n')\nraise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert "fixture diagnostic" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 native stderr semantics")
@pytest.mark.parametrize("exit_code", [0, 7])
def test_retained_powershell51_forwards_native_diagnostic_and_status(tmp_path, exit_code):
    host = shutil.which("powershell")
    assert host, "Windows PowerShell 5.1 is required for this native contract"
    root = tmp_path / "manager"
    slot = root / "versions" / "1.2.3"
    venv.EnvBuilder(with_pip=False).create(slot)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir()
    bootstrap.write_text(
        f"import sys\nsys.stderr.write('fixture diagnostic\\n')\nraise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "manager.ps1"
    launcher.write_text(si._ps1_binstub(), encoding="utf-8")
    result = _run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(launcher)],
        env={**os.environ, "WORKTREE_MANAGER_ROOT": str(root)}, cwd=tmp_path,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert "fixture diagnostic" in result.stderr


def test_unconstructed_launcher_requires_explicit_canonical_bootstrap(launcher, tmp_path):
    argv, env, slot = launcher
    _payload(slot)
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode != 0
    assert "invalid constructed worktree-manager runtime" in result.stderr
    assert not result.stdout.strip()
    assert not (slot / ".venv").exists()
    assert (slot.parents[1] / si.MARKER).read_text().strip() == "1.2.3"


@pytest.mark.parametrize(
    "damage", ["uninstalled", "missing-metadata", "missing-completion", "missing-entrypoint"],
)
def test_incomplete_environment_does_not_use_foreign_package(launcher, tmp_path, damage):
    argv, env, slot = launcher
    _payload(slot)
    venv.EnvBuilder(with_pip=False).create(slot / ".venv")
    if damage != "uninstalled":
        python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        uv = shutil.which("uv")
        assert uv
        installed = _run(
            [uv, "pip", "install", "--python", str(python), "--no-deps", str(slot)],
            env=env, cwd=slot,
        )
        assert installed.returncode == 0, installed.stderr
        if damage == "missing-metadata":
            metadata = list((slot / ".venv").rglob("copilot_extensions_worktree_manager-*.dist-info"))
            assert len(metadata) == 1
            shutil.rmtree(metadata[0])
        elif damage == "missing-completion":
            (slot / ".install-complete").unlink()
        else:
            (slot / "src" / "worktree_manager" / "__main__.py").unlink()
    _payload(tmp_path)
    # A broken existing environment must not silently import a caller's package
    # or trigger acquisition to turn an incomplete install into success.
    env["UV_OFFLINE"] = "1"
    env["PYTHONPATH"] = str(tmp_path / "src")
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode != 0
    assert "invalid constructed worktree-manager runtime" in result.stderr
    assert not result.stdout.strip()


def test_missing_selected_slot_is_not_another_project(launcher, tmp_path):
    argv, env, slot = launcher
    shutil.rmtree(slot)
    _payload(tmp_path)
    env["UV_OFFLINE"] = "1"
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode != 0
    assert not result.stdout.strip()
