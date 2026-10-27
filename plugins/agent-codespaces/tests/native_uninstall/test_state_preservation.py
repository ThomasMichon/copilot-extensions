"""Real native uninstall adapters: explicit runtime removal, never a state purge."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
PLUGINS = ("agent-codespaces", "agent-ssh", "agent-pull-requests")
ENGINE = ROOT / "libs" / "installer-engine"
pytestmark = pytest.mark.guard


def _policy(plugin):
    path = ROOT / "plugins" / plugin / "scripts" / "uninstall-state.py"
    spec = importlib.util.spec_from_file_location("uninstall_" + plugin.replace("-", "_"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _quote(path, shell):
    value = str(path)
    if shell == "bash":
        value = value.replace("\\", "/")
        if os.name == "nt" and len(value) > 1 and value[1] == ":":
            value = "/" + value[0].lower() + value[2:]
        return "'" + value.replace("'", "'\\''") + "'"
    return "'" + value.replace("'", "''") + "'"


def _host(shell):
    if shell == "bash" and os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
        host = str(candidate) if candidate and candidate.is_file() else None
    else:
        host = shutil.which(shell)
    if not host:
        pytest.skip(f"{shell} unavailable")
    return host


def _fixture(tmp_path, plugin):
    home = tmp_path / "home"
    root = home / ("." + plugin)
    root.mkdir(parents=True)
    (home / ".ssh" / "config.d").mkdir(parents=True)
    (home / ".ssh" / "config.d" / "static.conf").write_bytes(b"Host fixture\n HostName localhost\n")
    for directory in (".venv", "versions", "snapshots"):
        (root / directory).mkdir()
        generation = root / directory if directory == ".venv" else root / directory / "1.2.3"
        generation.mkdir(exist_ok=True)
        (generation / "runtime").write_bytes(b"reconstructible code")
    (root / ".venv" / "pyvenv.cfg").write_text(
        f"home = {Path(sys.executable).parent}\n", encoding="utf-8",
    )
    for name in ("current-version", "last-known-good", "payload-dir", "stamped-version"):
        (root / name).write_bytes(b"runtime selector")
    (root / "deploy-manifest.json").write_text(json.dumps({
        "schema_version": 3, "service": plugin,
        "source": {"plugin": plugin, "kind": "local", "path": str(ROOT / "plugins" / plugin), "version": "1.2.3", "repo": "copilot-extensions"},
        "venv": str(root / "versions" / "1.2.3"),
        "runtime": "python", "deployed_at": "2026-01-01T00:00:00Z", "deployed_by": "fixture",
    }), encoding="utf-8")
    durable = {
        "config.yaml": b"operator: config\n",
        "auth.json": b'{"identity": "fixture"}',
        "queue.db": b"\x00queue\xff",
        "unknown/subdir/precious": b"never remove unknown data\x00",
        "adopted-repos.yaml": b"repos: []\n",
        "account-bindings.json": b'{"account": "fixture"}',
        "account-bindings.json.lock": b"lock",
        "copilot-hosts.json": b'{"hosts": {}}',
        "fragment-warning-state.json": b'{"warned": true}',
        "subscriptions.json": b'{"watch": "durable"}',
        "watch-subscriptions.json": b'{"subscriptions": {"fixture": "durable"}}',
        "watch-daemon.lock": b"persistent watch coordination",
        "watch/journal.jsonl": b'{"event": 1}\n',
        "watch/replay.db": b"\x00replay",
        "watch/observations.json": b'{"sha": "a"}',
        ".install.lock": b"coordination",
        ".install-stage/live-owner/payload": b"publisher source",
        "bin/operator-custom": b"unknown helper content",
        "scripts/operator-custom.py": b"unknown script content",
        "versions/operator-note.txt": b"unknown runtime container child",
        "snapshots/operator-note.txt": b"unknown snapshot container child",
        "logs/operator.log": b"persistent operator log\n",
    }
    for name, value in durable.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    return home, root, durable


def _run(tmp_path, plugin, shell, home, root, *, preview=False, before=""):
    host = _host(shell)
    scripts = ROOT / "plugins" / plugin / "scripts"
    q = lambda value: _quote(value, shell)
    if shell == "bash":
        script = f"""
set -euo pipefail
SCRIPT_DIR={q(scripts)}
INSTALL_DIR={q(root)}
HOME={q(home)}
PY={q(sys.executable)}
_bootstrap_python() {{ printf '%s' "$PY"; }}
source {q(ENGINE / 'installer-engine.sh')}
source "$SCRIPT_DIR/uninstall-runtime.sh"
{before}
uninstall_state_preserving_runtime {'1' if preview else '0'}
"""
        arguments = [host]
        suffix = "sh"
    else:
        script = f"""
