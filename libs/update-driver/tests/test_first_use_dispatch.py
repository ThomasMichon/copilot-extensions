"""Dispatch's fixed legacy recipes, separately from ordinary publisher adoption."""

from collections.abc import Callable, Iterator
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import time
from types import ModuleType
from typing import Protocol, cast
import zipfile

import pytest
from _pytest.capture import CaptureManager
from packaging.version import Version

from agent_procutil import no_window_kwargs
from update_driver import (
    ParticipantError, Request, RequestSpec, TrustedRunner, drain, enqueue, foundation_status, status,
)
from update_driver import first_use as provision
from update_driver.foundation_inputs import GENERATED, PACKAGES

from test_first_use import primitive as _primitive
from test_first_use_native import native_command as _native_command
from test_first_use_bridge import (
    build_bridge_native_test_assets as _build_assets, native_recipe_runner as native_recipe_runner,
)
from test_first_use_bridge_command import diagnostic_for as _diagnostic_for
from test_first_use_genesis import observe as _observe
from test_first_use_pull_requests_command import command_for as _command_for
from test_first_use_vault import cold_environment as _cold_environment, native_genesis
from test_first_use_vault_bootstrap import receipt_completed_count as _receipt_completed_count

OwningPayload = tuple[Path, Path, Path]
Diagnostic = Callable[[], str]
Phase = Callable[[str], None]


class NativeCommand(Protocol):
    def __call__(
        self, root: Path, payload: Path, installation: Path, project: Path, *,
        client_only: bool = False, source: Path | None = None,
    ) -> list[str]: ...


class NativeRecipeRunner(Protocol):
    def start(
        self, command: list[str], *, env: dict[str, str], cwd: Path, root: Path,
        diagnostic: Diagnostic, contain_caller: bool = False,
    ) -> tuple[subprocess.Popen[bytes], Path]: ...

    def run(
        self, command: list[str], *, env: dict[str, str], cwd: Path, root: Path,
        diagnostic: Diagnostic,
    ) -> subprocess.CompletedProcess[str]: ...

    def lose_caller(self, caller: subprocess.Popen[bytes]) -> None: ...

    def owners_finished(self) -> bool: ...


primitive = cast(Callable[[Path], ModuleType], _primitive)
native_command = cast(NativeCommand, _native_command)
build_bridge_native_test_assets = cast(Callable[[Path, Path], None], _build_assets)
diagnostic_for = cast(Callable[[Path, Path], Diagnostic], _diagnostic_for)
observe = cast(Callable[[Callable[[], bool], float, Diagnostic], None], _observe)
command_for = cast(Callable[[Path], list[str]], _command_for)
cold_environment = cast(Callable[[Path, Path, Path], dict[str, str]], _cold_environment)
receipt_completed_count = cast(Callable[[Path, int, Path], bool], _receipt_completed_count)


def _request(request_id: str, root: Path) -> Request:
    value = status(request_id, root=root)
    assert isinstance(value, Request)
    return value


def _requests(root: Path) -> tuple[Request, ...]:
    value = status(root=root)
    assert isinstance(value, tuple)
    return value


pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="native Dispatch recipes are qualified only on Windows and Linux",
)
REPOSITORY = Path(__file__).resolve().parents[3]


def _dispatch_phase_reporter(capture: CaptureManager, *, active: bool) -> Phase:
    allowed = {
        "fixture-enter", "materialize-start", "materialize-ready", "body-enter", "body-exit",
        "setup-start", "setup-ready", "caller-start", "caller-acknowledged",
        "foundation-wait", "caller-closed",
        "receipt-wait", "receipt-completed", "registration-checked", "consumers-checked",
        "owner-settlement-wait", "owners-settled", "history-checked",
        "pending-cleanup-start", "pending-cleanup-finished",
    }
    emitted: set[str] = set()
    started = time.monotonic()

    def report(phase: str) -> None:
        if phase not in allowed:
            raise ValueError("unknown Dispatch diagnostic phase")
        if not active or phase in emitted:
            return
        emitted.add(phase)
        with capture.global_and_fixture_disabled():
            print(
                f"[dispatch-recipe-phase] phase={phase} elapsed={time.monotonic() - started:.3f}s",
                file=sys.stderr, flush=True,
            )

    return report


@pytest.fixture
def dispatch_cold_phase(request: pytest.FixtureRequest) -> Phase:
    capture = cast(CaptureManager, request.config.pluginmanager.getplugin("capturemanager"))
    phase = _dispatch_phase_reporter(
        capture, active=os.name == "nt" and request.node.name == (
            "test_actual_dispatch_original_provision_survives_caller_loss"
        ),
    )
    phase("fixture-enter")
    return phase


@pytest.fixture
def dispatch_cold_body_phase(
    native_recipe_runner: NativeRecipeRunner, dispatch_cold_phase: Phase,
) -> Iterator[Phase]:
    dispatch_cold_phase("body-enter")
    try:
        yield dispatch_cold_phase
    finally:
        dispatch_cold_phase("body-exit")


