from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "scripts" / "post-tool-hook.ps1"


@pytest.fixture
def admitted_protocol_script(tmp_path):
    scripts = tmp_path / "protocol-scripts"
    scripts.mkdir()
    for name in ("post-tool-hook.ps1", "hook_client.py"):
        shutil.copyfile(PLUGIN / "scripts" / name, scripts / name)
    (scripts / "managed-session.ps1").write_text(
        "function Test-ManagedWorktreesSession { return $true }\n", encoding="utf-8"
    )
    return scripts / "post-tool-hook.ps1"


@pytest.mark.guard
def test_post_hook_uses_same_shell_and_installs_the_client():
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    command = hooks["hooks"]["postToolUse"][0]["powershell"]
    assert ". $s; Invoke-PostToolHook -Raw $raw" in command
    assert "ReadToEnd()" in command
    assert "OpenStandardInput()" in command
    assert "$OutputEncoding = $utf8" in command
    assert "post-tool-hook.ps1" in command
    assert "$raw | & $py $fallback postToolUse" in command
    for name in ("install.ps1", "install.sh"):
        assert "post-tool-hook.ps1" in (PLUGIN / "scripts" / name).read_text(
            encoding="utf-8"
        )
    assert "post-tool-hook.ps1" in (
        PLUGIN / "src" / "agent_worktrees" / "installer.py"
    ).read_text(encoding="utf-8")


@pytest.mark.guard
def test_local_advisory_fallback_does_not_retry_daemon(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "post_tool_python_client", PLUGIN / "scripts" / "hook_client.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_managed_session_admitted", lambda payload: True)
    calls = []
    monkeypatch.setattr(
        module, "_fallback_post",
        lambda payload, home: calls.append(payload) or {"additionalContext": "local"},
    )
    monkeypatch.setattr(
        module, "_request",
        lambda *args: pytest.fail("local advisory fallback must not resend"),
    )
    monkeypatch.setattr(module.sys, "stdin", io.StringIO('{"toolName":"view"}'))
    stdout = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", stdout)
    assert module.main(["postToolUse", "--local-fallback"]) == 0
    assert calls == [{"toolName": "view"}]
    assert json.loads(stdout.getvalue()) == {"additionalContext": "local"}


@pytest.fixture
def post_client(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "post_tool_fallback_client", PLUGIN / "scripts" / "hook_client.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("AGENT_WORKTREES_NUDGE", "on")
    monkeypatch.setenv("AGENT_WORKTREES_NUDGE_CALLS", "2")
    monkeypatch.setenv("AGENT_WORKTREES_NUDGE_MINUTES", "999")
    return module


def _post_record(home, name):
    records = home / ".project" / "worktrees"
    records.mkdir(parents=True, exist_ok=True)
    root = home / name
    root.mkdir()
    record = records / f"{name}.yaml"
    record.write_text(
        f"state: active\nworktree_path: '{root}'\nhead_session: ''\n",
        encoding="utf-8",
    )
    return root, record


def test_post_fallback_shares_lookup_but_not_across_callbacks(
    post_client, monkeypatch, tmp_path,
):
    root, record = _post_record(tmp_path, "worktree-a")
    payload = {"cwd": str(root), "toolName": "view"}
    scans = []
    loads = []
    original_glob = Path.glob
    original_spec = importlib.util.spec_from_file_location

    def glob(path, pattern):
        if path == tmp_path:
            scans.append(pattern)
        return original_glob(path, pattern)

    def spec(name, location, *args, **kwargs):
        if Path(location).name == "nudge_status.py":
            loads.append(location)
        return original_spec(name, location, *args, **kwargs)

    monkeypatch.setattr(Path, "glob", glob)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", spec)
    first = post_client._fallback_post(payload, tmp_path)
    assert "bind-session" in first["additionalContext"]
    assert scans == [".*"]
    assert len(loads) == 1
    second = post_client._fallback_post(payload, tmp_path)
    assert "2 tool call(s)" in second["additionalContext"]
    assert "bind-session" not in second["additionalContext"]
    record.write_text("state: completed\nhead_session: bound\n", encoding="utf-8")
    assert post_client._fallback_post(payload, tmp_path) == {}
    assert scans == [".*"] * 3
    assert not (tmp_path / ".agent-worktrees" / "nudge-state" / "worktree-a.json").exists()


@pytest.mark.parametrize("distinct_cwd", [False, True])
def test_post_fallback_empty_result_is_fresh_and_cwd_scoped(
    post_client, monkeypatch, tmp_path, distinct_cwd,
):
    status_root = tmp_path / "worktree-a"
    binding_root = tmp_path / "worktree-b" if distinct_cwd else status_root
    payload = {"cwd": str(status_root), "workingDirectory": str(binding_root)}
    status = post_client._load_sibling("nudge_status.py")
    lookup = status._find_tracking_yaml
    calls = []

    def find(cwd, home):
        calls.append(cwd)
        return lookup(cwd, home)

    monkeypatch.setattr(status, "_find_tracking_yaml", find)
    assert post_client._fallback_post(payload, tmp_path) == {}
    assert calls == [str(status_root), str(binding_root)]
    _post_record(tmp_path, binding_root.name)
    text = post_client._fallback_post(payload, tmp_path)["additionalContext"]
    assert f"bind-session --worktree-dir={binding_root}" in text
    sidecar = tmp_path / ".agent-worktrees" / "nudge-state" / "worktree-a.json"
    assert sidecar.exists() is not distinct_cwd


def test_post_fallback_preserves_late_notice_and_independent_failure(
    post_client, monkeypatch, tmp_path,
):
    root, _record = _post_record(tmp_path, "worktree-a")
    payload = {"cwd": str(root)}
    status = post_client._load_sibling("nudge_status.py")
    lookup = status._find_tracking_yaml
    calls = []

    def find(cwd, home):
        calls.append(cwd)
        if len(calls) == 1:
            raise OSError("transient lookup failure")
        return lookup(cwd, home)

    monkeypatch.setattr(status, "_find_tracking_yaml", find)
    assert "bind-session" in post_client._fallback_post(payload, tmp_path)["additionalContext"]
    assert len(calls) == 2
    monkeypatch.setattr(status, "_late_guidance_notice", lambda *args: "late guidance")
    monkeypatch.setenv("AGENT_WORKTREES_NUDGE", "off")
    calls.clear()
    monkeypatch.setattr(status, "_find_tracking_yaml", lookup)
    assert post_client._fallback_post(payload, tmp_path) == {"additionalContext": "late guidance"}


def test_post_fallback_retries_failure_swallowed_by_tracking_resolver(
    post_client, monkeypatch, tmp_path,
):
    root, _record = _post_record(tmp_path, "worktree-a")
    resolve = Path.resolve
    attempts = []

    def transient(path, *args, **kwargs):
        if path == root:
            attempts.append(path)
            if len(attempts) == 1:
                raise OSError("transient cwd resolution failure")
        return resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", transient)
    text = post_client._fallback_post({"cwd": str(root)}, tmp_path)["additionalContext"]
    assert f"bind-session --worktree-dir={root}" in text
    assert len(attempts) == 2
    assert not (tmp_path / ".agent-worktrees" / "nudge-state" / "worktree-a.json").exists()


@pytest.mark.parametrize("status_missing", [False, True])
def test_post_fallback_default_cwd_and_missing_status_helper(
    post_client, monkeypatch, tmp_path, status_missing,
):
    root, _record = _post_record(tmp_path, "worktree-a")
    monkeypatch.chdir(root)
    if status_missing:
        load = post_client._load_sibling
        monkeypatch.setattr(
            post_client, "_load_sibling",
            lambda name: None if name == "nudge_status.py" else load(name),
        )
    text = post_client._fallback_post({}, tmp_path)["additionalContext"]
    assert f"bind-session --worktree-dir={root}" in text
    assert post_client._fallback_post({}, tmp_path).get("additionalContext", "").count(
        "2 tool call(s)"
    ) == (0 if status_missing else 1)


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_real_python_post_fallback_preserves_accounting_and_binding(tmp_path):
    from agent_procutil import no_window_kwargs

    root, record = _post_record(tmp_path, "worktree-a")
    git_dir = root / ".git"
    for name in ("objects", "refs"):
        (git_dir / name).mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    registry = tmp_path / ".agent-worktrees"
    registry.mkdir()
    (registry / "projects.yaml").write_text(
        "projects:\n  example: {}\n"
    )
    (registry / "repos.yaml").write_text(
        json.dumps({"repos": {"example": {"windows" if os.name == "nt" else "linux": str(root)}}})
    )
    env = os.environ.copy()
    env.update(
        HOME=str(tmp_path), USERPROFILE=str(tmp_path), AGENT_HOME=str(tmp_path),
        AGENT_WORKTREES_NUDGE="on", AGENT_WORKTREES_NUDGE_CALLS="2",
        AGENT_WORKTREES_NUDGE_MINUTES="999",
    )
    env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    payload = json.dumps({"sessionId": "post-fixture", "cwd": str(root), "toolName": "view"})

    def invoke():
        result = subprocess.run(
            [sys.executable, str(PLUGIN / "scripts" / "hook_client.py"),
             "postToolUse", "--local-fallback"],
            input=payload, text=True, capture_output=True, env=env, timeout=10,
            **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        return result.stdout

    assert "bind-session" in json.loads(invoke())["additionalContext"]
    second = json.loads(invoke())["additionalContext"]
    assert "2 tool call(s)" in second
    assert "bind-session" not in second
    record.write_text("state: completed\nhead_session: bound\n", encoding="utf-8")
    assert invoke() == ""


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows prepared membership")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("event_case", [
    "mutation", "late-watch", "oversized-mutation", "oversized-late-watch",
    "batch-shell", "batch-malformed", "batch-empty", "batch-too-many", "batch-call-cwd",
])
def test_registered_post_hook_reuses_original_prepared_membership(tmp_path, monkeypatch, shell, event_case):
    from agent_procutil import no_window_kwargs
    from agent_worktrees.hook_ipc import HookIpcServer
    from test_managed_session import environment, fixture_project, load

    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    session = tmp_path / ".copilot" / "session-state" / "prepared"
    session.mkdir(parents=True)
    payload = {
        "sessionId": "prepared", "cwd": str(anchor),
        "toolName": "edit" if event_case.endswith("mutation") else "view",
    }
    if event_case.startswith("batch-"):
        calls = [{"name": "edit", "args": {"path": "one"}}]
        if event_case == "batch-shell":
            calls.append({"name": "powershell", "args": {"command": "git status"}})
        elif event_case == "batch-malformed":
            calls.append(None)
        elif event_case == "batch-empty":
            calls = []
        elif event_case == "batch-too-many":
            calls *= 129
        elif event_case == "batch-call-cwd":
            calls.append({"name": "view", "cwd": "other"})
        payload["toolCalls"] = calls
    if event_case.endswith("late-watch"):
        (session / "local-guidance-watch.json").write_text("{}")
    oversized = event_case.startswith("oversized-")
    if oversized:
        payload["toolResult"] = "x" * 1100000
    assert module.admitted(payload, home=tmp_path)
    scripts = tmp_path / "payload" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(SCRIPT, scripts / SCRIPT.name)
    shutil.copyfile(PLUGIN / "scripts" / "hook-facts.ps1", scripts / "hook-facts.ps1")
    original = str(PLUGIN / "scripts" / "managed-session.ps1").replace("'", "''")
    interpreter = str(sys.executable).replace("'", "''")
    (scripts / "managed-session.ps1").write_text(
        f". '{original}'\n"
        + (
            f"function Get-ManagedWorktreesPython {{ return '{interpreter}' }}\n"
            if oversized else
            "function Get-ManagedWorktreesPython { throw 'interpreter lookup is forbidden' }\n"
        ),
        encoding="utf-8",
    )
    command = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["postToolUse"][0]["powershell"]
    harness = tmp_path / "registered-post.ps1"
    harness.write_text(
        "function Get-Command { throw 'a client/CLI child was requested' }\n" + command,
        encoding="utf-8",
    )
    seen = []

    def decide(kind, event, deadline):
        seen.append((kind, event))
        return {"additionalContext": "prepared resident"}

    server = HookIpcServer(decide)
    server.start()
    try:
        (registry / "status-monitor.lock").write_text(json.dumps(server.rendezvous()), encoding="utf-8")
        env = environment(tmp_path, anchor)
        env["COPILOT_PLUGIN_ROOT"] = str(scripts.parent)
        for _ in range(2):
            result = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-File", str(harness)],
                input=json.dumps(payload), text=True, capture_output=True, timeout=10,
                cwd=anchor, env=env, **no_window_kwargs(),
            )
            assert result.returncode == 0, result.stderr
            assert not result.stderr
            if event_case in {"mutation", "late-watch"}:
                assert result.stdout == ""
                if event_case == "late-watch":
                    assert (session / "local-guidance-watch.json").read_text() == "{}"
            else:
                assert json.loads(result.stdout) == {"additionalContext": "prepared resident"}
        expected = [] if event_case in {"mutation", "late-watch"} else [("postToolUse", payload)] * 2
        assert seen == expected
    finally:
        server.close()


