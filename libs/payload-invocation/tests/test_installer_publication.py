"""Cell staging and retained backend admission use the real deployment owner."""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from test_command_binding import (
    ROOT, _activate, _snapshot, _write,
    binding_fixture as binding_fixture,
    powershell_host as powershell_host,
)
from test_native_publication import SHELLS, _command, _native_fixture, _run


def _installer_environment(fixture: dict, shell: str) -> dict:
    environment = _native_fixture(fixture, shell)
    if os.name != "nt":
        # The relocated child has a fresh CLR; its OS-account lookup must use
        # the same isolated home as the parent, not the real WSL user's home.
        helper = fixture["payload"] / "scripts/installation-context/installation-context.ps1"
        content = helper.read_text(encoding="utf-8")
        original = "$passwdHome = [CePosixAccount]::Home()"
        assert content.count(original) == 1
        helper.write_text(content.replace(original, "$passwdHome = $env:HOME"), encoding="utf-8")
    return environment


def _cell(fixture: dict, environment: dict) -> tuple[Path, dict]:
    cell = _activate(fixture, fixture["payload"])
    _write(fixture["home"] / ".copilot-extensions/installation-mode.json", {
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True, "marketplaces": {}},
    })
    environment["COPILOT_EXTENSIONS_CONTEXT"] = cell["installReceipt"]
    root = Path(cell["pluginRoot"])
    arguments = {
        **fixture["arguments"], "context": cell["installReceipt"],
        "durable_home": fixture["home"] / ".copilot-extensions",
    }
    return root, arguments


def _policy(fixture: dict, root: Path, arguments: dict, state: str) -> None:
    if state == "missing":
        return
    primitive = fixture["primitive"]
    resolution = primitive.resolve_runtime_deployment(**arguments)
    document = {
        "schema": primitive.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
        "owner": primitive._deployment_owner(resolution, arguments),
        "managed": state != "standalone",
    }
    if state == "invalid":
        document["managed"] = "false"
    elif state == "foreign":
        document["owner"]["sourceFingerprint"] = "sha256:" + "0" * 64
    elif state == "future":
        document["version"] = 999
    _write(root / "runtime-deployment.json", document)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["managed", "invalid", "foreign", "future"])
@pytest.mark.parametrize("action", ["install", "update", "repair", "dev", "dev-release"])
def test_cell_installer_refuses_before_staging(
    binding_fixture: dict, shell: str, state: str, action: str, powershell_host,
) -> None:
    environment = _installer_environment(binding_fixture, shell)
    root, arguments = _cell(binding_fixture, environment)
    _policy(binding_fixture, root, arguments, state)
    stage = root / ".install-stage/retained-0"
    stage.mkdir(parents=True)
    (stage / "retained").write_text("preserve", encoding="utf-8")
    if shell == "powershell":
        (binding_fixture["payload"] / "scripts/cell-installer-probe.ps1").write_text(
            "param([string]$Action)\n"
            "& (Join-Path $PSScriptRoot 'install.ps1') -Action $Action -InstallDir '"
            + str(root).replace("'", "''") + "'\n",
            encoding="utf-8",
        )
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    command = (
        [*_command(binding_fixture, shell, "install"), action, "--install-dir", str(root)]
        if shell == "bash" else
        [*_command(binding_fixture, shell, "cell-installer-probe"), action]
    )
    result = _run(binding_fixture, environment, command, host)
    assert result.returncode != 0, result
    assert "Runtime publication refused" in result.stdout + result.stderr, result
    assert _snapshot(binding_fixture["home"]) == before
    if host:
        host.close()
        assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["missing", "standalone"])
def test_cell_installer_standalone_still_stages(
    binding_fixture: dict, shell: str, state: str, powershell_host,
) -> None:
    environment = _installer_environment(binding_fixture, shell)
    root, arguments = _cell(binding_fixture, environment)
    _policy(binding_fixture, root, arguments, state)
    if shell == "powershell":
        (binding_fixture["payload"] / "scripts/cell-installer-probe.ps1").write_text(
            "& (Join-Path $PSScriptRoot 'install.ps1') update -InstallDir '"
            + str(root).replace("'", "''") + "'\n",
            encoding="utf-8",
        )
        command = _command(binding_fixture, shell, "cell-installer-probe")
    else:
        command = [*_command(binding_fixture, shell, "install"), "update", "--install-dir", str(root)]
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    result = _run(binding_fixture, environment, command, host)
    assert result.returncode == 0, result.stdout + result.stderr
    # The unchanged cross-plugin smoke seam always records in the legacy home.
    smoke = json.loads((binding_fixture["legacy"] / "smoke.json").read_text(encoding="utf-8-sig"))
    assert smoke["staged"] is True
    assert Path(smoke["staged_from"]) == binding_fixture["payload"]
    assert Path(smoke["ran_from"]) != binding_fixture["payload"] / "scripts"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Git Bash")
