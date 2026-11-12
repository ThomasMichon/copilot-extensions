"""Actual native selection/credential/provider children against isolated fixtures."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import threading
import time

import pytest

from agent_pull_requests.native_report import NativeGitHubReport
from agent_pull_requests.report_client import ReportClient
from agent_pull_requests.report_read import ReportReader, ReportUnavailable

from test_report_read import reports as reports


@pytest.fixture
def native(tmp_path, monkeypatch):
    return native_fixture(tmp_path, monkeypatch)


def native_fixture(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("fixture executable is POSIX; native Windows qualification remains separate")
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins" / "agent-worktrees" / "src"))
    for source in (root / "libs").glob("*/src"):
        monkeypatch.syspath_prepend(str(source))
    home = tmp_path / "home"
    home.mkdir()
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(key, str(home))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_WORKTREES_PAYLOAD_ROOT"):
        monkeypatch.delenv(key, raising=False)
    state = home / ".agent-worktrees"
    state.mkdir()
    registry = state / "repos.yaml"
    registry.write_text('{"account_map":{"example":"selected"},"repos":{}}', encoding="utf-8")
    caller = tmp_path / "caller"
    caller.mkdir()
    path = tmp_path / "read.json"
    path.write_text(json.dumps({
        "repo_name": "caller",
        "repos": {
            "caller": {"anchor": str(caller)},
            "target": {"pr": {"repository": "example/project", "provider": "github"}},
        },
    }), encoding="utf-8")
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "calls.jsonl"
    executable = commands / "gh"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os,pathlib,sys,time\n"
        "args=sys.argv[1:]\n"
        "with open(os.environ['FIXTURE_LOG'],'a') as f:f.write(json.dumps(args)+'\\n')\n"
        "if args[:2]==['auth','token']:\n"
        " assert args[2:]==['--hostname','github.com','--user','selected']\n"
        " assert 'GH_TOKEN' not in os.environ\n"
        " if os.environ.get('FIXTURE_MISSING'):sys.exit(1)\n"
        " if os.environ.get('FIXTURE_SLEEP'):time.sleep(5)\n"
        " print('fixture-secret')\n"
        "else:\n"
        " assert args[:3]==['api','--hostname','github.com']\n"
        " assert not any(k in os.environ for k in ['GH_HOST','GH_REPO','GH_DEBUG','GH_HTTP_UNIX_SOCKET'])\n"
        " assert os.environ['GH_TOKEN']=='fixture-secret'\n"
        " assert 'GITHUB_TOKEN' not in os.environ\n"
        " if args[-1]=='https://api.github.com/user':\n"
        "  if os.environ.get('FIXTURE_ROTATE'):\n"
        "   p=pathlib.Path(os.environ['FIXTURE_REGISTRY']);p.write_text('{\"account_map\":{\"example\":\"replacement\"}}')\n"
        "  print('HTTP/2 200 OK\\r\\n\\r\\n'+json.dumps({'login':os.environ.get('FIXTURE_LOGIN','selected')}))\n"
        " elif args[3]=='https://api.github.com/graphql':\n"
        "  assert args[4]=='-f' and args[5].startswith('query=')\n"
        "  assert args[6:]==['-f','variables[owner]=example','-f','variables[name]=project','-F','variables[number]=7']\n"
        "  if os.environ.get('FIXTURE_NONZERO'):print('fixture-secret',file=sys.stderr);sys.exit(1)\n"
        "  if os.environ.get('FIXTURE_FAIL'):print('{\"errors\":[{\"message\":\"fixture-secret\"}]}');sys.exit(0)\n"
        "  body='body\\r\\ntext' if not os.environ.get('FIXTURE_LARGE') else 'λ\\r\\n'*int(os.environ.get('FIXTURE_THREAD_REPEATS','11000'))\n"
        "  print(json.dumps({'data':{'repository':{'pullRequest':{'reviewThreads':{'nodes':[{'path':'x','comments':{'nodes':[{'body':body,'author':{'login':'fixture'}}]}}]}}}}}))\n"
        " else:\n"
        "  assert args[3:]==['https://api.github.com/repos/example/project/pulls/7','-H','Accept: application/vnd.github.diff']\n"
        "  if os.environ.get('FIXTURE_FAIL'):print('fixture-secret',file=sys.stderr);sys.exit(1)\n"
        "  if os.environ.get('FIXTURE_NONZERO'):print('fixture-secret',file=sys.stderr);sys.exit(1)\n"
        "  if os.environ.get('FIXTURE_LARGE'):sys.stdout.buffer.write(b'x'*int(os.environ.get('FIXTURE_REPORT_BYTES','40000'))+'λ\\r\\ntail'.encode());sys.exit(0)\n"
        "  if os.environ.get('FIXTURE_ROTATE_AFTER_REPORT'):\n"
        "   p=pathlib.Path(os.environ['FIXTURE_REGISTRY']);p.write_text('{\"account_map\":{\"example\":\"replacement\"}}')\n"
        "  sys.stdout.buffer.write(b'diff --git a/x b/x\\r\\n+body\\rtail')\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FIXTURE_LOG", str(log))
    monkeypatch.setenv("FIXTURE_REGISTRY", str(registry))
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "GH_REPO", "GH_DEBUG", "GH_HTTP_UNIX_SOCKET"):
        monkeypatch.setenv(key, "hostile-ambient")
    monkeypatch.chdir(tmp_path)
    scope = {"provider": "github", "endpoint": "github.com", "account": "selected", "repo": "example/project"}
    owner = NativeGitHubReport(config_path=path, scope=scope, context_id="fixture-owner")
    active = [True]
    deadline = time.monotonic() + 30

    def remaining():
        if not active[0]:
            raise PermissionError("fixture-withdrawn")
        left = deadline - time.monotonic()
        if left <= 0:
            raise ReportUnavailable("fixture-timeout")
        return left

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return owner, remaining, calls, active, path


@pytest.mark.parametrize("operation", ["threads", "diff"])
def test_actual_native_children_keep_one_budget_and_use_pinned_reads(native, monkeypatch, operation):
    from agent_pull_requests import native_report
    from agent_worktrees import github_account_observation

    budgets = []
    for module in (native_report, github_account_observation):
        run = module.run_caller_owned

        def capture(*args, _run=run, **kwargs):
            budgets.append(kwargs["budget"])
            return _run(*args, **kwargs)

        monkeypatch.setattr(module, "run_caller_owned", capture)
    owner, remaining, calls, _, _ = native
    binding = owner.resolve(remaining)
    exit_code, text = binding.collect(operation, 7, remaining)
    report = json.loads(text)
    assert exit_code == 0 and report["complete"] is None
    assert len(budgets) > 5 and all(budget is budgets[0] for budget in budgets)
    assert calls()[0][:2] == ["auth", "token"] and calls()[1][-1].endswith("/user")
    assert len(calls()) == 3
    if operation == "threads":
        assert report["threads"][0]["comments"][0]["text"] == "body\r\ntext"
    else:
        assert report["diff"] == "diff --git a/x b/x\r\n+body\rtail"


def test_selected_executing_support_is_passive_and_refuses_prior_capture_api(native, monkeypatch):
    from agent_worktrees.github_account_observation import SelectedGitHubAccount

    owner, _, calls, _, _ = native
    owner.check_support()
    assert calls() == []
    monkeypatch.setattr(SelectedGitHubAccount, "capture", lambda self, stage, arguments: None)
    with pytest.raises(ValueError, match="native report APIs unsupported"):
        owner.check_support()
    assert calls() == []


@pytest.mark.parametrize("setting,value", [
    ("FIXTURE_LOGIN", "wrong-active"), ("FIXTURE_MISSING", "1"), ("FIXTURE_ROTATE", "1"),
])
def test_failed_account_or_replaced_selection_never_reads_bodies(native, monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    owner, remaining, calls, _, _ = native
    with pytest.raises(ReportUnavailable):
        owner.resolve(remaining)
    assert all("graphql" not in " ".join(call) and "/pulls/" not in " ".join(call) for call in calls())
    assert len(calls()) <= 2


def test_withdrawn_owner_cannot_start_a_later_report_process(native):
    owner, remaining, calls, active, _ = native
    binding = owner.resolve(remaining)
    before = calls()
    active[0] = False
    with pytest.raises(PermissionError):
        binding.collect("diff", 7, remaining)
    assert calls() == before


@pytest.mark.parametrize("operation", ["threads", "diff"])
def test_provider_failures_have_native_failure_exit_without_diagnostics(native, monkeypatch, operation):
    monkeypatch.setenv("FIXTURE_FAIL", "1")
    owner, remaining, _, _, _ = native
    binding = owner.resolve(remaining)
    exit_code, text = binding.collect(operation, 7, remaining)
    report = json.loads(text)
    assert exit_code == 1 and report["complete"] is False and report["supported"] is True
    assert report["error"] and "fixture-secret" not in text


@pytest.mark.parametrize("operation", ["threads", "diff"])
def test_nonzero_provider_child_remains_supported_native_error(native_gateway, monkeypatch, operation):
    monkeypatch.setenv("FIXTURE_NONZERO", "1")
    _, gateway, client, _ = native_gateway

    async def scenario():
        await client.capabilities()
        value = await client.read(operation, {"number": 7, "budget_seconds": 15})
        assert value["owner_exit"] == 1
        assert value["report"]["supported"] is True and value["report"]["complete"] is False
        assert value["report"]["error"] and "fixture-secret" not in json.dumps(value)
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


@pytest.fixture
def native_gateway(native, reports):
    store, gateway, reader, old_client, caller, _, _ = reports
    owner, _, _, _, _ = native
    scope = dict(owner.scope)
    from agent_pull_requests.watch_replay import ConsumerScope

    consumer = ConsumerScope(reader.consumer_scope.consumer, scope["account"], scope["repo"])
    binding = replace(old_client.binding, account=scope["account"])
    gateway.bindings = {
        (binding.principal, binding.target): consumer,
    }
    gateway.reports = {
        (binding.principal, binding.target): ReportReader(
            consumer, scope, owner.resolve, max_work_seconds=20,
        ),
    }
    client = ReportClient(caller, route_prefix="", binding=binding, scope=scope)
    return store, gateway, client, scope


def test_real_source_adapter_crosses_optional_gateway_client_without_retention(native_gateway):
    store, gateway, client, scope = native_gateway
    before = store.path.read_bytes()

    async def scenario():
        await client.capabilities()
        value = await client.read("diff", {"number": 7, "budget_seconds": 15})
        assert value["owner_exit"] == 0 and value["scope"] == scope
        assert value["report"]["diff"].endswith("\r\n+body\rtail")
        await gateway.drain(timeout=2)

    asyncio.run(scenario())
    assert store.path.read_bytes() == before
    assert "fixture-secret" not in store.path.read_text()


def test_credential_timeout_spends_the_original_configuration_budget(native, monkeypatch):
    monkeypatch.setenv("FIXTURE_SLEEP", "1")
    owner, _, calls, _, _ = native
    deadline = time.monotonic() + 2
    with pytest.raises(ReportUnavailable):
        owner.resolve(lambda: max(.001, deadline - time.monotonic()))
    assert time.monotonic() < deadline + 2
    assert len(calls()) <= 1


def test_oversized_native_capture_is_unavailable_not_an_empty_error_report(native, monkeypatch):
    monkeypatch.setenv("FIXTURE_LARGE", "1")
    owner, remaining, _, _, _ = native
    binding = owner.resolve(remaining)
    with pytest.raises(ReportUnavailable, match="transport unavailable"):
        binding.collect("diff", 7, remaining)


def test_case_equivalent_scope_preserves_configured_cli_store_spelling(native):
    owner, remaining, calls, _, path = native
    equivalent = NativeGitHubReport(
        config_path=path, scope={**owner.scope, "account": "SELECTED"}, context_id="equivalent",
    )
    binding = equivalent.resolve(remaining)
    assert binding.scope["account"] == "SELECTED"
    assert calls()[0][-1] == "selected"


def test_binding_change_after_native_read_prevents_gateway_publication(native_gateway, monkeypatch):
    from agent_pull_requests.watch_replay_client import ReplayClientError

    _, gateway, client, _ = native_gateway
    monkeypatch.setenv("FIXTURE_ROTATE_AFTER_REPORT", "1")

    async def scenario():
        await client.capabilities()
        with pytest.raises(ReplayClientError):
            await client.read("diff", {"number": 7, "budget_seconds": 15})
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize("disconnect", [True, False])
def test_delayed_native_cleanup_retains_original_gateway_task(native_gateway, monkeypatch, disconnect):
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_worktrees import github_account_observation
    from agent_pull_requests.watch_replay_client import ReplayClientError

    store, gateway, client, _ = native_gateway
    entered, release, settled = threading.Event(), threading.Event(), threading.Event()
    run = github_account_observation.run_caller_owned

    def cleanup(deadline):
        entered.set()
        if not release.wait(max(0, deadline - time.monotonic())):
            raise TimeoutError
        settled.set()

    def delayed(argv, **kwargs):
        if any("/pulls/" in argument for argument in argv):
            raise OwnedCleanupError(None, cleanup_retry=cleanup)
        return run(argv, **kwargs)

    monkeypatch.setattr(github_account_observation, "run_caller_owned", delayed)
    before = store.path.read_bytes()

    async def scenario():
        await client.capabilities()
        pending = asyncio.create_task(client.read("diff", {"number": 7, "budget_seconds": 15}))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            if disconnect:
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            assert len(gateway._tasks) == 1 and not settled.is_set()
            if disconnect:
                with pytest.raises(TimeoutError):
                    await gateway.drain(timeout=.01)
            else:
                assert not pending.done()
            assert len(gateway._tasks) == 1
        finally:
            release.set()
            results = await asyncio.gather(pending, return_exceptions=True)
            if not disconnect:
                assert isinstance(results[0], ReplayClientError)
            await gateway.drain(timeout=5)
        assert settled.is_set() and not gateway._tasks

    asyncio.run(scenario())
    assert store.path.read_bytes() == before


def test_publication_selection_cleanup_is_off_loop_and_keeps_request_custody(native_gateway, monkeypatch):
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_pull_requests import native_report
    from agent_pull_requests.watch_replay_client import ReplayClientError

    _, gateway, client, _ = native_gateway
    entered, release = threading.Event(), threading.Event()
    armed = [False]
    original_shield = asyncio.shield
    original_run = native_report.run_caller_owned
    loop_thread = threading.get_ident()

    async def prepared(awaitable):
        result = await original_shield(awaitable)
        if not isinstance(awaitable, asyncio.Task) and json.loads(result.body).get("operation") == "diff":
            armed[0] = True
        return result

    def cleanup(deadline):
        entered.set()
        if not release.wait(max(0, deadline - time.monotonic())):
            raise TimeoutError

    def delayed(argv, **kwargs):
        if armed[0]:
            assert threading.get_ident() != loop_thread
            raise OwnedCleanupError(None, cleanup_retry=cleanup)
        return original_run(argv, **kwargs)

    monkeypatch.setattr(asyncio, "shield", prepared)
    monkeypatch.setattr(native_report, "run_caller_owned", delayed)

    async def scenario():
        await client.capabilities()
        pending = asyncio.create_task(client.read("diff", {"number": 7, "budget_seconds": 15}))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            assert not pending.done() and len(gateway._tasks) == 1
            with pytest.raises(TimeoutError):
                await gateway.drain(timeout=.01)
            assert not pending.done()
        finally:
            release.set()
            results = await asyncio.gather(pending, return_exceptions=True)
            assert isinstance(results[0], ReplayClientError)
            await gateway.drain(timeout=5)

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["threads", "diff"])
@pytest.mark.parametrize("failure", ["cleanup", "capture"])
def test_selection_checkpoint_failure_cannot_be_swallowed_as_a_provider_report(
    native_gateway, native, monkeypatch, operation, failure,
):
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_pull_requests import native_report
    from agent_pull_requests.watch_replay_client import ReplayClientError
    from agent_worktrees.providers import github_report_capture

    _, gateway, client, _ = native_gateway
    inside, fired, settled = [False], [False], []
    original_run = native_report.run_caller_owned
    original_report = getattr(github_report_capture, operation)

    def run(argv, **kwargs):
        if inside[0] and not fired[0]:
            fired[0] = True
            if failure == "cleanup":
                raise OwnedCleanupError(None, cleanup_retry=lambda deadline: settled.append(True))
            raise RuntimeError("fixture-private-capture-failure")
        return original_run(argv, **kwargs)

    def report(*args, **kwargs):
        inside[0] = True
        try:
            return original_report(*args, **kwargs)
        finally:
            inside[0] = False

    monkeypatch.setattr(native_report, "run_caller_owned", run)
    monkeypatch.setattr(github_report_capture, operation, report)

    async def scenario():
        await client.capabilities()
        with pytest.raises(ReplayClientError) as caught:
            await client.read(operation, {"number": 7, "budget_seconds": 15})
        assert caught.value.http_status == 503
        await gateway.drain(timeout=2)

    asyncio.run(scenario())
    assert fired[0] and settled == ([True] if failure == "cleanup" else [])
    assert all("/pulls/" not in " ".join(call) and "graphql" not in " ".join(call) for call in native[2]())