def test_dispatch_diagnostic_phases_are_bounded_and_context_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager
    import io
    from types import SimpleNamespace

    events: list[str] = []

    class Output(io.StringIO):
        def flush(self) -> None:
            events.append("flush")
            super().flush()

    @contextmanager
    def disabled() -> Iterator[None]:
        events.append("disabled-enter")
        yield
        events.append("disabled-exit")

    output = Output()
    monkeypatch.setattr(sys, "stderr", output)
    monkeypatch.setattr(time, "monotonic", lambda: 10.0)
    capture = cast(CaptureManager, SimpleNamespace(global_and_fixture_disabled=disabled))
    phase = _dispatch_phase_reporter(capture, active=True)
    phase("receipt-wait")
    phase("receipt-wait")
    with pytest.raises(ValueError, match="unknown Dispatch diagnostic phase"):
        phase("foreign private diagnostic context")
    assert output.getvalue() == "[dispatch-recipe-phase] phase=receipt-wait elapsed=0.000s\n"
    assert events == ["disabled-enter", "flush", "disabled-exit"]
    _dispatch_phase_reporter(capture, active=False)("receipt-wait")
    assert events == ["disabled-enter", "flush", "disabled-exit"]


@pytest.mark.parametrize(("fixture_name", "plugin"), [
    ("owning_bridge", "agent-bridge"), ("owning_dispatch", "agent-dispatch"),
])
def test_installed_fixture_provenance_uses_its_private_copilot_home(
    area: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
    fixture_name: str, plugin: str,
) -> None:
    foreign = area / "foreign-copilot"
    foreign.mkdir()
    monkeypatch.setenv("COPILOT_HOME", str(foreign))
    payload, installation, project = cast(OwningPayload, request.getfixturevalue(fixture_name))
    copilot = installation.parent / ".copilot"
    assert Path(os.environ["COPILOT_HOME"]) == copilot
    settings = copilot / "settings.json"
    before = settings.read_bytes()
    source = primitive(payload).resolve_payload_source(
        payload_root=payload, plugin_id=plugin, project_root=project,
    )
    assert source["source"]["canonical"] == "github:example/fixture-market"
    assert source["pluginId"] == plugin and Path(source["payloadRoot"]) == payload
    assert source["locator"]["kind"] == "installed"
    assert Path(source["locator"]["copilotHome"]) == copilot
    assert source["locator"]["marketplaceKey"] == "fixture-market"
    assert settings.read_bytes() == before and not installation.exists()
    assert list(foreign.iterdir()) == []


@pytest.mark.portfolio_tier("T2")
@pytest.mark.timeout(120)
def test_dispatch_faulthandler_observes_finite_finalizer_without_disabling_capture(
    area: Path,
) -> None:
    suite = area / "controlled-pytest"
    suite.mkdir()
    target = suite / "test_controlled_finalizer.py"
    target.write_text(
        "from pathlib import Path\n"
        "import time\n"
        "import pytest\n"
        "@pytest.fixture\n"
        "def controlled_fixture_finalizer(request):\n"
        "    yield\n"
        "    assert request.config.getoption('capture') == 'fd'\n"
        "    print('controlled finalizer remains captured')\n"
        "    time.sleep(61)\n"
        "    Path(__file__).with_suffix('.completed').write_text('completed')\n"
        "def test_config_and_capture(request, controlled_fixture_finalizer):\n"
        "    assert float(request.config.getini('faulthandler_timeout')) == 60.0\n"
        "    assert request.config.getini('faulthandler_exit_on_timeout') is False\n"
        "    assert request.config.getoption('capture') == 'fd'\n"
        "    print('controlled body remains captured')\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PYTEST_ADDOPTS": "-o faulthandler_timeout=60 -o faulthandler_exit_on_timeout=false",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-m", "pytest", "-q", "--color=no",
             "-p", "no:cacheprovider", "-o", "addopts=", "--confcutdir", str(suite), str(target)],
            env=environment, cwd=suite, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=90, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        pytest.fail("controlled fixture finalizer did not finish")
    if result.returncode != 0:
        pytest.fail(f"controlled pytest exited with status {result.returncode}")
    if "controlled_fixture_finalizer" not in result.stderr or "pytest_runtest_teardown" not in result.stderr:
        pytest.fail("whole-protocol traceback did not observe the controlled fixture finalizer")
    assert target.with_suffix(".completed").read_text() == "completed"
    assert "1 passed" in result.stdout
    assert "controlled body remains captured" not in result.stdout + result.stderr
    assert "controlled finalizer remains captured" not in result.stdout + result.stderr


