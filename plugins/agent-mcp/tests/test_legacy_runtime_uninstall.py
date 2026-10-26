"""Real native init dispatch over disposable legacy installations, never a host install."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import venv
from types import SimpleNamespace
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DERIVED = (
    "versions", ".venv", "snapshots", "current-version", "last-known-good",
    "running-version.json", "payload-dir", "stamped-version", "deploy-manifest.json",
    ".provision-status",
)


def test_packaged_legacy_uninstall_has_no_canonical_checkout_dependency(tmp_path: Path) -> None:
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("native POSIX packaged cleanup")
    root, env, durable = _fixture(tmp_path / "installation")
    payload = tmp_path / "packaged" / "agent-mcp"
    shutil.copytree(SCRIPTS, payload / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("plugin.json", "pyproject.toml"):
        shutil.copyfile(SCRIPTS.parent / name, payload / name)
    assert not (tmp_path / "libs" / "installer-engine").exists()
    result = subprocess.run(
        [shutil.which("bash"), str(payload / "scripts" / "init.sh"),
         "uninstall", "--install-dir", str(root)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_derived(root, present=False)
    for name, content in durable.items():
        if name != ".install.lock":
            assert (root / name).read_bytes() == content


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
        if name == "snapshots" and not present:
            assert not (root / name).exists() or {
                path.name for path in (root / name).iterdir()
            } <= {".construction-leases"}
            continue
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


@pytest.fixture
def review_shell() -> str:
    name = "pwsh" if os.name == "nt" else "bash"
    executable = shutil.which(name)
    if executable is None and os.name == "nt":
        executable = shutil.which("powershell.exe")
    if executable is None:
        pytest.skip("native review-control shell unavailable")
    return executable


def _review_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> tuple[Any, Path, dict[str, bytes]]:
    spec = importlib.util.spec_from_file_location("mcp_review_uninstall", SCRIPTS / "uninstall_runtime.py")
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

    def run(command: list[str]) -> str:
        if os.name == "nt" and "Get-CimInstance Win32_Process" in command[-1]:
            assert root.is_relative_to(tmp_path)
            return json.dumps([{"pid": os.getpid(), "image": sys.executable, "command": "inspector"}])
        return real_run(command)

    monkeypatch.setattr(helper, "_run", run)
    return helper, root, durable


def _review_source_snapshot(helper: Any, root: Path, tmp_path: Path) -> Path:
    source = helper._load("_mcp_review_source", "versioned_runtime_source.py")
    payload = tmp_path / "source-payload"
    payload.mkdir()
    (payload / "pyproject.toml").write_text(
        '[project]\nname = "agent-mcp"\nversion = "1.0.0"\n', encoding="utf-8",
    )
    (payload / "plugin.json").write_text(
        '{"name":"agent-mcp","version":"1.0.0"}', encoding="utf-8",
    )
    (payload / "src").mkdir()
    (payload / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    digest = source.assembly_hash(payload)
    identity = source.SourceIdentity(
        "agent-mcp", "1.0.0", "0" * 39 + "1", "b" * 40, digest,
        source.runtime_version("1.0.0", "0" * 39 + "1", digest),
    )
    (payload / "runtime-source.json").write_text(
        json.dumps(identity.document()), encoding="utf-8",
    )
    snapshot = source.publish_source_snapshot(payload, root, identity)
    source.publish_runtime_redirects(
        root, snapshot, version=identity.runtime_version,
        expected_source_identity=json.dumps(source.manifest_fields(identity), sort_keys=True),
    )
    return snapshot


def test_review_source_snapshot_coordination_survives_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, review_shell: str,
) -> None:
    helper, root, durable = _review_adapter(monkeypatch, tmp_path)
    snapshot = _review_source_snapshot(helper, root, tmp_path)
    (root / "deploy-manifest.json").unlink()
    coordination = root / "snapshots" / ".construction-leases"
    before = {path.name: path.stat().st_ino for path in coordination.iterdir()}
    assert before
    helper.uninstall(root, review_shell)
    assert not snapshot.exists()
    assert {path.name: path.stat().st_ino for path in coordination.iterdir()}.items() >= before.items()
    _assert_derived(root, present=False)
    assert helper.check_root(root) is False
    helper.uninstall(root, review_shell)
    for name, content in durable.items():
        assert (root / name).read_bytes() == content


def test_review_snapshot_publication_custody_refuses_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, review_shell: str,
) -> None:
    helper, root, _ = _review_adapter(monkeypatch, tmp_path)
    snapshot = _review_source_snapshot(helper, root, tmp_path)
    source = helper._load("_mcp_review_custody_source", "versioned_runtime_source.py")
    with source._runtime().construction_lease(root / "snapshots", snapshot.name, timeout=0):
        with pytest.raises(TimeoutError):
            helper.uninstall(root, review_shell)
    assert snapshot.is_dir()
    _assert_derived(root, present=True)


@pytest.mark.parametrize("ownership", ("manifest", "stamp"))
@pytest.mark.parametrize("failure", ("resolver", "command", "snapshot", "owner"))
def test_review_partial_cleanup_retains_owner_and_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, review_shell: str,
    ownership: str, failure: str,
) -> None:
    helper, root, durable = _review_adapter(monkeypatch, tmp_path)
    snapshot = _review_source_snapshot(helper, root, tmp_path)
    if ownership == "stamp":
        (root / "deploy-manifest.json").unlink()
    owner = root / "deploy-manifest.json" if ownership == "manifest" else snapshot / "pyproject.toml"
    owner_bytes = owner.read_bytes()
    real_remove = helper._remove
    fired = []

    def remove(shell: str, directory: Path, names: tuple[str, ...]) -> None:
        hit = (
            (failure == "resolver" and directory == root / "bin")
            or (failure == "command" and directory == root.parent / ".local" / "bin")
            or (failure == "snapshot" and directory == root / "snapshots")
            or (failure == "owner" and directory == root and names == (
                "deploy-manifest.json" if ownership == "manifest" else "payload-dir",
            ))
        )
        if hit and not fired:
            fired.append(directory)
            real_remove(shell, directory, (snapshot.name,) if failure == "snapshot" else names[:1])
            raise OSError("injected failure after real native deletion")
        real_remove(shell, directory, names)

    monkeypatch.setattr(helper, "_remove", remove)
    with pytest.raises(OSError, match="injected failure after real native deletion"):
        helper.uninstall(root, review_shell)
    assert fired
    assert owner.read_bytes() == owner_bytes
    assert helper.check_root(root) is True
    helper.uninstall(root, review_shell)
    _assert_derived(root, present=False)
    assert helper.check_root(root) is False
    assert not (root.parent / ".local" / "bin" / "agent-mcp.cmd").exists()
    assert not (root.parent / ".local" / "bin" / "agent-mcp").exists()
    for name, content in durable.items():
        assert (root / name).read_bytes() == content


@pytest.mark.parametrize(
    "arguments",
    (
        ("uninstall", "--dry-run"),
        ("uninstall", "--install-dir={custom}"),
        ("uninstall", "--unknown-option"),
        ("uninstall", "--install-dir"),
        ("uninstall", "--install-dir", ""),
        ("uninstall", "--install-dir", "--force"),
        ("uninstall", "--install-dir", "{custom}", "--install-dir", "{default}"),
        ("uninstall", "stamp"),
        ("provision", "uninstall"),
    ),
)
def test_round2_posix_rejects_arguments_without_root_mutation(
    tmp_path: Path, arguments: tuple[str, ...],
) -> None:
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("native POSIX Bash required")
    root, env, _ = _fixture(tmp_path)
    custom = root.parent / "custom-runtime"
    shutil.copytree(root, custom)
    before = {
        path.relative_to(root.parent): path.read_bytes()
        for path in root.parent.rglob("*") if path.is_file()
    }
    result = subprocess.run(
        [shutil.which("bash"), str(SCRIPTS / "init.sh"),
         *(value.format(custom=custom, default=root) for value in arguments)],
        env=env, cwd=SCRIPTS.parent, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert {
        path.relative_to(root.parent): path.read_bytes()
        for path in root.parent.rglob("*") if path.is_file()
    } == before


@pytest.mark.parametrize("action", ("install", "init", "stamp", "provision"))
def test_round2_posix_known_actions_still_dispatch(tmp_path: Path, action: str) -> None:
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("native POSIX Bash required")
    root, env, _ = _fixture(tmp_path)
    env.update(
        AGENT_MCP_ACTION="uninstall", COPILOT_PLUGIN_INSTALL_SMOKE="1",
        COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP="0",
    )
    result = subprocess.run(
        [shutil.which("bash"), str(SCRIPTS / "init.sh"), action],
        env=env, cwd=SCRIPTS.parent, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "smoke.json").is_file()
    _assert_derived(root, present=True)


def test_round2_posix_rejects_inherited_uninstall(tmp_path: Path) -> None:
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("native POSIX Bash required")
    root, env, _ = _fixture(tmp_path)
    env["AGENT_MCP_ACTION"] = "uninstall"
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = subprocess.run(
        [shutil.which("bash"), str(SCRIPTS / "init.sh")],
        env=env, cwd=SCRIPTS.parent, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_round2_posix_trailing_root_uninstall_succeeds(tmp_path: Path) -> None:
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("native POSIX Bash required")
    root, env, durable = _fixture(tmp_path)
    result = subprocess.run(
        [shutil.which("bash"), str(SCRIPTS / "init.sh"), "uninstall",
         "--install-dir", str(root) + "/"],
        env=env, cwd=SCRIPTS.parent, capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_derived(root, present=False)
    for name, content in durable.items():
        if name != ".install.lock":
            assert (root / name).read_bytes() == content


@pytest.mark.parametrize("invalid", ("none", "pid", "script", "action", "root", "flag", "image"))
def test_round2_only_verified_installer_parent_is_excluded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid: str,
) -> None:
    helper, root, _ = _review_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(helper.os, "getppid", lambda: 123)
    arguments = ["bash", str(SCRIPTS / "init.sh"), "uninstall", "--install-dir", str(root) + os.sep]
    pid = 124 if invalid == "pid" else 123
    image = str((root if invalid == "image" else Path(sys.executable).parent) / "bash")
    if invalid == "script":
        arguments[1] = str(tmp_path / "unrelated.sh")
    elif invalid == "action":
        arguments[2] = "provision"
    elif invalid == "root":
        arguments[-1] = str(tmp_path / "different-runtime")
    elif invalid == "flag":
        arguments.append("--dry-run")
    assert helper._verified_uninstall_parent(root, pid, arguments, image) is (invalid == "none")


def test_round2_windows_launcher_only_bootstrap(tmp_path: Path, review_shell: str) -> None:
    if os.name != "nt":
        pytest.skip("native Windows Python launcher boundary")
    launcher = shutil.which("py")
    if launcher is None or Path(launcher).suffix.casefold() != ".exe":
        pytest.skip("existing native py.exe unavailable")
    root, env, _ = _fixture(tmp_path)
    bin_dir = tmp_path / "launcher-only"
    bin_dir.mkdir()
    shutil.copyfile(launcher, bin_dir / "py.exe")
    env["PATH"] = str(bin_dir)
    assert shutil.which("python", path=env["PATH"]) is None
    assert shutil.which("python3", path=env["PATH"]) is None
    absent = tmp_path / "never-installed"
    result = _run(review_shell, absent, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not absent.exists()
    _assert_derived(root, present=True)
    # Exercise the same resolver with an existing runtime candidate that must
    # never be selected by uninstall, without executing or modifying that runtime.
    text = (SCRIPTS / "init.ps1").read_text(encoding="utf-8")
    match = re.search(r"(?ms)^function Get-BootstrapPython \{.*?^\}", text)
    assert match is not None
    candidate = root / "versions" / "1.0.0" / "Scripts" / "python.exe"
    candidate.parent.mkdir()
    candidate.write_bytes(b"not an executable")
    quoted_scripts = str(SCRIPTS).replace("'", "''")
    quoted_slot = str(candidate.parent.parent).replace("'", "''")
    command = (
        "$ErrorActionPreference='Stop'; "
        f". '{quoted_scripts}\\versioned_runtime_native.ps1'; "
        f"$VenvDir='{quoted_slot}'; $LinkDir=$VenvDir; "
        + match.group() + "; Get-BootstrapPython -ExternalOnly"
    )
    resolved = subprocess.run(
        [review_shell, "-NoProfile", "-NonInteractive", "-Command", command],
        env=env, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert resolved.returncode == 0, resolved.stdout + resolved.stderr
    assert Path(resolved.stdout.strip()).is_file()
    assert not Path(resolved.stdout.strip()).is_relative_to(root)


@pytest.mark.parametrize("action", ("check", "remove"))
def test_final_helper_rejects_original_parent_components(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], action: str,
) -> None:
    helper, root, _ = _review_adapter(monkeypatch, tmp_path)
    requested = str(root / "link" / ".." / "target")

    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("request reached normalization or uninstall dispatch")

    monkeypatch.setattr(helper, "os", SimpleNamespace(path=SimpleNamespace(abspath=unexpected)))
    monkeypatch.setattr(helper.sys, "argv", [
        "uninstall_runtime.py", action, "--root", requested, "--shell", "unused",
    ])
    monkeypatch.setattr(helper, "uninstall", unexpected)
    assert helper.main() == 1
    assert "parent-traversal path components" in capsys.readouterr().err
    _assert_derived(root, present=True)


def test_final_posix_symlink_parent_request_preserves_both_roots(tmp_path: Path) -> None:
    bash = shutil.which("bash") if os.name != "nt" else None
    if bash is None:
        pytest.skip("native POSIX Bash required")
    default, env, _ = _fixture(tmp_path)
    lexical = default.parent / "target"
    outside = tmp_path / "outside"
    nested = outside / "nested"
    nested.mkdir(parents=True)
    physical = outside / "target"
    for root in (lexical, physical):
        shutil.copytree(default, root)
        (root / ".install.lock").unlink()
    link = default.parent / "link"
    link.symlink_to(nested, target_is_directory=True)
    requested = str(link) + "/../target"
    before = {
        path: path.read_bytes() for root in (default, lexical, physical)
        for path in root.rglob("*") if path.is_file()
    }
    result = subprocess.run(
        [bash, str(SCRIPTS / "init.sh"), "uninstall", "--install-dir", requested],
        env=env, cwd=default.parent, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "parent-traversal path components" in result.stderr
    assert {
        path: path.read_bytes() for root in (default, lexical, physical)
        for path in root.rglob("*") if path.is_file()
    } == before
    assert not (lexical / ".install.lock").exists()
    assert not (physical / ".install.lock").exists()


def test_final_windows_parent_request_precedes_bootstrap(tmp_path: Path, review_shell: str) -> None:
    if os.name != "nt":
        pytest.skip("native Windows request boundary")
    root, env, _ = _fixture(tmp_path)
    (root / "child").mkdir()
    empty_path = tmp_path / "no-interpreters"
    empty_path.mkdir()
    env["PATH"] = str(empty_path)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = _run(review_shell, Path(str(root) + "\\child\\.."), env)
    assert result.returncode != 0
    assert "parent-traversal path components" in result.stderr, result.stdout + result.stderr
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_final_normal_relative_root_remains_supported(tmp_path: Path, review_shell: str) -> None:
    default, env, _ = _fixture(tmp_path)
    target = default.parent / "release..dev"
    if os.name == "nt":
        command = [
            review_shell, "-NoProfile", "-NonInteractive", "-File", str(SCRIPTS / "init.ps1"),
            "uninstall", "-InstallDir", ".\\release..dev",
        ]
    else:
        shutil.copytree(default, target)
        command = [
            review_shell, str(SCRIPTS / "init.sh"), "uninstall", "--install-dir", "./release..dev",
        ]
    before = {path: path.read_bytes() for path in default.rglob("*") if path.is_file()}
    result = subprocess.run(
        command, env=env, cwd=default.parent, capture_output=True, text=True, timeout=90,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if os.name == "nt":
        assert not target.exists()
    else:
        _assert_derived(target, present=False)
    assert {path: path.read_bytes() for path in default.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("outcome", ("success", "timeout", "error"))
def test_final_posix_runner_uses_caller_held_custody(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str,
) -> None:
    helper, _, _ = _review_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(helper, "os", SimpleNamespace(name="posix", environ=os.environ))
    observed = []

    def run_owned(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed.append(kwargs)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 60)
        if outcome == "error":
            raise subprocess.CalledProcessError(1, command, stderr="injected failure")
        return subprocess.CompletedProcess(command, 0, "settled", "")

    def load(name: str, filename: str) -> Any:
        assert Path(filename).name == "installer_engine_process.py"
        return SimpleNamespace(run_owned=run_owned)

    monkeypatch.setattr(helper, "_load", load)
    if outcome == "timeout":
        with pytest.raises(subprocess.TimeoutExpired):
            helper._run(["fixture-child"])
    elif outcome == "error":
        with pytest.raises(RuntimeError, match="injected failure"):
            helper._run(["fixture-child"])
    else:
        assert helper._run(["fixture-child"]) == "settled"
    assert observed == [{
        "cwd": Path.cwd(), "env": dict(os.environ), "timeout": 60,
        "procutil": None, "construction_fd": None,
    }]


def test_final_posix_timeout_settles_descendant_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    bash = shutil.which("bash") if os.name != "nt" else None
    if bash is None:
        pytest.skip("native POSIX process settlement required")
    helper, _, _ = _review_adapter(monkeypatch, tmp_path)
    original_load = helper._load

    def load(name: str, filename: str) -> Any:
        module = original_load(name, filename)
        if Path(filename).name == "installer_engine_process.py":
            original_run = module.run_owned

            def short_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                assert kwargs["timeout"] == 60
                kwargs["timeout"] = 0.2
                return original_run(command, **kwargs)

            module.run_owned = short_run
        return module

    monkeypatch.setattr(helper, "_load", load)
    late = tmp_path / "late-write"
    with pytest.raises(subprocess.TimeoutExpired):
        helper._run([
            bash, "-c", '(sleep 1; printf late > "$1") & wait', "timeout-fixture", str(late),
        ])
    time.sleep(1.2)
    assert not late.exists()


@pytest.mark.parametrize("host", ("powershell.exe", "pwsh"))
def test_final_windows_relative_root_uses_provider_location(tmp_path: Path, host: str) -> None:
    executable = shutil.which(host) if os.name == "nt" else None
    if executable is None:
        pytest.skip(f"native Windows {host} required")
    default, env, _ = _fixture(tmp_path)
    desired = tmp_path / "desired-parent"
    other = tmp_path / "other-parent"
    desired.mkdir()
    other.mkdir()
    owned = other / "scoped-runtime"
    shutil.copytree(default, owned)
    before = {
        path: path.read_bytes() for root in (default, owned)
        for path in root.rglob("*") if path.is_file()
    }
    wrapper = tmp_path / "provider-cwd-control.ps1"
    wrapper.write_text(
        "param($Installer, $Desired, $Other)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "Set-Location -LiteralPath $Desired\n"
        "[IO.Directory]::SetCurrentDirectory($Other)\n"
        "& $Installer uninstall -InstallDir scoped-runtime\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(wrapper),
         str(SCRIPTS / "init.ps1"), str(desired), str(other)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already absent" in result.stdout
    assert not (desired / "scoped-runtime").exists()
    assert {
        path: path.read_bytes() for root in (default, owned)
        for path in root.rglob("*") if path.is_file()
    } == before


@pytest.mark.parametrize("lifecycle", [False, True])
def test_bom_manifest_provenance_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lifecycle: bool,
) -> None:
    root, _, _ = _fixture(tmp_path)
    record: dict[str, object] = {"schema_version": 3, "service": "agent-mcp"}
    if lifecycle:
        record["commandLifecycle"] = {"owner": "incumbent"}
    manifest = root / "deploy-manifest.json"
    manifest.write_text(json.dumps(record), encoding="utf-8-sig")
    spec = importlib.util.spec_from_file_location("mcp_bom_test", SCRIPTS / "uninstall_runtime.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    binding = helper._load("_mcp_bom_binding", "command_binding.py")
    primitive = binding._load_primitive(SCRIPTS.parent)
    monkeypatch.setattr(primitive, "probe_legacy_entrypoint", lambda **kwargs: {"allowMutation": True})
    monkeypatch.setattr(binding, "_load_primitive", lambda _: primitive)

    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise binding.SourceProvenanceUnavailable("fixture provenance unavailable")

    monkeypatch.setattr(binding, "publish_legacy_binding", unavailable)
    monkeypatch.setattr(helper, "_load", lambda *args: binding)
    before = manifest.read_bytes()
    assert before.startswith(b"\xef\xbb\xbf")
    if lifecycle:
        with pytest.raises(RuntimeError, match="Cannot revalidate incumbent lifecycle ownership"):
            helper.check_root(root)
    else:
        assert helper.check_root(root) is True
    assert manifest.read_bytes() == before
    _assert_derived(root, present=True)
