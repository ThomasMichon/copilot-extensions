"""Actual Bridge hook, independently materialized foundation and native effects."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import agent_procutil
import pytest
from update_driver import core, foundation_status, status
from update_driver.foundation_inputs import PACKAGES, SUPPORT
from update_driver.foundation_inputs import inspect as inspect_foundation

from test_first_use import primitive
from test_first_use_bridge import (
    assert_bridge_registration, bridge_service_adapter,
    materialized_bridge as materialized_bridge, set_payload_version,
)
from test_first_use_codespaces_bootstrap import hook_command
from test_first_use_genesis import observe
from test_first_use_logger import registration_effects
from test_first_use_pull_requests_command import files
from test_first_use_vault import cold_environment, native_genesis
from test_first_use_vault_bootstrap import ordinary_receipt, owned_hook, receipt_completed_count


pytestmark = [
    pytest.mark.skipif(
        os.name != "nt" and sys.platform != "linux", reason="native Windows/Linux Bridge bootstrap",
    ),
    pytest.mark.skipif(
        os.environ.get("UPDATE_DRIVER_BRIDGE_BOOTSTRAP_ACCEPTANCE") != "1",
        reason="explicit manual Bridge bootstrap portfolio",
    ),
]
ACKNOWLEDGEMENT = "[agent-bridge] driver launch acknowledgement: "
REPOSITORY = Path(__file__).resolve().parents[3]


@pytest.fixture
def bridge_admission_payload(owning_bridge):
    """Real admission code; refusal cases need no copied runtime/dependency graph."""
    payload, installation, project = owning_bridge
    for name in ("hooks.json", "scripts/bootstrap-check.ps1", "scripts/bootstrap-check.sh"):
        shutil.copyfile(REPOSITORY / "plugins/agent-bridge" / name, payload / name)
    shutil.copytree(
        REPOSITORY / "libs/update-driver/scripts", payload / "libs/update-driver/scripts",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return payload, installation, project


@pytest.fixture
def settle_bootstrap():
    roots = []
    yield roots
    for root, diagnostic in roots:
        observe(lambda: foundation_status(root=root) is None or
                foundation_status(root=root).state != "running", 900, diagnostic)
        observe(lambda: all(item.state not in {"pending", "running"}
                            for item in status(root=root)), 900, diagnostic)

        def released():
            lock = core.OperationLock(root / "execution.lock")
            try:
                lock.acquire()
            except core.OperationLockedError:
                return False
            lock.release()
            return True

        observe(released, 900, diagnostic)


def test_materialized_bridge_has_complete_canonical_foundation(materialized_bridge):
    payload, _installation, _project = materialized_bridge
    source = payload / "libs/update-driver"
    assert {path.name for path in inspect_foundation(source).packages} == set(PACKAGES)
    for support in SUPPORT:
        assert (source / "scripts" / support).is_file()
    for suffix in ("ps1", "sh"):
        assert (source / "scripts" / f"first-use.{suffix}").is_file()
    assert "../../libs/" not in (payload / "pyproject.toml").read_text()


def test_complete_materialized_bridge_managed_refusal_is_read_only(materialized_bridge):
    payload, installation, project = materialized_bridge
    api = primitive(payload)
    arguments = dict(
        payload_root=payload, plugin_id="agent-bridge",
        legacy_root=installation, project_root=project,
    )
    resolution = api.resolve_installation_mode(**arguments)
    installation.mkdir()
    (installation / "runtime-deployment.json").write_text(json.dumps({
        "schema": api.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
        "owner": api._deployment_owner(resolution, arguments), "managed": True,
    }), encoding="utf-8")
    source = files(payload)
    for phase in ("cold", "drift"):
        if phase == "drift":
            (installation / "deploy-manifest.json").write_text(json.dumps({
                "source": {"version": "0.0.0", "path": str(payload)},
            }), encoding="utf-8")
        before = files(installation)
        result = subprocess.run(
            hook_command(payload, project, installation.parent), env=dict(os.environ), cwd=project,
            capture_output=True, text=True, encoding="utf-8", timeout=15,
            **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode == 3 and result.stdout == "{}" and result.stderr
        assert not (installation.parent / ".copilot-extensions/update-driver").exists()
        assert files(installation) == before
    assert files(payload) == source


@pytest.mark.parametrize("phase", ["cold", "drift"])
@pytest.mark.parametrize("reason", [
    "managed", "malformed", "no-self", "pinned", "nested", "cell",
    "missing-driver", "old-driver", "noninstalled", "declaration",
])
def test_actual_bridge_bootstrap_refuses_before_writes(
    bridge_admission_payload, monkeypatch, area, phase, reason,
):
    payload, installation, project = bridge_admission_payload
    if reason == "noninstalled":
        copied = area / "copy/agent-bridge"
        shutil.copytree(payload, copied)
        payload = copied
    if phase == "drift":
        installation.mkdir()
        (installation / "deploy-manifest.json").write_text(json.dumps({
            "source": {"version": "0.0.0", "path": str(payload)},
        }), encoding="utf-8")
        (installation / "reconcile-status.json").write_text(
            '{"launched_pid":1,"at":"2000-01-01T00:00:00Z"}', encoding="utf-8",
        )
    if reason in {"managed", "malformed"}:
        api = primitive(payload)
        arguments = dict(
            payload_root=payload, plugin_id="agent-bridge",
            legacy_root=installation, project_root=project,
        )
        resolution = api.resolve_installation_mode(**arguments)
        installation.mkdir(exist_ok=True)
        policy = {
            "schema": api.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": api._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    elif reason in {"no-self", "pinned", "nested", "cell"}:
        monkeypatch.setenv({
            "no-self": "AGENT_BRIDGE_NO_SELFPROVISION",
            "pinned": "AGENT_BRIDGE_PINNED_CONTEXT",
            "nested": "UPDATE_DRIVER_WITNESS", "cell": "AGENT_BRIDGE_INSTALLATION_ID",
        }[reason], "foreign")
    elif reason in {"missing-driver", "old-driver"}:
        driver = payload / "libs/update-driver/scripts" / (
            "first-use.ps1" if os.name == "nt" else "first-use.sh"
        )
        if reason == "missing-driver":
            driver.unlink()
        else:
            driver.write_text("# Installation driver lifecycle contract: vault-stamp-v1\nexit 99\n",
                              encoding="utf-8")
    elif reason == "declaration":
        declaration = payload / "payload-invocation.json"
        manifest = json.loads(declaration.read_text())
        manifest["provisionMode"] = "snapshot"
        declaration.write_text(json.dumps(manifest), encoding="utf-8")
    before, source = files(installation), files(payload)
    result = subprocess.run(
        hook_command(payload, project, installation.parent), env=dict(os.environ), cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=15,
        **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == (0 if reason in {"no-self", "pinned"} else 3), (
        result.stdout + result.stderr
    )
    assert result.stdout == "{}" and result.stderr
    assert not (installation.parent / ".copilot-extensions/update-driver").exists()
    assert files(installation) == before and files(payload) == source


@pytest.mark.parametrize("reason", ["namespaced", "killswitch"])
def test_actual_bridge_bootstrap_retains_nonpublication_noops(
    bridge_admission_payload, monkeypatch, reason,
):
    payload, installation, project = bridge_admission_payload
    if reason == "namespaced":
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{}")
    else:
        guard = payload / "scripts" / (
            "bootstrap-killswitch-guard.ps1" if os.name == "nt" else "bootstrap-killswitch-guard.sh"
        )
        guard.write_text("exit 0\n", encoding="utf-8")
    result = subprocess.run(
        hook_command(payload, project, installation.parent), env=dict(os.environ), cwd=project,
        capture_output=True, text=True, timeout=15, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 0 and result.stdout == "{}" and not result.stderr
    assert not installation.exists()
    assert not (installation.parent / ".copilot-extensions/update-driver").exists()


def test_bootstrap_refusal_keeps_independent_bridge_guidance(materialized_bridge):
    payload, installation, project = materialized_bridge
    profile = installation.parent
    env = {**os.environ, "AGENT_BRIDGE_NO_SELFPROVISION": "1", "COPILOT_PLUGIN_ROOT": str(payload)}
    for name, timeout, stdin in (
        ("bootstrap-check", 15, None),
        ("write-session-guidance", 45, '{"sessionId":"bridge-bootstrap-independent","cwd":'
         + json.dumps(str(project)) + "}"),
    ):
        result = subprocess.run(
            hook_command(payload, project, profile, name), env=env, cwd=project,
            input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
            **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode == 0 and result.stdout == "{}", result.stdout + result.stderr
    guidance = profile / (
        ".copilot/session-state/bridge-bootstrap-independent/instructions"
        "/agent-bridge/session-guidance.instructions.md"
    )
    text = guidance.read_text()
    assert "# Agent Bridge session guidance" in text
    catalogs = [json.loads(block.split("\n```", 1)[0]) for block in text.split("```json\n")[1:]]
    catalog = next(item for item in catalogs
                   if item.get("schema") == "copilot-extensions.session-command-catalog")
    assert catalog["commands"][0]["argv"] == [str(payload / "bin" / (
        "agent-bridge.ps1" if os.name == "nt" else "agent-bridge"
    ))]
    assert not installation.exists()
    assert not (profile / ".copilot-extensions/update-driver").exists()


@native_genesis
def test_actual_bridge_hook_deadline_caller_loss_drift_install_and_consumers(
    materialized_bridge, area, native_window_observation, settle_bootstrap,
):
    payload, installation, project = materialized_bridge
    profile = installation.parent
    root = profile / ".copilot-extensions/update-driver"
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    env["AGENT_BRIDGE_CONFIG_DIR"] = str(installation)
    env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(profile / "bootstrap-killswitch.json")
    foreign = area / "foreign-project"
    foreign.mkdir()
    env["COPILOT_PROJECT_DIR"] = env["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] = str(foreign)
    adapter = bridge_service_adapter(env)
    deadline = json.loads((payload / "hooks.json").read_text())["hooks"]["sessionStart"][0]["timeoutSec"]
    assert deadline == 15
    command = hook_command(payload, project, profile)

    def diagnostic():
        return "\n".join(
            f"{path.relative_to(area)}:\n{path.read_text(errors='replace')[-16000:]}"
            for path in area.rglob("*")
            if path.is_file() and (path.suffix == ".log" or path.name in {"stdout", "stderr"})
        )

    @contextmanager
    def owned(label):
        directory = area / label
        with owned_hook(
            command, env=env, project=project, directory=directory,
            timeout=deadline, diagnostic=diagnostic, acknowledgement_prefix=ACKNOWLEDGEMENT,
        ) as caller:
            assert (directory / "stdout").read_text(encoding="utf-8-sig") == "{}", diagnostic()
            yield caller

    settle_bootstrap.append((root, diagnostic))

    def assert_admission(item, action):
        admission = json.loads(item.spec.source)
        assert admission["plugin"] == "agent-bridge" and admission["installer_action"] == action
        assert Path(admission["payload"]) == payload
        assert Path(admission["runtime_root"]) == installation
        assert Path(admission["project"]) == project

    with owned("cold-first") as first:
        observe(lambda: foundation_status(root=root) is not None, 90, diagnostic)
        operation = foundation_status(root=root).operation_id
        assert foundation_status(root=root).state == "running" and not first.exited(), diagnostic()
        first.close_caller()
        with owned("cold-second") as second:
            second.close_caller()

            def both_completed():
                receipts = [ordinary_receipt(caller.receipt_log) for caller in (first, second)]
                return all(receipts) and all(
                    status(receipt, root=root).state == "completed" for receipt in receipts
                )

            observe(both_completed, 850, diagnostic)
            for caller in (first, second):
                observe(caller.exited, 60, diagnostic)
    requests = status(root=root)
    assert 1 <= len(requests) <= 2
    assert all(item.state == "completed" and item.exit_code == 0 for item in requests), diagnostic()
    assert foundation_status(root=root).operation_id == operation
    ordered = sorted(requests, key=lambda item: item.started_at)
    assert all(left.finished_at <= right.started_at for left, right in zip(ordered, ordered[1:]))
    for item in requests:
        assert_admission(item, "stamp")
    assert not (installation / "current-version").exists()
    assert not registration_effects(adapter)
    count = len(requests)
    history = installation / "reconcile-status.json"
    history.write_text('{"launched_pid":1,"at":"2000-01-01T00:00:00Z"}', encoding="utf-8")
    history_bytes = history.read_bytes()

    manifest = installation / "deploy-manifest.json"
    assert not manifest.exists()
    # A prior unprepared deployment, unlike stamp alone, selects drift install.
    manifest.write_text(json.dumps({
        "source": {"version": "0.0.0", "path": str(payload)},
    }), encoding="utf-8")
    with owned("drift-install") as caller:
        caller.close_caller()
        observe(lambda: receipt_completed_count(root, count + 1, caller.receipt_log), 850, diagnostic)
        drift = status(ordinary_receipt(caller.receipt_log), root=root)
        assert drift.exit_code == 0, diagnostic()
        assert_admission(drift, "install")
        observe(caller.exited, 60, diagnostic)
    count += 1
    assert_bridge_registration(installation, adapter, unattended=os.name == "nt")
    selected = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / selected
    completion = slot / ".install-complete.json"
    complete = completion.read_bytes()
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    healthy_env = dict(env)
    assert shutil.which("python3", path=healthy_env["PATH"]) is None
    assert shutil.which("python", path=healthy_env["PATH"]) is None
    healthy = subprocess.run(
        command, env=healthy_env, cwd=project, capture_output=True, text=True,
        timeout=deadline, **agent_procutil.no_window_kwargs(),
    )
    assert healthy.returncode == 0 and healthy.stdout == "{}" and not healthy.stderr, diagnostic()
    assert len(status(root=root)) == count and completion.read_bytes() == complete

    effects = registration_effects(adapter)
    set_payload_version(payload, "99.0.0")
    (payload / "src/agent_bridge/__init__.py").write_text(
        'raise RuntimeError("fixture failed bootstrap candidate")\n', encoding="utf-8",
    )
    with owned("failed-candidate") as caller:
        caller.close_caller()
        observe(lambda: receipt_completed_count(root, count + 1, caller.receipt_log), 850, diagnostic)
        failed = status(ordinary_receipt(caller.receipt_log), root=root)
        assert failed.exit_code != 0, diagnostic()
        assert_admission(failed, "install")
        observe(caller.exited, 60, diagnostic)
    count += 1
    assert (installation / "current-version").read_text().strip() == selected
    assert completion.read_bytes() == complete
    assert registration_effects(adapter) == effects
    assert history.read_bytes() == history_bytes
    payload.rename(payload.with_name("removed-bridge"))
    primary = profile / ".local/bin" / ("agent-bridge.ps1" if os.name == "nt" else "agent-bridge")
    dispatch = (
        [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
         "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(primary), "--help"]
        if os.name == "nt" else [str(Path(env["PATH"]) / "bash"), str(primary), "--help"]
    )
    for argv in (
        dispatch, [str(python), "-I", "-B", "-m", "agent_bridge", "--help"],
        [str(python), "-I", "-B", "-c",
         "import importlib.metadata as m; import agent_bridge; "
         "from pathlib import Path; from packaging.version import Version; "
         f"assert Path(agent_bridge.__file__).is_relative_to(Path({str(slot)!r})); "
         f"assert Version(m.version('agent-bridge')) == Version({selected!r}); print('installed-consumer-ok')"],
    ):
        result = subprocess.run(
            argv, env={**env, "AGENT_BRIDGE_NO_SELFPROVISION": "1"}, cwd=project,
            capture_output=True, text=True, timeout=30, **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert len(status(root=root)) == count and completion.read_bytes() == complete