@pytest.fixture
def prepared_read_event(tmp_path, monkeypatch, monkeypatch_config, request):
    from agent_worktrees import __main__ as main, config as cfg, handoff_diagnostics, sessions, tracking
    from agent_worktrees.hook_facts_publication import HookFactsPublisher
    from agent_worktrees.worktrees_runtime import WorktreesRuntime
    from test_managed_session import environment, fixture_linked_worktree, fixture_project, load
    from test_session_worktree_binding import _save_record

    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    counter_home = registry
    context = None
    selected = None
    payload_root = tmp_path / "payload"
    if getattr(request, "param", None) == "cell":
        from types import SimpleNamespace
        import test_registry_paths as fixtures
        from agent_procutil import no_window_kwargs

        native_run = subprocess.run
        monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
            run=lambda *args, **kwargs: native_run(
                *args, **(kwargs | no_window_kwargs() | {"timeout": 10}),
            ),
        ))
        payload_root = fixtures._payload(tmp_path, "payload")
        context, registry = fixtures._stamp(
            tmp_path / "durable", payload_root, marketplace="selected", repository="example/selected",
        )
        for name in ("projects.yaml", "repos.yaml"):
            (registry / name).write_bytes((counter_home / name).read_bytes())
        fixtures._select(monkeypatch, context, payload_root)
        selected = load("registry_root").resolve_registry_context(payload_root=payload_root)
        counter_home = registry
    case = getattr(request.node, "callspec", None)
    case = case.params.get("case") if case else None
    if case == "distinct-home":
        agent_home = tmp_path / "agent-home"
        agent_home.mkdir()
        registry = Path(shutil.move(str(registry), str(agent_home / ".agent-worktrees")))
        monkeypatch.setenv("AGENT_HOME", str(agent_home))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    checkout_home = tmp_path
    if case == "quoted-bind":
        checkout_home = tmp_path / "space's $workspace; literal"
        checkout_home.mkdir()
    workspace, _ = fixture_linked_worktree(checkout_home, anchor)
    project = tmp_path / "resolved-project-state"
    records = project / "worktrees"
    records.mkdir(parents=True)
    cfg.set_active_project("example")
    monkeypatch.setattr(cfg, "tracking_dir", lambda: records)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project)
    session_root = tmp_path / ".copilot" / "session-state"
    session = session_root / "prepared"
    session.mkdir(parents=True)
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: session_root)
    _save_record(records, "worktree", str(workspace))
    path = records / "worktree.yaml"
    record = tracking._load_record_uncached(path)
    record.repo = "example"
    record.creation_nonce = "read-incarnation"
    record.status_note_at = "2026-11-05T20:00:00.123+05:30"
    tracking.save_record(record, path)
    tracking.register_session("worktree", "prepared")
    record = tracking._load_record_uncached(path)
    binding = handoff_diagnostics.stamp_session_state_worktree_binding(
        "prepared", "worktree", worktree_dir="not-a-locator", worktree_record=record,
    )
    assert binding["hookFacts"]["projectRoot"] == str(project)
    assert binding["hookFacts"]["workspace"] == str(workspace)
    row = main._worktree_to_dict(record, current_only=True)
    assert "record_head_session" not in row
    record_heads = {
        record.worktree_id: {
            "creation_nonce": record.creation_nonce, "workspace": record.worktree_path,
            "head_session": record.resolved_head_session,
        },
    }
    assert record_heads[record.worktree_id]["head_session"] == "prepared"
    row.update(
        live_session_ids=["prepared"],
        fact_provenance={"record": {"quality": "current", "observed_at": 1}},
    )
    publisher = HookFactsPublisher(registry, "a" * 32, project_root=lambda name: project, context=selected)
    runtime = WorktreesRuntime(tmp_path / "current.sqlite3", hook_facts=publisher)
    runtime.activate()
    runtime.publish("example", [row], observed_at=1, record_heads=record_heads)
    fact_path = project / "worktrees" / "worktree" / "hook-facts" / f"{publisher.generation}.json"
    discovery = {
        "hook_facts_version": 2, "hook_facts_generation": publisher.generation,
        **{key: publisher.producer[key] for key in (
            "runtime_generation", "marketplaceId", "installReceipt", "pluginRoot",
        )},
    }
    (registry / "status-monitor.lock").write_text(json.dumps(discovery), encoding="utf-8")
    payload = {"sessionId": "prepared", "cwd": str(workspace), "toolName": "view"}
    if case == "distinct-home":
        assert load("managed_session").admitted(payload)
    else:
        assert load("managed_session").admitted(payload, home=tmp_path)
    scripts = payload_root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("post-tool-hook.ps1", "hook-facts.ps1"):
        shutil.copyfile(PLUGIN / "scripts" / name, scripts / name)
    with (scripts / "post-tool-hook.ps1").open("a", encoding="utf-8") as stream:
        stream.write("\nfunction Invoke-ResidentPostTool { throw 'synchronous resident call' }\n")
    original = str(PLUGIN / "scripts" / "managed-session.ps1").replace("'", "''")
    (scripts / "managed-session.ps1").write_text(
        f". '{original}'\n"
        "function Get-ManagedWorktreesPython {\n"
        " $script:unexpectedPython++; throw 'interpreter lookup is forbidden'\n"
        "}\n",
        encoding="utf-8",
    )
    command = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["postToolUse"][0]["powershell"]
    measured_command = command.removesuffix("exit 0") + (
        "if ($script:unexpectedPython -ne [int]$env:EXPECTED_PYTHON_LOOKUPS -or $script:unexpectedClient) {\n"
        " throw 'unexpected interpreter/client lookup'\n"
        "}\n"
        "$entrySeconds=$entryClock.Elapsed.TotalSeconds\n"
        f"$counterPath='{str(counter_home / 'nudge-state' / 'worktree.lock').replace(chr(39), chr(39)*2)}'\n"
        "if (-not $env:COUNTER_TEST_ROLE -and [IO.File]::Exists($counterPath)) {\n"
        " $probe=[IO.File]::Open($counterPath,[IO.FileMode]::Open,"
        "[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)\n"
        " try { $probe.Lock(0,1) } finally { $probe.Dispose() }\n"
        "}\n"
        "[Console]::Error.WriteLine('entry_seconds=' + "
        "$entrySeconds.ToString([Globalization.CultureInfo]::InvariantCulture)); exit 0"
    )
    harness = tmp_path / "registered-read.ps1"
    harness.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$script:unexpectedPython=0; $script:unexpectedClient=$false\n"
        "function Get-Command { $script:unexpectedClient=$true; throw 'a client/CLI child was requested' }\n"
        "$entryClock=[Diagnostics.Stopwatch]::StartNew()\n"
        "try {\n" + measured_command + "\n} catch {\n"
        "  [Console]::Error.WriteLine($_.Exception.ToString() + \"`n\" + $_.ScriptStackTrace)\n"
        "  exit 91\n}\n",
        encoding="utf-8",
    )
    env = environment(tmp_path, workspace)
    env.update(
        COPILOT_PLUGIN_ROOT=str(scripts.parent),
        AGENT_HOME=str(registry.parent),
        AGENT_WORKTREES_NUDGE_CALLS="2", AGENT_WORKTREES_NUDGE_MINUTES="999",
        EXPECTED_PYTHON_LOOKUPS="0",
    )
    if context is not None:
        env.update(
            COPILOT_EXTENSIONS_CONTEXT=str(context), AGENT_WORKTREES_PAYLOAD_ROOT=str(payload_root),
        )
    env.pop("AGENT_WORKTREES_NUDGE", None)
    try:
        yield dict(
            env=env, harness=harness, payload=payload, facts=fact_path,
            row=row, record_heads=record_heads, runtime=runtime, session=session, registry=registry,
            home=tmp_path, tracking_path=path,
            sidecar=counter_home / "nudge-state" / "worktree.json",
            context=context, selected=selected, payload_root=payload_root,
        )
    finally:
        runtime.shutdown()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("network")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows prepared installation-cell consumer")
