"""Natural registered pre-tool decisions beyond the first statelessness guard."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import time
from typing import Any

import pytest
from agent_procutil import no_window_kwargs

from test_managed_session import (
    SCRIPTS, environment, fixture_checkout, fixture_linked_worktree, fixture_project, load,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows policy composition")


@contextmanager
def resident_policy_owner(
    scripts: Path, registry: Path, env: dict[str, str], event: dict[str, Any],
) -> Iterator[list[dict[str, Any]]]:
    from agent_worktrees.hook_ipc import HookIpcServer, HookUnavailable
    from agent_worktrees.native_tool_policy import dispatch_pre

    spec = importlib.util.spec_from_file_location("native_composition_client", scripts / "hook_client.py")
    assert spec and spec.loader
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    for filename in (
        "statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py",
        "pr_supersede_guard.py", "hook_pre_policy.py", "tool_call_forms.py",
    ):
        client._load_sibling(filename)
    assert client._managed_session_admitted(event)
    policy = SimpleNamespace(
        hook_client=client, _module=client._load_sibling,
        pre=lambda *_args: pytest.fail("native policy entered the legacy resident operation"),
    )
    seen: list[dict[str, Any]] = []

    def decide(kind: str, event: dict[str, Any], deadline: float) -> dict[str, Any]:
        assert kind == "preToolUse"
        if not client._managed_session_admitted(event):
            raise HookUnavailable
        seen.append(event)
        return dispatch_pre(policy, event, deadline)

    server = HookIpcServer(decide)
    lock = registry / "status-monitor.lock"
    server.start()
    try:
        discovery = server.rendezvous()
        if context := env.get("COPILOT_EXTENSIONS_CONTEXT"):
            discovery.update(
                marketplaceId=registry.parent.parent.name, installReceipt=context,
                pluginRoot=str(registry),
            )
        lock.write_text(json.dumps(discovery), encoding="utf-8")
        yield seen
    finally:
        server.close()
        lock.unlink(missing_ok=True)


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("cell", [False, True], ids=["legacy", "cell"])
@pytest.mark.parametrize("case", [
    "cross-modes", "anchor", "ask-then-deny", "warn-then-deny",
    "grant-revoke", "map-empty", "map-missing", "anchor-unavailable",
])
def test_registered_full_policy_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    shell: str, cell: bool, case: str,
) -> None:
    import test_registry_paths as cells
    from agent_worktrees import allow_edits, project_state, related

    case_started = time.monotonic()

    def progress(phase: str) -> None:
        with capsys.disabled():
            print(json.dumps({"natural_policy_progress": {
                "shell": shell, "cell": cell, "case": case, "phase": phase,
                "fixture_call_seconds": time.monotonic() - case_started,
            }}), flush=True)

    progress("fixture-start")

    executable = shutil.which(shell)
    if not executable:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"{shell} is required")
        pytest.skip(f"{shell} is unavailable")
    anchor, legacy_registry = fixture_project(tmp_path)
    workspace, _ = fixture_linked_worktree(tmp_path, anchor)
    delegated = fixture_checkout(tmp_path / "delegated")
    selected_anchor = fixture_checkout(tmp_path / "selected-anchor")
    alternate_anchor = fixture_checkout(tmp_path / "alternate-anchor")
    unregistered = fixture_checkout(tmp_path / "unregistered")
    with (legacy_registry / "repos.yaml").open("a", encoding="utf-8") as output:
        output.write('    remote: "https://github.com/example/caller.git"\n')
        if case not in {"map-empty", "map-missing"}:
            output.write(
                "  delegated:\n    class: reference\n"
                f"    windows: {json.dumps(str(delegated))}\n"
                "  platform-anchor:\n    class: worktree\n"
                f"    windows: {json.dumps(str(selected_anchor))}\n"
                f"    linux: {json.dumps(str(alternate_anchor))}\n"
            )
    if case in {"map-empty", "map-missing"}:
        with (legacy_registry / "projects.yaml").open("a", encoding="utf-8") as output:
            output.write("    base_repo: true\n")
    config = workspace / ".agent-worktrees"
    config.mkdir()
    (config / "config.yaml").write_text("stateless: false\n", encoding="utf-8")
    env = environment(tmp_path, workspace)
    for key in list(os.environ):
        if key.startswith(("COPILOT_", "AGENT_WORKTREES_", "WORKTREE_")) or key in {
            "CROSS_REPO_GUARD", "CROSS_REPO_GUARD_MODE",
            "ANCHOR_WRITE_GUARD", "ANCHOR_WRITE_GUARD_MODE",
            "PR_SUPERSEDE_GUARD", "PR_SUPERSEDE_GUARD_MODE",
            "PRETOOL_KILLSWITCH_STATE_FILE", "AGENT_RT_ROOT",
        }:
            monkeypatch.delenv(key, raising=False)
            env.pop(key, None)
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(key, str(tmp_path))
    payload = tmp_path / "payload"
    if cell:
        native_run = subprocess.run
        monkeypatch.setattr(cells, "subprocess", SimpleNamespace(
            run=lambda *args, **kwargs: native_run(
                *args, **(kwargs | no_window_kwargs() | {"timeout": 10}),
            ),
        ))
        payload = cells._payload(tmp_path, "selected")
    scripts = payload / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for path in SCRIPTS.iterdir():
        if path.suffix == ".py" or path.name in {
            "pre-tool-hook.ps1", "post-tool-hook.ps1", "hook-facts.ps1",
            "managed-session.ps1", "hook-read-policy.json",
        }:
            shutil.copyfile(path, scripts / path.name)
    shutil.copytree(SCRIPTS / "installation-context", scripts / "installation-context", dirs_exist_ok=True)
    progress("payload-copied")
    registry = legacy_registry
    if cell:
        if case == "grant-revoke":
            allow_edits.grant("delegated", "synthetic unselected-registry control", 10)
        context, registry = cells._stamp(
            tmp_path / "durable", payload, marketplace="selected", repository="example/selected",
        )
        for name in ("projects.yaml", "repos.yaml"):
            (registry / name).write_bytes((legacy_registry / name).read_bytes())
        cells._select(monkeypatch, context, payload)
        project_state.ensure_project_state("example")
        env.update(COPILOT_EXTENSIONS_CONTEXT=str(context), AGENT_WORKTREES_PAYLOAD_ROOT=str(payload))
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(payload))
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    related.write_related(workspace, related.RelatedConfig(
        related={} if case in {"map-empty", "map-missing"} else {
            "delegated": related.RelatedEntry(name="delegated", delegate="agent-bridge"),
        },
    ))
    reader = load("guarded_anchor_map")
    map_path = reader.map_path(workspace, registry)
    facts = reader.read_map(workspace, registry)
    assert facts.status == "ready"
    progress("original-writer-published-map")
    if case in {"map-empty", "map-missing"}:
        assert facts.roots == [] and facts.postures == []
    if case == "map-missing":
        map_path.unlink()
    target = (
        unregistered if case in {"map-empty", "map-missing"} else
        alternate_anchor if case in {"anchor", "anchor-unavailable"} else delegated
    ) / "not-created.py"
    event: dict[str, Any] = {
        "sessionId": "natural-policy", "cwd": str(workspace), "toolName": "edit",
        "toolArgs": {"path": str(target)},
    }
    if case in {"ask-then-deny", "warn-then-deny"}:
        event.update(toolName="multi_edit", toolArgs={"files": [
            {"path": str(target)}, {"path": str(alternate_anchor / "not-created.py")},
        ]})
        mode = case.split("-", 1)[0]
        monkeypatch.setenv("CROSS_REPO_GUARD_MODE", mode)
        env["CROSS_REPO_GUARD_MODE"] = mode
    assert load("statelessness_guard").decide(event, env=env, home=tmp_path) is None
    if case in {"anchor", "anchor-unavailable", "ask-then-deny", "warn-then-deny"}:
        assert load("anchor_write_guard").decide(event, env=env, home=tmp_path)["permissionDecision"] == "deny"
        cross = load("cross_repo_guard").decide(event, env=env, home=tmp_path)
        assert cross is None if case.startswith("anchor") else (
            cross.get("permissionDecision") == "ask" if case == "ask-then-deny"
            else "cross-repo-guard:" in cross["additionalContext"]
        )
    session = tmp_path / ".copilot" / "session-state" / "natural-policy"
    session.mkdir(parents=True)
    assert load("managed_session").admitted(event, home=tmp_path)
    (scripts / "resolve-runtime.ps1").write_text("throw 'runtime lookup is forbidden'\n", encoding="utf-8")
    with (scripts / "managed-session.ps1").open("a", encoding="utf-8") as output:
        output.write("\nfunction Get-ManagedWorktreesPython { throw 'interpreter lookup is forbidden' }\n")
    progress("prepared-membership-ready")
    originals = {name: hashlib.sha256((scripts / name).read_bytes()).hexdigest() for name in (
        "pre-tool-hook.ps1", "post-tool-hook.ps1", "hook-facts.ps1",
        "managed-session.ps1", "hook-read-policy.json", "resolve-runtime.ps1",
        "hook_policy_process.py", "hook_client.py", "hook_pre_policy.py",
        "statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py", "pr_supersede_guard.py",
    )}
    native = scripts / "post-tool-hook.ps1"
    source = native.read_text(encoding="utf-8")
    declaration = "function Invoke-SelectedResidentToolHook {"
    assert source.count(declaration) == 1
    source = source.replace(declaration, """function Invoke-SelectedResidentToolHook {
    param([string]$Kind, [string]$Raw, [bool]$PreparedMembership = $false)
    [Console]::Error.WriteLine('NATURAL_POLICY_START')
    try { return Invoke-FixtureOriginalResidentPolicy @PSBoundParameters }
    finally { [Console]::Error.WriteLine('NATURAL_POLICY_COMPLETE') }
}
function Invoke-FixtureOriginalResidentPolicy {""", 1)
    native.write_text(source, encoding="utf-8")
    if case == "anchor-unavailable":
        (scripts / "anchor_write_guard.py").unlink()
    entry = json.loads((SCRIPTS.parent / "hooks.json").read_text(encoding="utf-8"))["hooks"]["preToolUse"][0]["powershell"]
    contract = (scripts / "hook-read-policy.json").read_bytes()
    observations: list[dict[str, Any]] = []
    executable_sha256 = hashlib.sha256(Path(executable).read_bytes()).hexdigest()

    def oracle_module(filename: str) -> Any:
        return load(filename.removesuffix(".py"))

    def invoke(variant: str) -> dict[str, Any]:
        progress("oracle-start:" + variant)
        expected = load("hook_pre_policy").decide(
            event, home=tmp_path, load_sibling=oracle_module,
            merge_decisions=load("hook_client")._merge_pre_decisions,
            budget_seconds=25, require_policy=True,
        )
        capsys.readouterr()
        progress("registered-entry-start:" + variant)
        prior = len(seen)
        started = time.monotonic()
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::Error.WriteLine('NATURAL_SHELL_BUILD ' + $PSVersionTable.PSVersion); " + entry],
            input=json.dumps(event), capture_output=True, text=True, encoding="utf-8",
            cwd=workspace, env=env, timeout=10, **no_window_kwargs(),
        )
        elapsed = time.monotonic() - started
        progress("registered-entry-complete:" + variant)
        assert result.returncode == 0, result.stderr
        assert len(seen) == prior + 1
        phases = [line for line in result.stderr.splitlines() if line.startswith("NATURAL_POLICY_")]
        assert phases == ["NATURAL_POLICY_START", "NATURAL_POLICY_COMPLETE"], result.stderr
        builds = [line.removeprefix("NATURAL_SHELL_BUILD ") for line in result.stderr.splitlines()
                  if line.startswith("NATURAL_SHELL_BUILD ")]
        assert len(builds) == 1
        actual: dict[str, Any] = json.loads(result.stdout)
        if case == "anchor-unavailable":
            assert actual.get("permissionDecision") is None
            assert "anchor_write_guard.py unavailable" in actual["additionalContext"]
        else:
            assert actual == expected, (actual, expected, result.stderr)
        if case == "map-missing":
            assert not map_path.exists() and "missing guard map" in capsys.readouterr().err
        elif case == "map-empty":
            assert "guard map" not in capsys.readouterr().err
        assert (scripts / "hook-read-policy.json").read_bytes() == contract
        assert not target.exists() and not (alternate_anchor / "not-created.py").exists()
        observations.append({"variant": variant, "outer_seconds": elapsed,
                             "shell_build": builds[0], "decision": actual.get("permissionDecision"),
                             "selected_resident": True, "resident_completed": True})
        return actual

    with resident_policy_owner(scripts, registry, env, event) as seen:
        if case == "cross-modes":
            for mode in ("deny", "ask", "warn"):
                monkeypatch.setenv("CROSS_REPO_GUARD_MODE", mode)
                env["CROSS_REPO_GUARD_MODE"] = mode
                result = invoke(mode)
                assert result.get("permissionDecision") == (None if mode == "warn" else mode)
        elif case == "grant-revoke":
            assert invoke("before-selected-grant")["permissionDecision"] == "deny"
            allow_edits.grant("delegated", "synthetic native policy qualification", 10)
            assert invoke("selected-grant").get("permissionDecision") is None
            assert allow_edits.revoke("delegated")
            assert invoke("revoked-grant")["permissionDecision"] == "deny"
        else:
            result = invoke(case)
            assert result.get("permissionDecision") == (
                None if case in {"map-empty", "map-missing", "anchor-unavailable"} else "deny"
            )
            if case == "warn-then-deny":
                assert "cross-repo-guard:" in result["additionalContext"]
    with capsys.disabled():
        print(json.dumps({"natural_registered_policy": {
            "shell": executable, "executable_sha256": executable_sha256,
            "cell": cell, "case": case, "source_sha256": originals,
            "fixture_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "native_entry_fixture_sha256": hashlib.sha256(native.read_bytes()).hexdigest(),
            "fault": "copied anchor guard unavailable" if case == "anchor-unavailable" else None,
            "registered_command_sha256": hashlib.sha256(entry.encode("utf-8")).hexdigest(),
            "observations": observations,
            "fixture_call_seconds": time.monotonic() - case_started,
            "limits": "Synthetic native consumer; selected resident and original policy, passive entry markers. Not a distribution, installed/CLI/ACP/model, composed-latency or throughput pass.",
        }}), flush=True)