@pytest.mark.parametrize("action", ["stamp", "provision"])
@pytest.mark.parametrize("submitted_wsl", [None, "0", "1"])
def test_dispatch_fixed_action_keeps_native_options_context_custody_and_result(
    owning_dispatch: OwningPayload, area: Path, monkeypatch: pytest.MonkeyPatch, action: str,
    submitted_wsl: str | None,
) -> None:
    payload, installation, project = owning_dispatch
    root = area / "queue"
    if submitted_wsl is None:
        monkeypatch.delenv("AGENT_DISPATCH_WSL_WINDOWS_CLIENT", raising=False)
    else:
        monkeypatch.setenv("AGENT_DISPATCH_WSL_WINDOWS_CLIENT", submitted_wsl)
    controls = {
        "AGENT_DISPATCH_SUPERVISE_MACHINE": "submitter-machine",
        "AGENT_DISPATCH_BASE_PYTHON": str(area / "submitter-python"),
    }
    submitted_controls = submitted_wsl is not None
    for key, value in controls.items():
        if submitted_controls:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)
    request = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    repeated = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    assert request.id == repeated.id and not installation.exists()
    assert request.runner.context_keys == tuple(sorted(provision.DISPATCH_CONTEXT_KEYS))
    for key in ("ACTION", "DRY_RUN", "FORCE", "PAYLOAD_ROOT", "VALIDATED_CONTEXT", "NO_SELFPROVISION",
                "CONFIG_DIR", "INSTALL_DIR", "ROUTING_DIR", "RUN_DIR", "COMMAND_MODULE",
                "INSTALLATION_CONFIG"):
        monkeypatch.setenv("AGENT_DISPATCH_" + key, "foreign")
    monkeypatch.setenv("AGENT_DISPATCH_WSL_WINDOWS_CLIENT", "0" if submitted_wsl == "1" else "1")
    for key in controls:
        monkeypatch.setenv(key, "foreign-worker")
    drain(root=root)
    result = _request(request.id, root)
    assert result.state == "completed" and result.exit_code == 7, result.log_path.read_text()
    assert (installation / "executed").read_text() == action
    assert (installation / "client-only").read_text().strip() == "0"
    assert (installation / "wsl-client-history").read_text().strip() == f"[{submitted_wsl or ''}]"
    assert (installation / "supervise-machine").read_text() == (
        controls["AGENT_DISPATCH_SUPERVISE_MACHINE"] if submitted_controls else ""
    )
    assert (installation / "base-python").read_text() == (
        controls["AGENT_DISPATCH_BASE_PYTHON"] if submitted_controls else ""
    )
    assert Path((installation / "project-path").read_text()) == project
    frozen = Path((installation / "source-path").read_text().strip()).parent
    assert frozen.name == "agent-dispatch"
    assert frozen.parent.parent == installation / ".install-stage"
    assert frozen.parent.name.startswith(request.id + "-")
    assert (frozen / "payload-invocation.json").read_bytes() == (
        payload / "payload-invocation.json"
    ).read_bytes()
    admission = json.loads(request.spec.source)
    assert (admission["plugin"], admission["payload"], admission["runtime_root"], admission["project"]) == (
        "agent-dispatch", str(payload), str(installation), str(project),
    )


