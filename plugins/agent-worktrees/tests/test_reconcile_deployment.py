"""Plugin reconciliation respects installation-owned publication policy."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import reconcile, reconcile_deployment
from test_reconcile import env as env


@pytest.fixture
def deployment(env: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("HOME", str(env.home))
    monkeypatch.setenv("USERPROFILE", str(env.home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: env.home))
    native_run = subprocess.run

    def isolated_probe(command: list[str], **kwargs: Any) -> Any:
        if os.name == "nt" or not any(
            str(argument).endswith("installation_context.py") for argument in command
        ):
            return native_run(command, **kwargs)
        helper_index = next(
            index for index, argument in enumerate(command)
            if str(argument).endswith("installation_context.py")
        )
        bootstrap = (
            "import pwd,runpy,sys\n"
            "home=sys.argv[1]\n"
            "pwd.getpwuid=lambda uid: pwd.struct_passwd("
            "('fixture','x',uid,uid,'fixture',home,'/bin/sh'))\n"
            "sys.argv=sys.argv[2:]\n"
            "runpy.run_path(sys.argv[0],run_name='__main__')\n"
        )
        return native_run(
            [command[0], "-I", "-c", bootstrap, str(env.home), *command[helper_index:]],
            **kwargs,
        )

    monkeypatch.setattr(reconcile.subprocess, "run", isolated_probe)
    env.write_settings({f"agent-index@{reconcile.MARKETPLACE}": True})
    env.payload = env.install_payload("agent-index", "2.0.0", scope="universal")
    env.deploy_runtime("agent-index", "1.0.0")
    env.root = env.home / ".agent-index"
    env.context = None

    def policy(state: str) -> None:
        helper = reconcile._installation_context_helper("agent-index", env.payload)
        resolution = reconcile._run_installation_mode_helper(
            "agent-index", env.payload, helper, env.home / ".agent-index",
            context=env.context,
        )
        owner = {
            "sourceFingerprint": "sha256:" + hashlib.sha256(
                reconcile._CORE_SOURCE_RECORD.encode("utf-8"),
            ).hexdigest(),
            "pluginId": "agent-index", "mode": resolution["actualMode"],
            "runtimeRoot": str(env.root), "environment": resolution["environment"],
        }
        if state == "foreign":
            owner["sourceFingerprint"] = "sha256:" + "0" * 64
        record = {
            "schema": "copilot-extensions.runtime-deployment",
            "version": 2 if state == "unsupported" else 1,
            "owner": owner, "managed": state != "standalone",
        }
        (env.root / "runtime-deployment.json").write_text(
            "invalid json" if state == "invalid" else json.dumps(record),
            encoding="utf-8",
        )

    env.policy = policy
    return env


def _snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes())
        for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize(
    ("state", "allowed", "reason"),
    [
        ("missing", True, None), ("standalone", True, None),
        ("managed", False, "deployment-managed"),
        ("invalid", False, "deployment-policy-invalid"),
        ("unsupported", False, "deployment-policy-version-unsupported"),
        ("foreign", False, "deployment-policy-invalid"),
    ],
)
def test_plan_uses_real_deployment_probe_without_publication(
    deployment: Any, state: str, allowed: bool, reason: str | None,
) -> None:
    if state != "missing":
        deployment.policy(state)
    before = _snapshot(deployment.home)
    plan = reconcile.build_plan(
        deployment.repo, machine="test", cache={}, save=False,
        include_payload_refresh=True,
    )
    assert _snapshot(deployment.home) == before
    phases = [step["phase"] for step in plan.get("updates", [])]
    assert phases == (["payload", "runtime"] if allowed else ["payload"])
    if reason is not None:
        diagnostic = next(d for d in plan["diagnostics"] if d["reason"] == reason)
        assert diagnostic["message"]
        assert diagnostic["service"] == "agent-index"


def test_managed_target_does_not_disable_other_runtime(deployment: Any) -> None:
    deployment.policy("managed")
    deployment.write_settings({
        f"agent-index@{reconcile.MARKETPLACE}": True,
        f"agent-bridge@{reconcile.MARKETPLACE}": True,
    })
    deployment.install_payload("agent-bridge", "2.0.0", scope="universal")
    plan = reconcile.build_plan(deployment.repo, machine="test", cache={}, save=False)
    assert [(step["service"], step["phase"]) for step in plan["updates"]] == [
        ("agent-bridge", "runtime"),
    ]
    assert plan["diagnostics"][0]["reason"] == "deployment-managed"


@pytest.mark.parametrize("custom", [False, True])
def test_execution_rechecks_policy_added_after_plan(
    deployment: Any, monkeypatch: pytest.MonkeyPatch, custom: bool,
) -> None:
    plan = reconcile.build_plan(deployment.repo, machine="test", cache={}, save=False)
    deployment.policy("managed")
    before = _snapshot(deployment.home)
    monkeypatch.setattr(reconcile, "build_plan", lambda *args, **kwargs: plan)
    calls: list[Any] = []
    monkeypatch.setattr(
        reconcile, "_run_plan_step", lambda *args: calls.append(args) or 0,
    )
    logs: list[str] = []
    summary = reconcile.apply_plan(
        deployment.repo, machine="test", passes=1, log=logs.append,
        runner=(lambda argv: calls.append(argv) or 0) if custom else None,
    )
    assert calls == []
    assert _snapshot(deployment.home) == before
    assert summary["executed"][0]["returncode"] == 3
    assert summary["executed"][0]["diagnostic"]["reason"] == "deployment-managed"
    assert any("explicit installation management" in line for line in logs)


def test_execution_rejects_changed_root(deployment: Any) -> None:
    plan = reconcile.build_plan(deployment.repo, machine="test", cache={}, save=False)
    step = plan["updates"][0]
    step["runtime_root"] = str(deployment.home / "another-runtime")
    diagnostic = reconcile_deployment.execution_diagnostic(step)
    assert diagnostic is not None
    assert "runtime_root changed" in diagnostic["message"]


@pytest.mark.parametrize("phase", ["payload", "uninstall"])
def test_nonpublication_steps_do_not_probe_runtime(
    monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    monkeypatch.setattr(
        reconcile, "core_installed_payload_dir", lambda name: pytest.fail(name),
    )
    assert reconcile_deployment.execution_diagnostic({
        "phase": phase, "service": "agent-worktrees",
    }) is None


def test_old_helper_cannot_ignore_present_policy(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment.policy("managed")
    monkeypatch.setattr(reconcile, "_installation_context_helper", lambda *args: None)
    diagnostic = reconcile_deployment.publication_diagnostic(
        "agent-index", deployment.payload, deployment.root, context=None,
    )
    assert diagnostic is not None
    assert diagnostic["reason"] == "deployment-policy-unavailable"
    assert "update the plugin adapter explicitly" in diagnostic["message"]


def test_environment_hint_is_not_publication_authority(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment.policy("managed")
    monkeypatch.setenv("COPILOT_EXTENSIONS_MANAGED_UPDATE", "1")
    diagnostic = reconcile_deployment.publication_diagnostic(
        "agent-index", deployment.payload, deployment.root, context=None,
    )
    assert diagnostic is not None and diagnostic["reason"] == "deployment-managed"


@pytest.mark.parametrize("state", ["standalone", "managed", "invalid"])
def test_namespaced_policy_uses_actual_root_not_legacy(
    deployment: Any, state: str,
) -> None:
    helper = reconcile._installation_context_helper("agent-index", deployment.payload)
    current = reconcile._run_installation_mode_helper(
        "agent-index", deployment.payload, helper, deployment.root, context=None,
    )
    context, root = deployment.select_context(
        "agent-index", deployment.payload, "2.0.0", "1.0.0",
    )
    receipt = json.loads(context.read_text(encoding="utf-8"))
    namespace = json.loads(Path(receipt["namespaceReceipt"]).read_text(encoding="utf-8"))
    (root / "installation-activation.json").write_text(json.dumps({
        "schema": "copilot-extensions.installation-activation", "version": 1,
        "marketplaceId": reconcile.CORE_MARKETPLACE_ID, "pluginId": "agent-index",
        "mode": "namespaced", "state": "active",
        "environment": current["environment"], "context": str(context),
        "namespaceGeneration": namespace["generation"],
        "installGeneration": receipt["generation"], "generation": 1,
        "legacy": {
            "disposition": "absent",
            "probe": {"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
        },
        "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")
    (deployment.home / ".copilot-extensions" / "installation-mode.json").write_text(
        json.dumps({
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": True},
        }), encoding="utf-8",
    )
    # The legacy policy is deliberately invalid. Only the active cell owns
    # this runtime decision; probing the conventional root would refuse it.
    deployment.policy("invalid")
    deployment.root, deployment.context = root, context
    deployment.policy(state)
    before = _snapshot(deployment.home)
    plan = reconcile.build_plan(deployment.repo, machine="test", cache={}, save=False)
    assert _snapshot(deployment.home) == before
    if state == "standalone":
        # Publication permission does not invent an unsupported cell installer.
        assert plan["action"] == "continue"
        assert plan["diagnostics"][0]["reason"] == "installation-context-unsupported"
        assert reconcile_deployment.execution_diagnostic({
            "phase": "runtime", "service": "agent-index",
            "runtime_root": str(root), "payload_root": str(deployment.payload),
            "environment": {"COPILOT_EXTENSIONS_CONTEXT": str(context)},
        }) is None
    else:
        assert plan["action"] == "continue"
        assert plan["diagnostics"][0]["reason"] == (
            "deployment-managed" if state == "managed" else "deployment-policy-invalid"
        )


def test_managed_healthy_runtime_is_not_republished(deployment: Any) -> None:
    deployment.policy("managed")
    deployment.deploy_runtime("agent-index", "2.0.0")
    before = _snapshot(deployment.home)
    plan = reconcile.build_plan(deployment.repo, machine="test", cache={}, save=False)
    assert plan["action"] == "continue"
    assert _snapshot(deployment.home) == before


def test_old_probe_rejects_unknown_publication_action(
    deployment: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment.policy("managed")
    monkeypatch.setattr(
        reconcile.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 2, "", "invalid choice: probe-plugin-publication",
        ),
    )
    diagnostic = reconcile_deployment.publication_diagnostic(
        "agent-index", deployment.payload, deployment.root, context=None,
    )
    assert diagnostic is not None and diagnostic["reason"] == "deployment-policy-unavailable"
    assert "invalid choice" in diagnostic["message"]


@pytest.mark.parametrize("state", ["missing", "standalone", "managed"])
def test_aliased_home_retains_installation_policy_scope(
    deployment: Any, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    if state != "missing":
        deployment.policy(state)
    physical_home = deployment.home
    alias = physical_home.parent / "home-alias"
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(physical_home)],
            capture_output=True, check=True, timeout=10, **no_window_kwargs(),
        )
    else:
        alias.symlink_to(physical_home, target_is_directory=True)
    monkeypatch.setattr(reconcile, "_home", lambda: alias)
    monkeypatch.setenv("HOME", str(alias))
    monkeypatch.setenv("USERPROFILE", str(alias))
    before = _snapshot(physical_home)
    plan = reconcile.build_plan(deployment.repo, machine="test", cache={}, save=False)
    assert _snapshot(physical_home) == before
    if state == "managed":
        assert plan["action"] == "continue"
        assert plan["diagnostics"][0]["reason"] == "deployment-managed"
    else:
        assert plan["updates"][0]["runtime_root"] == str(alias / ".agent-index")
        assert reconcile_deployment.execution_diagnostic(plan["updates"][0]) is None
    assert _snapshot(physical_home) == before
    # The same normalization applies to optional context and policy paths;
    # missing, relative and wrong-target values still do not match.
    assert reconcile_deployment._matching_path(str(physical_home), alias)
    assert reconcile_deployment._matching_path(None, None)
    for value in (None, "", "relative", 1, str(alias.parent / "foreign")):
        assert not reconcile_deployment._matching_path(value, alias)
    assert not reconcile_deployment._matching_path(str(physical_home), None)