@pytest.mark.parametrize("prepared_read_event", ["cell"], indirect=True)
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", [
    "read", "native", "batch", "shell", "shell-read", "no-reply", "missing-discovery",
    "old-provider", "wrong-generation", "closed", "foreign-marketplace",
    "foreign-receipt", "foreign-root", "legacy-discovery", "missing-row",
    "changed-receipt", "changed-payload", "unadmitted", "old-membership",
    "live-nudge-off", "late-guidance",
])
def test_registered_prepared_cell_post_tool_uses_selected_original_owners(
    prepared_read_event, monkeypatch, shell, case,
):
    from types import SimpleNamespace

    from agent_procutil import no_window_kwargs
    from agent_worktrees import __main__ as main, local_cache_worker
    from agent_worktrees.hook_ipc import HookIpcServer

    executable = shutil.which(shell)
    if not executable:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"Required native membership shell {shell} is unavailable")
        pytest.skip(f"{shell} is unavailable")
    fixture = prepared_read_event
    invalidated, notices, synchronous = [], [], []
    received, release = threading.Event(), threading.Event()
    policy = main._ResidentHookPolicy(main._load_hook_client_module())
    target = str(fixture["home"] / "changed")
    cache = SimpleNamespace(
        invalidate=lambda path: invalidated.append(path),
        invalidate_all=lambda: invalidated.append("all"),
    )

    class Server(HookIpcServer):
        def _on_read_demand(self, request):
            super()._on_read_demand(request)
            notices.append(request)
            received.set()

        def _on_native_mutation(self, request):
            super()._on_native_mutation(request)
            notices.append(request)
            received.set()

    class NoReply(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            raw = b""
            while not raw.endswith(b"\n") and len(raw) < 65536:
                chunk = self.request.recv(65536 - len(raw))
                if not chunk:
                    break
                raw += chunk
            notices.append(json.loads(raw))
            received.set()
            assert release.wait(10)

    server = Server(
        lambda *args: synchronous.append(args) or {},
        read_demand_admitted=lambda: True,
        native_mutation_decide=lambda kind, payload, deadline: main._resident_hook_decision(
            kind, payload, deadline=deadline, policy=policy, segment_cache=cache,
        ),
    )
    server.start()
    stalled = None
    stalled_thread = None
    try:
        path = fixture["registry"] / "status-monitor.lock"
        discovery = {**json.loads(path.read_text()), **server.rendezvous()}
        if case == "old-provider":
            discovery.pop("hook_capabilities")
        elif case == "wrong-generation":
            discovery["hook_generation"] = "b" * 32
        elif case == "closed":
            server.close_admission()
        elif case.startswith("foreign-"):
            field = {
                "foreign-marketplace": "marketplaceId", "foreign-receipt": "installReceipt",
                "foreign-root": "pluginRoot",
            }[case]
            discovery[field] = "other" if field == "marketplaceId" else str(fixture["home"] / "other")
        elif case == "legacy-discovery":
            for name in ("marketplaceId", "installReceipt", "pluginRoot"):
                discovery.pop(name)
        elif case == "no-reply":
            stalled = socketserver.ThreadingTCPServer(("127.0.0.1", 0), NoReply)
            stalled_thread = threading.Thread(target=stalled.serve_forever)
            stalled_thread.start()
            discovery["hook_endpoint"] = f"127.0.0.1:{stalled.server_address[1]}"
        path.write_text(json.dumps(discovery), encoding="utf-8")
        if case == "missing-discovery":
            path.unlink()
        elif case == "missing-row":
            fixture["facts"].unlink()
        elif case in {"changed-receipt", "changed-payload"}:
            changed = fixture["context"] if case == "changed-receipt" else fixture["payload_root"] / "plugin.json"
            before = changed.stat()
            changed.write_bytes(changed.read_bytes().replace(b"{", b"[", 1))
            os.utime(changed, ns=(before.st_atime_ns, before.st_mtime_ns))
        elif case == "unadmitted":
            (fixture["session"] / "agent-worktrees" / "managed-session.json").unlink()
        elif case == "old-membership":
            marker = fixture["session"] / "agent-worktrees" / "managed-session.json"
            value = json.loads(marker.read_text())
            value.pop("installation")
            marker.write_text(json.dumps(value))
        elif case == "live-nudge-off":
            fixture["env"]["AGENT_WORKTREES_NUDGE"] = "off"
        elif case == "late-guidance":
            monkeypatch.setenv("USERPROFILE", str(fixture["home"]))
            monkeypatch.setattr(Path, "home", lambda: fixture["home"])
            root = Path(fixture["payload"]["cwd"])
            local_cache_worker.watch_completion(root, "prepared", initial_revision="a" * 32, started_at=1)
            watch = json.loads((fixture["session"] / "local-guidance-watch.json").read_text())
            metadata = Path(watch["metadata"])
            metadata.mkdir(parents=True, exist_ok=True)
            (metadata / "request.json").write_text(json.dumps({
                "version": 1, "root": str(root), "phase": "done", "completed_at": 2,
                "result": {"status": "ready", "restart_required": True, "publication_revision": "b" * 32},
            }))
            (metadata / "publication.json").write_text(json.dumps({
                "version": 1, "root": str(root), "revision": "b" * 32,
            }))
        payload = {**fixture["payload"], "toolResult": "must-not-be-sent"}
        if case in {"changed-receipt", "changed-payload", "unadmitted", "old-membership"}:
            fixture["env"]["EXPECTED_PYTHON_LOOKUPS"] = "1"
        if case == "native":
            payload.update(toolName="multi_edit", toolArgs={"edits": [{"path": target}]})
        elif case == "batch":
            payload.pop("toolName")
            payload["toolCalls"] = [
                {"name": "view", "args": {"path": "read-only"}},
                {"name": "edit", "args": json.dumps({"path": target})},
            ]
        elif case in {"shell", "shell-read"}:
            payload.update(
                toolName="powershell",
                toolArgs=json.dumps({"command": "git pull --ff-only" if case == "shell" else "git status"}),
            )
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File", str(fixture["harness"])],
            input=json.dumps(payload), text=True, capture_output=True, timeout=10,
            env=fixture["env"], **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr.startswith("entry_seconds="), result.stderr
        print(f"{shell}/cell/{case}: {result.stderr.strip()}")
        if case == "late-guidance":
            assert "Restart/new context" in json.loads(result.stdout)["additionalContext"]
            assert not (fixture["session"] / "local-guidance-watch.json").exists()
        else:
            assert result.stdout == ""
        no_send = case.startswith("foreign-") or case in {
            "legacy-discovery", "missing-discovery", "old-provider", "changed-receipt",
            "changed-payload", "unadmitted", "old-membership",
        }
        if no_send:
            assert not notices and not server.has_recent_hook_demand()
        elif case in {"wrong-generation", "closed"}:
            received.wait(2)
            assert not server.has_recent_hook_demand()
        else:
            assert received.wait(2), "selected original IPC owner received no notification"
            assert len(notices) == 1
            expected_kind = {
                "native": "nativeToolMutation", "batch": "nativeToolBatchMutation",
                "shell": "shellToolMutation", "shell-read": "shellToolMutation",
            }.get(case, "readHookDemand")
            assert notices[0]["kind"] == expected_kind
            if expected_kind == "readHookDemand":
                assert "payload" not in notices[0]
            else:
                assert notices[0]["payload"] == {
                    key: value for key, value in payload.items() if key != "toolResult"
                }
            assert server.has_recent_hook_demand() is (case != "no-reply")
        assert invalidated == ([target] if case in {"native", "batch"} else ["all"] if case == "shell" else [])
        assert synchronous == [], "prepared events must never request synchronous policy or advice"
        no_counter = case.startswith("foreign-") or case in {
            "legacy-discovery", "missing-discovery", "missing-row", "changed-receipt",
            "changed-payload", "unadmitted", "old-membership", "live-nudge-off", "late-guidance",
        }
        assert fixture["sidecar"].exists() is not no_counter
        if not no_counter:
            assert json.loads(fixture["sidecar"].read_text())["count"] == 1
        assert not (fixture["home"] / ".agent-worktrees" / "nudge-state").exists()
    finally:
        release.set()
        if stalled is not None:
            stalled.shutdown()
            stalled.server_close()
            stalled_thread.join(timeout=2)
            assert not stalled_thread.is_alive()
        server.close()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("network")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows shell demand consumer")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", [
    "current", "old-provider", "wrong-generation", "closed", "revoked",
    "missing-discovery", "missing-row", "unadmitted", "no-reply",
])
def test_registered_read_demand_is_send_only(prepared_read_event, shell, case):
    from agent_procutil import no_window_kwargs
    from agent_worktrees.hook_ipc import HookIpcServer

    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    fixture = prepared_read_event
    calls, notices = [], []
    received, release = threading.Event(), threading.Event()

    class Server(HookIpcServer):
        def _on_read_demand(self, request):
            notices.append(request)
            super()._on_read_demand(request)
            received.set()

    class NoReply(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            raw = b""
            while not raw.endswith(b"\n") and len(raw) < 2048:
                chunk = self.request.recv(2048 - len(raw))
                if not chunk:
                    break
                raw += chunk
            notices.append(json.loads(raw))
            received.set()
            assert release.wait(10), "test did not release its non-replying peer"

    server = Server(lambda *args: calls.append(args) or {}, read_demand_admitted=lambda: case != "revoked")
    server.start()
    stalled = None
    stalled_thread = None
    try:
        path = fixture["registry"] / "status-monitor.lock"
        discovery = {**json.loads(path.read_text()), **server.rendezvous()}
        if case == "old-provider":
            discovery.pop("hook_capabilities")
        elif case == "wrong-generation":
            discovery["hook_generation"] = "b" * 32
        elif case == "closed":
            server.close_admission()
        elif case == "missing-row":
            fixture["facts"].unlink()
        elif case == "unadmitted":
            (fixture["session"] / "agent-worktrees" / "managed-session.json").unlink()
        elif case == "no-reply":
            stalled = socketserver.ThreadingTCPServer(("127.0.0.1", 0), NoReply)
            stalled_thread = threading.Thread(target=stalled.serve_forever)
            stalled_thread.start()
            discovery["hook_endpoint"] = f"127.0.0.1:{stalled.server_address[1]}"
        path.write_text(json.dumps(discovery), encoding="utf-8")
        if case == "missing-discovery":
            path.unlink()
        payload = {**fixture["payload"], "toolArgs": {"path": "must-not-be-sent"}}
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File", str(fixture["harness"])],
            input=json.dumps(payload), text=True, capture_output=True, timeout=10,
            env=fixture["env"], **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert not result.stdout
        assert result.stderr.startswith("entry_seconds="), result.stderr
        print(f"{shell}/{case}: {result.stderr.strip()}")
        if case in {"old-provider", "missing-discovery", "unadmitted"}:
            assert not notices and not server.has_recent_hook_demand()
        elif case in {"wrong-generation", "closed", "revoked"} and not received.wait(2):
            # Delivery is optional; the direct wire controls prove receiver rejection.
            assert not notices and not server.has_recent_hook_demand()
        else:
            assert received.wait(2), "send-only notification did not reach its receiver"
            assert len(notices) == 1
            assert set(notices[0]) == {"version", "kind", "token", "hook_generation", "deadline"}
            assert notices[0]["kind"] == "readHookDemand"
            assert server.has_recent_hook_demand() is (case in {"current", "missing-row"})
        assert calls == [], "a read-demand notice must not run policy, advice or invalidation"
    finally:
        release.set()
        if stalled is not None:
            stalled.shutdown()
            stalled.server_close()
            stalled_thread.join(timeout=2)
            assert not stalled_thread.is_alive()
        server.close()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("network")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows shell mutation consumer")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", [
    "structured", "patch", "old-provider", "wrong-generation", "closed",
    "missing-discovery", "oversized", "no-reply",
    "batch-structured", "batch-reads", "batch-old-provider", "batch-wrong-generation",
    "batch-closed", "batch-missing-discovery", "batch-oversized", "batch-no-reply",
    "shell-write", "shell-read", "shell-json-args", "shell-raw-args",
    "shell-fallback-args", "shell-unknown-args", "shell-pull",
    "shell-old-provider", "shell-wrong-generation", "shell-closed",
    "shell-missing-discovery", "shell-oversized", "shell-no-reply",
    "shell-assignment", "shell-literal-assignment", "shell-read-sed",
    "shell-quoted-parentheses", "batch-shell-metadata",
    "shell-control-block", "shell-conditional", "shell-bash-assignment",
    "shell-typed-assignment", "shell-pipeline-block", "shell-invoke-block",
    "shell-literal-scriptblock",
    "shell-read-bracket-path", "shell-read-type-call", "shell-sed-cluster",
    "shell-git-output", "shell-dot-write", "shell-dot-read", "shell-dot-assignment",
    "shell-evaluation", "shell-module-write", "shell-module-read", "shell-quoted-evaluation",
    "shell-read-select-string", "shell-read-select-object", "shell-read-expansion",
    "shell-read-assignment",
])
def test_registered_native_mutation_is_send_only(prepared_read_event, shell, case):
    from types import SimpleNamespace

    from agent_procutil import no_window_kwargs
    from agent_worktrees import __main__ as main
    from agent_worktrees.hook_ipc import HookIpcServer

    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    fixture = prepared_read_event
    batch = case.startswith("batch-")
    shell_event = case.startswith("shell-")
    case = case.removeprefix("batch-") if batch else case.removeprefix("shell-")
    paths = [str(fixture["home"] / name) for name in ("one", "two")]
    policy = main._ResidentHookPolicy(main._load_hook_client_module())
    invalidated = []
    cache = SimpleNamespace(
        invalidate=lambda path: invalidated.append(path),
        invalidate_all=lambda: invalidated.append("all") if shell_event else pytest.fail("known paths must retain target scope"),
    )
    received, release = threading.Event(), threading.Event()
    notices = []
    callback_errors = []

    class Server(HookIpcServer):
        def _on_native_mutation(self, request):
            notices.append(request)
            try:
                super()._on_native_mutation(request)
            except Exception as exc:
                callback_errors.append(repr(exc))
                raise
            finally:
                received.set()

    class NoReply(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            raw = b""
            while not raw.endswith(b"\n") and len(raw) < 65536:
                chunk = self.request.recv(65536 - len(raw))
                if not chunk:
                    break
                raw += chunk
            notices.append(json.loads(raw))
            received.set()
            assert release.wait(10)

    server = Server(
        lambda *args: pytest.fail("synchronous request or policy replay"),
        native_mutation_decide=lambda kind, payload, deadline: main._resident_hook_decision(
            kind, payload, deadline=deadline, policy=policy, segment_cache=cache,
        ),
    )
    server.start()
    stalled = None
    stalled_thread = None
    try:
        path = fixture["registry"] / "status-monitor.lock"
        discovery = {**json.loads(path.read_text()), **server.rendezvous()}
        if case == "old-provider":
            if shell_event:
                discovery["hook_capabilities"].remove("shell-hook-mutation-v1")
            elif batch:
                discovery["hook_capabilities"].remove("native-hook-batch-mutation-v1")
            else:
                discovery.pop("hook_capabilities")
        elif case == "wrong-generation":
            discovery["hook_generation"] = "b" * 32
        elif case == "closed":
            server.close_admission()
        elif case == "no-reply":
            stalled = socketserver.ThreadingTCPServer(("127.0.0.1", 0), NoReply)
            stalled_thread = threading.Thread(target=stalled.serve_forever)
            stalled_thread.start()
            discovery["hook_endpoint"] = f"127.0.0.1:{stalled.server_address[1]}"
        path.write_text(json.dumps(discovery), encoding="utf-8")
        if case == "missing-discovery":
            path.unlink()
        args = {"edits": [{"path": item} for item in paths]}
        tool = "multi_edit"
        if case == "patch":
            tool = "apply_patch"
            args = {"input": f"*** Update File: {paths[0]}\n*** Move to: {paths[1]}\n"}
        elif case == "oversized":
            args["content"] = "x" * 65536
        payload = {**fixture["payload"], "toolName": tool, "toolArgs": args, "toolResult": "not transmitted"}
        if batch:
            payload.pop("toolName")
            payload.pop("toolArgs")
            payload["workingDirectory"] = payload["cwd"]
            payload["toolCalls"] = [
                {"id": "read", "name": "view", "args": {"path": "not-a-write"}},
                {"id": "write", "name": "multi_edit", "args": args},
                {"name": "apply_patch", "args": f"*** Update File: {paths[0]}\n*** Move to: {paths[1]}\n"},
                {"name": "edit", "args": json.dumps({"path": paths[1]})},
            ]
            if case == "reads":
                payload["toolCalls"] = payload["toolCalls"][:1]
            elif case == "shell-metadata":
                payload["toolName"] = "powershell"
        elif case == "structured":
            payload["workingDirectory"] = payload["cwd"]
            payload["toolArgs"] = {}
            payload["tool_input"] = args
        if shell_event:
            args = {"command": "git -c core.quotepath=false --no-pager commit -m test"}
            if case == "read":
                args = {"command": "git --no-pager status --short"}
            elif case == "pull":
                args = {"command": "git pull --ff-only"}
            elif case == "unknown-args":
                args = {"command": ["not-a-command"]}
            elif case == "oversized":
                args["padding"] = "x" * 65536
            elif case == "assignment":
                args = {"command": "$result = git -c core.quotepath=false commit -m test"}
            elif case == "literal-assignment":
                args = {"command": "$result = 'git commit -m test'"}
            elif case == "read-sed":
                args = {"command": "sed -n '1,100p' file.py"}
            elif case == "quoted-parentheses":
                args = {"command": 'Write-Output "' + "(" * 20000 + '"'}
            elif case == "control-block":
                args = {"command": "& { git commit -m test }"}
            elif case == "conditional":
                args = {"command": "if ($true) { Set-Content file value }"}
            elif case == "bash-assignment":
                args = {"command": 'result="$(git commit -m test)"'}
            elif case == "typed-assignment":
                args = {"command": "[string]$result = git commit -m test"}
            elif case == "pipeline-block":
                args = {"command": "1 | % { Set-Content file value }"}
            elif case == "invoke-block":
                args = {"command": "Invoke-Command -ScriptBlock { Set-Content file value }"}
            elif case == "literal-scriptblock":
                args = {"command": "Write-Output 'Invoke-Command -ScriptBlock { Set-Content file value }'"}
            elif case == "read-bracket-path":
                args = {"command": "Get-Content -LiteralPath file[ab]"}
            elif case == "read-type-call":
                args = {"command": "[IO.File]::ReadAllText('file')"}
            elif case == "sed-cluster":
                args = {"command": "sed -sni s/a/b/ file"}
            elif case == "git-output":
                args = {"command": "git diff --output=report.txt"}
            elif case == "dot-write":
                args = {"command": ". Set-Content file value"}
            elif case == "dot-read":
                args = {"command": ". git status --short"}
            elif case == "dot-assignment":
                args = {"command": "$result = . git commit -m test"}
            elif case == "evaluation":
                args = {"command": "Invoke-Expression 'Set-Content file value'"}
            elif case == "module-write":
                args = {"command": r"Microsoft.PowerShell.Management\Set-Content file value"}
            elif case == "module-read":
                args = {"command": r"Microsoft.PowerShell.Management\Get-Content file"}
            elif case == "quoted-evaluation":
                args = {"command": "Write-Output \"Invoke-Expression 'Set-Content file value'\""}
            elif case == "read-select-string":
                args = {"command": "Select-String -Pattern error -Path file"}
            elif case == "read-select-object":
                args = {"command": "Select-Object -Property Name"}
            elif case == "read-expansion":
                args = {"command": "Write-Output ${env:USERPROFILE}"}
            elif case == "read-assignment":
                args = {"command": "$copy = $value"}
            payload.update(toolName="powershell", toolArgs=args)
            if case == "bash-assignment":
                payload["toolName"] = "bash"
            if case == "json-args":
                payload["toolArgs"] = json.dumps(args)
            elif case == "raw-args":
                payload["toolArgs"] = args["command"]
            elif case == "fallback-args":
                payload.update(toolArgs={}, tool_input=args, workingDirectory=payload["cwd"])
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File", str(fixture["harness"])],
            input=json.dumps(payload), text=True, capture_output=True, timeout=10,
            env=fixture["env"], **no_window_kwargs(),
        )
        assert result.returncode == 0 and not result.stdout, result.stderr
        assert result.stderr.startswith("entry_seconds="), result.stderr
        print(f"{shell}/{case}: {result.stderr.strip()}")
        if case == "missing-discovery":
            assert not fixture["sidecar"].exists()
        else:
            assert json.loads(fixture["sidecar"].read_text())["count"] == 1
        if case in {"old-provider", "missing-discovery", "oversized"}:
            assert not notices and not server.has_recent_hook_demand()
        elif case in {"wrong-generation", "closed"} and not received.wait(2):
            assert not notices
        else:
            assert received.wait(2), f"arrived={bool(notices)}, callback_errors={callback_errors}"
            assert len(notices) == 1
            expected = {
                key: value for key, value in payload.items()
                if key != "toolResult" and not (batch and key == "toolName")
            }
            assert notices[0]["payload"] == expected
            assert notices[0]["kind"] == (
                "shellToolMutation" if shell_event else "nativeToolBatchMutation" if batch else "nativeToolMutation"
            )
        assert callback_errors == []
        writes = case in {"write", "json-args", "raw-args", "fallback-args", "unknown-args", "pull", "assignment", "control-block", "conditional", "bash-assignment", "typed-assignment", "pipeline-block", "invoke-block", "sed-cluster", "git-output", "dot-write", "dot-assignment", "evaluation", "module-write"}
        assert invalidated == (["all"] if shell_event and writes else paths if case in {"structured", "patch", "shell-metadata"} else [])
        assert server.has_recent_hook_demand() is (writes or case in {
            "structured", "patch", "reads", "read", "literal-assignment", "read-sed",
            "quoted-parentheses", "shell-metadata",
            "literal-scriptblock",
            "read-bracket-path", "read-type-call",
            "dot-read",
            "module-read", "quoted-evaluation",
            "read-select-string", "read-select-object", "read-expansion", "read-assignment",
        })
    finally:
        release.set()
        if stalled is not None:
            stalled.shutdown()
            stalled.server_close()
            stalled_thread.join(timeout=2)
            assert not stalled_thread.is_alive()
        server.close()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows shell read consumer")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", [
    "changed-partial", "failed", "later-publication", "missing-row", "missing-locator", "root-prefix",
])
def test_registered_late_guidance_needs_no_python_or_resident(prepared_read_event, monkeypatch, shell, case):
    from agent_procutil import no_window_kwargs
    from agent_worktrees import local_cache_worker

    fixture = prepared_read_event
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    root = Path(fixture["payload"]["cwd"])
    monkeypatch.setenv("USERPROFILE", str(fixture["home"]))
    monkeypatch.setattr(Path, "home", lambda: fixture["home"])
    local_cache_worker.watch_completion(root, "prepared", initial_revision="a" * 32, started_at=1)
    watch_path = fixture["session"] / "local-guidance-watch.json"
    watch = json.loads(watch_path.read_text())
    if case == "root-prefix":
        root = Path(root.anchor)
        watch["root"] = str(root)
        watch_path.write_text(json.dumps(watch))
    metadata = Path(watch["metadata"])
    metadata.mkdir(parents=True, exist_ok=True)
    status = "partial" if case == "changed-partial" else "failed" if case == "failed" else "ready"
    result = {
        "status": status, "restart_required": case != "later-publication",
        "publication_revision": "a" * 32 if case == "later-publication" else "b" * 32,
    }
    (metadata / "request.json").write_text(json.dumps({
        "version": 1, "root": str(root), "phase": "done", "completed_at": 2, "result": result,
    }))
    (metadata / "publication.json").write_text(json.dumps({
        "version": 1, "root": str(root), "revision": "b" * 32,
    }))
    fixture["env"]["AGENT_WORKTREES_NUDGE"] = "off"
    if case == "missing-row":
        fixture["facts"].unlink()
    elif case == "missing-locator":
        binding_path = fixture["session"] / "worktree-binding.json"
        binding = json.loads(binding_path.read_text())
        binding.pop("hookFacts")
        binding_path.write_text(json.dumps(binding))
    else:
        heads = {"worktree": {**fixture["record_heads"]["worktree"], "head_session": None}}
        fixture["runtime"].publish(
            "example", [fixture["row"]], observed_at=2, record_heads=heads,
        )

    def invoke():
        response = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File", str(fixture["harness"])],
            input=json.dumps(fixture["payload"]), text=True, capture_output=True, timeout=10,
            env=fixture["env"], **no_window_kwargs(),
        )
        assert response.returncode == 0, response.stderr
        assert response.stderr.startswith("entry_seconds="), response.stderr
        return json.loads(response.stdout) if response.stdout else {}

    notice = invoke()["additionalContext"]
    assert "Restart/new context is required" in notice
    assert "not proof of model admission" in notice
    assert ("bind-session" in notice) is (case not in {"missing-row", "missing-locator"})
    if case == "changed-partial":
        assert "partially refreshed" in notice and "Retain reviewed fallback" in notice
    elif case == "failed":
        assert "failed" in notice and "repair" in notice
    elif case == "later-publication":
        assert "reprepare" in notice
    assert not watch_path.exists()
    assert not fixture["sidecar"].exists()
    assert invoke() == {}


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows late-guidance consumer")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", ["live", "absent", "invalid-owner", "old-result", "oversized-request"])
def test_registered_late_guidance_retains_pending_and_unknown_watch(
    prepared_read_event, monkeypatch, shell, case,
):
    from agent_procutil import no_window_kwargs
    from agent_worktrees import local_cache_worker

    fixture = prepared_read_event
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    root = Path(fixture["payload"]["cwd"])
    monkeypatch.setattr(Path, "home", lambda: fixture["home"])
    local_cache_worker.watch_completion(root, "prepared", initial_revision="a" * 32, started_at=2)
    path = fixture["session"] / "local-guidance-watch.json"
    original = path.read_bytes()
    metadata = Path(json.loads(original)["metadata"])
    metadata.mkdir(parents=True, exist_ok=True)
    owner = {"pid": os.getpid(), "start_time": "unknown-but-live"}
    if case == "absent":
        # A waited native child is absent, not a guessed unused PID.
        with subprocess.Popen(
            [sys.executable, "-c", "pass"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **no_window_kwargs(),
        ) as child:
            child.communicate(timeout=10)
            assert child.returncode == 0
            owner["pid"] = child.pid
    elif case == "invalid-owner":
        owner["pid"] = True
    state = {"version": 1, "root": str(root), "phase": "running", "owner": owner}
    if case == "old-result":
        state.update(phase="done", completed_at=1, result={"status": "ready"})
    request_path = metadata / "request.json"
    request_path.write_text(json.dumps(state))
    if case == "oversized-request":
        request_path.write_text('{"padding":"' + "x" * 262144 + '"}')
    response = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(fixture["harness"])],
        input=json.dumps(fixture["payload"]), text=True, capture_output=True, timeout=10,
        env=fixture["env"], **no_window_kwargs(),
    )
    assert response.returncode == 0, response.stderr
    assert response.stderr.startswith("entry_seconds="), response.stderr
    actual = json.loads(response.stdout) if response.stdout else {}
    if case == "absent":
        assert "failed" in actual["additionalContext"]
        assert not path.exists()
        assert not fixture["sidecar"].exists()
    else:
        assert actual == {}
        assert path.read_bytes() == original
        assert json.loads(fixture["sidecar"].read_text())["count"] == 1


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows late-guidance parity")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("status", ["ready", "partial", "skipped", "timeout"])
def test_shell_late_guidance_unchanged_matches_original_consumer(
    prepared_read_event, monkeypatch, shell, status,
):
    from agent_procutil import no_window_kwargs
    from agent_worktrees import local_cache_worker
    from test_nudge_status import nudge

    fixture = prepared_read_event
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    root = Path(fixture["payload"]["cwd"])
    monkeypatch.setattr(Path, "home", lambda: fixture["home"])
    local_cache_worker.watch_completion(root, "prepared", initial_revision="a" * 32, started_at=1)
    path = fixture["session"] / "local-guidance-watch.json"
    snapshot = path.read_bytes()
    metadata = Path(json.loads(snapshot)["metadata"])
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / "request.json").write_text(json.dumps({
        "version": 1, "root": str(root), "phase": "done", "completed_at": 2,
        "result": {"status": status, "publication_revision": "a" * 32},
    }))
    expected = nudge._late_guidance_notice(fixture["payload"], fixture["home"])
    assert expected and not path.exists()
    path.write_bytes(snapshot)
    fixture["env"]["AGENT_WORKTREES_NUDGE"] = "off"
    response = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(fixture["harness"])],
        input=json.dumps(fixture["payload"]), text=True, capture_output=True, timeout=10,
        env=fixture["env"], **no_window_kwargs(),
    )
    assert response.returncode == 0, response.stderr
    assert response.stderr.startswith("entry_seconds="), response.stderr
    assert json.loads(response.stdout) == {"additionalContext": expected}
    assert not path.exists()
    assert not fixture["sidecar"].exists()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name != "nt", reason="Native competing Windows watch consumers")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("second_owner", ["shell", "python"])