@pytest.mark.parametrize("native", [False, True], ids=["python-api", "native-front"])
@pytest.mark.parametrize("action", ["stamp", "provision"])
@pytest.mark.parametrize("reason", [
    "client-only", "no-self", "pinned", "managed", "malformed", "copy", "snapshot",
    "custom-root", "cell", "nested", "dispatcher", "module", "provision-mode",
    "lifecycle", "catalog", "compound", "install", "missing-helper", "missing-native",
    "missing-reconcile", "missing-source",
    "missing-supervisor", "missing-base",
    "missing-health", "missing-cache", "missing-daemon-runtime", "missing-reconcile-python",
    "missing-gate-ps1", "missing-gate-sh", "missing-resolver-ps1", "missing-resolver-sh",
    "missing-retained", "missing-scheduling", "missing-driver", "missing-action-controls",
    "installation-policy",
])
def test_dispatch_refusal_precedes_acquisition_queue_and_publication(
    owning_dispatch: OwningPayload, area: Path, monkeypatch: pytest.MonkeyPatch,
    native: bool, action: str, reason: str,
) -> None:
    payload, installation, project = owning_dispatch
    if reason in {"copy", "snapshot"}:
        copied = (
            installation / "snapshots/retained" if reason == "snapshot"
            else area / "copy/agent-dispatch"
        )
        shutil.copytree(payload, copied)
        payload = copied
    elif reason == "custom-root":
        installation = installation.with_name("other-dispatch")
    elif reason in {"no-self", "pinned", "nested", "cell", "installation-policy"}:
        monkeypatch.setenv({
            "no-self": "AGENT_DISPATCH_NO_SELFPROVISION",
            "pinned": "AGENT_DISPATCH_PINNED_CONTEXT",
            "nested": "UPDATE_DRIVER_WITNESS",
            "cell": "AGENT_DISPATCH_INSTALLATION_ID",
            "installation-policy": "AGENT_DISPATCH_INSTALLATION_CONFIG",
        }[reason], "foreign")
    elif reason in {"managed", "malformed"}:
        module = primitive(payload)
        arguments = dict(payload_root=payload, plugin_id="agent-dispatch",
                         legacy_root=installation, project_root=project)
        resolution = module.resolve_installation_mode(**arguments)
        installation.mkdir()
        policy = {
            "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": module._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    elif reason == "missing-scheduling":
        (payload / "src/agent_dispatch/windows-scheduling.ps1").unlink()
    elif reason == "missing-action-controls":
        installation.mkdir()
        (installation / "action-controls.json").write_text("{}", encoding="utf-8")
        (payload / "scripts/action_controls.py").unlink(missing_ok=True)
    elif reason.startswith("missing-"):
        name = {
            "missing-helper": "versioned_runtime_publication.py",
            "missing-native": "installer-engine.ps1",
            "missing-reconcile": "installer-instance-reconcile.sh",
            "missing-source": "source-runtime.ps1",
            "missing-supervisor": "supervisor-lifecycle.ps1",
            "missing-base": "base-python.ps1",
            "missing-health": "installer_engine_health.py",
            "missing-cache": "versioned_runtime_cache.py",
            "missing-daemon-runtime": "daemon_instance_runtime.pyz",
            "missing-reconcile-python": "installer_instance_reconcile.py",
            "missing-gate-ps1": "runtime-gate.ps1",
            "missing-gate-sh": "runtime-gate.sh",
            "missing-resolver-ps1": "resolve-runtime.ps1",
            "missing-resolver-sh": "resolve-runtime.sh",
            "missing-retained": "installer_retained_content.py",
            "missing-driver": "installer_driver.py",
        }[reason]
        (payload / "scripts" / name).unlink()
    elif reason in {"dispatcher", "module", "provision-mode", "lifecycle", "catalog"}:
        path = payload / "payload-invocation.json"
        manifest = json.loads(path.read_text())
        if reason == "dispatcher":
            manifest["payloadDispatcher"]["windows"] = "scripts/other.ps1"
        elif reason == "module":
            manifest["module"] = "foreign_module"
        elif reason == "provision-mode":
            manifest["provisionMode"] = "snapshot"
        elif reason == "lifecycle":
            manifest["installerLifecycle"] = False
        else:
            manifest["windowsCatalogShim"] = "powershell"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    if reason in {"compound", "install"}:
        action = "stamp-provision" if reason == "compound" else "install"
    root = area / "cold"
    before = {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()}
    source = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    if native:
        command = native_command(root, payload, installation, project, client_only=reason == "client-only")
        command += ["-InstallerAction", action] if os.name == "nt" else ["--installer-action", action]
        result = subprocess.run(command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=60, **no_window_kwargs())
        assert result.returncode != 0, result.stdout + result.stderr
    else:
        with pytest.raises(provision.ProvisionError):
            provision.enqueue(payload, installation, project, root=root,
                              client_only=reason == "client-only", installer_action=action)
    assert not root.exists()
    assert {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()} == before
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == source


@pytest.mark.parametrize("submitted", [False, True], ids=["unset", "saved"])
def test_dispatch_routing_controls_restore_submitter_before_worker_startup(
    owning_dispatch: OwningPayload, area: Path, monkeypatch: pytest.MonkeyPatch, submitted: bool,
) -> None:
    payload, installation, project = owning_dispatch
    controls = {
        "AGENT_DISPATCH_SUPERVISE_MACHINE": "submitter-machine",
        "AGENT_DISPATCH_BASE_PYTHON": str(area / "submitter-python"),
    }
    for key, value in controls.items():
        if submitted:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)
    accepted = provision.enqueue(payload, installation, project, root=area / "accepted")
    for key in controls:
        monkeypatch.setenv(key, "foreign-worker")
    observed = area / "observed.json"
    root = area / "worker"
    worker = enqueue(
        accepted.spec, root=root,
        runner=TrustedRunner((
            sys.executable, "-c",
            "import json,os,sys; from pathlib import Path; "
            "Path(sys.argv[1]).write_text(json.dumps({k:os.environ.get(k) for k in sys.argv[2:]}))",
            str(observed), *controls,
        ), str(project), context_keys=accepted.runner.context_keys),
    )
    drain(root=root)
    result = _request(worker.id, root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    assert json.loads(observed.read_text()) == (
        controls if submitted else dict.fromkeys(controls)
    )
    assert not installation.exists()


@pytest.mark.parametrize("action", ["stamp", "provision"])
def test_dispatch_native_admits_exact_recipe_before_source_comparison(
    owning_dispatch: OwningPayload, area: Path, action: str,
) -> None:
    payload, installation, project = owning_dispatch
    root = area / "cold"
    command = native_command(root, payload, installation, project)
    command += (
        ["-Mode", "owner", "-ExpectedSource", "sha256:" + "0" * 64, "-InstallerAction", action]
        if os.name == "nt" else
        ["--run-owner", "sha256:" + "0" * 64, "--installer-action", action]
    )
    result = subprocess.run(command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=60, **no_window_kwargs())
    assert result.returncode != 0
    assert "Owning source changed after native first-use admission" in result.stderr, result.stderr
    assert not root.exists() and not installation.exists()


def test_dispatch_stamp_and_provision_are_distinct_requests(owning_dispatch: OwningPayload, area: Path) -> None:
    payload, installation, project = owning_dispatch
    root = area / "queue"
    requests = [
        provision.enqueue(payload, installation, project, root=root, installer_action=action)
        for action in ("stamp", "provision")
    ]
    assert len({request.id for request in requests}) == 2
    assert [json.loads(request.spec.source).get("installer_action", "provision")
            for request in requests] == ["stamp", "provision"]
    assert not installation.exists()


@pytest.mark.parametrize("change", ["substituted", "unavailable"])
def test_dispatch_never_substitutes_accepted_source(
    owning_dispatch: OwningPayload, area: Path, change: str,
) -> None:
    payload, installation, project = owning_dispatch
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    if change == "substituted":
        (payload / "scripts/install.ps1").write_text("exit 99\n", encoding="utf-8")
    else:
        payload.rename(payload.with_name("unavailable-dispatch"))
    with pytest.raises(ParticipantError):
        drain(root=root)
    result = _request(request.id, root)
    assert result.state == "interrupted" and result.exit_code is None
    assert not installation.exists()


@pytest.fixture
def materialized_dispatch(
    owning_dispatch: OwningPayload, area: Path, dispatch_cold_phase: Phase,
) -> OwningPayload:
    dispatch_cold_phase("materialize-start")
    payload, installation, project = owning_dispatch
    candidate = area / "materialized/plugins/agent-dispatch"
    shutil.copytree(REPOSITORY / "plugins/agent-dispatch", candidate, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", ".pytest_cache", "tests", "build", "dist", "*.egg-info",
    ))
    previous = sys.path[:]
    try:
        release = runpy.run_path(str(REPOSITORY / "tools/materialize_main.py"))
        result = release["materialize"](candidate.parents[1], canonical_root=REPOSITORY)
    finally:
        sys.path[:] = previous
    assert not any(line.startswith("SKIP ") for line in result), result
    shutil.rmtree(payload)
    shutil.copytree(candidate, payload)
    dispatch_cold_phase("materialize-ready")
    return payload, installation, project


