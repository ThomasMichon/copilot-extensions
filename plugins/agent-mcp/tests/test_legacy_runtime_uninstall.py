"""Real native init dispatch over disposable legacy installations, never a host install."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DERIVED = (
    "versions", ".venv", "snapshots", "current-version", "last-known-good",
    "running-version.json", "payload-dir", "stamped-version", "deploy-manifest.json",
    ".provision-status",
)


@pytest.fixture(params=("powershell.exe", "pwsh", "bash"))
def shell(request: pytest.FixtureRequest) -> str:
    name = str(request.param)
    if (name == "bash") == (os.name == "nt"):
        pytest.skip("native OS shell only; no WSL/Git Bash relay")
    executable = shutil.which(name)
    if executable is None:
        pytest.skip(f"{name} unavailable")
    return executable


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, str], dict[str, bytes]]:
    home = tmp_path / "home"
    root = home / ".agent-mcp"
    root.mkdir(parents=True)
    for name in DERIVED:
        path = root / name
        if name in ("versions", ".venv", "snapshots"):
            path.mkdir()
            if name in ("versions", "snapshots"):
                (path / "1.0.0").mkdir()
            else:
                (path / "derived").write_text("rebuildable", encoding="utf-8")
        else:
            path.write_text(
                '{"schema_version":3,"service":"agent-mcp"}'
                if name == "deploy-manifest.json" else "1.0.0",
                encoding="utf-8",
            )
    (root / "bin").mkdir()
    for name in ("resolve-runtime.ps1", "resolve-runtime.sh"):
        (root / "bin" / name).write_text("generated resolver", encoding="utf-8")
    durable = {
        "bridges/example.yaml": b"server: preserved\n",
        "state.db": b"durable database",
        "tokens.json": b'{"durable":"credential fixture"}',
        "unknown.txt": b"operator content",
        "bin/operator-command": b"preserved unknown command",
        "materialized/example/tool.py": b"preserved materialized fleet",
        ".install.lock": b"persistent lock inode",
    }
    for name, content in durable.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "agent-mcp").write_text(
        '#!/usr/bin/env bash\n_name="agent-mcp"\n_root="$HOME/.$_name"\n'
        '. "$_root/bin/resolve-runtime.sh"\nexec "$AGENT_RT_PY" -m agent_mcp "$@"\n',
        encoding="utf-8",
    )
    (local_bin / "agent-mcp.cmd").write_text(
        '@echo off\nsetlocal\nset "_ROOT=%USERPROFILE%\\.agent-mcp"\n'
        'call :_resolve\n"%_PY%" -m agent_mcp %*\n', encoding="utf-8",
    )
    (local_bin / "agent-mcp.ps1").write_text("operator command", encoding="utf-8")
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_PLUGIN_", "COPILOT_EXTENSIONS_"))
        and key not in ("AGENT_MCP_HOME", "AGENT_MCP_ACTION", "AGENT_RT_ROOT")
    }
    env.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1",
        COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC="1",
    )
    if os.name == "nt":
        # pwsh's own startup cache is not an installer mutation.
        for key in ("LOCALAPPDATA", "APPDATA"):
            directory = tmp_path / key
            directory.mkdir()
            env[key] = str(directory)
    return root, env, durable


def _run(shell: str, root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    if os.name == "nt":
        command = [shell, "-NoProfile", "-NonInteractive", "-File", str(SCRIPTS / "init.ps1"),
                   "uninstall", "-InstallDir", str(root)]
    else:
        command = [shell, str(SCRIPTS / "init.sh"), "uninstall", "--install-dir", str(root)]
    return subprocess.run(
        command, env=env, cwd=SCRIPTS.parent, capture_output=True, text=True, timeout=90,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _assert_derived(root: Path, *, present: bool) -> None:
    for name in DERIVED:
        assert (root / name).exists() is present, name


def test_uninstall_preserves_state_and_is_idempotent(
    tmp_path: Path, shell: str,
) -> None:
    root, env, durable = _fixture(tmp_path)
    lock_identity = (root / ".install.lock").stat().st_ino
    for _ in range(2):
        result = _run(shell, root, env)
        if result.returncode and "Unattributed MCP runtime process" in result.stderr:
            pytest.skip("host has a base-interpreter MCP process with no attributable installation")
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_derived(root, present=False)
        for name, content in durable.items():
            if name != ".install.lock":
                assert (root / name).read_bytes() == content
        assert (root / ".install.lock").stat().st_ino == lock_identity
        for name in ("resolve-runtime.ps1", "resolve-runtime.sh"):
            assert not (root / "bin" / name).exists()
        local_bin = root.parent / ".local" / "bin"
        assert not (local_bin / "agent-mcp").exists()
        assert not (local_bin / "agent-mcp.cmd").exists()
        assert (local_bin / "agent-mcp.ps1").read_text(encoding="utf-8") == "operator command"


@pytest.mark.parametrize(
    "unsafe", ("home", "ancestor", "file", "cell", "managed", "foreign-manifest", "reservation"),
)
def test_invalid_root_refuses_before_lock_or_runtime_mutation(
    tmp_path: Path, shell: str, unsafe: str,
) -> None:
    root, env, _ = _fixture(tmp_path)
    if unsafe == "home":
        target = root.parent
    elif unsafe == "ancestor":
        target = tmp_path
    elif unsafe == "file":
        target = root / "unknown.txt"
    elif unsafe in ("cell", "managed"):
        target = root
        (root / ("install.json" if unsafe == "cell" else "runtime-deployment.json")).write_text(
            "{}", encoding="utf-8",
        )
    else:
        target = root
        manifest: dict[str, object] = {
            "schema_version": 3, "service": "another-product" if unsafe == "foreign-manifest" else "agent-mcp",
        }
        if unsafe == "reservation":
            manifest["commandLifecycle"] = {"unrecognized": "owner"}
        (root / "deploy-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = _run(shell, target, env)
    assert result.returncode != 0, result.stdout
    after = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert after == before
    if target != root:
        assert not (target / ".install.lock").exists()
    _assert_derived(root, present=True)


def test_absent_root_is_noop_without_creation(tmp_path: Path, shell: str) -> None:
    _, env, _ = _fixture(tmp_path)
    root = tmp_path / "never-installed"
    result = _run(shell, root, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not root.exists()


@pytest.mark.parametrize("ownership", ("missing", "foreign-stamp", "outside-stamp"))
def test_unattributed_root_is_preserved(
    tmp_path: Path, shell: str, ownership: str,
) -> None:
    root, env, _ = _fixture(tmp_path)
    (root / "deploy-manifest.json").unlink()
    if ownership == "missing":
        (root / "payload-dir").unlink()
    else:
        payload = root / "snapshots" / "1.0.0" if ownership == "foreign-stamp" else tmp_path / "payload"
        payload.mkdir(exist_ok=True)
        (payload / "pyproject.toml").write_text(
            '[project]\nname = "another-product"\n', encoding="utf-8",
        )
        (root / "payload-dir").write_text(str(payload), encoding="utf-8")
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = _run(shell, root, env)
    assert result.returncode != 0
    assert {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert (root / "versions" / "1.0.0").is_dir()


def test_owned_stamp_without_manifest_can_be_removed(tmp_path: Path, shell: str) -> None:
    root, env, durable = _fixture(tmp_path)
    (root / "deploy-manifest.json").unlink()
    payload = root / "snapshots" / "1.0.0"
    (payload / "pyproject.toml").write_text('[project]\nname = "agent-mcp"\n', encoding="utf-8")
    (root / "payload-dir").write_text(str(payload), encoding="utf-8")
    result = _run(shell, root, env)
    if result.returncode and "Unattributed MCP runtime process" in result.stderr:
        pytest.skip("host has a base-interpreter MCP process with no attributable installation")
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_derived(root, present=False)
    for name, content in durable.items():
        if name != ".install.lock":
            assert (root / name).read_bytes() == content


def test_active_generation_refuses_without_deletion(tmp_path: Path, shell: str) -> None:
    root, env, _ = _fixture(tmp_path)
    slot = root / "versions" / "1.0.0"
    venv.EnvBuilder(with_pip=False).create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    process = subprocess.Popen(
        [str(python), "-I", "-c", "import sys; print('ready', flush=True); sys.stdin.readline()"],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        result = _run(shell, root, env)
        assert result.returncode != 0, result.stdout
        assert "Active" in result.stderr, result.stdout + result.stderr
        _assert_derived(root, present=True)
        assert process.poll() is None
    finally:
        process.communicate("\n", timeout=15)
    assert process.returncode == 0


def test_linked_ancestor_refuses_before_mutation(tmp_path: Path, shell: str) -> None:
    root, env, _ = _fixture(tmp_path)
    link = tmp_path / "redirected"
    try:
        link.symlink_to(root.parent, target_is_directory=True)
    except OSError:
        pytest.skip("creating directory symlinks is unavailable")
    before = (root / ".install.lock").read_bytes()
    result = _run(shell, link / root.name, env)
    assert result.returncode != 0
    _assert_derived(root, present=True)
    assert (root / ".install.lock").read_bytes() == before


@pytest.mark.parametrize("inventory", ("versions", "snapshots"))
def test_unrecognized_inventory_is_preserved(
    tmp_path: Path, shell: str, inventory: str,
) -> None:
    root, env, _ = _fixture(tmp_path)
    unknown = root / inventory / "operator-notes.txt"
    unknown.write_text("preserve", encoding="utf-8")
    result = _run(shell, root, env)
    assert result.returncode != 0
    assert "Unrecognized" in result.stderr, result.stdout + result.stderr
    assert unknown.read_text(encoding="utf-8") == "preserve"
    _assert_derived(root, present=True)


def test_install_lock_contention_refuses_without_deletion(tmp_path: Path, shell: str) -> None:
    if os.name != "nt":
        pytest.skip("native Windows FileShare.None lock boundary")
    root, env, _ = _fixture(tmp_path)
    lock_path = str(root / ".install.lock").replace("'", "''")
    script = (
        f"$f=[IO.File]::Open('{lock_path}','OpenOrCreate','ReadWrite','None'); "
        "try { [Console]::Out.WriteLine('held'); [Console]::Out.Flush(); "
        "[void][Console]::In.ReadLine() } finally { $f.Dispose() }"
    )
    owner = subprocess.Popen(
        [shell, "-NoProfile", "-NonInteractive", "-Command", script], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        assert owner.stdout is not None
        assert owner.stdout.readline().strip() == "held"
        result = _run(shell, root, env)
        assert result.returncode != 0
        _assert_derived(root, present=True)
        assert owner.poll() is None
    finally:
        owner.communicate("\n", timeout=15)
    assert owner.returncode == 0


def test_unsupported_process_inspection_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("mcp_uninstall_test", SCRIPTS / "uninstall_runtime.py")
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    monkeypatch.setattr(helper.os, "name", "posix")
    monkeypatch.setattr(helper.sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="supported only on Windows and Linux"):
        helper.refuse_active(tmp_path, "unavailable-shell", object())


def test_native_removal_with_quiescent_process_inventory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shell: str,
) -> None:
    """Mock only CIM's inventory, still executing custody and real native deletion."""
    if os.name != "nt":
        pytest.skip("Windows CIM adapter boundary")
    spec = importlib.util.spec_from_file_location("mcp_uninstall_quiescent", SCRIPTS / "uninstall_runtime.py")
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    root, env, durable = _fixture(tmp_path)
    for key in tuple(os.environ):
        if key not in env:
            monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    real_run = helper._run
    inspections = []

    def run(command: list[str]) -> str:
        if "Get-CimInstance Win32_Process" in command[-1]:
            inspections.append(command)
            return json.dumps([{"pid": os.getpid(), "image": sys.executable, "command": "inspector"}])
        return real_run(command)

    monkeypatch.setattr(helper, "_run", run)
    # The native dispatch tests separately exercise .install.lock admission.
    helper.uninstall(root, shell)
    _assert_derived(root, present=False)
    for name, content in durable.items():
        assert (root / name).read_bytes() == content
    assert (root.parent / ".local" / "bin" / "agent-mcp.ps1").read_text(
        encoding="utf-8",
    ) == "operator command"
    assert len(inspections) == 2
    helper.uninstall(root, shell)
    _assert_derived(root, present=False)
    # Deferred stamp rebuilds source snapshots/wrappers only, with no venv,
    # package installation, network request, task, or service operation.
    stamped = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(SCRIPTS / "init.ps1"),
         "stamp", "-InstallDir", str(root)],
        env=env, cwd=SCRIPTS.parent, capture_output=True, text=True, timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    assert (root / "payload-dir").is_file()
    assert (root / "stamped-version").is_file()
    assert (root.parent / ".local" / "bin" / "agent-mcp.cmd").is_file()
    assert not (root / "versions").exists()
    helper.uninstall(root, shell)
    _assert_derived(root, present=False)
    for name, content in durable.items():
        if name != ".install.lock":
            assert (root / name).read_bytes() == content
