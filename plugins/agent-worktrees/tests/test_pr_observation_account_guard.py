"""Account evidence and material continuity at the actual typed/direct boundary."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent_worktrees import pr_cli, pr_credential_command, pr_inventory_cli
from agent_worktrees.providers import gitea
from agent_worktrees.providers.observation_credentials import (
    ObservationCredentialError, ObservationCredentials, with_http_profile,
)


@pytest.fixture
def native(tmp_path, monkeypatch):
    endpoint = "https://forge.example"
    config = tmp_path / "native.json"
    config.write_text(json.dumps({"repos": {"target": {"anchor": str(tmp_path), "pr": {
        "provider": "gitea", "api_base": endpoint, "repository": "owner/repo",
    }}}}), encoding="utf-8")
    state = SimpleNamespace(
        acquisitions=[], requests=[], actor='{"login":"Reader"}', status=200,
        change=lambda value, count: value, inventory_pages=1, review_forever=False,
        after_read=lambda: None, config=config,
    )

    def acquire(argv, **kwargs):
        state.acquisitions.append((argv, kwargs))
        value = {
            "schema": "fleet-manager.forge-credential", "version": 1,
            "token": "synthetic-private-material", "expires_at": time.time() + 60,
            "forge": {"provider": "gitea", "api_base": endpoint, "repository": "owner/repo",
                      "permission": "observe", "authentication": "bearer"},
        }
        value = state.change(value, len(state.acquisitions))
        if value is None:
            return subprocess.CompletedProcess(argv, 1, "", "synthetic-private-diagnostic")
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    def send(argv, **kwargs):
        state.requests.append((argv, kwargs))
        assert argv[0] == "curl" and "--retry" in argv and "--max-redirs" in argv
        assert kwargs["max_stdout_bytes"] <= 1_048_584
        assert "synthetic-private" not in repr(argv)
        url = next(part for part in argv if part.startswith("https://"))
        status = 200
        if url.endswith("/user"):
            body, status = state.actor, state.status
        elif "/pulls?" in url:
            page = int(url.split("page=")[1].split("&")[0])
            body = json.dumps([{"number": page, "state": "open"}]
                              if page <= state.inventory_pages else [])
        elif "/reviews?" in url:
            body = json.dumps([{"id": index, "state": "APPROVED", "user": {"login": "r"}}
                               for index in range(50)] if state.review_forever else [])
        elif url.endswith("/status"):
            body = '{"statuses":[],"state":"pending"}'
        else:
            body = '{"state":"open","head":{"sha":"abc"},"base":{"ref":"dev"}}'
        state.after_read()
        return subprocess.CompletedProcess(argv, 0, body + "\n" + str(status), "")

    monkeypatch.setattr(pr_credential_command, "run_caller_owned", acquire)
    monkeypatch.setattr(gitea, "run_cli", send)

    def invoke(operation="inventory", *, guarded=True, extra=()):
        args = [operation, "owner/repo"]
        if operation == "snapshot":
            args.append("7")
        args += ["--config", str(config), "--credential-command", json.dumps([sys.executable]),
                 "--http-profile", "direct-v1", "--json"]
        if guarded:
            args += ["--expected-account", "reader"]
        return pr_cli.cmd_pr_watch_dispatch([*args, *extra])

    state.invoke = invoke
    return state


def assert_refused_output(capsys) -> None:
    captured = capsys.readouterr()
    assert '"schema"' not in captured.out
    assert "synthetic-" not in captured.out + captured.err


@pytest.mark.parametrize("operation", ["inventory", "snapshot"])
@pytest.mark.parametrize("guarded", [True, False])
def test_native_parser_executes_opt_in_actor_get_without_changing_results(native, capsys, operation, guarded):
    assert native.invoke(operation, guarded=guarded) == 0
    result = json.loads(capsys.readouterr().out)
    assert "expected_account" not in result and "observation_account_guard" not in result
    urls = [next(part for part in argv if part.startswith("https://")) for argv, _ in native.requests]
    assert sum(url.endswith("/user") for url in urls) == int(guarded)
    assert len(urls) == len(native.acquisitions) == (2 if operation == "inventory" else 3) + int(guarded)
    if guarded:
        assert urls[0] == "https://forge.example/api/v1/user"
    if operation == "inventory":
        assert result["numbers"] == [1] and result["outcome"] == "complete"
    else:
        assert result["head_sha"] == "abc"
    assert "synthetic-private" not in repr(result)


@pytest.mark.parametrize("actor,status", [
    ('{"login":"other"}', 200), ('{}', 200), ('[]', 200), ('null', 200),
    ('{"login":null}', 200), ('{"login":1}', 200), ('{"login":""}', 200),
    ('{"login":" reader"}', 200), ('{"login":"reader","login":"reader"}', 200),
    ('{"login":"reader"} trailing', 200), ('{"login":"reader"}', 401),
    ('{"login":"reader"}', 403), ('{"login":"reader"}', 302),
    ('synthetic-private-response', 500),
])
@pytest.mark.parametrize("operation", ["inventory", "snapshot"])
def test_actor_denial_refuses_entire_operation_before_repository_reads(
    native, capsys, actor, status, operation,
):
    native.actor, native.status = actor, status
    assert native.invoke(operation) == (3 if operation == "snapshot" else 2)
    assert len(native.acquisitions) == len(native.requests) == 1
    assert_refused_output(capsys)


@pytest.mark.parametrize("kind", ["token", "binding", "withdraw", "expired"])
@pytest.mark.parametrize("operation", ["inventory", "snapshot"])
def test_changed_material_with_same_selected_binding_never_reaches_repository(
    native, capsys, kind, operation,
):
    def change(value, count):
        if count == 2:
            if kind == "token":
                value["token"] = "synthetic-other-account"
            elif kind == "binding":
                value["forge"]["api_base"] = "https://other.example"
            elif kind == "expired":
                value["expires_at"] = 0
            else:
                return None
        return value

    native.change = change
    assert native.invoke(operation) == (3 if operation == "snapshot" else 2)
    assert len(native.acquisitions) == 2 and len(native.requests) == 1
    assert_refused_output(capsys)


def test_inventory_does_not_report_partial_success_after_later_account_switch(native, capsys):
    def change(value, count):
        if count == 3:
            value["token"] = "synthetic-other-account"
        return value

    native.change = change
    assert native.invoke() == 2
    assert len(native.acquisitions) == 3 and len(native.requests) == 2
    assert_refused_output(capsys)


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_actor_get_is_inside_lower_snapshot_credential_ceiling(native, capsys, limit):
    assert native.invoke("snapshot", extra=("--max-credential-requests", str(limit))) == 3
    assert len(native.acquisitions) == len(native.requests) == limit
    assert_refused_output(capsys)


def test_actor_get_is_inside_existing_snapshot_ceiling(native, capsys):
    native.review_forever = True
    assert native.invoke("snapshot") == 3
    assert len(native.acquisitions) == len(native.requests) == 64
    assert_refused_output(capsys)


def test_guarded_inventory_adds_one_request_not_one_page(native, capsys):
    native.inventory_pages = 10
    assert native.invoke(extra=("--max-pages", "4")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["pages"] == 4 and result["numbers"] == [1, 2, 3, 4]
    assert len(native.acquisitions) == len(native.requests) == 5


@pytest.mark.parametrize("later_expiry", [1001, 1100])
def test_later_acquisition_cannot_extend_original_verified_deadline(monkeypatch, later_expiry):
    now = [1000]
    monkeypatch.setattr(time, "time", lambda: now[0])
    value = SimpleNamespace(
        token="synthetic", expires_at=1001, forge=SimpleNamespace(
            provider="gitea", api_base="https://forge.example", repository="owner/repo",
            permission="observe", authentication="bearer",
        ),
    )
    source = with_http_profile(lambda: deepcopy(value), "direct-v1", expected_account="reader")
    selected = ObservationCredentials(
        source, provider="gitea", api_base="https://forge.example", repository="owner/repo",
    )
    selected.acquire()
    now[0], value.expires_at = 1002, later_expiry
    with pytest.raises(ObservationCredentialError, match="expired"):
        selected.acquire()


def test_shorter_reported_deadline_is_retained_and_mutable_material_is_copied(monkeypatch):
    now = [1000]
    monkeypatch.setattr(time, "time", lambda: now[0])
    value = SimpleNamespace(
        token="synthetic", expires_at=1100, forge=SimpleNamespace(
            provider="gitea", api_base="https://forge.example", repository="owner/repo",
            permission="observe", authentication="bearer",
        ),
    )
    selected = ObservationCredentials(
        with_http_profile(lambda: value, "direct-v1", expected_account="reader"),
        provider="gitea", api_base="https://forge.example", repository="owner/repo",
    )
    pinned = selected.acquire()
    value.expires_at = 1001
    assert selected.acquire().expires_at == 1001
    assert pinned.expires_at == 1100
    now[0], value.expires_at = 1002, 1200
    with pytest.raises(ObservationCredentialError, match="expired"):
        selected.acquire()


@pytest.mark.parametrize("operation", ["inventory", "snapshot"])
@pytest.mark.parametrize("extra", [
    [], ["--token", "synthetic"], ["--credential-command", json.dumps([sys.executable])],
])
def test_missing_typed_direct_inputs_refuse_before_acquisition(monkeypatch, operation, extra):
    monkeypatch.setattr(pr_credential_command, "run_caller_owned",
                        lambda *_a, **_kw: pytest.fail("invalid guard acquired material"))
    args = [operation, "owner/repo", *(["7"] if operation == "snapshot" else []),
            "--expected-account", "reader", "--json", *extra]
    assert pr_cli.cmd_pr_watch_dispatch(args) == 2


@pytest.mark.parametrize("provider", ["github", "azure-devops"])
def test_unsupported_provider_guard_refuses_without_acquisition(provider):
    def acquire():
        pytest.fail("unsupported guard acquired material")
    source = with_http_profile(acquire, "direct-v1", expected_account="reader")
    with pytest.raises(ObservationCredentialError, match="unsupported"):
        ObservationCredentials(source, provider=provider, api_base="https://forge.example",
                               repository="owner/repo")


@pytest.mark.parametrize("operation", ["inventory", "snapshot"])
@pytest.mark.parametrize("provider", ["github", "azure-devops"])
def test_native_parser_unsupported_provider_refuses_without_acquisition(
    native, capsys, operation, provider,
):
    value = json.loads(native.config.read_text(encoding="utf-8"))
    value["repos"]["target"]["pr"]["provider"] = provider
    native.config.write_text(json.dumps(value), encoding="utf-8")
    assert native.invoke(operation) != 0
    assert not native.acquisitions and not native.requests
    assert_refused_output(capsys)


def test_guarded_inventory_keeps_absolute_deadline_after_actor_read(native, monkeypatch, capsys):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    native.after_read = lambda: now.__setitem__(0, 1061.0)
    assert native.invoke() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "unavailable" and not result["numbers"]
    assert len(native.acquisitions) == len(native.requests) == 1


@pytest.mark.timeout(60)
@pytest.mark.parametrize("operation", ["inventory", "snapshot"])
@pytest.mark.parametrize("rotate", [False, True])
def test_real_native_and_credential_children_keep_verified_material(
    tmp_path, operation, rotate,
):
    from agent_procutil.owned_process import run_caller_owned

    count, sent = tmp_path / "count", tmp_path / "sent"
    credential = tmp_path / "credential.py"
    credential.write_text(
        "import json,pathlib,time\n"
        f"p=pathlib.Path({str(count)!r})\n"
        "n=int(p.read_text())+1 if p.exists() else 1\np.write_text(str(n))\n"
        f"token='synthetic-fixture-'+str(n if {rotate!r} else 1)\n"
        "print(json.dumps({'schema':'fleet-manager.forge-credential','version':1,\n"
        "'token':token,'expires_at':time.time()+60,'forge':{'provider':'gitea',\n"
        "'api_base':'https://forge.example','repository':'owner/repo',\n"
        "'permission':'observe','authentication':'bearer'}}))\n",
        encoding="utf-8",
    )
    config = tmp_path / "native.json"
    config.write_text(json.dumps({"repos": {"target": {
        "anchor": str(tmp_path), "pr": {"provider": "gitea", "repository": "owner/repo",
                                     "api_base": "https://forge.example"},
    }}}), encoding="utf-8")
    child = tmp_path / "native.py"
    child.write_text(
        "import pathlib,subprocess,sys\n"
        "from agent_worktrees import __main__ as native\n"
        "from agent_worktrees.providers import gitea\n"
        f"sent=pathlib.Path({str(sent)!r})\n"
        "def send(argv,**kw):\n"
        "    assert 'synthetic-fixture-1' in kw['input_text']\n"
        "    url=next(p for p in argv if p.startswith('https://'))\n"
        "    with sent.open('a') as f: f.write(url+'\\n')\n"
        "    if url.endswith('/user'): body='{\"login\":\"reader\"}'\n"
        "    elif url.endswith('/pulls/7'): body='{\"head\":{\"sha\":\"abc\"},\"base\":{}}'\n"
        "    elif url.endswith('/status'): body='{\"statuses\":[]}'\n"
        "    else: body='[]'\n"
        "    return subprocess.CompletedProcess(argv,0,body+'\\n200','')\n"
        "gitea.run_cli=send\n"
        "sys.exit(native.main(['pr-watch',*sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    result = run_caller_owned(
        [sys.executable, str(child), operation, "owner/repo",
         *(["7"] if operation == "snapshot" else []), "--config", str(config),
         "--credential-command", json.dumps([sys.executable, str(credential)]),
         "--http-profile", "direct-v1", "--expected-account", "reader", "--json"],
        timeout=45, max_stdout_bytes=65536, max_stderr_bytes=8192,
    )
    assert (result.returncode != 0) is rotate
    expected = 1 if rotate else (2 if operation == "inventory" else 4)
    assert len(sent.read_text(encoding="utf-8").splitlines()) == expected
    assert int(count.read_text(encoding="utf-8")) == (2 if rotate else expected)
    assert "synthetic-fixture" not in result.stdout + result.stderr


def test_contract_discovery_is_passive_and_rejects_account_selection(monkeypatch, capsys):
    monkeypatch.setattr(pr_credential_command, "run_caller_owned",
                        lambda *_a, **_kw: pytest.fail("discovery acquired material"))
    assert pr_inventory_cli.dispatch(["--contract"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["observation_account_guard"] == {
        "version": 1, "option": "--expected-account", "providers": ["gitea"],
        "http_profiles": ["direct-v1"], "inventory_extra_requests": 1,
        "snapshot_within_credential_budget": True,
    }
    assert pr_inventory_cli.dispatch(["--contract", "--expected-account", "reader"]) == 2