def test_registered_late_guidance_has_one_competing_consumer(
    prepared_read_event, monkeypatch, shell, second_owner,
):
    from agent_procutil import no_window_kwargs
    from agent_worktrees import local_cache_worker

    fixture = prepared_read_event
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    root = Path(fixture["payload"]["cwd"])
    monkeypatch.setattr(Path, "home", lambda: fixture["home"])
    local_cache_worker.watch_completion(root, "prepared", started_at=1)
    watch_path = fixture["session"] / "local-guidance-watch.json"
    metadata = Path(json.loads(watch_path.read_text())["metadata"])
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / "request.json").write_text(json.dumps({
        "version": 1, "root": str(root), "phase": "done", "completed_at": 2,
        "result": {"status": "partial"},
    }))
    scripts = fixture["harness"].parent / "payload" / "scripts"
    with (scripts / "hook-facts.ps1").open("a", encoding="utf-8") as stream:
        stream.write("""
$script:originalWatchConsumer = ${function:Remove-PostToolLateWatch}
function Remove-PostToolLateWatch {
    param([string]$Path, [string]$Snapshot)
    [IO.File]::WriteAllText(
        [IO.Path]::Combine($env:LATE_TEST_CONTROL, $env:LATE_TEST_ROLE), 'ready')
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while (-not [IO.File]::Exists([IO.Path]::Combine($env:LATE_TEST_CONTROL, 'go'))) {
        if ($clock.Elapsed.TotalSeconds -ge 6) { throw 'watch rendezvous timed out' }
        [Threading.Thread]::Sleep(5)
    }
    & $script:originalWatchConsumer $Path $Snapshot
}
""")
    driver = fixture["home"] / "competing-watch.py"
    driver.write_text("""
import importlib.util, json, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("competing_nudge", sys.argv[1])
nudge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nudge)
original = nudge._consume_late_watch
def consume(path, snapshot):
    control = Path(os.environ["LATE_TEST_CONTROL"])
    (control / os.environ["LATE_TEST_ROLE"]).write_text("ready")
    end = time.monotonic() + 6
    while not (control / "go").exists():
        if time.monotonic() >= end:
            raise RuntimeError("watch rendezvous timed out")
        time.sleep(0.005)
    return original(path, snapshot)
nudge._consume_late_watch = consume
notice = nudge._late_guidance_notice(json.load(sys.stdin), Path(sys.argv[2]))
print(json.dumps({"additionalContext": notice} if notice else {}))
""", encoding="utf-8")
    control = fixture["home"] / "watch-control"
    control.mkdir()
    fixture["harness"].write_text(
        "[IO.File]::WriteAllText([IO.Path]::Combine("
        "$env:LATE_TEST_CONTROL, $env:LATE_TEST_ROLE + '-started'), 'ready')\n"
        + fixture["harness"].read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with driver.open("r+", encoding="utf-8") as stream:
        body = stream.read()
        stream.seek(0)
        stream.write(
            "import os\nfrom pathlib import Path\n"
            "(Path(os.environ['LATE_TEST_CONTROL']) / "
            "(os.environ['LATE_TEST_ROLE'] + '-started')).write_text('ready')\n"
            + body
        )
    processes = []
    deadlines = {}
    try:
        for role in ("first", "second"):
            env = dict(
                fixture["env"], LATE_TEST_CONTROL=str(control), LATE_TEST_ROLE=role,
                AGENT_WORKTREES_NUDGE="off",
            )
            command = [executable, "-NoProfile", "-NonInteractive", "-File", str(fixture["harness"])]
            if role == "second" and second_owner == "python":
                command = [
                    sys.executable, str(driver), str(PLUGIN / "scripts" / "nudge_status.py"),
                    str(fixture["home"]),
                ]
            process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env, **no_window_kwargs(),
            )
            processes.append(process)
            deadlines[process.pid] = time.monotonic() + 10
        for suffix in ("-started", ""):
            end = min(time.monotonic() + 6, *deadlines.values())
            while not all((control / (role + suffix)).exists() for role in ("first", "second")):
                if time.monotonic() >= end or any(process.poll() is not None for process in processes):
                    diagnostics = []
                    for role, process in zip(("first", "second"), processes):
                        code = process.poll()
                        if code is None:
                            process.kill()
                        stdout, stderr = process.communicate(timeout=10)
                        diagnostics.append({
                            "role": role, "ready": (control / (role + suffix)).exists(),
                            "exit_before_cleanup": code, "stdout": stdout, "stderr": stderr,
                        })
                    pytest.fail(f"watch rendezvous stage {suffix or 'evaluated'}: {diagnostics}")
                time.sleep(0.005)
            if suffix:
                for process in processes:
                    process.stdin.write(json.dumps(fixture["payload"]))
                    process.stdin.close()
                    process.stdin = None
        (control / "go").write_text("go")
        notices = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=max(0.01, deadlines[process.pid] - time.monotonic()))
            assert process.returncode == 0, stderr
            assert not stderr or stderr.startswith("entry_seconds="), stderr
            notices.append(json.loads(stdout) if stdout else {})
        assert sum("additionalContext" in notice for notice in notices) == 1, notices
        assert "incomplete" in next(notice["additionalContext"] for notice in notices if notice)
        assert not watch_path.exists()
        assert not list(watch_path.parent.glob("local-guidance-watch.json.consuming-*"))
        assert not fixture["sidecar"].exists()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="Windows rename must not overwrite a newer watch")