@pytest.mark.parametrize("state", ["missing", "standalone"])
def test_windows_bash_cell_standalone_preserves_staging(
    binding_fixture: dict, state: str,
) -> None:
    git = shutil.which("git")
    bash = Path(git).resolve().parent.parent / "bin/bash.exe" if git else None
    if bash is None or not bash.is_file():
        pytest.skip("Native Windows Git Bash is unavailable")
    environment = _installer_environment(binding_fixture, "bash")
    root, arguments = _cell(binding_fixture, environment)
    _policy(binding_fixture, root, arguments, state)
    command = [
        str(bash), "-c",
        'exec bash "$1" update --install-dir "$(cygpath -u "$2")" '
        '--runtime-publication-python "$(cygpath -u "$3")"',
        "--", (binding_fixture["payload"] / "scripts/install.sh").as_posix(),
        str(root), sys.executable,
    ]
    result = _run(binding_fixture, environment, command)
    assert result.returncode == 0, result.stdout + result.stderr
    smoke = json.loads((binding_fixture["legacy"] / "smoke.json").read_text(encoding="utf-8-sig"))
    assert smoke["staged"] is True
    assert ".install-stage" in smoke["ran_from"]


@pytest.fixture
def backend(binding_fixture: dict, monkeypatch: pytest.MonkeyPatch):
    scripts = ROOT / "plugins/agent-worktrees/scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("publication_backend", scripts / "runtime_install.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "runtime_install", module)
    monkeypatch.delitem(sys.modules, "runtime_install_modes", raising=False)
    for key, value in binding_fixture["environment"].items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    # The resolver is unchanged; only its OS-profile identity uses the isolated home.
    real_exec = importlib.machinery.SourceFileLoader.exec_module

    def execute(loader, target):
        real_exec(loader, target)
        if target.__name__ == "_worktrees_installation_context":
            current = target._current_environment
            monkeypatch.setattr(target, "_current_environment", lambda **kw: current(
                **{**kw, "os_profile": binding_fixture["home"]},
            ))

    monkeypatch.setattr(importlib.machinery.SourceFileLoader, "exec_module", execute)
    return module


@pytest.mark.parametrize("cell", [False, True])
@pytest.mark.parametrize("state", ["managed", "invalid", "foreign", "future"])
@pytest.mark.parametrize("entry", ["install", "mode", "settle"])
def test_direct_backend_refuses_before_product_writes(
    binding_fixture: dict, backend, monkeypatch: pytest.MonkeyPatch,
    cell: bool, state: str, entry: str,
) -> None:
    root, arguments = binding_fixture["legacy"], binding_fixture["arguments"]
    if cell:
        environment = {}
        root, arguments = _cell(binding_fixture, environment)
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", environment["COPILOT_EXTENSIONS_CONTEXT"])
    _policy(binding_fixture, root, arguments, state)
    before = _snapshot(binding_fixture["home"])
    with pytest.raises(backend.InstallationRefused, match="Runtime publication refused"):
        if entry == "settle":
            backend.settle_candidate(
                root, "unselected", payload=binding_fixture["payload"],
                payload_hash="not-admitted", expected_governance={},
            )
        else:
            import runtime_install_modes as modes

            target = backend.install if entry == "install" else modes.run
            target(
                root, binding_fixture["payload"], payload=binding_fixture["payload"],
                uv=root / "unused", base_python=Path(sys.executable),
                **({"mode": "repair"} if entry == "mode" else {}),
            )
    assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("cell", [False, True])
@pytest.mark.parametrize("state", ["missing", "standalone"])
def test_backend_standalone_and_policy_recheck(
    binding_fixture: dict, backend, monkeypatch: pytest.MonkeyPatch, cell: bool, state: str,
) -> None:
    root, arguments = binding_fixture["legacy"], binding_fixture["arguments"]
    if cell:
        environment = {}
        root, arguments = _cell(binding_fixture, environment)
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", environment["COPILOT_EXTENSIONS_CONTEXT"])
    _policy(binding_fixture, root, arguments, state)
    before = _snapshot(binding_fixture["home"])
    governance = backend.Governance(root, binding_fixture["payload"])
    assert _snapshot(binding_fixture["home"]) == before
    with governance.held(timeout=1):
        if cell:
            assert len(governance.publication_locks) == 2
            for lock in governance.publication_locks:
                lock.assert_owned()
        _policy(binding_fixture, root, arguments, "managed")
        with pytest.raises(backend.InstallationRefused, match="Runtime publication refused"):
            governance.recheck()


