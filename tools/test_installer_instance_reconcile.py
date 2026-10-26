"""Contained adapter contracts; never invoke an installer or the host registry."""

from __future__ import annotations

import importlib.util
import ast
from contextlib import nullcontext
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "libs" / "installer-engine"
SPEC = importlib.util.spec_from_file_location(
    "installer_instance_adapter_tests", ENGINE / "installer_instance_reconcile.py",
)
assert SPEC and SPEC.loader
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)
INSTALLERS = (
    *(
        (name, "install")
        for name in (
            "agent-bridge", "agent-codespaces", "agent-dispatch", "agent-index",
            "agent-logger", "agent-pull-requests", "agent-ssh", "agent-vault",
            "agent-worktrees", "budget-guidance",
        )
    ),
    *((name, "init") for name in ("agent-containers", "agent-mcp", "agent-machines")),
)


def _bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git is None:
        return None
    candidate = Path(git).parent.parent / "bin" / "bash.exe"
    return str(candidate) if candidate.is_file() else None


@pytest.mark.parametrize("name,entry", INSTALLERS)
@pytest.mark.parametrize("extension", ("ps1", "sh"))
def test_every_native_installer_has_one_explicit_action_guard(name, entry, extension):
    text = (REPO / "plugins" / name / "scripts" / f"{entry}.{extension}").read_text()
    invocation = (
        "Invoke-InstallerInstanceReconcile -HelperRoot $PSScriptRoot"
        if extension == "ps1" else 'reconcile_installer_instances "$SCRIPT_DIR"'
    )
    assert text.count(invocation) == (2 if name == "agent-machines" else 1)
    position = text.rindex(invocation)
    if extension == "ps1":
        prefix = text[:position]
        guard = prefix[prefix.rfind("if ($Action -in @("):]
        assert guard.startswith("if ($Action -in @(")
        actions = re.findall(r"'([a-z-]+)'", guard.split("\n")[0])
    else:
        prefix = text[:position]
        guard = prefix[prefix.rfind('case "$ACTION'):]
        actions = guard.splitlines()[1].strip().rstrip(")").split("|")
        actions = [action.split(":")[0] for action in actions]
    assert "install" in actions
    assert "provision" in actions
    assert ("cell-provision" in actions) == (name == "agent-machines")
    assert not set(actions) & {"stamp", "status", "start", "stop", "uninstall", "dev"}
    assert position > text.index("# === end install-contract:v4 self-stage ===")