@pytest.mark.parametrize("native", [False, True], ids=["python-api", "native-front"])
@pytest.mark.parametrize("missing", [
    "scripts/installer_driver.py", "src/agent_dispatch/windows-scheduling.ps1",
    "scripts/action_controls.py",
])
def test_dispatch_actual_installer_missing_prerequisite_refuses_before_acquisition(
    owning_dispatch: OwningPayload, area: Path, native: bool, missing: str,
) -> None:
    payload, installation, project = owning_dispatch
    shutil.copyfile(REPOSITORY / "plugins/agent-dispatch/scripts/install.ps1",
                    payload / "scripts/install.ps1")
    shutil.copyfile(REPOSITORY / "plugins/agent-dispatch/scripts/install.sh",
                    payload / "scripts/install.sh")
    (payload / missing).unlink(missing_ok=missing == "scripts/action_controls.py")
    if missing == "scripts/action_controls.py":
        installation.mkdir()
        (installation / "action-controls.json").write_text("{}", encoding="utf-8")
    root = area / "cold"
    before = {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()}
    installers = {
        payload / "scripts" / name: (payload / "scripts" / name).read_bytes()
        for name in ("install.ps1", "install.sh")
    }
    if native:
        result = subprocess.run(
            native_command(root, payload, installation, project),
            env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=60, **no_window_kwargs(),
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert "complete materialized installation helpers" in result.stderr, result.stderr
    else:
        with pytest.raises(provision.ProvisionError, match="complete materialized installation helpers"):
            provision.enqueue(payload, installation, project, root=root)
    assert not root.exists()
    assert {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()} == before
    assert {path: path.read_bytes() for path in installers} == installers


def test_actual_dispatch_stamp_defers_construction_and_service_effects(
    materialized_dispatch: OwningPayload, area: Path,
) -> None:
    payload, installation, project = materialized_dispatch
    before = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action="stamp")
    drain(root=root)
    result = _request(request.id, root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    pointer = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    assert pointer.is_dir()
    command = installation.parent / ".local/bin" / ("agent-dispatch.cmd" if os.name == "nt" else "agent-dispatch")
    assert command.is_file()
    assert not (installation / "current-version").exists()
    assert not list((installation / "versions").glob("*/pyvenv.cfg"))
    assert not (installation.parent / ".config/systemd").exists()
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == before


@pytest.mark.skipif(os.name != "nt", reason="actual Windows native installer admission")
@pytest.mark.parametrize("default_root", [True, False])
def test_dispatch_native_installer_admission_joins_original_queue(
    materialized_dispatch: OwningPayload, area: Path, default_root: bool,
) -> None:
    payload, installation, project = materialized_dispatch
    scripts = payload / "scripts"
    (scripts / "installer-engine.ps1").write_text(
        "function Ensure-Uv { param([string]$InstallRoot)\n"
        "return (Get-Command python -CommandType Application).Source\n}\n",
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        "param([string]$Action,[string]$InstallDir)\n$ErrorActionPreference = 'Stop'\n"
        "if (-not $env:UPDATE_DRIVER_WITNESS) { throw 'missing native participant handoff' }\n"
        ". (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')\n"
        "$result = Enter-NativeInstallerDriver -Payload (Split-Path $PSScriptRoot) "
        "-Root $InstallDir -Plugin agent-dispatch -Action $Action\n"
        "if (-not $result.Participant -or $result.ExitCode -ne 0) {\n"
        "  throw 'native installer admission did not join the original request'\n}\n"
        "[IO.File]::WriteAllText((Join-Path $InstallDir 'native-admitted'), $Action)\n"
        "exit 0\n", encoding="utf-8",
    )
    root = installation.parent / ".copilot-extensions/update-driver" if default_root else area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    drain(root=root)
    result = _request(request.id, root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    assert (installation / "native-admitted").read_text() == "provision"
    assert [item.id for item in _requests(root)] == [request.id]
    if not default_root:
        assert not (installation.parent / ".copilot-extensions/update-driver").exists()


@pytest.mark.parametrize("link_kind", ["hardlink", "symlink"])
def test_dispatch_systemctl_adapter_preserves_the_link_target(
    area: Path, link_kind: str,
) -> None:
    if link_kind == "symlink" and os.name == "nt":
        pytest.skip("POSIX fixture symlink; hardlink control exercises replacement on Windows")
    host_tool = area / "host-systemctl"
    host_tool.write_bytes(b"untouched host tool\n")
    adapter = area / "native-tools"
    adapter.mkdir()
    if link_kind == "hardlink":
        os.link(host_tool, adapter / "systemctl")
    else:
        (adapter / "systemctl").symlink_to(host_tool)
    replace_systemctl_adapter(adapter)
    assert host_tool.read_bytes() == b"untouched host tool\n"
    assert not (adapter / "systemctl").is_symlink()
    assert (adapter / "systemctl").read_bytes() == (
        Path(__file__).parent / "fixtures/dispatch-systemctl.sh"
    ).read_bytes()


def replace_systemctl_adapter(adapter: Path) -> None:
    (adapter / "systemctl").unlink(missing_ok=True)
    shutil.copyfile(Path(__file__).parent / "fixtures/dispatch-systemctl.sh", adapter / "systemctl")
    (adapter / "systemctl").chmod(0o700)


def dispatch_effect_adapter(payload: Path, env: dict[str, str]) -> Path:
    """Isolate OS registration and the installer's otherwise auto-starting health probe."""
    fixtures = Path(__file__).parent / "fixtures"
    if os.name == "nt":
        adapter = Path(env["PSModulePath"]) / "ScheduledTasks"
        adapter.mkdir()
        shutil.copyfile(fixtures / "dispatch-scheduled-tasks.psm1", adapter / "ScheduledTasks.psm1")
    else:
        adapter = Path(env["PATH"])
        for name in ("seq", "xargs", "cc", "as", "ld"):
            executable = shutil.which(name)
            assert executable, f"native installer requires {name}"
            (adapter / name).symlink_to(executable)
        replace_systemctl_adapter(adapter)
    main = payload / "src/agent_dispatch/__main__.py"
    body = main.read_text(encoding="utf-8")
    seam = 'if __name__ == "__main__":\n'
    assert body.count(seam) >= 1
    # Change the fixture source before acceptance, never a built or completed slot.
    synthetic = (
        "    if sys.argv[1:] in (['--local', 'health'], ['--local', 'deploy', '--json']):\n"
        f"        _effects = Path({str(adapter)!r})\n"
        "        _task = _effects / 'agent-dispatch.json'\n"
        "        if os.name == 'nt' and (not _task.is_file() or "
        "json.loads(_task.read_text(encoding='utf-8-sig'))['State'] not in {'Ready', 'Running'}):\n"
        "            raise SystemExit(97)\n"
        "        with (_effects / 'effects.jsonl').open('a', encoding='utf-8') as _out:\n"
        "            _out.write(json.dumps({'operation': 'synthetic-deploy' "
        "if sys.argv[2] == 'deploy' else 'synthetic-health'}) + '\\n')\n"
        "        from . import __version__\n"
        "        print(json.dumps({'version': __version__, 'fixture': True}))\n"
        "        raise SystemExit(0)\n"
        "    if sys.argv[1:] == ['_ensure-coordinator'] or "
        "any(_arg in {'serve', 'supervise'} for _arg in sys.argv[1:]):\n"
        "        raise RuntimeError('fixture refuses actual service launch')\n"
    )
    main.write_text(body.replace(seam, seam + synthetic, 1), encoding="utf-8")
    return adapter


def assert_dispatch_registration(installation: Path, adapter: Path) -> None:
    effects = [
        json.loads(line) for line in (adapter / "effects.jsonl").read_text(
            encoding="utf-8-sig",
        ).splitlines()
    ]
    if os.name == "nt":
        coordinator = json.loads((adapter / "agent-dispatch.json").read_text(encoding="utf-8-sig"))
        supervisor = json.loads((adapter / "agent-dispatch-supervisor.json").read_text(encoding="utf-8-sig"))
        assert coordinator["State"] == "Ready"
        assert coordinator["Principal"]["LogonType"] == "S4U"
        assert [item["AtStartup"] for item in coordinator["Triggers"]] == [True, False]
        assert [item["AtLogOn"] for item in coordinator["Triggers"]] == [False, True]
        assert supervisor["State"] == "Disabled"
        assert supervisor["Principal"]["LogonType"] == "Interactive"
        for task, launcher in ((coordinator, "serve-service.ps1"), (supervisor, "supervise-service.ps1")):
            assert task["Actions"][0]["Execute"] == "conhost.exe"
            assert str(installation / launcher) in task["Actions"][0]["Arguments"]
            assert task["Actions"][0]["WorkingDirectory"] == str(installation)
            assert task["Settings"]["RestartCount"] == 3
            assert task["Settings"]["RestartSeconds"] == 60
            assert task["Settings"]["LimitSeconds"] == 0
        assert {"operation": "start", "detail": "agent-dispatch"} not in effects
        assert supervisor["Settings"]["Enabled"] is False
        assert {"operation": "register", "detail": "agent-dispatch-supervisor"} in effects
        assert {"operation": "synthetic-deploy"} in effects
    else:
        units = installation.parent / ".config/systemd/user"
        coordinator = (units / "agent-dispatch.service").read_text()
        supervisor = (units / "agent-dispatch-supervisor.service").read_text()
        assert f"WorkingDirectory={installation}" in coordinator
        assert f"Environment=AGENT_DISPATCH_INSTALL_DIR={installation}" in coordinator
        assert f"ExecStart={installation}/versions/" in coordinator
        assert "-m agent_dispatch serve" in coordinator
        assert f"WorkingDirectory={installation}" in supervisor
        assert "supervise-service.sh --systemd-unit agent-dispatch-supervisor.service" in supervisor
        for arguments in (
            "--user enable agent-dispatch.service", "--user restart agent-dispatch.service",
            "--user stop agent-dispatch-supervisor.service",
            "--user disable agent-dispatch-supervisor.service",
        ):
            assert {"operation": "systemctl", "arguments": arguments} in effects
        assert (adapter / "agent-dispatch.service.active").read_text() == "active"
        assert (adapter / "agent-dispatch-supervisor.service.enabled").read_text() == "disabled"
    assert not (installation / "tasks.db").exists()
    assert not (installation / "rendezvous.json").exists()


def dispatch_native_setup(
    materialized_dispatch: OwningPayload, area: Path,
) -> tuple[dict[str, str], Path, dict[Path, bytes], Path, Diagnostic, list[str]]:
    payload, installation, project = materialized_dispatch
    inputs = area / "foundation-source" / "libs"
    for name in PACKAGES:
        shutil.copytree(REPOSITORY / "libs" / name, inputs / name, ignore=lambda _path, names: {
            name for name in names if name in GENERATED
            or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))
        })
    build_bridge_native_test_assets(payload, inputs / "agent-procutil")
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    if os.name == "nt":
        installation.mkdir()
        (installation / "installation.json").write_text(json.dumps({
            "version": 1,
            "installation": {
                "root": str(installation), "local": {"windows_scheduled_task": "enabled"},
            },
        }), encoding="utf-8")
    adapter = dispatch_effect_adapter(payload, env)
    before = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    root = installation.parent / ".copilot-extensions/update-driver"
    diagnostic = diagnostic_for(root, area)
    command = native_command(
        root, payload, installation, project, source=inputs / "update-driver",
    ) + (["-Wait"] if os.name == "nt" else ["--wait"])
    return env, adapter, before, root, diagnostic, command


