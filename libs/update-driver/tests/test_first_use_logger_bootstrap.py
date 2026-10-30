"""Actual Logger bootstrap, packaged foundation and independently owned hooks."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

import agent_procutil
import pytest
from update_driver import foundation_status, status
from update_driver.foundation_inputs import PACKAGES, SUPPORT
from update_driver.foundation_inputs import inspect as inspect_foundation

from test_first_use import primitive
from test_first_use_codespaces_bootstrap import hook_command
from test_first_use_genesis import observe
from test_first_use_logger import (
    assert_logger_registration, assert_no_periodic_worker, logger_registration_adapter,
    materialized_logger as materialized_logger, registration_effects,
)
from test_first_use_native import native_command
from test_first_use_pull_requests_command import files
from test_first_use_vault import cold_environment, native_genesis
from test_first_use_vault_bootstrap import ordinary_receipt, owned_hook, receipt_completed_count


pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux", reason="native Windows/Linux Logger bootstrap",
)
ACKNOWLEDGEMENT = "[agent-logger] driver launch acknowledgement: "


def _cold_results_completed(root, callers, progress):
    assert progress is not None, "Established foundation progress disappeared"
    assert progress.state not in {"failed", "interrupted"}, (
        f"Foundation {progress.state}: exit={progress.exit_code}, reason={progress.error}"
    )
    receipts = []
    for caller in callers:
        receipt = ordinary_receipt(caller.receipt_log)
        if receipt is None and caller.exited():
            receipt = ordinary_receipt(caller.receipt_log)
            assert receipt is not None, "Native owner exited without an ordinary request receipt"
        receipts.append(receipt)
    if not all(receipts):
        return False
    requests = [status(receipt, root=root) for receipt in receipts]
    assert all(item.state != "interrupted" for item in requests), "Ordinary bootstrap request interrupted"
    return all(item.state == "completed" for item in requests)


@pytest.mark.parametrize("state", ["failed", "interrupted"])
def test_cold_results_reject_failed_foundation_before_waiting_for_receipts(area, state):
    progress = SimpleNamespace(state=state, exit_code=7 if state == "failed" else None,
                               error="fixture_foundation_failure")
    with pytest.raises(AssertionError, match=f"Foundation {state}"):
        _cold_results_completed(area, [], progress)


def test_cold_results_reject_owner_exit_without_receipt(area):
    progress = SimpleNamespace(state="running")
    caller = SimpleNamespace(receipt_log=area / "absent.log", exited=lambda: True)
    with pytest.raises(AssertionError, match="without an ordinary request receipt"):
        _cold_results_completed(area, [caller], progress)


def test_cold_results_recheck_receipt_committed_before_owner_exit(area, monkeypatch):
    log = area / "receipt.log"

    def exited():
        log.write_text('{"request_id":"fixture-request","state":"pending"}\n', encoding="utf-8")
        return True

    monkeypatch.setattr(sys.modules[__name__], "status", lambda _receipt, **_kwargs: SimpleNamespace(state="completed"))
    caller = SimpleNamespace(receipt_log=log, exited=exited)
    assert _cold_results_completed(area, [caller], SimpleNamespace(state="completed"))


@pytest.mark.parametrize("state", ["pending", "running", "completed", "interrupted"])
def test_cold_results_preserve_ordinary_request_outcomes(area, monkeypatch, state):
    monkeypatch.setattr(sys.modules[__name__], "ordinary_receipt", lambda _path: "fixture-request")
    monkeypatch.setattr(sys.modules[__name__], "status", lambda _receipt, **_kwargs: SimpleNamespace(state=state))
    caller = SimpleNamespace(receipt_log=area / "receipt.log", exited=lambda: False)
    if state == "interrupted":
        with pytest.raises(AssertionError, match="request interrupted"):
            _cold_results_completed(area, [caller], SimpleNamespace(state="completed"))
    else:
        assert _cold_results_completed(area, [caller], SimpleNamespace(state="completed")) == (state == "completed")


def test_materialized_logger_has_complete_independent_foundation(materialized_logger):
    payload, _installation, _project = materialized_logger
    source = payload / "libs/update-driver"
    assert {path.name for path in inspect_foundation(source).packages} == set(PACKAGES)
    for support in SUPPORT:
        assert (source / "scripts" / support).is_file()
    for suffix in ("ps1", "sh"):
        assert (source / "scripts" / f"first-use.{suffix}").is_file()
    assert '../../libs/' not in (payload / "pyproject.toml").read_text()


@pytest.mark.parametrize("phase", ["cold", "drift"])
@pytest.mark.parametrize("reason", [
    "managed", "malformed", "no-self", "pinned", "nested", "cell",
    "missing-driver", "old-driver", "noninstalled", "declaration",
])
def test_actual_logger_bootstrap_refuses_before_writes(
    materialized_logger, monkeypatch, area, phase, reason,
):
    payload, installation, project = materialized_logger
    if reason == "noninstalled":
        copied = area / "copy/agent-logger"
        shutil.copytree(payload, copied)
        payload = copied
    if phase == "drift":
        installation.mkdir()
        (installation / "deploy-manifest.json").write_text(json.dumps({
            "source": {"version": "0.0.0", "path": str(payload)},
        }), encoding="utf-8")
        (installation / "reconcile.lock").write_text("424242", encoding="utf-8")
    if reason in {"managed", "malformed"}:
        api = primitive(payload)
        arguments = dict(
            payload_root=payload, plugin_id="agent-logger",
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
            "no-self": "AGENT_LOGGER_NO_SELFPROVISION",
            "pinned": "AGENT_LOGGER_PINNED_CONTEXT",
            "nested": "UPDATE_DRIVER_WITNESS", "cell": "AGENT_LOGGER_INSTALLATION_ID",
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
        manifest["provisionMode"] = "direct"
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
def test_actual_logger_bootstrap_retains_nonpublication_noops(
    materialized_logger, monkeypatch, reason,
):
    payload, installation, project = materialized_logger
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


def test_logger_bootstrap_refusal_keeps_independent_provider_and_guidance(materialized_logger):
    payload, installation, project = materialized_logger
    profile = installation.parent
    env = {
        **os.environ, "AGENT_LOGGER_NO_SELFPROVISION": "1",
        "COPILOT_PLUGIN_ROOT": str(payload),
        "AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR": str(profile / "providers.d"),
    }
    for name, timeout, stdin in (
        ("bootstrap-check", 15, None),
        ("register-cold-store-provider", 10, None),
        ("write-session-guidance", 45, '{"sessionId":"bootstrap-independent","cwd":'
         + json.dumps(str(project)) + "}"),
    ):
        result = subprocess.run(
            hook_command(payload, project, profile, name), env=env, cwd=project,
            input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
            **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode == 0 and result.stdout == "{}", result.stdout + result.stderr
    provider = json.loads((profile / "providers.d/agent-logger.json").read_text(encoding="utf-8-sig"))
    assert Path(provider["plugin_root"]) == payload
    assert provider["command"] == [str(payload / "bin" / (
        "agent-logger.cmd" if os.name == "nt" else "agent-logger"
    ))]
    guidance = profile / (
        ".copilot/session-state/bootstrap-independent/instructions"
        "/agent-logger/session-guidance.instructions.md"
    )
    assert guidance.read_text().startswith("# Agent Logger session guidance\n")
    assert not installation.exists()
    assert not (profile / ".copilot-extensions/update-driver").exists()


@native_genesis
def test_actual_logger_hook_deadline_caller_loss_drift_install_and_consumers(
    materialized_logger, area, native_window_observation,
):
    print("Logger cold hook: preparing environment", file=sys.stderr, flush=True)
    payload, installation, project = materialized_logger
    profile = installation.parent
    root = profile / ".copilot-extensions/update-driver"
    env = cold_environment(installation, project, area, python_prerequisite=True)
    print("Logger cold hook: environment ready", file=sys.stderr, flush=True)
    env.pop("AGENT_VAULT_HOME")
    env["AGENT_LOGGER_HOME"] = str(installation)
    env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(profile / "bootstrap-killswitch.json")
    foreign = area / "foreign-project"
    foreign.mkdir()
    env["COPILOT_PROJECT_DIR"] = env["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] = str(foreign)
    adapter = logger_registration_adapter(env, area)
    deadline = json.loads((payload / "hooks.json").read_text())["hooks"]["sessionStart"][0]["timeoutSec"]
    assert deadline == 15
    command = hook_command(payload, project, profile)

    def diagnostic():
        reports = []
        for path in area.rglob("*"):
            if path.is_file() and (path.suffix == ".log" or path.name in {"stdout", "stderr"}):
                print(f"Logger diagnostic read: {path.relative_to(area)}", file=sys.stderr, flush=True)
                reports.append(f"{path.relative_to(area)}:\n{path.read_text(errors='replace')[-16000:]}")
        return "\n".join(reports)

    def owned(label):
        print(f"Logger cold hook: entering {label}", file=sys.stderr, flush=True)
        return owned_hook(
            command, env=env, project=project, directory=area / label,
            timeout=deadline, diagnostic=diagnostic, acknowledgement_prefix=ACKNOWLEDGEMENT,
            keep_contained_job_until_settlement=(
                os.name == "nt" and env.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1"
            ),
        )

    def assert_admission(item, action):
        admission = json.loads(item.spec.source)
        assert admission["plugin"] == "agent-logger" and admission["installer_action"] == action
        assert Path(admission["payload"]) == payload
        assert Path(admission["runtime_root"]) == installation
        assert Path(admission["project"]) == project

    with owned("cold-first") as first:
        print("Logger cold hook: first owner acknowledged", file=sys.stderr, flush=True)
        observe(lambda: foundation_status(root=root) is not None, 90, diagnostic)
        print("Logger cold hook: foundation observed", file=sys.stderr, flush=True)
        operation = foundation_status(root=root).operation_id
        assert foundation_status(root=root).state == "running" and not first.exited(), diagnostic()
        first.close_caller()
        print("Logger cold hook: first caller closed", file=sys.stderr, flush=True)
        assert not first.exited(), diagnostic()
        with owned("cold-second") as second:
            print("Logger cold hook: second owner acknowledged", file=sys.stderr, flush=True)
            second.close_caller()
            last_progress = None

            def both_completed():
                nonlocal last_progress
                progress = foundation_status(root=root)
                snapshot = None if progress is None else (
                    progress.state, progress.exit_code, progress.error,
                )
                if snapshot != last_progress:
                    print(f"Logger cold foundation outcome: {snapshot}", file=sys.stderr, flush=True)
                    last_progress = snapshot
                return _cold_results_completed(root, (first, second), progress)

            print("Logger cold hook: waiting for both ordinary results", file=sys.stderr, flush=True)
            observe(both_completed, 850, diagnostic)
            print("Logger cold hook: both ordinary results observed", file=sys.stderr, flush=True)
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
    assert_no_periodic_worker(installation)
    assert not registration_effects(adapter)
    count = len(requests)

    # Establish the runtime through the already-qualified recipe, not a command publisher.
    prepare = native_command(root, payload, installation, project, source=payload / "libs/update-driver")
    prepare += ["-Wait"] if os.name == "nt" else ["--wait"]
    initial = subprocess.run(
        prepare, env=env, cwd=project, capture_output=True, text=True,
        timeout=850, **agent_procutil.no_window_kwargs(),
    )
    assert initial.returncode == 0, initial.stdout + initial.stderr + diagnostic()
    count += 1
    assert all(item["operation"] == "stop" for item in registration_effects(adapter))
    assert_no_periodic_worker(installation)

    def set_version(version):
        project_file = payload / "pyproject.toml"
        project_file.write_text(re.sub(
            r'(?m)^version\s*=\s*"[^"]+"', f'version = "{version}"', project_file.read_text(),
        ), encoding="utf-8")
        identity = json.loads((payload / "plugin.json").read_text())
        identity["version"] = version
        (payload / "plugin.json").write_text(json.dumps(identity), encoding="utf-8")

    set_version("99.0.0")
    with owned("drift-install") as caller:
        caller.close_caller()
        observe(lambda: receipt_completed_count(root, count + 1, caller.receipt_log), 850, diagnostic)
        drift = status(ordinary_receipt(caller.receipt_log), root=root)
        assert drift.exit_code == 0, diagnostic()
        assert_admission(drift, "install")
        observe(caller.exited, 60, diagnostic)
    count += 1
    assert_logger_registration(installation, adapter)
    selected = (installation / "current-version").read_text().strip()
    assert selected == "99.0.0"
    slot = installation / "versions" / selected
    completion = slot / ".install-complete.json"
    complete = completion.read_bytes()
    # Fresh construction rewrites deployment metadata. A subsequent reused
    # provision retains the lifecycle reservation whose canonical owner matters.
    reused = subprocess.run(
        prepare, env=env, cwd=project, capture_output=True, text=True,
        timeout=850, **agent_procutil.no_window_kwargs(),
    )
    assert reused.returncode == 0, reused.stdout + reused.stderr + diagnostic()
    count += 1
    manifest = json.loads((installation / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    if os.name == "nt":
        owner = manifest["commandLifecycle"]["owner"]
        admitted = json.loads(drift.spec.source)
        assert owner["sourceFingerprint"] == admitted["source_fingerprint"]
        assert not owner.get("unattributed")
        assert str(installation / ".install-stage") not in json.dumps(owner)
    healthy = subprocess.run(
        command, env=env, cwd=project, capture_output=True, text=True,
        timeout=deadline, **agent_procutil.no_window_kwargs(),
    )
    assert healthy.returncode == 0 and healthy.stdout == "{}" and not healthy.stderr
    assert len(status(root=root)) == count and completion.read_bytes() == complete

    effects = registration_effects(adapter)
    set_version("99.0.1")
    (payload / "src/agent_logger/__init__.py").write_text(
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
    assert not (installation / "reconcile.lock").exists()
    payload.rename(payload.with_name("removed-logger"))
    for name in (
        "agent-logger", "collate-session", "read-session-digest", "prepare-session-log",
        "ramp-up-session", "session-sync",
    ):
        primary = profile / ".local/bin" / (name + ".ps1" if os.name == "nt" else name)
        dispatch = (
            [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
             "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(primary), "--help"]
            if os.name == "nt" else [str(Path(env["PATH"]) / "bash"), str(primary), "--help"]
        )
        result = subprocess.run(
            dispatch, env={**env, "AGENT_LOGGER_NO_SELFPROVISION": "1"}, cwd=project,
            capture_output=True, text=True, timeout=30, **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode == 0 and "usage:" in result.stdout.lower(), result.stdout + result.stderr
    assert not (installation / "logs/session-sync.log").exists()
    assert not (profile / "task-effects").exists()
    assert len(status(root=root)) == count and completion.read_bytes() == complete
