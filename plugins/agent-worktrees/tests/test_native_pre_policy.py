"""Natural registered pre-tool decisions beyond the first statelessness guard."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import time
from typing import Any

import pytest
from agent_procutil import no_window_kwargs
from native_rpc_fixture import (
    BOOTSTRAP_PHASES, _BUFFERED_PHASES, diagnostic_policy_bootstrap, diagnostic_policy_transport,
    retained_rpc_command, retained_rpc_phases, rpc_clock_phase,
)

from test_managed_session import (
    SCRIPTS, environment, fixture_checkout, fixture_linked_worktree, fixture_project, load,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows policy composition")

NATURAL_POLICY_WRAPPER = """function Invoke-SelectedResidentToolHook {
    param([string]$Kind, [string]$Raw, [bool]$PreparedMembership = $false, [object]$DecodedPayload)
    [Console]::Error.WriteLine('NATURAL_POLICY_START')
    try { return Invoke-FixtureOriginalResidentPolicy @PSBoundParameters }
    finally { [Console]::Error.WriteLine('NATURAL_POLICY_COMPLETE') }
}
function Invoke-FixtureOriginalResidentPolicy {"""


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("decoded", [False, True])
def test_full_policy_wrapper_preserves_decoded_payload(shell: str, decoded: bool) -> None:
    executable = shutil.which(shell)
    if not executable:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"{shell} is required")
        pytest.skip(f"{shell} is unavailable")
    raw = '{"sessionId":"fixture-session","toolCalls":[{"name":"edit","args":{"path":"source.py","text":"complete"}}]}'
    program = (
        "$ErrorActionPreference = 'Stop'\n"
        + "$script:fixtureRaw = '" + raw + "'\n"
        + "$script:fixturePayload = @{ sessionId='fixture-session'; toolCalls=@("
        "@{ name='edit'; args=@{ path='source.py'; text='complete' } }) }\n"
        + NATURAL_POLICY_WRAPPER
        + """
    param([string]$Kind, [string]$Raw, [bool]$PreparedMembership = $false, [object]$DecodedPayload)
    @{
        kind = $Kind
        raw = $Raw
        prepared = $PreparedMembership
        has_decoded = $PSBoundParameters.ContainsKey('DecodedPayload')
        same_payload = [Object]::ReferenceEquals($DecodedPayload, $script:fixturePayload)
    } | ConvertTo-Json -Compress
}
Invoke-SelectedResidentToolHook -Kind preToolUse -Raw $script:fixtureRaw -PreparedMembership $true"""
        + (" -DecodedPayload $script:fixturePayload" if decoded else "")
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", program],
        capture_output=True, text=True, encoding="utf-8", timeout=10,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.splitlines() == ["NATURAL_POLICY_START", "NATURAL_POLICY_COMPLETE"]
    assert json.loads(result.stdout) == {
        "kind": "preToolUse", "raw": raw, "prepared": True,
        "has_decoded": decoded, "same_payload": decoded,
    }


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_full_policy_diagnostic_transport(
    tmp_path: Path, shell: str, *, retain: bool = False,
    reporter: Callable[[dict[str, object]], None] | None = None,
) -> None:
    from agent_worktrees.hook_ipc import HookIpcServer

    executable = shutil.which(shell)
    if not executable:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"{shell} is required")
        pytest.skip(f"{shell} is unavailable")
    seen: list[dict[str, Any]] = []
    expected = {"permissionDecision": "ask", "permissionDecisionReason": "synthetic"}

    def decide(kind: str, event: dict[str, Any], deadline: float) -> dict[str, str]:
        assert kind == "preToolUse"
        seen.append(event)
        return expected

    source = (SCRIPTS / "post-tool-hook.ps1").read_text(encoding="utf-8")
    copied = tmp_path / "diagnostic.ps1"
    copied.write_text(
        diagnostic_policy_transport(source, enabled=True, retain=retain), encoding="utf-8",
    )
    shutil.copyfile(SCRIPTS / "managed-session.ps1", tmp_path / "managed-session.ps1")
    event = {"sessionId": "diagnostic-control", "cwd": str(tmp_path), "toolName": "edit"}
    server = HookIpcServer(decide)
    server.start()
    try:
        endpoint = json.dumps(server.rendezvous()).replace("'", "''")
        raw = json.dumps(event).replace("'", "''")
        copied_path = str(copied).replace("'", "''")
        program = (
            f"$ErrorActionPreference = 'Stop'; . '{copied_path}'; "
            f"$endpoint = ConvertFrom-PostToolJson '{endpoint}'; "
            f"$payload = ConvertFrom-PostToolJson '{raw}'; "
            f"Invoke-ResidentPostTool -Endpoint $endpoint -PayloadJson '{raw}' "
            "-Kind preToolUse -DecodedPayload $payload"
        )
        with retained_rpc_phases(enabled=retain, reporter=reporter) as prefix:
            result = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-Command",
                 retained_rpc_command(prefix, program)],
                capture_output=True, text=True, encoding="utf-8", timeout=10,
                **no_window_kwargs(),
            )
    finally:
        server.close()
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected, result.stderr
    assert len(seen) == 1 and all(seen[0][key] == value for key, value in event.items())
    phases = [
        phase for line in result.stderr.splitlines(keepends=True)
        if (phase := rpc_clock_phase(line.encode("utf-8"))) is not None
    ]
    assert 0 < len(phases) <= 64
    assert {
        "rpc-entry", "rpc-encoded", "rpc-connected", "rpc-sent", "rpc-received", "rpc-result",
    } <= {phase.split()[1] for phase in phases}
    assert "NATIVE_WRITE_FIXTURE_RPC_" not in result.stderr
    assert (SCRIPTS / "post-tool-hook.ps1").read_text(encoding="utf-8") == source


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_full_policy_diagnostic_transport_retained(
    tmp_path: Path, shell: str, capsys: pytest.CaptureFixture[str],
) -> None:
    test_full_policy_diagnostic_transport(tmp_path, shell, retain=True)
    report = json.loads(capsys.readouterr().err.removeprefix("retained_rpc_phases="))
    assert report["status"] == "complete" and report["region_closed"] is True
    assert report["error"] is False and report["truncated"] is False
    assert 0 < len(report["records"]) <= 64
    assert {2, 6, 11, 12, 13, 14} <= {row["phase_id"] for row in report["records"]}


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_full_policy_diagnostic_transport_retained_reporter(
    tmp_path: Path, shell: str, capsys: pytest.CaptureFixture[str],
) -> None:
    reports: list[dict[str, object]] = []

    def report(value: dict[str, object]) -> None:
        reports.append(value)
        with capsys.disabled():
            print("retained_rpc_phases=" + json.dumps(
                value, sort_keys=True,
            ), file=sys.stderr, flush=True)

    test_full_policy_diagnostic_transport(tmp_path, shell, retain=True, reporter=report)
    assert capsys.readouterr().err == ""
    assert len(reports) == 1
    assert reports[0]["region_closed"] is True and reports[0]["status"] == "complete"
    assert reports[0]["error"] is False and reports[0]["truncated"] is False
    records = reports[0]["records"]
    assert isinstance(records, list) and 0 < len(records) <= 64


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
    diagnostic_rpc = os.environ.get("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE") in {
        "report-only", "post-exit", "code-stack",
    }
    bootstrap_diagnostics = os.environ.get("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE") in {
        "post-exit", "code-stack",
    }

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
    bootstrap = scripts / "pre-tool-hook.ps1"
    if bootstrap_diagnostics:
        bootstrap.write_text(
            diagnostic_policy_bootstrap(bootstrap.read_text(encoding="utf-8"), enabled=True),
            encoding="utf-8",
        )
    native = scripts / "post-tool-hook.ps1"
    source = native.read_text(encoding="utf-8")
    declaration = "function Invoke-SelectedResidentToolHook {"
    assert source.count(declaration) == 1
    source = source.replace(declaration, NATURAL_POLICY_WRAPPER, 1)
    native.write_text(
        diagnostic_policy_transport(source, enabled=diagnostic_rpc, retain=diagnostic_rpc),
        encoding="utf-8",
    )
    if case == "anchor-unavailable":
        (scripts / "anchor_write_guard.py").unlink()
    entry = json.loads((SCRIPTS.parent / "hooks.json").read_text(encoding="utf-8"))["hooks"]["preToolUse"][0]["powershell"]
    contract = (scripts / "hook-read-policy.json").read_bytes()
    observations: list[dict[str, Any]] = []
    executable_sha256 = hashlib.sha256(Path(executable).read_bytes()).hexdigest()

    def oracle_module(filename: str) -> Any:
        return load(filename.removesuffix(".py"))

    def report_retained(report: dict[str, object]) -> None:
        with capsys.disabled():
            print("retained_rpc_phases=" + json.dumps(
                report, sort_keys=True,
            ), file=sys.stderr, flush=True)

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
        with retained_rpc_phases(
            enabled=diagnostic_rpc, reporter=report_retained if diagnostic_rpc else None,
        ) as prefix:
            started = time.monotonic()
            result = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-Command",
                 retained_rpc_command(
                     prefix,
                     "[Console]::Error.WriteLine('NATURAL_SHELL_BUILD ' + $PSVersionTable.PSVersion); " + entry,
                     bootstrap=bootstrap_diagnostics,
                 )],
                input=json.dumps(event), capture_output=True, text=True, encoding="utf-8",
                cwd=workspace, env=env, timeout=10, **no_window_kwargs(),
            )
            elapsed = time.monotonic() - started
        progress("registered-entry-complete:" + variant)
        if diagnostic_rpc:
            rpc_phases = [
                phase for line in result.stderr.splitlines(keepends=True)
                if (phase := rpc_clock_phase(line.encode("utf-8"))) is not None
            ]
            with capsys.disabled():
                print(json.dumps({"natural_policy_rpc": {
                    "shell": shell, "cell": cell, "case": case, "variant": variant,
                    "phases": rpc_phases,
                    "truncated": "NATIVE_WRITE_FIXTURE_RPC_TRUNCATED" in result.stderr.splitlines(),
                    "report_error": "NATIVE_WRITE_FIXTURE_RPC_REPORT_ERROR" in result.stderr.splitlines(),
                    "diagnostic_only": True,
                }}), flush=True)
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
            **({"bootstrap_fixture_sha256": hashlib.sha256(bootstrap.read_bytes()).hexdigest()}
               if bootstrap_diagnostics else {}),
            "fault": "copied anchor guard unavailable" if case == "anchor-unavailable" else None,
            "registered_command_sha256": hashlib.sha256(entry.encode("utf-8")).hexdigest(),
            "observations": observations,
            "fixture_call_seconds": time.monotonic() - case_started,
            "limits": "Synthetic native consumer; selected resident and original policy, passive entry markers. Not a distribution, installed/CLI/ACP/model, composed-latency or throughput pass.",
        }}), flush=True)


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("network")
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_bootstrap_phase_buffer_covers_registered_policy_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    shell: str,
) -> None:
    retained = retained_rpc_phases
    reports: list[dict[str, object]] = []

    @contextmanager
    def capture(
        *, enabled: bool, reporter: Callable[[dict[str, object]], None] | None = None,
    ) -> Iterator[str]:
        def record(report: dict[str, object]) -> None:
            reports.append(report)
            if reporter is not None:
                reporter(report)

        with retained(enabled=enabled, reporter=record) as prefix:
            yield prefix

    monkeypatch.setattr(sys.modules[__name__], "retained_rpc_phases", capture)
    monkeypatch.setenv("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE", "post-exit")
    test_registered_full_policy_composition(tmp_path, monkeypatch, capsys, shell, True, "anchor")
    assert len(reports) == 1
    report = reports[0]
    assert report["status"] == "complete" and report["region_closed"] is True
    assert report["error"] is False and report["truncated"] is False
    rows = report["records"]
    assert isinstance(rows, list) and 10 < len(rows) <= 64
    names = [_BUFFERED_PHASES[row["phase_id"]] for row in rows]
    assert tuple(name for name in names if name in BOOTSTRAP_PHASES) == BOOTSTRAP_PHASES
    assert names.index("pretool-posttool-loaded") < names.index("rpc-entry")
    assert names.index("rpc-entry") < names.index("rpc-result")


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_bootstrap_phase_buffer_closes_after_script_error(tmp_path: Path, shell: str) -> None:
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    reports: list[dict[str, object]] = []
    with retained_rpc_phases(enabled=True, reporter=reports.append) as prefix:
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", retained_rpc_command(
                prefix, "throw 'synthetic bootstrap fixture failure'", bootstrap=True,
            )],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
            cwd=tmp_path, **no_window_kwargs(),
        )
    assert result.returncode != 0 and "synthetic bootstrap fixture failure" in result.stderr
    assert len(reports) == 1
    report = reports[0]
    assert report["status"] == "complete" and report["region_closed"] is True
    assert report["error"] is False and report["truncated"] is False
    rows = report["records"]
    assert isinstance(rows, list) and len(rows) == 1
    assert _BUFFERED_PHASES[rows[0]["phase_id"]] == "bootstrap-capture-ready"