@pytest.mark.parametrize("newer_during_restore", [False, True])
def test_python_late_watch_claim_preserves_replacement(tmp_path, monkeypatch, newer_during_restore):
    from test_nudge_status import nudge

    path = tmp_path / "local-guidance-watch.json"
    original = b'{"version":1,"started_at":1}'
    replacement = b'{"version":1,"started_at":2}'
    newest = b'{"version":1,"started_at":3}'
    path.write_bytes(original)
    rename = Path.rename

    def replace_before_claim(source, target):
        if source == path:
            path.write_bytes(replacement)
        elif target == path and newer_during_restore:
            path.write_bytes(newest)
        return rename(source, target)

    monkeypatch.setattr(Path, "rename", replace_before_claim)
    assert not nudge._consume_late_watch(path, original)
    assert path.read_bytes() == (newest if newer_during_restore else replacement)
    assert not list(tmp_path.glob("local-guidance-watch.json.consuming-*"))


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows watch replacement")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("newer_during_restore", [False, True])
def test_shell_late_watch_claim_preserves_replacement(tmp_path, shell, newer_during_restore):
    from agent_procutil import no_window_kwargs

    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    watch = tmp_path / "local-guidance-watch.json"
    watch.write_text('{"version":1,"started_at":2}', encoding="utf-8")
    script = """
. $env:TEST_MANAGED_SCRIPT
. $env:TEST_FACTS_SCRIPT
$script:originalRead = ${function:Read-PostToolBoundedJson}
function Read-PostToolBoundedJson {
    param([string]$Path, [int]$Limit, [ref]$Snapshot)
    if ($env:TEST_REPLACE -eq 'yes') {
        [IO.File]::WriteAllText($env:TEST_WATCH, '{"version":1,"started_at":3}')
    }
    & $script:originalRead $Path $Limit $Snapshot
}
$consumed = Remove-PostToolLateWatch $env:TEST_WATCH '{"version":1,"started_at":1}'
[Console]::Write((ConvertTo-Json -InputObject @{ consumed = $consumed } -Compress))
"""
    response = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=10,
        env=dict(
            os.environ, TEST_MANAGED_SCRIPT=str(PLUGIN / "scripts" / "managed-session.ps1"),
            TEST_FACTS_SCRIPT=str(PLUGIN / "scripts" / "hook-facts.ps1"),
            TEST_WATCH=str(watch), TEST_REPLACE="yes" if newer_during_restore else "no",
        ), **no_window_kwargs(),
    )
    assert response.returncode == 0, response.stderr
    assert not response.stderr
    assert json.loads(response.stdout) == {"consumed": False}
    assert json.loads(watch.read_text())["started_at"] == (3 if newer_during_restore else 2)
    assert not list(tmp_path.glob("local-guidance-watch.json.consuming-*"))


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name != "nt", reason="Native overlapping stale watch transactions")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("second_owner", ["shell", "python"])
def test_overlapping_stale_claims_preserve_latest_publication(tmp_path, shell, second_owner):
    from agent_procutil import no_window_kwargs

    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    watch = tmp_path / "local-guidance-watch.json"
    watch.write_text('{"started_at":2}', encoding="utf-8")
    shell_driver = tmp_path / "stale-watch.ps1"
    shell_driver.write_text("""
. $env:TEST_MANAGED_SCRIPT
. $env:TEST_FACTS_SCRIPT
$script:originalRead = ${function:Read-PostToolBoundedJson}
function Read-PostToolBoundedJson {
    param([string]$Path, [int]$Limit, [ref]$Snapshot)
    $control = $env:TEST_CONTROL
    [IO.File]::WriteAllText([IO.Path]::Combine($control, $env:TEST_ROLE + '-claimed'), 'ready')
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while (-not [IO.File]::Exists([IO.Path]::Combine($control, $env:TEST_ROLE + '-go'))) {
        if ($clock.Elapsed.TotalSeconds -ge 6) { throw 'stale claim rendezvous timed out' }
        [Threading.Thread]::Sleep(5)
    }
    & $script:originalRead $Path $Limit $Snapshot
}
$consumed = Remove-PostToolLateWatch $env:TEST_WATCH $env:TEST_SNAPSHOT
[Console]::Write((ConvertTo-Json -InputObject @{ consumed = $consumed } -Compress))
""", encoding="utf-8")
    python_driver = tmp_path / "stale-watch.py"
    python_driver.write_text("""
import importlib.util, json, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("stale_nudge", sys.argv[1])
nudge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nudge)
watch = Path(os.environ["TEST_WATCH"])
control = Path(os.environ["TEST_CONTROL"])
role = os.environ["TEST_ROLE"]
rename = Path.rename
def paused_rename(source, target):
    result = rename(source, target)
    if source == watch:
        (control / (role + "-claimed")).write_text("ready")
        end = time.monotonic() + 6
        while not (control / (role + "-go")).exists():
            if time.monotonic() >= end:
                raise RuntimeError("stale claim rendezvous timed out")
            time.sleep(0.005)
    return result
Path.rename = paused_rename
print(json.dumps({"consumed": nudge._consume_late_watch(
    watch, os.environ["TEST_SNAPSHOT"].encode("utf-8"))}))
""", encoding="utf-8")
    processes = []

    def start(role, snapshot):
        command = [executable, "-NoProfile", "-NonInteractive", "-File", str(shell_driver)]
        if role == "second" and second_owner == "python":
            command = [sys.executable, str(python_driver), str(PLUGIN / "scripts" / "nudge_status.py")]
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=dict(
                os.environ, TEST_MANAGED_SCRIPT=str(PLUGIN / "scripts" / "managed-session.ps1"),
                TEST_FACTS_SCRIPT=str(PLUGIN / "scripts" / "hook-facts.ps1"),
                TEST_WATCH=str(watch), TEST_CONTROL=str(tmp_path), TEST_ROLE=role, TEST_SNAPSHOT=snapshot,
            ), **no_window_kwargs(),
        )
        processes.append(process)
        return process

    def wait_for(predicate):
        end = time.monotonic() + 6
        while not predicate():
            assert time.monotonic() < end, "stale claim control did not reach its boundary"
            time.sleep(0.005)

    def finish(process):
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert not stderr
        assert json.loads(stdout) == {"consumed": False}

    try:
        first = start("first", '{"started_at":1}')
        wait_for(lambda: (tmp_path / "first-claimed").exists())
        watch.write_text('{"started_at":3}', encoding="utf-8")
        second = start("second", '{"started_at":2}')
        wait_for(lambda: second.poll() is not None or (tmp_path / "second-claimed").exists())
        (tmp_path / "first-go").write_text("go")
        finish(first)
        (tmp_path / "second-go").write_text("go")
        finish(second)
        assert json.loads(watch.read_text()) == {"started_at": 3}
        assert not list(tmp_path.glob("local-guidance-watch.json.consuming-*"))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows shell read consumer")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", [
    "accounting", "note-reset", "bind-reminder", "quoted-bind", "distinct-home", "malformed-counter", "disabled", "terminal",
    "mixed-note", "missing-membership", "changed-input", "missing-locator", "wrong-incarnation",
    "wrong-workspace", "missing-discovery", "legacy-facts", "malformed-facts",
])
def test_registered_read_event_needs_no_python_or_resident(prepared_read_event, shell, case):
    from agent_procutil import no_window_kwargs

    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    fixture = prepared_read_event
    env, payload = fixture["env"], fixture["payload"]
    session, registry = fixture["session"], fixture["registry"]
    sidecar = fixture["sidecar"]

    def invoke(tool="view"):
        event = {**payload, "toolName": tool}
        started = time.monotonic()
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File", str(fixture["harness"])],
            input=json.dumps(event), text=True, capture_output=True, timeout=10,
            env=env, **no_window_kwargs(),
        )
        elapsed = time.monotonic() - started
        assert result.returncode == 0, result.stderr
        prefix = "entry_seconds="
        assert result.stderr.startswith(prefix) and result.stderr.count("\n") == 1, result.stderr
        entry = float(result.stderr[len(prefix):].strip())
        print(f"{shell}/{case}/{tool}: entry={entry:.4f}s process={elapsed:.4f}s")
        return json.loads(result.stdout) if result.stdout else {}

    if case == "mixed-note":
        from agent_worktrees import tracking
        from test_nudge_status import nudge

        assert invoke() == {}
        assert "T" not in fixture["row"]["status_note_at"]
        text = nudge.decide(
            {**payload, "toolName": "edit"}, env=env, home=fixture["home"],
            tracking_record=("worktree", fixture["tracking_path"]),
        )
        assert "2 tool call(s)" in text
        assert invoke() == {}
        assert json.loads(sidecar.read_text())["count"] == 1
        record = tracking._load_record_uncached(fixture["tracking_path"])
        record.status_note_at = "2026-11-05T20:01:00.123+05:30"
        tracking.save_record(record, fixture["tracking_path"])
        saved = tracking._load_record_uncached(fixture["tracking_path"])
        row = {**fixture["row"], "status_note_at": saved.status_note_at}
        fixture["runtime"].publish(
            "example", [row], observed_at=2, record_heads=fixture["record_heads"],
        )
        assert nudge.decide(
            {**payload, "toolName": "edit"}, env=env, home=fixture["home"],
            tracking_record=("worktree", fixture["tracking_path"]),
        ) is None
        assert json.loads(sidecar.read_text())["count"] == 1
        assert "2 tool call(s)" in invoke()["additionalContext"]
        return
    if case in {"accounting", "note-reset", "terminal"}:
        assert invoke() == {}
        assert json.loads(sidecar.read_text())["count"] == 1
        if case == "accounting":
            assert "2 tool call(s)" in invoke("rg")["additionalContext"]
            assert json.loads(sidecar.read_text())["count"] == 0
            assert invoke("glob") == {}
            assert json.loads(sidecar.read_text())["count"] == 1
            return
        row = dict(fixture["row"])
        if case == "note-reset":
            row["status_note_at"] = "2026-11-05T20:01:00Z"
        else:
            row["status"] = "completed"
        fixture["runtime"].publish(
            "example", [row], observed_at=2, record_heads=fixture["record_heads"],
        )
        assert invoke() == {}
        assert sidecar.exists() is (case == "note-reset")
        if sidecar.exists():
            assert json.loads(sidecar.read_text())["count"] == 1
        return
    if case in {"bind-reminder", "quoted-bind", "distinct-home", "malformed-counter"}:
        row = {**fixture["row"], "last_session_id": "resumable"}
        heads = {"worktree": {**fixture["record_heads"]["worktree"], "head_session": None}}
        fixture["runtime"].publish("example", [row], observed_at=2, record_heads=heads)
        if case == "malformed-counter":
            sidecar.parent.mkdir(parents=True)
            sidecar.write_text(json.dumps({
                "count": [], "window_start": 1, "seen_note_at": row["status_note_at"],
            }))
        reminder = invoke()["additionalContext"]
        assert "bind-session" in reminder
        if case == "quoted-bind":
            command = next(line.strip() for line in reminder.splitlines() if "agent-worktrees bind-session" in line)
            result = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-Command",
                 "function agent-worktrees { [Console]::Write((ConvertTo-Json -InputObject @($args) -Compress)) }; " + command],
                text=True, capture_output=True, timeout=10, env=env, **no_window_kwargs(),
            )
            assert result.returncode == 0, result.stderr
            assert not result.stderr
            assert json.loads(result.stdout) == [
                "bind-session", "--worktree-dir=" + payload["cwd"],
            ]
            return
        if case == "malformed-counter":
            assert invoke() == {}
            return
        second = invoke()["additionalContext"]
        assert "2 tool call(s)" in second and "bind-session" not in second
        if case == "distinct-home":
            assert json.loads(sidecar.read_text())["count"] == 0
            assert sidecar.parent == fixture["home"] / ".agent-worktrees" / "nudge-state"
            assert not (registry / "nudge-state").exists()
        return
    if case == "disabled":
        env["AGENT_WORKTREES_NUDGE"] = " OFF "
    elif case == "missing-membership":
        (session / "agent-worktrees" / "managed-session.json").unlink()
    elif case == "changed-input":
        (registry / "projects.yaml").write_text("projects: {}\n")
    elif case in {"missing-locator", "wrong-incarnation", "wrong-workspace"}:
        path = session / "worktree-binding.json"
        binding = json.loads(path.read_text())
        if case == "missing-locator":
            binding.pop("hookFacts")
        elif case == "wrong-incarnation":
            binding["creationNonce"] = "another-incarnation"
        else:
            binding["hookFacts"]["workspace"] = str(registry)
        path.write_text(json.dumps(binding))
    elif case == "missing-discovery":
        (registry / "status-monitor.lock").unlink()
    elif case == "legacy-facts":
        row = dict(fixture["row"])
        fixture["runtime"].publish("example", [row], observed_at=2)
    else:
        fixture["facts"].write_text('{"version":')
    assert invoke() == {}
    assert not sidecar.exists()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name != "nt", reason="Native shared shell/Python counter transaction")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("second_owner", ["shell", "python"])