$ErrorActionPreference = 'Stop'
$InstallDir = {q(root)}
$OwnerTaskName = 'fixture-owner'
$RealPython = {q(sys.executable)}
function Get-BootstrapPython {{ $RealPython }}
function Get-ScheduledTask {{ param($ErrorAction) }}
function Invoke-Hidden {{
    param($FilePath, $ArgList, $ErrorOutput)
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $FilePath
    foreach ($argument in $ArgList) {{ $start.ArgumentList.Add($argument) }}
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($start)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $ErrorOutput.Value = $stderr.Result
    $global:LASTEXITCODE = $process.ExitCode
    return $stdout.Result
}}
. {q(ENGINE / 'installer-engine.ps1')}
. {q(scripts / 'uninstall-runtime.ps1')}
{before}
Invoke-StatePreservingUninstall {'-Preview' if preview else ''}
"""
        arguments = [host, "-NoProfile", "-NonInteractive", "-File"]
        suffix = "ps1"
    harness = tmp_path / f"uninstall.{suffix}"
    harness.write_text(script, encoding="utf-8")
    environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    environment["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    return subprocess.run(
        [*arguments, str(harness)], cwd=tmp_path, env=environment,
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _assert_retained(root, durable):
    assert root.is_dir()
    for name, value in durable.items():
        assert (root / name).read_bytes() == value, name
    assert (root.resolve().parent / ".ssh" / "config.d" / "static.conf").read_bytes() == b"Host fixture\n HostName localhost\n"


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", ("pwsh", "bash"))
def test_native_success_removes_runtime_and_preserves_all_state(tmp_path, plugin, shell):
    home, root, durable = _fixture(tmp_path, plugin)
    identity = root.stat().st_ino
    result = _run(tmp_path, plugin, shell, home, root)
    # Do not skip or substitute a fake remover when the canonical uninstall
    # engine or its entry-list API has not reached the source base yet.
    assert result.returncode == 0, result.stdout + result.stderr
    assert root.stat().st_ino == identity
    _assert_retained(root, durable)
    for name in _policy(plugin).ENTRIES:
        if name in ("versions", "snapshots"):
            assert not (root / name / "1.2.3").exists()
            continue
        assert not os.path.lexists(root / name), name


@pytest.mark.parametrize("plugin", ("agent-codespaces", "agent-ssh"))
@pytest.mark.parametrize("shell", ("pwsh", "bash"))
def test_native_preview_is_byte_preserving(tmp_path, plugin, shell):
    home, root, durable = _fixture(tmp_path, plugin)
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = _run(tmp_path, plugin, shell, home, root, preview=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dry-run" in result.stdout
    _assert_retained(root, durable)
    assert before == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", ("pwsh", "bash"))
@pytest.mark.parametrize("mutation", ("foreign", "missing", "traversal", "hardlink"))
def test_native_refuses_invalid_ownership_without_touching_state(tmp_path, plugin, shell, mutation):
    home, root, durable = _fixture(tmp_path, plugin)
    marker = root / "deploy-manifest.json"
    if mutation == "foreign":
        data = json.loads(marker.read_text())
        data["source"]["plugin"] = "foreign"
        marker.write_text(json.dumps(data))
    elif mutation == "missing":
        marker.unlink()
    elif mutation == "hardlink":
        os.link(marker, root / "unknown-manifest-link")
    else:
        root = root / "versions" / ".."
    result = _run(tmp_path, plugin, shell, home, root)
    assert result.returncode != 0
    assert "uninstall" in (result.stdout + result.stderr).casefold()
    _assert_retained(root, durable)
    assert (root / "versions" / "1.2.3" / "runtime").read_bytes() == b"reconstructible code"


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", ("pwsh", "bash"))
def test_native_refuses_linked_root(tmp_path, plugin, shell):
    home, root, durable = _fixture(tmp_path, plugin)
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink privilege unavailable")
    result = _run(tmp_path, plugin, shell, home, linked)
    assert result.returncode != 0
    _assert_retained(root, durable)
    assert (root / "versions").exists()


@pytest.mark.parametrize("shell", ("pwsh", "bash"))
@pytest.mark.parametrize("plugin,name", (
    ("agent-codespaces", "sockets/master"),
    ("agent-codespaces", "ssh/reconnect.conf"),
    ("agent-codespaces", "connection-owner.json"),
    ("agent-codespaces", "connection-owner.live.json"),
    ("agent-ssh", "forward-keepers/lease.json"),
))
def test_native_transport_custody_refuses_without_disconnect(tmp_path, shell, plugin, name):
    home, root, durable = _fixture(tmp_path, plugin)
    transport = root / name
    transport.parent.mkdir(parents=True, exist_ok=True)
    value = (
        f"ProxyCommand {root}/versions/1.2.3/bin/python -m agent_codespaces\n".encode()
        if name.endswith(".conf") else b"transport closure"
    )
    if name == "connection-owner.json":
        value = b'{"fixture": {"pinned": true, "sessions": {"tenant": {}}}}'
    elif name == "connection-owner.live.json":
        value = b'{"active": ["fixture"], "bridge_forwards": []}'
    transport.write_bytes(value)
    result = _run(tmp_path, plugin, shell, home, root)
    assert result.returncode != 0
    assert "transport" in (result.stdout + result.stderr) or "keeper" in (result.stdout + result.stderr)
    assert transport.read_bytes() == value
    _assert_retained(root, durable)
    assert (root / "versions").exists()


@pytest.mark.parametrize("shell", ("pwsh", "bash"))
def test_native_ssh_reconnect_configuration_is_preserved(tmp_path, shell):
    home, root, durable = _fixture(tmp_path, "agent-ssh")
    fragment = home / ".ssh" / "config.d" / "fixture.conf"
    fragment.write_text(f'Host fixture\n ProxyCommand "{root}/.venv/bin/python" -m agent_ssh\n')
    before = fragment.read_bytes()
    result = _run(tmp_path, "agent-ssh", shell, home, root)
    assert result.returncode != 0
    assert "reconnect" in result.stdout + result.stderr
    assert fragment.read_bytes() == before
    _assert_retained(root, durable)
    assert (root / "versions").exists()


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", ("pwsh", "bash"))
@pytest.mark.parametrize("kind", ("construction", "retention", "snapshot", "redirects"))
def test_native_concurrent_publisher_retains_kernel_custody(tmp_path, plugin, shell, kind):
    home, root, durable = _fixture(tmp_path, plugin)
    helper = ROOT / "plugins" / plugin / "scripts" / "versioned_runtime_leases.py"
    spec = importlib.util.spec_from_file_location("uninstall_fixture_leases", helper)
    assert spec and spec.loader
    leases = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(leases)
    context = {
        "construction": lambda: leases.construction_lease(root, "1.2.3", timeout=0),
        "retention": lambda: leases._kernel_lease(root, "1.2.3", kind="retention", shared=True, timeout=0),
        "snapshot": lambda: leases.construction_lease(root / "snapshots", "1.2.3", timeout=0),
        "redirects": lambda: leases._kernel_lease(root, "redirects", kind="publication", shared=False, timeout=0),
    }[kind]
    with context():
        result = _run(tmp_path, plugin, shell, home, root)
        assert result.returncode != 0
        assert "lease" in (result.stdout + result.stderr).lower()
        assert (root / "versions" / "1.2.3" / "runtime").read_bytes() == b"reconstructible code"
        _assert_retained(root, durable)


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", ("pwsh", "bash"))
def test_native_settled_lease_inodes_survive_success(tmp_path, plugin, shell):
    home, root, durable = _fixture(tmp_path, plugin)
    helper = ROOT / "plugins" / plugin / "scripts" / "versioned_runtime_leases.py"
    spec = importlib.util.spec_from_file_location("uninstall_fixture_settled", helper)
    assert spec and spec.loader
    leases = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(leases)
    with leases.batch_removal_custody(root, ["1.2.3"]):
        pass
    with leases.construction_lease(root / "snapshots", "1.2.3", timeout=0):
        pass
    with leases._kernel_lease(root, "redirects", kind="publication", shared=False, timeout=0):
        pass
    inodes = {p: p.stat().st_ino for p in root.rglob("*.lock")}
    result = _run(tmp_path, plugin, shell, home, root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert inodes == {p: p.stat().st_ino for p in inodes}
    _assert_retained(root, durable)
    assert not (root / "versions" / "1.2.3").exists()
    assert not (root / "snapshots" / "1.2.3").exists()


@pytest.mark.parametrize("shell", ("pwsh", "bash"))
def test_native_actual_socket_remains_connectable(tmp_path, shell, monkeypatch):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("Unix sockets unavailable")
    home, root, durable = _fixture(tmp_path, "agent-codespaces")
    (root / "sockets").mkdir()
    monkeypatch.chdir(root)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        try:
            listener.bind("sockets/master")
        except OSError as exc:
            pytest.skip(f"fixture Unix socket unavailable: {exc}")
        listener.listen(1)
        listener.settimeout(3)
        result = _run(tmp_path, "agent-codespaces", shell, home, root)
        assert result.returncode != 0
        assert "transport custody" in result.stdout + result.stderr
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            client.connect("sockets/master")
            connection, _ = listener.accept()
            connection.close()
        _assert_retained(root, durable)


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", ("pwsh", "bash"))
def test_native_foreign_binstub_is_never_removed(tmp_path, plugin, shell):
    home, root, durable = _fixture(tmp_path, plugin)
    stub = home / ".local" / "bin" / (plugin + ".ps1")
    stub.parent.mkdir(parents=True)
    stub.write_text("foreign operator command\n")
    result = _run(tmp_path, plugin, shell, home, root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert stub.read_text() == "foreign operator command\n"
    _assert_retained(root, durable)
    assert not (root / "versions" / "1.2.3").exists()


@pytest.mark.parametrize("plugin", PLUGINS)
def test_profile_guard_accepts_settled_lease_registries(tmp_path, plugin, monkeypatch):
    home, root, durable = _fixture(tmp_path, plugin)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for name in (".construction-leases", ".retention-leases", ".publication-leases"):
        directory = root / name
        directory.mkdir()
        (directory / "settled.lock").write_bytes(b"persistent coordination")
    policy = _policy(plugin)
    policy.check_under_custody(root, ROOT / "plugins" / plugin, plugin)
    _assert_retained(root, durable)
    for path in root.glob(".*-leases/settled.lock"):
        assert path.read_bytes() == b"persistent coordination"


def test_codespaces_guard_retains_idle_owner_and_static_ssh_data(tmp_path, monkeypatch):
    home, root, durable = _fixture(tmp_path, "agent-codespaces")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (root / "connection-owner.json").write_bytes(b"{}")
    (root / "connection-owner.live.json").write_bytes(b'{"active": [], "bridge_forwards": []}')
    (root / "ssh").mkdir()
    config = root / "ssh" / "static.conf"
    config.write_bytes(b"Host fixture\n ProxyCommand gh codespace ssh --stdio\n")
    policy = _policy("agent-codespaces")
    policy.check_under_custody(root, ROOT / "plugins" / "agent-codespaces", "agent-codespaces")
    assert config.read_bytes() == b"Host fixture\n ProxyCommand gh codespace ssh --stdio\n"
    assert (root / "connection-owner.json").read_bytes() == b"{}"
    _assert_retained(root, durable)


@pytest.mark.parametrize("plugin", PLUGINS)
def test_profile_selects_only_reconstructible_leaf_entries(plugin):
    profile = json.loads((ROOT / "plugins" / plugin / "scripts" / "uninstall-profile.json").read_text())
    assert profile["version"] == 1
    assert profile["guard_module"] == "uninstall-state.py"
    assert profile["guard_function"] == "check_under_custody"
    forbidden = {
        "config.yaml", "auth.json", "queue.db", "subscriptions.json", "adopted-repos.yaml",
        "account-bindings.json", "account-bindings.json.lock", "forward-keepers",
        "sockets", "ssh", "logs", "copilot-hosts.json", "fragment-warning-state.json",
        ".install.lock", ".install-stage", ".construction-leases", ".retention-leases",
        ".publication-leases", "versions", "snapshots",
    }
    for key in ("root_entries", "bin_entries", "script_entries", "local_bin_entries"):
        assert not (forbidden & set(profile[key]))
        assert all(name not in (".", "..") and "/" not in name and "\\" not in name for name in profile[key])


@pytest.mark.parametrize("plugin,name,value", (
    ("agent-codespaces", "sockets/master", b"owned socket receipt"),
    ("agent-codespaces", "connection-owner.json", b'{"fixture": {"pinned": true}}'),
    ("agent-codespaces", "connection-owner.live.json", b'{"active": ["fixture"]}'),
    ("agent-codespaces", "ssh/proxy.conf", b"ProxyCommand agent-codespaces proxy fixture\n"),
    ("agent-ssh", "forward-keepers/fixture.json", b'{"keeper": "active"}'),
))
def test_profile_guard_refuses_reconnect_custody_without_mutating_data(
    tmp_path, monkeypatch, plugin, name, value,
):
    home, root, durable = _fixture(tmp_path, plugin)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    receipt = root / name
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_bytes(value)
    with pytest.raises(RuntimeError):
        _policy(plugin).check_under_custody(root, ROOT / "plugins" / plugin, plugin)
    assert receipt.read_bytes() == value
    _assert_retained(root, durable)