def test_dispatch_copied_foundation_supplies_the_actual_lifecycle_archive(
    materialized_dispatch: OwningPayload, area: Path,
) -> None:
    import io

    assets = runpy.run_path(str(REPOSITORY / "tools/daemon_lifecycle_assets.py"))
    lifecycle_archive = cast(Callable[[Path], bytes], assets["lifecycle_archive"])
    dispatch_native_setup(materialized_dispatch, area)
    with zipfile.ZipFile(io.BytesIO(lifecycle_archive(area / "foundation-source"))) as archive:
        assert {
            "agent_procutil/_daemon_instance_cli.py", "agent_procutil/_daemon_instance_store.py",
            "agent_procutil/_daemon_instance_io.py",
        } <= set(archive.namelist())


def assert_dispatch_consumers(
    payload: Path, installation: Path, project: Path, env: dict[str, str],
) -> None:
    selected = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / selected
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    identity = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import agent_dispatch,json;from importlib.metadata import version;"
         "print(json.dumps({'origin':agent_dispatch.__file__,'version':version('agent-dispatch')}))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert identity.returncode == 0, identity.stdout + identity.stderr
    actual = json.loads(identity.stdout)
    assert Path(actual["origin"]).is_relative_to(slot)
    assert Version(actual["version"]) == Version(json.loads((payload / "plugin.json").read_text())["version"])
    suffix = ".ps1" if os.name == "nt" else ""
    for script in (
        payload / "bin" / ("agent-dispatch" + suffix),
        installation.parent / ".local/bin" / ("agent-dispatch" + suffix),
    ):
        consumer = subprocess.run(
            command_for(script), env={**env, "AGENT_DISPATCH_NO_SELFPROVISION": "1"},
            cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert consumer.returncode == 0 and "usage:" in consumer.stdout.lower(), consumer.stdout + consumer.stderr


@native_genesis
def test_actual_dispatch_original_provision_survives_caller_loss(
    materialized_dispatch: OwningPayload, area: Path,
    native_recipe_runner: NativeRecipeRunner, native_window_observation: None,
    dispatch_cold_body_phase: Phase,
) -> None:
    phase = dispatch_cold_body_phase
    payload, installation, project = materialized_dispatch
    phase("setup-start")
    env, adapter, before, root, diagnostic, command = dispatch_native_setup(materialized_dispatch, area)
    phase("setup-ready")
    phase("caller-start")
    caller, _log = native_recipe_runner.start(
        command, env=env, cwd=project, root=root, diagnostic=diagnostic, contain_caller=True,
    )
    phase("caller-acknowledged")
    phase("foundation-wait")
    observe(lambda: foundation_status(root=root) is not None, 120, diagnostic)
    foundation = foundation_status(root=root)
    assert foundation is not None and foundation.state == "running", diagnostic()
    native_recipe_runner.lose_caller(caller)
    phase("caller-closed")

    def original_completed() -> bool:
        foundation = foundation_status(root=root)
        if foundation is not None and foundation.state == "completed":
            assert foundation.exit_code == 0, diagnostic()
        logs = list(root.glob("first-use-*.log"))
        assert len(logs) <= 1, diagnostic()
        if native_recipe_runner.owners_finished():
            assert logs and receipt_completed_count(root, 1, logs[0]), diagnostic()
        return bool(logs) and receipt_completed_count(root, 1, logs[0])

    phase("receipt-wait")
    observe(original_completed, 900, diagnostic)
    phase("receipt-completed")
    result, = _requests(root)
    assert result.state == "completed" and result.exit_code == 0, diagnostic()
    assert result.spec.argv == ()
    admitted = json.loads(result.spec.source)
    assert (admitted["plugin"], admitted["payload"], admitted["runtime_root"], admitted["project"]) == (
        "agent-dispatch", str(payload), str(installation), str(project),
    )
    assert_dispatch_registration(installation, adapter)
    phase("registration-checked")
    selected = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / selected
    completion = slot / ".install-complete.json"
    completed = completion.read_bytes()
    assert_dispatch_consumers(payload, installation, project, env)
    phase("consumers-checked")
    phase("owner-settlement-wait")
    observe(native_recipe_runner.owners_finished, 120, diagnostic)
    phase("owners-settled")
    runner = TrustedRunner((sys.executable, "-c", "pass"), str(project))
    failed = enqueue(RequestSpec("retained-dispatch-failure"), runner=runner, root=root)
    drain(root=root, executor=lambda _transaction: 23)
    assert _request(failed.id, root).exit_code == 23
    pending = enqueue(RequestSpec("retained-dispatch-pending"), runner=runner, root=root)
    history = _requests(root)
    effects = (adapter / "effects.jsonl").read_bytes()
    try:
        assert_dispatch_consumers(payload, installation, project, env)
        assert _requests(root) == history and completion.read_bytes() == completed
        assert (adapter / "effects.jsonl").read_bytes() == effects
        assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == before
        phase("history-checked")
    finally:
        phase("pending-cleanup-start")
        assert [item.id for item in _requests(root) if item.state == "pending"] == [pending.id]
        drain(root=root, executor=lambda _transaction: 0)
        phase("pending-cleanup-finished")


@native_genesis
def test_actual_dispatch_concurrent_provision_coalesces_one_request(
    materialized_dispatch: OwningPayload, area: Path,
    native_recipe_runner: NativeRecipeRunner, native_window_observation: None,
) -> None:
    payload, installation, project = materialized_dispatch
    env, adapter, before, root, diagnostic, command = dispatch_native_setup(materialized_dispatch, area)
    first, _ = native_recipe_runner.start(command, env=env, cwd=project, root=root, diagnostic=diagnostic)
    second, _ = native_recipe_runner.start(command, env=env, cwd=project, root=root, diagnostic=diagnostic)
    assert first.wait(timeout=900) == second.wait(timeout=900) == 0, diagnostic()
    result, = _requests(root)
    assert result.state == "completed" and result.exit_code == 0, diagnostic()
    assert_dispatch_registration(installation, adapter)
    assert_dispatch_consumers(payload, installation, project, env)
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == before


@native_genesis
def test_actual_dispatch_reuse_failed_candidate_and_unavailable_source_preserve_selection(
    materialized_dispatch: OwningPayload, area: Path,
    native_recipe_runner: NativeRecipeRunner, native_window_observation: None,
) -> None:
    payload, installation, project = materialized_dispatch
    env, adapter, _before, root, diagnostic, command = dispatch_native_setup(materialized_dispatch, area)

    def provision_once() -> subprocess.CompletedProcess[str]:
        return native_recipe_runner.run(command, env=env, cwd=project, root=root, diagnostic=diagnostic)

    assert provision_once().returncode == 0, diagnostic()
    selected = (installation / "current-version").read_text().strip()
    completion = installation / "versions" / selected / ".install-complete.json"
    bin_dir = installation.parent / ".local/bin"
    retained = {
        path: path.read_bytes() for path in (
            completion, installation / "current-version", installation / "last-known-good",
            installation / "payload-dir", installation / "stamped-version", *bin_dir.glob("agent-dispatch*"),
        ) if path.is_file()
    }
    assert provision_once().returncode == 0, diagnostic()
    assert len(_requests(root)) == 2
    assert {path: path.read_bytes() for path in retained} == retained
    assert_dispatch_consumers(payload, installation, project, env)
    (payload / "src/agent_dispatch/__init__.py").write_text(
        'raise RuntimeError("fixture failed Dispatch candidate")\n', encoding="utf-8",
    )
    manifest = payload / "plugin.json"
    data = json.loads(manifest.read_text())
    data["version"] = "99.0.0"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    metadata = payload / "pyproject.toml"
    body, count = re.subn(r'(?m)^version = "[^"]+"$', 'version = "99.0.0"', metadata.read_text(), count=1)
    assert count == 1
    metadata.write_text(body, encoding="utf-8")
    assert provision_once().returncode != 0, diagnostic()
    failed, = [item for item in _requests(root) if item.exit_code != 0]
    assert failed.state == "completed" and failed.exit_code is not None, diagnostic()
    failure_log = failed.log_path.read_text(errors="replace")
    assert any(message in failure_log for message in (
        "Fresh runtime slot failed its health gate", "fixture failed Dispatch candidate",
    )), diagnostic()
    assert {path: path.read_bytes() for path in retained} == retained
    assert not list((installation / "versions").glob("99.0.0*/.install-complete.json"))
    payload.rename(payload.with_name("unavailable-dispatch"))
    python = installation / "versions" / selected / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    history = _requests(root)
    consumer = subprocess.run(
        [str(python), "-I", "-B", "-m", "agent_dispatch", "--help"],
        env={**env, "AGENT_DISPATCH_NO_SELFPROVISION": "1"},
        cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert consumer.returncode == 0 and "usage:" in consumer.stdout.lower(), consumer.stdout + consumer.stderr
    assert _requests(root) == history
    assert {path: path.read_bytes() for path in retained} == retained


@native_genesis
def test_actual_dispatch_supervisor_effect_failure_is_visible(
    materialized_dispatch: OwningPayload, area: Path,
    native_recipe_runner: NativeRecipeRunner, native_window_observation: None,
) -> None:
    payload, installation, project = materialized_dispatch
    env, adapter, _before, root, diagnostic, command = dispatch_native_setup(materialized_dispatch, area)
    failure = "supervisor-registration-denied" if os.name == "nt" else "supervisor-disable-denied"
    (adapter / "mode").write_text(failure, encoding="utf-8")
    operation = native_recipe_runner.run(command, env=env, cwd=project, root=root, diagnostic=diagnostic)
    assert (operation.returncode != 0) if os.name == "nt" else (operation.returncode == 0), diagnostic()
    result, = _requests(root)
    assert result.state == "completed"
    assert result.exit_code is not None
    assert (result.exit_code != 0) if os.name == "nt" else (result.exit_code == 0), diagnostic()
    log = result.log_path.read_text(errors="replace")
    assert "supervisor" in log.lower(), diagnostic()
    assert "failed" in log.lower() or "denied" in log.lower(), diagnostic()
    effects = [
        json.loads(line) for line in (adapter / "effects.jsonl").read_text(
            encoding="utf-8-sig",
        ).splitlines()
    ]
    assert any(item["operation"] == failure for item in effects)
    if os.name == "nt":
        assert not (adapter / "agent-dispatch-supervisor.json").exists()
    else:
        assert not (adapter / "agent-dispatch-supervisor.service.active").exists() or (
            adapter / "agent-dispatch-supervisor.service.active"
        ).read_text() != "active"
    assert_dispatch_consumers(payload, installation, project, env)
    assert not (installation / "tasks.db").exists()