def test_native_bash_adapter_has_bounded_calls_and_preserves_install_success(tmp_path):
    shell = _bash()
    if shell is None:
        pytest.skip("native Bash unavailable (never substitute WSL)")
    helper_root = tmp_path / "helpers"
    helper_root.mkdir()
    shutil.copyfile(ENGINE / "installer-instance-reconcile.sh", helper_root / "installer-instance-reconcile.sh")
    registry = tmp_path / "registry"
    registry.mkdir()
    (helper_root / "daemon_instances.sh").write_text(
        """
reconcile_daemon_instances() {
    [[ "$1" == 128 && "$2" == 5 && -d "$3" ]] || return 9
    printf '%s\\n' one >> "$3/calls"
    printf '%s\\n' 'held proof-changed synthetic'
    printf '%s\\n' '[instance-gc] held: proof-changed' >&2
    return 7
}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            shell, "--noprofile", "--norc", "-c",
            '. "$1/installer-instance-reconcile.sh"; '
            'for state in fresh repeat healthy; do reconcile_installer_instances "$1" "$2"; done; '
            "printf installer-success",
            "adapter-test", str(helper_root), str(registry),
        ],
        text=True, capture_output=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "installer-success"
    assert (registry / "calls").read_text().splitlines() == ["one"] * 3
    assert result.stderr.count("held: proof-changed") == 3
    assert result.stderr.count("deferred: native-reconcile-failed") == 3


@pytest.mark.parametrize("name,entry", INSTALLERS)
def test_installer_bash_syntax(name, entry):
    shell = _bash()
    if shell is None:
        pytest.skip("native Bash unavailable (never substitute WSL)")
    result = subprocess.run(
        [shell, "--noprofile", "--norc", "-n", str(REPO / "plugins" / name / "scripts" / f"{entry}.sh")],
        text=True, capture_output=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr


def test_implicit_contained_reconciliation_never_invokes_a_shell(monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    monkeypatch.setattr(
        adapter.subprocess, "run",
        lambda *args, **kwargs: pytest.fail("implicit host-registry access"),
    )
    assert adapter.reconcile_installer_instances(ENGINE)


def test_explicit_root_and_budget_use_one_native_call(tmp_path, monkeypatch):
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(adapter.subprocess, "run", execute)
    assert adapter.reconcile_installer_instances(ENGINE, registry_root=tmp_path)
    assert len(calls) == 1
    argv, options = calls[0]
    assert str(tmp_path) in " ".join(argv)
    assert options["timeout"] == 15
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stdout"] == subprocess.DEVNULL


def test_python_adapter_loads_its_own_wrapper_not_a_procutil_package_wrapper(tmp_path, monkeypatch):
    root = tmp_path / "native-package"
    root.mkdir()
    calls = []
    monkeypatch.setattr(
        adapter.subprocess, "run",
        lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0),
    )
    assert not (root / "installer-instance-reconcile.sh").exists()
    assert not (root / "installer-instance-reconcile.ps1").exists()
    assert adapter.reconcile_installer_instances(root, registry_root=tmp_path / "registry")
    command = " ".join(calls[0])
    wrapper = ENGINE / ("installer-instance-reconcile.ps1" if os.name == "nt" else "installer-instance-reconcile.sh")
    assert str(wrapper) in command
    assert str(root) in command
    assert str(root / wrapper.name) not in command


@pytest.mark.parametrize("failure", (OSError, subprocess.TimeoutExpired))
def test_native_failure_does_not_become_install_failure(tmp_path, monkeypatch, capsys, failure):
    def execute(*args, **kwargs):
        if failure is subprocess.TimeoutExpired:
            raise failure("native-helper", 15)
        raise failure("private error details must not be printed")

    monkeypatch.setattr(adapter.subprocess, "run", execute)
    assert not adapter.reconcile_installer_instances(ENGINE, registry_root=tmp_path)
    diagnostic = capsys.readouterr().err
    assert "deferred: native-reconcile-failed" in diagnostic
    assert "private error details" not in diagnostic


@pytest.mark.skipif(os.name != "nt", reason="PowerShell adapter routing contract")
def test_python_adapter_invokes_binding_only_root_and_preserves_unrelated_proof(tmp_path):
    helpers = tmp_path / "native-package"
    helpers.mkdir()
    registry = tmp_path / "registry"
    registry.mkdir()
    owned = registry / "owned-artifact"
    owned.write_text("owned")
    proof = registry / "unrelated-proof"
    proof.write_text("preserve")
    (helpers / "daemon_instances.ps1").write_text(
        """
function Invoke-DaemonInstanceReconcile {
    param([string]$RegistryRoot, [int]$Limit, [double]$MaxSeconds)
    if ($Limit -ne 128 -or $MaxSeconds -ne 5) { throw 'invalid bounds' }
    [IO.File]::Delete((Join-Path $RegistryRoot 'owned-artifact'))
    return @{status='settled';reason='synthetic-routing-proof';instance_id='synthetic'}
}
""",
        encoding="utf-8",
    )
    assert not (helpers / "installer-instance-reconcile.ps1").exists()
    assert adapter.reconcile_installer_instances(helpers, registry_root=registry)
    assert not owned.exists()
    assert proof.read_text() == "preserve"


@pytest.mark.skipif(os.name != "nt", reason="PowerShell native adapter contract")
def test_real_powershell_adapter_fresh_repeat_healthy_and_partial_outcomes(tmp_path):
    helper_root = tmp_path / "helpers"
    helper_root.mkdir()
    shutil.copyfile(ENGINE / "installer-instance-reconcile.ps1", helper_root / "installer-instance-reconcile.ps1")
    registry = tmp_path / "explicit-registry"
    registry.mkdir()
    (helper_root / "daemon_instances.ps1").write_text(
        """
function Invoke-DaemonInstanceReconcile {
    param([string]$RegistryRoot, [int]$Limit, [double]$MaxSeconds)
    if ($Limit -ne 128 -or $MaxSeconds -ne 5) { throw 'unbounded invocation' }
    [IO.File]::AppendAllText((Join-Path $RegistryRoot 'calls'), "one`n")
    $state = [IO.File]::ReadAllText((Join-Path $RegistryRoot 'state')).Trim()
    if ($state -eq 'error') { throw 'private registry error details' }
    if ($state -eq 'partial') {
        [Console]::Error.WriteLine('[instance-gc] held: proof-changed')
        return @{status='held';reason='proof-changed';instance_id='synthetic'}
    }
    return @{status='settled';reason='synthetic';instance_id='synthetic'}
}
""",
        encoding="utf-8",
    )
    shell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    command = (
        f". {quote(helper_root / 'installer-instance-reconcile.ps1')}; "
        "$global:LASTEXITCODE=37; "
        "foreach ($state in @('fresh','repeat','healthy','partial','error')) { "
        f"[IO.File]::WriteAllText({quote(registry / 'state')}, $state); "
        f"Invoke-InstallerInstanceReconcile -HelperRoot {quote(helper_root)} "
        f"-RegistryRoot {quote(registry)}; "
        "if ($global:LASTEXITCODE -ne 37) { throw 'installer status changed' } }; "
        "[Console]::Out.Write('installer-success')"
    )
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        text=True, capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "installer-success"
    assert (registry / "calls").read_text().splitlines() == ["one"] * 5
    assert "held: proof-changed" in result.stderr
    assert "deferred: native-reconcile-failed" in result.stderr
    assert "private registry error details" not in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="PowerShell native registry boundary")
def test_actual_native_missing_registry_is_readonly_and_unknown_proof_is_held(tmp_path):
    helper_root = tmp_path / "helpers"
    helper_root.mkdir()
    for source in (
        ENGINE / "installer-instance-reconcile.ps1",
        REPO / "libs" / "agent-procutil" / "src" / "agent_procutil" / "daemon_instances.ps1",
    ):
        shutil.copyfile(source, helper_root / source.name)
    registry = tmp_path / "never-created-registry"
    unknown = registry / ("a" * 32 + ".instance")
    shell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    command = (
        f". {quote(helper_root / 'installer-instance-reconcile.ps1')}; "
        "1..2 | ForEach-Object { "
        f"Invoke-InstallerInstanceReconcile -HelperRoot {quote(helper_root)} "
        f"-RegistryRoot {quote(registry)} }}; "
        f"if (Test-Path -LiteralPath {quote(registry)}) {{ throw 'missing registry was created' }}; "
        f"[void][IO.Directory]::CreateDirectory({quote(registry)}); "
        f"[IO.File]::WriteAllText({quote(unknown)}, 'unowned-proof'); "
        f"Invoke-InstallerInstanceReconcile -HelperRoot {quote(helper_root)} "
        f"-RegistryRoot {quote(registry)}; "
        f"if ([IO.File]::ReadAllText({quote(unknown)}) -cne 'unowned-proof') "
        "{ throw 'unowned proof changed' }; [Console]::Out.Write('preserved')"
    )
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        text=True, capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "preserved"
    assert "held" in result.stderr


def test_standalone_host_extras_include_admission_dependency():
    for relative, extras in (
        ("fleet-manager", ("server", "service-gateway", "dev")),
        ("services/chat", ("service",)),
    ):
        project = tomllib.loads((REPO / relative / "pyproject.toml").read_text())["project"]
        for extra in extras:
            assert any(
                item.startswith("agent-procutil")
                for item in project["optional-dependencies"][extra]
            )


@pytest.mark.parametrize("relative,uid,home", (
    ("fleet-manager/containers/gateway/Dockerfile", 10001, "/state"),
    ("services/pr/container/Dockerfile", 65532, "/data"),
))
def test_shipped_service_uid_has_a_real_owned_os_profile(relative, uid, home):
    recipe = (REPO / relative).read_text().replace("\\\n", " ")
    if relative.startswith("fleet-manager/"):
        assert "ARG GATEWAY_UID=10001" in recipe and "ARG GATEWAY_GID=10001" in recipe
        assert 'case "$value" in ""|0*|*[!0-9]*)' in recipe
        assert 'getent passwd "$GATEWAY_UID"' in recipe
        assert 'usermod --gid "$GATEWAY_GID" --home /state "$account"' in recipe
        assert 'install -d -m 700 -o "$GATEWAY_UID" -g "$GATEWAY_GID" /state' in recipe
        assert "USER ${GATEWAY_UID}:${GATEWAY_GID}" in recipe
    else:
        assert f"groupadd --gid {uid} " in recipe
        assert f"useradd --uid {uid} --gid {uid} --home-dir {home} --no-create-home" in recipe
        assert f"chown {uid}:{uid} {home}" in recipe
        assert f"USER {uid}:{uid}" in recipe
    assert f"HOME={home}" in recipe


@pytest.mark.parametrize("state", ("fresh", "repeat", "healthy"))
def test_manager_install_reconciles_once_before_candidate_reuse(tmp_path, monkeypatch, state):
    source = REPO / "worktree-manager" / "src" / "worktree_manager" / "self_install.py"
    definition = next(
        node for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "self_install"
    )
    events = []
    payload = tmp_path / "payload"

    def reconcile(root):
        assert root == payload / "scripts"
        events.append("reconcile")

    def candidate(*args, **kwargs):
        events.append("candidate")
        return nullcontext(SimpleNamespace(slot=tmp_path / "slot"))

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setitem(sys.modules, "installer_instance_reconcile", SimpleNamespace(
        reconcile_installer_instances=reconcile,
    ))
    namespace = {
        "Path": Path, "SelfInstallResult": SimpleNamespace,
        "os": os, "subprocess": subprocess,
        "running_payload_dir": lambda: payload,
        "payload_version": lambda path: "1.0",
        "version_slot": lambda version, root: root / "versions" / version,
        "current_version": lambda root: None if state == "fresh" else "1.0",
        "needs_install": lambda version, root: state == "fresh",
        "_runtime_installer": lambda: SimpleNamespace(candidate=candidate),
        "_copy_payload": object(),
        "_complete_candidate": lambda *args, **kwargs: SimpleNamespace(
            action="installed" if state == "fresh" else "already-current",
        ),
    }
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), "exec"), namespace)

    result = namespace["self_install"](payload, root=tmp_path, dry_run=False)
    assert result.action == ("installed" if state == "fresh" else "already-current")
    assert events == ["reconcile", "candidate"]


@pytest.mark.parametrize(
    "command,expected",
    (([], True), (["update"], True), (["self-install"], True), (["status"], False), (["version"], False)),
)
def test_manager_bootstrap_does_not_sweep_for_ordinary_commands(command, expected):
    source = REPO / "worktree-manager" / "scripts" / "runtime_install.py"
    assignment = next(
        node for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "reconcile" for target in node.targets)
    )
    assert eval(compile(ast.Expression(assignment.value), str(source), "eval"), {"command": command}) is expected


@pytest.mark.skipif(os.name != "nt", reason="PowerShell installed-helper distribution")
def test_dtssh_installer_copies_native_binding_before_launcher_without_host_effects(tmp_path):
    source = (
        REPO / "plugins" / "agent-ssh" / "transports" / "dtssh" / "scripts" / "install-host.ps1"
    ).read_text()
    resolver = source.split("function Get-DaemonInstanceHelperRoot {", 1)[1].split(
        "\nfunction Install-Shortcut {", 1,
    )[0]
    publisher = source.split("function Install-Shortcut {", 1)[1].split(
        "    Copy-Item $LauncherSrc $LauncherDst -Force", 1,
    )[0]
    helpers = tmp_path / "source"
    helpers.mkdir()
    for name in ("daemon_instances.ps1", "installer-instance-reconcile.ps1"):
        (helpers / name).write_text(f"synthetic {name}\n")
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / "unrelated-proof").write_bytes(b"preserved")
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = helpers / "publish.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$InstallDir={quote(installed)}\n"
        "function Get-DaemonInstanceHelperRoot {" + resolver + "\n"
        "function Publish-OnlyInstanceHelpers {" + publisher + "\n}\n"
        "Publish-OnlyInstanceHelpers\nPublish-OnlyInstanceHelpers\n",
        encoding="utf-8",
    )
    shell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
        text=True, capture_output=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    for name in ("daemon_instances.ps1", "installer-instance-reconcile.ps1"):
        assert (installed / name).read_bytes() == (helpers / name).read_bytes()
    assert (installed / "unrelated-proof").read_bytes() == b"preserved"


@pytest.mark.skipif(os.name != "nt", reason="Windows VBS generator quoting")
def test_wsl_vbs_invokes_installed_owner_with_literal_arguments_without_launching_wsl(tmp_path):
    source = (
        REPO / "plugins" / "wsl-setup" / "skills" / "setting-up-wsl" / "references" / "wsl-keepalive.ps1"
    ).read_text()
    generator = source.split("function New-KeepaliveVbs {", 1)[1].split(
        "\nfunction Publish-KeepaliveInstanceHelpers {", 1,
    )[0]
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    distro = 'Ubuntu Test "quoted" & literal'
    vbs = tmp_path / "generated" / "keepalive.vbs"
    command = (
        "function New-KeepaliveVbs {" + generator + "\n"
        f"New-KeepaliveVbs -Distro {quote(distro)} -Service ssh -VbsPath {quote(vbs)}"
    )
    shell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        text=True, capture_output=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    text = vbs.read_text(encoding="utf-16")
    run = next(line for line in text.splitlines() if line.startswith("CreateObject"))
    assert run.endswith('", 0, False')
    native_command = run.split('.Run "', 1)[1].removesuffix('", 0, False').replace('""', '"')
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    arguments = shell32.CommandLineToArgvW(native_command, ctypes.byref(count))
    assert arguments
    try:
        argv = [arguments[index] for index in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(arguments, ctypes.c_void_p))
    assert Path(argv[0]).name == "conhost.exe"
    assert argv[1] == "--headless"
    assert Path(argv[2]).name == "powershell.exe"
    assert argv[argv.index("-File") + 1] == str(vbs.parent / "wsl-keepalive-owner.ps1")
    assert argv[argv.index("-Distro") + 1] == distro
    assert argv[argv.index("-Service") + 1] == "ssh"
    assert "wsl.exe" not in argv