def test_cell_backend_rechecks_after_actual_publication_locks(
    binding_fixture: dict, backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {}
    root, arguments = _cell(binding_fixture, environment)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", environment["COPILOT_EXTENSIONS_CONTEXT"])
    governance = backend.Governance(root, binding_fixture["payload"])
    original = governance.helper._DirectoryLock
    observed = []

    @contextmanager
    def held(*args, **kwargs):
        with original(*args, **kwargs) as lock:
            observed.append(kwargs["kind"])
            if kwargs["kind"] == "install":
                _policy(binding_fixture, root, arguments, "managed")
            yield lock

    monkeypatch.setattr(governance.helper, "_DirectoryLock", held)
    with pytest.raises(backend.InstallationRefused, match="Runtime publication refused"):
        with governance.held(timeout=1):
            pytest.fail("Late managed policy reached publication")
    assert observed == ["genesis", "install"]
    assert not (root / ".installer-inputs").exists()
    assert not (root / "versions").exists()
    assert not (root / ".locks/agent-worktrees.install.lock").exists()


def test_backend_management_requires_real_live_legacy_lease(
    binding_fixture: dict, backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = binding_fixture
    _policy(fixture, fixture["legacy"], fixture["arguments"], "managed")
    binding, primitive = fixture["binding"], fixture["primitive"]
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(
        binding, primitive, managed_deployment=True, **fixture["arguments"],
    ) as lease:
        monkeypatch.setenv(lifecycle.TICKET_ENV, json.dumps(lease.ticket))
        governance = backend.Governance(fixture["legacy"], fixture["payload"])
        with governance.held(timeout=1):
            lease.assert_owned()
            governance.recheck()
    with pytest.raises(governance.helper.InstallationContextError, match="(owner|lock|lease|unavailable)"):
        governance.recheck()


@pytest.mark.parametrize("state", ["managed", "missing", "standalone"])
def test_retained_backend_admits_without_source_modules(
    binding_fixture: dict, backend, state: str,
) -> None:
    fixture = binding_fixture
    retained = fixture["home"] / "retained/.runtime-bootstrap"
    retained.mkdir(parents=True)
    candidate = (ROOT / "plugins/agent-worktrees/src/agent_worktrees/runtime_candidate.py").read_text()
    assignment = next(
        node for node in ast.parse(candidate).body
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_LIFECYCLE_FILES"
            for target in node.targets
        )
    )
    names = ast.literal_eval(assignment.value)
    names += (
        "versioned_runtime.py", "versioned_runtime_leases.py", "versioned_runtime_source.py",
        "versioned_runtime_construction.py", "versioned_runtime_publication.py",
        "runtime_launcher.py",
    )
    for name in names:
        shutil.copyfile(backend.SCRIPTS / name, retained / name)
    shutil.copytree(backend.SCRIPTS / "installation-context", retained / "installation-context")
    _policy(fixture, fixture["legacy"], fixture["arguments"], state)
    program = (
        "import importlib.machinery,json,sys\n"
        f"sys.path.insert(0,{str(retained)!r})\n"
        "load=importlib.machinery.SourceFileLoader.exec_module\n"
        "def isolated(loader,module):\n"
        " load(loader,module)\n"
        " if module.__name__=='_worktrees_installation_context':\n"
        "  current=module._current_environment\n"
        f"  module._current_environment=lambda **kw: current(**{{**kw,'os_profile':{str(fixture['home'])!r}}})\n"
        "importlib.machinery.SourceFileLoader.exec_module=isolated\n"
        "from pathlib import Path\n"
        "import runtime_install\n"
        f"assert Path(runtime_install.__file__).parent==Path({str(retained)!r})\n"
        f"governance=runtime_install.Governance(Path({str(fixture['legacy'])!r}),"
        f"Path({str(fixture['payload'])!r}))\n"
        "with governance.held(timeout=1): governance.recheck()\n"
        "print('retained-publication-admitted')\n"
    )
    binding, primitive = fixture["binding"], fixture["primitive"]
    with binding._load_lifecycle().legacy_lifecycle(
        binding, primitive, managed_deployment=state == "managed", **fixture["arguments"],
    ) as lease:
        before = _snapshot(fixture["home"])
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", program],
            cwd=fixture["home"], capture_output=True, text=True, timeout=30,
            env={**os.environ, "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE": json.dumps(lease.ticket)},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "retained-publication-admitted"
        assert _snapshot(fixture["home"]) == before
