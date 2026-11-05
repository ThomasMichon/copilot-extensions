"""Actual MCP hook admission and materialized consumers, not production Job proof."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil.daemon_instances import daemon_test_lifecycle, inspect_instances
from update_driver import foundation_status, status
from update_driver.foundation_inputs import PACKAGES
from update_driver.foundation_inputs import inspect as inspect_foundation

from test_first_use import primitive
from test_first_use_bridge import (
    native_recipe_runner as native_recipe_runner,
    set_payload_version,
)
from test_first_use_codespaces_bootstrap import hook_command
from test_first_use_genesis import observe
from test_first_use_mcp import REPOSITORY, owning_mcp as owning_mcp
from test_first_use_mcp_materialized import (
    assert_installed_mcp, materialized_mcp as materialized_mcp,
)
from test_first_use_pull_requests_command import files
from test_first_use_vault import cold_environment, native_genesis
from test_first_use_vault_bootstrap import ordinary_receipt


pytestmark = [
    pytest.mark.skipif(
        os.name != "nt" and sys.platform != "linux", reason="native Windows/Linux MCP hook",
    ),
    pytest.mark.skipif(
        os.environ.get("UPDATE_DRIVER_MCP_BOOTSTRAP_ACCEPTANCE") != "1",
        reason="explicit MCP bootstrap portfolio",
    ),
]
ACKNOWLEDGEMENT = "[agent-mcp] driver launch acknowledgement: "


@pytest.fixture
def mcp_instance_lifecycle(area, native_recipe_runner, record_property):
    registry = area / "mcp-daemon-instances"
    outcomes = []
    with daemon_test_lifecycle(
        owner="mcp-bootstrap-acceptance", registry_root=registry, cleanup_results=outcomes,
    ) as environment:
        yield environment, registry
    record_property("mcp-daemon-cleanup", json.dumps([vars(item) for item in outcomes]))
    remaining = inspect_instances(registry)
    if remaining:
        from agent_procutil._daemon_instance_identity import birth_identity

        observations = []
        for item in remaining:
            observation = {"record": item}
            try:
                observation["observed_birth"] = birth_identity(
                    int(item["pid"]), include_exited=True,
                )
            except (OSError, ValueError, KeyError) as error:
                observation["observation_error"] = type(error).__name__
            observations.append(observation)
        record_property("mcp-daemon-unsettled", json.dumps(observations))
    assert outcomes and all(item.status == "settled" for item in outcomes), outcomes
    assert remaining == []


@pytest.fixture
def admission_payload(owning_mcp):
    payload, installation, project = owning_mcp
    for name in ("hooks.json", "scripts/bootstrap-check.ps1", "scripts/bootstrap-check.sh"):
        shutil.copyfile(REPOSITORY / "plugins/agent-mcp" / name, payload / name)
    shutil.copytree(
        REPOSITORY / "libs/update-driver", payload / "libs/update-driver",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "tests"),
    )
    return payload, installation, project


@pytest.mark.parametrize("phase", ["cold", "drift"])
@pytest.mark.parametrize(("reason", "message"), [
    ("managed", "publication"),
    ("malformed", "publication"),
    ("no-self", "bootstrap deferred"),
    ("pinned", "bootstrap deferred"),
    ("nested", "Nested bootstrap"),
    ("cell", "default legacy root"),
    ("missing-driver", "lifecycle contract"),
    ("old-driver", "lifecycle contract"),
    ("noninstalled", "canonical installed payload"),
    ("declaration", "fixed first-use provision recipe"),
])
def test_actual_hook_refuses_before_acquisition_or_writes(
    admission_payload, monkeypatch, area, phase, reason, message,
):
    payload, installation, project = admission_payload
    if reason == "noninstalled":
        payload = Path(shutil.copytree(payload, area / "copied/agent-mcp"))
    if phase == "drift":
        installation.mkdir()
        (installation / "deploy-manifest.json").write_text(json.dumps({
            "source": {"version": "0.0.0", "path": str(payload)},
        }), encoding="utf-8")
        (installation / "reconcile.lock").write_text("1", encoding="utf-8")
    if reason in {"managed", "malformed"}:
        api = primitive(payload)
        arguments = dict(payload_root=payload, plugin_id="agent-mcp",
                         legacy_root=installation, project_root=project)
        resolution = api.resolve_installation_mode(**arguments)
        installation.mkdir(exist_ok=True)
        policy = {
            "schema": api.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": api._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    elif reason in {"no-self", "pinned", "nested", "cell"}:
        monkeypatch.setenv({
            "no-self": "AGENT_MCP_NO_SELFPROVISION", "pinned": "AGENT_MCP_PINNED_CONTEXT",
            "nested": "UPDATE_DRIVER_WITNESS", "cell": "AGENT_MCP_INSTALLATION_ID",
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
        value = json.loads(declaration.read_text())
        value["installer"] = "install"
        declaration.write_text(json.dumps(value), encoding="utf-8")
    before, source = files(installation), files(payload)
    root = installation.parent / ".copilot-extensions/update-driver"
    output, errors = area / "hook.stdout", area / "hook.stderr"
    with output.open("wb") as stdout, errors.open("wb") as stderr:
        try:
            result = subprocess.run(
                hook_command(payload, project, installation.parent), env=dict(os.environ), cwd=project,
                stdout=stdout, stderr=stderr, timeout=15, **no_window_kwargs(),
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"Hook exceeded 15 seconds: {errors.read_text(errors='replace')}")
    detail = errors.read_text(encoding="utf-8-sig")
    assert result.returncode == (0 if reason in {"no-self", "pinned"} else 3), detail
    assert output.read_text(encoding="utf-8-sig") == "{}" and message.lower() in detail.lower(), detail
    assert not root.exists() and files(installation) == before and files(payload) == source


def test_materialized_hook_packages_complete_canonical_foundation(materialized_mcp):
    payload, _, _ = materialized_mcp
    assert {path.name for path in inspect_foundation(payload / "libs/update-driver").packages} == set(PACKAGES)
    for suffix in ("ps1", "sh"):
        assert (payload / "scripts" / f"bootstrap-check.{suffix}").read_bytes() == (
            REPOSITORY / "plugins/agent-mcp/scripts" / f"bootstrap-check.{suffix}"
        ).read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="Windows setup Python prerequisite")
def test_actual_hook_missing_python_refuses_without_launch(materialized_mcp, area):
    payload, installation, project = materialized_mcp
    environment = cold_environment(installation, project, area)
    root = installation.parent / ".copilot-extensions/update-driver"
    result = subprocess.run(
        hook_command(payload, project, installation.parent), env=environment, cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 3 and result.stdout == "{}", result.stderr
    assert "Python 3.10+ must be available as python on PATH" in result.stderr, result.stderr
    assert ACKNOWLEDGEMENT not in result.stderr
    assert not installation.exists() and foundation_status(root=root) is None and not status(root=root)


@pytest.mark.parametrize("reason", ["namespaced", "killswitch"])
def test_actual_hook_keeps_nonpublication_noops(admission_payload, monkeypatch, reason):
    payload, installation, project = admission_payload
    environment = dict(os.environ)
    if reason == "namespaced":
        environment["COPILOT_EXTENSIONS_CONTEXT"] = "{}"
    else:
        switch = installation.parent / "killswitch.json"
        switch.write_text('{"active":true,"reason":"fixture hold"}', encoding="utf-8")
        environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(switch)
    result = subprocess.run(
        hook_command(payload, project, installation.parent), env=environment, cwd=project,
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0 and result.stdout == "{}", result.stderr
    assert not installation.exists()
    assert not (installation.parent / ".copilot-extensions/update-driver").exists()


def test_bootstrap_deferral_keeps_independent_guidance(materialized_mcp):
    payload, installation, project = materialized_mcp
    environment = {**os.environ, "COPILOT_PLUGIN_ROOT": str(payload), "AGENT_MCP_NO_SELFPROVISION": "1"}
    for name, timeout, request in (
        ("bootstrap-check", 15, None),
        ("write-session-guidance", 45,
         json.dumps({"sessionId": "mcp-bootstrap-independent", "cwd": str(project)})),
    ):
        result = subprocess.run(
            hook_command(payload, project, installation.parent, name), env=environment, cwd=project,
            input=request, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
            **no_window_kwargs(),
        )
        assert result.returncode == 0 and result.stdout == "{}", result.stdout + result.stderr
    guidance = installation.parent / (
        ".copilot/session-state/mcp-bootstrap-independent/instructions/agent-mcp/session-guidance.instructions.md"
    )
    text = guidance.read_text()
    assert "# Agent MCP session guidance" in text
    catalogs = [json.loads(block.split("\n```", 1)[0]) for block in text.split("```json\n")[1:]]
    catalog = next(item for item in catalogs
                   if item.get("schema") == "copilot-extensions.session-command-catalog")
    assert catalog["commands"][0]["argv"] == [str(payload / "bin" / (
        "agent-mcp.cmd" if os.name == "nt" else "agent-mcp"
    ))]
    assert not installation.exists()
    assert not (installation.parent / ".copilot-extensions/update-driver").exists()


@native_genesis
def test_actual_hook_cold_drift_repeat_failure_and_installed_consumers(
    materialized_mcp, area, native_recipe_runner, native_window_observation, rename_source, capsys,
    mcp_instance_lifecycle, record_property,
):
    payload, installation, project = materialized_mcp
    root = installation.parent / ".copilot-extensions/update-driver"
    environment = cold_environment(installation, project, area, python_prerequisite=True)
    instance_environment, registry = mcp_instance_lifecycle
    environment.update(instance_environment)
    environment.pop("AGENT_VAULT_HOME")
    environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(installation.parent / "killswitch.json")
    sentinel = installation / "bridges/fixture-preserved.yaml"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text(json.dumps({
        "server": {"type": "stdio", "command": ["fixture-server-not-started"]},
        "auth": {"kind": "none"},
    }), encoding="utf-8")
    preserved = sentinel.read_bytes()
    command = hook_command(payload, project, installation.parent)

    def diagnostic():
        return "\n".join(f"{path.name}:\n{path.read_text(errors='replace')[-16000:]}"
                         for path in area.rglob("*.log") if path.is_file())

    def stage(message):
        with capsys.disabled():
            print(f"MCP bootstrap qualification: {message}", flush=True)

    def submit():
        started = time.monotonic()
        caller, log = native_recipe_runner.start(
            command, env=environment, cwd=project, root=root, diagnostic=diagnostic,
            stdout=subprocess.PIPE, acknowledgement_prefix=ACKNOWLEDGEMENT,
        )
        output, _ = caller.communicate(timeout=15)
        assert caller.returncode == 0 and output == b"{}", log.read_text(errors="replace") + diagnostic()
        elapsed = time.monotonic() - started
        stage(f"hook returned after {elapsed:.3f}s")
        assert elapsed <= 15, f"Hook took {elapsed:.3f}s, deadline 15s\n" + diagnostic()
        text = log.read_text(errors="replace")
        frame = next(line.partition(ACKNOWLEDGEMENT)[2] for line in text.splitlines()
                     if line.startswith(ACKNOWLEDGEMENT))
        receipt_log = Path(json.loads(frame)["log"]) if os.name == "nt" else Path(frame.partition(" log:")[2])
        return receipt_log

    def completed(receipt_log):
        progress = foundation_status(root=root)
        if progress is not None:
            assert progress.state not in {"failed", "interrupted"}, diagnostic()
        receipt = ordinary_receipt(receipt_log)
        if receipt is None:
            if native_recipe_runner.owners_finished():
                receipt = ordinary_receipt(receipt_log)
                assert receipt is not None, "Owner exited without an ordinary receipt\n" + diagnostic()
            else:
                return False
        outcome = status(receipt, root=root)
        assert outcome.state != "interrupted", diagnostic()
        return outcome.state == "completed"

    def result(receipt_log, action, success=True):
        observe(lambda: completed(receipt_log), 900, diagnostic)
        outcome = status(ordinary_receipt(receipt_log), root=root)
        assert (outcome.exit_code == 0) is success, diagnostic()
        admission = json.loads(outcome.spec.source)
        assert admission["plugin"] == "agent-mcp" and admission.get("installer_action", "provision") == action
        assert Path(admission["payload"]) == payload and Path(admission["runtime_root"]) == installation
        assert Path(admission["project"]) == project
        return outcome

    # Caller processes exit normally; outer runner containment remains intact.
    # Production caller-Job/group destruction is a separate acceptance boundary.
    stage("cold stamp")
    result(submit(), "stamp")
    assert not (installation / "current-version").exists()
    history = installation / "reconcile.lock"
    history.write_text("1", encoding="utf-8")
    (installation / "deploy-manifest.json").write_text(json.dumps({
        "source": {"version": "0.0.0", "path": str(payload)},
    }), encoding="utf-8")
    stage("drift provision admissions")
    first, second = submit(), submit()
    first_result, second_result = result(first, "provision"), result(second, "provision")
    assert first_result.spec == second_result.spec and first_result.runner == second_result.runner, diagnostic()
    if first_result.id != second_result.id:
        earlier, later = sorted((first_result, second_result), key=lambda item: item.created_at)
        assert earlier.finished_at is not None and earlier.finished_at <= later.created_at, diagnostic()
    record_property("mcp-hook-coalescing", json.dumps({
        "same_request": first_result.id == second_result.id,
        "contract": "equivalent overlapping requests must share an identity",
        "requests": [
            {"id": item.id, "created_at": item.created_at, "finished_at": item.finished_at}
            for item in (first_result, second_result)
        ],
    }))
    stage("installed consumers and healthy repeat")
    slot = assert_installed_mcp(installation, environment, project)
    selection = (installation / "current-version").read_bytes()
    healthy = files(slot)
    count = len(status(root=root))
    for _ in range(2):
        repeat = subprocess.run(
            command, env=environment, cwd=project, capture_output=True, text=True,
            timeout=15, **no_window_kwargs(),
        )
        assert repeat.returncode == 0 and repeat.stdout == "{}" and not repeat.stderr, repeat.stderr
        assert len(status(root=root)) == count and files(slot) == healthy
    stage("incoming failure preservation")
    set_payload_version(payload, "999.0.0")
    (payload / "src/agent_mcp/__init__.py").write_text(
        "raise RuntimeError('fixture incoming module health failure')\n", encoding="utf-8",
    )
    result(submit(), "provision", success=False)
    assert (installation / "current-version").read_bytes() == selection and files(slot) == healthy
    assert history.read_text() == "1" and sentinel.read_bytes() == preserved
    stage("source-independent consumers and owner settlement")
    unavailable = payload.with_name("unavailable-mcp")
    rename_source(payload, unavailable)
    try:
        assert_installed_mcp(installation, environment, project)
        observe(native_recipe_runner.owners_finished, 900, diagnostic)
        records = inspect_instances(registry)
        record_property("mcp-source-unavailable-instances", json.dumps(records))
        if os.name == "nt":
            assert any(
                Path(item["runtime_root"]).is_relative_to(payload)
                and not Path(item["runtime_root"]).exists()
                for item in records
            )
    finally:
        # Restore the original runtime identity before canonical lifecycle cleanup.
        rename_source(unavailable, payload)