def test_overlapping_read_and_edit_share_counter_transaction(
    prepared_read_event, shell, second_owner,
):
    from agent_procutil import no_window_kwargs

    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    fixture = prepared_read_event
    home = fixture["home"]
    first_ready, second_ready, release = [home / name for name in ("first-ready", "second-ready", "release")]
    support = fixture["harness"].parent / "payload" / "scripts" / "hook-facts.ps1"
    quote = lambda path: str(path).replace("'", "''")
    with support.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n$script:CounterRead = ${function:Read-PostToolBoundedJson}\n"
            "$script:CounterEnter = ${function:Enter-PostToolCounterLock}\n"
            "function Read-PostToolBoundedJson { param($Path, $Limit)\n"
            f" if ($env:COUNTER_TEST_ROLE -eq 'first' -and $Path -ceq '{quote(fixture['sidecar'])}') {{\n"
            f"  [IO.File]::WriteAllText('{quote(first_ready)}', 'locked')\n"
            "  $clock = [Diagnostics.Stopwatch]::StartNew()\n"
            f"  while (-not [IO.File]::Exists('{quote(release)}')) {{\n"
            "   if ($clock.Elapsed.TotalSeconds -ge 5) { throw 'release not observed' }\n"
            "   [Threading.Thread]::Sleep(5)\n"
            "  }\n }\n & $script:CounterRead $Path $Limit\n}\n"
            "function Enter-PostToolCounterLock { param($Path)\n"
            " if ($env:COUNTER_TEST_ROLE -eq 'second') {\n"
            f"  [IO.File]::WriteAllText('{quote(second_ready)}', 'entering')\n"
            " }\n & $script:CounterEnter $Path\n}\n"
        )
    env = {**fixture["env"], "AGENT_WORKTREES_NUDGE_CALLS": "999"}
    children = []

    def start(command, role):
        child = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env={**env, "COUNTER_TEST_ROLE": role}, **no_window_kwargs(),
        )
        children.append(child)
        child.stdin.write(json.dumps(fixture["payload"]))
        child.stdin.close()
        child.stdin = None
        return child

    def wait_for(path):
        until = time.monotonic() + 5
        while not path.exists():
            assert time.monotonic() < until, f"barrier not reached: {path.name}"
            time.sleep(0.005)

    command = [executable, "-NoProfile", "-NonInteractive", "-File", str(fixture["harness"])]
    try:
        first = start(command, "first")
        wait_for(first_ready)
        if second_owner == "python":
            script = (
                "import importlib.util,json,os,pathlib,sys;"
                f"s=importlib.util.spec_from_file_location('nudge',{str(PLUGIN / 'scripts' / 'nudge_status.py')!r});"
                "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                f"pathlib.Path({str(second_ready)!r}).write_text('entering');"
                f"result=m.decide(json.load(sys.stdin),env=os.environ,home=pathlib.Path({str(home)!r}),"
                f"tracking_record=('worktree',pathlib.Path({str(fixture['tracking_path'])!r})));"
                "assert result is None,result"
            )
            command = [sys.executable, "-c", script]
        second = start(command, "second")
        wait_for(second_ready)
        stdout, stderr = second.communicate(timeout=10)
        assert second.returncode == 0, (stdout, stderr)
        assert not stdout
        assert not fixture["sidecar"].exists()
        release.write_text("continue")
        def finish(child):
            stdout, stderr = child.communicate(timeout=10)
            assert child.returncode == 0, (stdout, stderr)
            assert not stdout
            assert not stderr or stderr.startswith("entry_seconds="), stderr
        finish(first)
        finish(start(command, "sequential"))
        assert json.loads(fixture["sidecar"].read_text())["count"] == 2
    finally:
        release.touch(exist_ok=True)
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=10)


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_SHELL_HOOK_INTEGRATION") != "1",
    reason="explicit native shell/daemon integration",
)
def test_real_powershell_roundtrip_and_validation_without_cli_children(tmp_path, admitted_protocol_script):
    from agent_procutil import no_window_kwargs
    from agent_worktrees.hook_ipc import HookIpcServer

    shell = shutil.which(os.environ.get("AGENT_WORKTREES_TEST_SHELL", "pwsh"))
    shell = shell or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell unavailable in explicit integration venue")
    seen = []
    def decide(kind, payload, deadline):
        seen.append((kind, payload))
        if payload.get("toolName") == "empty":
            return {}
        if payload.get("toolName") == "deny":
            return {"permissionDecision": "deny", "permissionDecisionReason": "policy"}
        if payload.get("toolName") == "manifest":
            return {"additionalContext": payload["text"]}
        return {"additionalContext": "resident"}

    server = HookIpcServer(decide)
    server.start()
    try:
        root = tmp_path / ".agent-worktrees"
        root.mkdir()
        (root / "status-monitor.lock").write_text(
            json.dumps(server.rendezvous()), encoding="utf-8"
        )
        harness = tmp_path / "hook.ps1"
        escaped = str(admitted_protocol_script).replace("'", "''")
        harness.write_text(
            f". '{escaped}'\n"
            "function Get-Command { throw 'a client/CLI child was requested' }\n"
            "Invoke-PostToolHook -Raw '{\"toolName\":\"view\"}'\n"
            "if ($PSVersionTable.PSVersion.Major -lt 6 -and "
            "(Get-Module Microsoft.PowerShell.Utility)) {\n"
            "  throw 'ordinary post-tool JSON loaded the utility module'\n"
            "}\n"
            "Invoke-PostToolHook -Raw '{\"toolName\":\"view\"}'\n"
            "Invoke-PostToolHook -Raw '{\"toolName\":\"empty\"}'\n"
            "$deny = Invoke-ResidentPostTool -Endpoint "
            "(Get-Content (Join-Path $env:USERPROFILE '.agent-worktrees/status-monitor.lock') "
            "-Raw | ConvertFrom-Json | ForEach-Object {"
            "$h=@{}; foreach($p in $_.PSObject.Properties){$h[$p.Name]=$p.Value};$h}) "
            "-PayloadJson '{\"toolName\":\"deny\"}'\n"
            "if (($deny | ConvertFrom-Json).permissionDecision -ne 'deny') { throw 'denial lost' }\n"
            "$bad = @{hook_transport='tcp';hook_endpoint='127.0.0.1:70000';hook_token='x'}\n"
            "if ($null -ne (Invoke-ResidentPostTool $bad '{}')) { throw 'bad port accepted' }\n"
            "$bad.hook_endpoint='example.invalid:80'\n"
            "if ($null -ne (Invoke-ResidentPostTool $bad '{}')) { throw 'remote endpoint accepted' }\n"
            "function Invoke-ResidentPostTool { return $null }\n"
            "function Get-Command { [pscustomobject]@{Source='Invoke-FakePython'} }\n"
            "function Invoke-FakePython {\n"
            "  if ($args[-1] -ne '--local-fallback') { throw 'request would be resent' }\n"
            "  [Console]::Out.Write('local-fallback')\n"
            "}\n"
            "Invoke-PostToolHook -Raw '{\"toolName\":\"view\"}'\n"
            "$env:COPILOT_EXTENSIONS_CONTEXT='selected-cell'\n"
            "function Invoke-FakePython {\n"
            "  if ($args[-1] -ne 'postToolUse') { throw 'cell bypassed validated client' }\n"
            "  [Console]::Out.Write('cell-client')\n"
            "}\n"
            "Invoke-PostToolHook -Raw '{\"toolName\":\"view\"}'\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["USERPROFILE"] = str(tmp_path)
        env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
            **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert result.stdout == (
            '{"additionalContext":"resident"}'
            '{"additionalContext":"resident"}'
            'local-fallbackcell-client'
        )
        assert seen == [
            ("postToolUse", {"toolName": "view"}),
            ("postToolUse", {"toolName": "view"}),
            ("postToolUse", {"toolName": "empty"}),
            ("postToolUse", {"toolName": "deny"}),
        ]
        bin_dir = root / "bin"
        bin_dir.mkdir()
        shutil.copyfile(SCRIPT, bin_dir / SCRIPT.name)
        shutil.copyfile(
            admitted_protocol_script.parent / "managed-session.ps1",
            bin_dir / "managed-session.ps1",
        )
        command = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))[
            "hooks"
        ]["postToolUse"][0]["powershell"]
        manifest_harness = tmp_path / "manifest.ps1"
        manifest_harness.write_text(
            "function Get-Command { throw 'manifest requested a child client' }\n"
            + command,
            encoding="utf-8",
        )
        payload = {"toolName": "manifest", "text": "\u00e9\u4e2d\U0001f680"}
        manifest_result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(manifest_harness)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
            **no_window_kwargs(),
        )
        assert manifest_result.returncode == 0, manifest_result.stderr
        assert manifest_result.stderr == ""
        assert json.loads(manifest_result.stdout) == {"additionalContext": payload["text"]}
        assert seen[-1] == ("postToolUse", payload)
        (bin_dir / SCRIPT.name).unlink()
        (bin_dir / "hook_client.py").write_text(
            "import json,sys\n"
            "payload=json.loads(sys.stdin.buffer.read().decode('utf-8'))\n"
            "sys.stdout.buffer.write(json.dumps(payload,ensure_ascii=False).encode('utf-8'))\n",
            encoding="utf-8",
        )
        python = sys.executable.replace("'", "''")
        manifest_harness.write_text(
            f"function Get-Command {{ [pscustomobject]@{{Source='{python}'}} }}\n" + command,
            encoding="utf-8",
        )
        fallback_result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(manifest_harness)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
            **no_window_kwargs(),
        )
        assert fallback_result.returncode == 0, fallback_result.stderr
        assert fallback_result.stderr == ""
        assert json.loads(fallback_result.stdout) == payload
    finally:
        server.close()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_SHELL_HOOK_INTEGRATION") != "1",
    reason="explicit native shell/daemon integration",
)
def test_real_shell_json_values_and_legacy_metadata_compatibility(tmp_path, admitted_protocol_script):
    from agent_procutil import no_window_kwargs
    from agent_worktrees.hook_ipc import HookIpcServer

    shell = shutil.which(os.environ.get("AGENT_WORKTREES_TEST_SHELL", "pwsh"))
    shell = shell or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell unavailable in explicit integration venue")

    values = {
        "array": [{}, [], None, True, False, 42, 1.25, "\u00e9\u4e2d\U0001f680"],
        "integer": 9223372036854775807,
        "iso": "2026-01-02T03:04:05Z",
        "epoch": "/Date(0)/",
        "text": "quote'\"\\\n\t",
    }
    raw_payloads = [
        json.dumps(values),
        '{"__type":null,"value":42}',
        '{"nested":{"__type":null,"value":42}}',
        r'{"epoch":"\/Date(0)\/","offset":"\/Date(0+0500)\/"}',
        '{"small":1e-100,"big":1.234567890123456789e30,'
        '"long":0.12345678901234567890123456789}',
        json.dumps({"nested": {"__type": "ordinary-data", "value": values}}),
    ]
    seen = []

    def decide(kind, payload, deadline):
        seen.append((kind, payload))
        return payload

    server = HookIpcServer(decide)
    server.start()
    try:
        root = tmp_path / ".agent-worktrees"
        root.mkdir()
        (root / "status-monitor.lock").write_text(
            json.dumps(server.rendezvous()), encoding="utf-8"
        )
        harness = tmp_path / "values.ps1"
        escaped = str(admitted_protocol_script).replace("'", "''")
        env = os.environ.copy()
        env["USERPROFILE"] = str(tmp_path)
        env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
        reference_harness = tmp_path / "legacy-json.ps1"
        expected_requests = []
        for payload in raw_payloads:
            raw = payload.replace("'", "''")
            harness.write_text(
                "$ErrorActionPreference='Stop'\n"
                f". '{escaped}'\n"
                "function Get-Command { throw 'a client/CLI child was requested' }\n"
                f"Invoke-PostToolHook -Raw '{raw}'\n",
                encoding="utf-8",
            )
            reference_harness.write_text(
                "$ErrorActionPreference='Stop'\n"
                "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false,$true)\n"
                f"$request=ConvertFrom-Json -InputObject '{raw}'\n"
                "$wire=ConvertTo-Json -InputObject $request -Depth 100 -Compress\n"
                "[Console]::Out.WriteLine($wire)\n"
                "$reply=ConvertFrom-Json -InputObject $wire\n"
                "[Console]::Out.WriteLine((ConvertTo-Json -InputObject $reply -Depth 100 -Compress))\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
                capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
                **no_window_kwargs(),
            )
            assert result.returncode == 0, result.stderr
            assert result.stderr == ""
            legacy = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-File", str(reference_harness)],
                capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
                **no_window_kwargs(),
            )
            assert legacy.returncode == 0, legacy.stderr
            assert legacy.stderr == ""
            expected = [json.loads(line) for line in legacy.stdout.splitlines()]
            assert len(expected) == 2
            assert json.loads(result.stdout) == expected[1], payload
            expected_requests.append(("postToolUse", expected[0]))
        assert seen == expected_requests
    finally:
        server.close()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_SHELL_HOOK_INTEGRATION") != "1",
    reason="explicit native shell/daemon integration",
)
def test_real_shell_rejects_invalid_or_unbounded_responses(tmp_path, admitted_protocol_script):
    from agent_procutil import no_window_kwargs

    shell = shutil.which(os.environ.get("AGENT_WORKTREES_TEST_SHELL", "pwsh"))
    shell = shell or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell unavailable in explicit integration venue")

    requests = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            request = json.loads(self.rfile.readline())
            mode = request["payload"]["mode"]
            requests.append(mode)
            responses = {
                "version": b'{"version":2,"result":{}}\n',
                "fallback": b'{"version":1,"fallback":true}\n',
                "shape": b'{"version":1,"result":[]}\n',
                "malformed": b'{"version":1,"result":{bad}}\n',
                "oversized": b"x" * 65536 + b"\n",
                "unterminated": b'{"version":1,"result":{}}',
            }
            if mode == "timeout":
                time.sleep(0.8)
                return
            self.wfile.write(responses[mode])

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        harness = tmp_path / "responses.ps1"
        root = tmp_path / ".agent-worktrees"
        root.mkdir()
        (root / "status-monitor.lock").write_text(json.dumps({
            "hook_transport": "tcp",
            "hook_endpoint": f"127.0.0.1:{server.server_address[1]}",
            "hook_token": "fixture",
        }), encoding="utf-8")
        escaped = str(admitted_protocol_script).replace("'", "''")
        harness.write_text(
            f". '{escaped}'\n"
            f"$endpoint=@{{hook_transport='tcp';hook_endpoint='127.0.0.1:{server.server_address[1]}';hook_token='fixture'}}\n"
            "foreach($mode in @('version','fallback','shape','oversized','unterminated')) {\n"
            "  $payload=ConvertTo-Json @{mode=$mode} -Compress\n"
            "  if($null -ne (Invoke-ResidentPostTool $endpoint $payload)){throw \"$mode accepted\"}\n"
            "}\n"
            "$timer=[Diagnostics.Stopwatch]::StartNew()\n"
            "try { $null=Invoke-ResidentPostTool $endpoint '{\"mode\":\"timeout\"}' } catch [IO.IOException] {}\n"
            "if($timer.Elapsed.TotalSeconds -gt 2){throw 'unbounded response wait'}\n"
            "function Get-Command { [pscustomobject]@{Source='Invoke-FakePython'} }\n"
            "function Invoke-FakePython {\n"
            "  if ($args[-1] -ne '--local-fallback') { throw 'request would be resent' }\n"
            "  [Console]::Out.Write('local-fallback')\n"
            "}\n"
            "Invoke-PostToolHook -Raw '{\"mode\":\"malformed\"}'\n"
            "$endpoint.installReceipt='another-cell'\n"
            "if($null -ne (Invoke-ResidentPostTool $endpoint '{}')){throw 'foreign cell accepted'}\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["USERPROFILE"] = str(tmp_path)
        env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
            capture_output=True, text=True, env=env, timeout=15, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "local-fallback"
        assert result.stderr == (
            "agent-worktrees postToolUse: resident request unavailable; using local advisory fallback\n"
        )
        assert requests.count("malformed") == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
