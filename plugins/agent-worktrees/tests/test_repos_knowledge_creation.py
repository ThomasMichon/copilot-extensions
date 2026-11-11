"""Native remote-effect ownership using only private synthetic provider state."""

import json
import os
from contextlib import contextmanager
import subprocess
import sys
import time
from types import SimpleNamespace

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned
import pytest
import yaml

from agent_worktrees import github_account_observation as credentials
from agent_worktrees import repos, repos_knowledge_creation as creation
from agent_worktrees import knowledge_creation_receipts as receipts
from agent_worktrees.repos_configuration import command
from agent_worktrees.repos_configuration_sources import ConfigurationError
from test_github_knowledge_repository import metadata
from test_repos_configuration import SECRET, cli, local_knowledge, native as native, tree

URL = "https://github.com/selected/knowledge"


@pytest.fixture
def selected(native, monkeypatch):
    local_knowledge(native)
    source = repos._repos_yaml_path()
    value = yaml.safe_load(source.read_text())
    value["account_map"] = {"selected": "selected"}
    source.write_text(yaml.safe_dump(value), encoding="utf-8")
    state = {"created": False, "protected": False, "calls": [], "failure": "", "login": "selected"}
    monkeypatch.setattr(credentials.shutil, "which", lambda _: "fixture-gh")

    def run(argv, **kwargs):
        args = argv[1:]
        state["calls"].append((args, kwargs.get("input_text")))
        if args[:2] == ["auth", "token"]:
            assert args == ["auth", "token", "--hostname", "github.com", "--user", "selected"]
            assert "GH_TOKEN" not in kwargs["env"]
            return SimpleNamespace(returncode=0, stdout="fixture-token", stderr="")
        assert kwargs["env"]["GH_TOKEN"] == "fixture-token"
        assert kwargs["budget"].execution_deadline > time.monotonic()
        if state["failure"] == "credential":
            return SimpleNamespace(returncode=9, stdout="", stderr=SECRET)
        route = next(argument for argument in args if argument.startswith("https://"))
        route = route.replace("/repos/Selected/Knowledge", "/repos/selected/knowledge")
        status, body = 200, {}
        if route.endswith("/user"):
            body = {"login": state["login"]}
        elif "POST" in args:
            stage = "creation" if route.endswith("/user/repos") else "protection"
            body = json.loads(kwargs["input_text"])
            if state["failure"] == stage + "-denied":
                status = 403
            else:
                state["created" if stage == "creation" else "protected"] = True
                if state["failure"] == stage + "-lost":
                    raise subprocess.TimeoutExpired("synthetic-secret", 1)
                if state["failure"] == stage + "-cancel":
                    raise KeyboardInterrupt
                status, body = 201, metadata() if stage == "creation" else {"id": 7}
        elif route.endswith("/protection"):
            status = 404
        elif "/rules/branches/" in route:
            body = [
                {"type": kind, "ruleset_id": 7, "ruleset_source_type": "Repository"}
                for kind in ("deletion", "non_fast_forward")
            ] if state["protected"] else []
        elif route.endswith("/rulesets/7"):
            body = {
                "id": 7, "target": "branch", "source_type": "Repository", "source": "selected/knowledge",
                "enforcement": "active", "bypass_actors": [],
                "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
            }
        elif route.endswith("/repos/selected/knowledge"):
            status, body = (200, metadata()) if state["created"] else (404, {})
        else:
            pytest.fail("unexpected provider route")
        return SimpleNamespace(
            returncode=0 if status in {200, 201} else 1,
            stdout=f"HTTP/2 {status} Response\r\n\r\n" + json.dumps(body), stderr=SECRET,
        )

    monkeypatch.setattr(credentials, "run_caller_owned", run)
    return state


def worker(mode, *, operation="create", token=None, receipt="", url=URL):
    budget = OwnedExecutionBudget.from_timeout(30, cleanup_reserve=2)
    return creation._worker({
        "project": "sample", "url": url, "operation": operation, "mode": mode,
        "token": token, "receipt": receipt, "deadline": budget.deadline, "cleanup_reserve": 2,
    })


def mutations(state):
    return [call for call in state["calls"] if "POST" in call[0]]


def test_create_then_verify_server_branch_preserves_all_native_source_bytes(native, selected):
    before = tree(native)
    proposal = worker("preview")
    assert proposal["version"] == 2 and proposal["scope"] == "remote-repository"
    assert proposal["operation"] == "create" and mutations(selected) == []
    assert tree(native) == before
    receipt = worker("apply", token=proposal["token"])
    assert receipt["status"] == "created" and receipt["creation"] == "confirmed"
    assert receipt["repository"]["default_branch"] == "provider-default"
    assert receipt["safeguards"]["status"] == "verified"
    assert receipt["registration_changed"] is False and receipt["binding_changed"] is False
    assert len(mutations(selected)) == 2
    assert receipts.read("sample", receipt["receipt"])["creation"] == "confirmed"
    after = tree(native)
    for key, value in before.items():
        assert after[key] == value
    receipt_path = receipts.path("sample", receipt["receipt"])
    allowed = {str(path.relative_to(native)) for path in (
        receipt_path, receipt_path.parent, receipt_path.parent.parent,
        repos._repos_yaml_path().with_suffix(".lock"),
        repos._repos_yaml_path().parent / "pr-rewrite-authority.lock",
    )}
    assert set(after) - set(before) <= allowed
    assert repos._repos_yaml_path().read_bytes() == before[str(repos._repos_yaml_path().relative_to(native))]
    assert SECRET not in json.dumps(receipt) + json.dumps(proposal)
    with pytest.raises(ConfigurationError, match="existing native receipt"):
        worker("preview")
    assert len(mutations(selected)) == 2


@pytest.mark.parametrize("failure,confirmed", [
    ("creation-denied", False), ("creation-lost", False),
    ("protection-denied", True), ("protection-lost", True),
    ("creation-cancel", False), ("protection-cancel", True),
])
def test_one_attempt_retains_partial_or_unknown_effects_without_delete_or_replay(native, selected, failure, confirmed):
    proposed = worker("preview")
    selected["failure"] = failure
    result = worker("apply", token=proposed["token"])
    assert result["status"] == "incomplete"
    assert result["creation"] == ("confirmed" if confirmed else "unknown")
    assert len(mutations(selected)) == (2 if confirmed else 1)
    assert SECRET not in json.dumps(result) and "synthetic-secret" not in json.dumps(result)
    calls = len(mutations(selected))
    with pytest.raises(ConfigurationError, match="existing native receipt"):
        worker("preview")
    assert len(mutations(selected)) == calls
    selected["failure"] = ""
    if confirmed:
        proposed = worker("preview", operation="protect-default", receipt=result["receipt"])
        assert proposed["operation"] == "protect-default" and proposed["scope"] == "remote-repository"
        completed = worker("apply", operation="protect-default", receipt=result["receipt"], token=proposed["token"])
        assert completed["status"] == "protected" and completed["creation"] == "confirmed"
        assert len(mutations(selected)) == calls + (0 if failure in {"protection-lost", "protection-cancel"} else 1)
    else:
        with pytest.raises(ConfigurationError, match="reconciliation"):
            worker("preview", operation="protect-default", receipt=result["receipt"])


@pytest.mark.parametrize("change", ["registry", "target", "binding", "existing", "account-mismatch"])
def test_drift_or_existing_target_refuses_before_mutation(native, selected, change):
    proposal = worker("preview")
    if change == "registry":
        source = repos._repos_yaml_path()
        source.write_text(source.read_text() + "additional_owner_field: changed\n", encoding="utf-8")
    elif change == "target":
        proposal["token"] = "0" * 64
    elif change == "binding":
        from agent_worktrees import config as cfg
        source = cfg.project_dir("sample") / "config.yaml"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("knowledge_repo: elsewhere\n", encoding="utf-8")
    elif change == "existing":
        selected["created"] = True
    else:
        selected["login"] = "different"
    with pytest.raises((ConfigurationError, creation.CredentialObservationError)):
        worker("apply", token=proposal["token"])
    assert mutations(selected) == []


@pytest.mark.parametrize("policy", ["owner", "default", "exact", "unconfigured", "organization"])
def test_account_selection_uses_registry_policy_not_namespace_or_ambient(native, selected, policy):
    source = repos._repos_yaml_path()
    value = yaml.safe_load(source.read_text())
    value.pop("account_map")
    if policy == "owner":
        value["account_map"] = {"selected": "selected"}
    elif policy == "default":
        value["account_map"] = {"github.com:*": "selected"}
    elif policy == "exact":
        value["account_map"] = {"selected": "another"}
        value["repos"]["known"] = {"remote": URL, "account": "selected", "class": "reference"}
    elif policy == "organization":
        value["account_map"] = {"selected": "different"}
    source.write_text(yaml.safe_dump(value), encoding="utf-8")
    if policy in {"unconfigured", "organization"}:
        with pytest.raises(ConfigurationError):
            creation._selection("sample", URL)
        assert selected["calls"] == []
    else:
        assert creation._selection("sample", URL)["account_source"] == ("repository" if policy == "exact" else policy)


@pytest.mark.parametrize("extra", [
    ["--repository", "unexpected"], ["--account", "unexpected"], ["--default-branch", "main"],
    ["--login", "unexpected"], ["--workspace", "unexpected"], ["--operation", "reset"],
    ["--accepted-default"], ["--checkout-path", "unexpected"], ["--creation-receipt", "a" * 64],
    ["--unsupported-creation-argument"], ["--repository-u", URL],
])
def test_native_parser_refuses_unknown_and_unrelated_fields_without_effects(native, extra):
    before = tree(native)
    result = cli("knowledge-create-preview", "--context-project", "sample", "--repository-url", URL, *extra)
    assert result.returncode == 1 and json.loads(result.stdout)["code"] == "invalid-request"
    assert tree(native) == before


def test_owned_cleanup_custody_is_fatal_through_worker_and_public_command(native, selected, monkeypatch):
    proposed = worker("preview")
    retained = OwnedCleanupError(None, object(), cleanup_resources=(("reader", object()),))

    def unconfirmed(*args, **kwargs):
        raise retained

    monkeypatch.setattr(creation.provider, "create", unconfirmed)
    with pytest.raises(OwnedCleanupError) as caught:
        worker("apply", token=proposed["token"])
    assert caught.value is retained
    monkeypatch.setattr(creation, "run_caller_owned", unconfirmed)
    with pytest.raises(OwnedCleanupError) as caught:
        command([
            "knowledge-create-apply", "--context-project", "sample", "--repository-url", URL,
            "--preview-token", proposed["token"],
        ])
    assert caught.value is retained


def test_equivalent_policy_is_not_duplicated_and_changed_created_identity_refuses_repair(native, selected, monkeypatch):
    proposed = worker("preview")
    selected["protected"] = True
    result = worker("apply", token=proposed["token"])
    assert result["status"] == "created" and len(mutations(selected)) == 1
    original = creation.provider.repository

    def replacement(*args, **kwargs):
        return {**original(*args, **kwargs), "id": 999}

    monkeypatch.setattr(creation.provider, "repository", replacement)
    with pytest.raises(ConfigurationError, match="identity or default branch changed"):
        worker("preview", operation="protect-default", receipt=result["receipt"])
    assert len(mutations(selected)) == 1


def test_new_protection_confirmation_after_denial_does_not_replay_an_old_attempt(native, selected):
    proposed = worker("preview")
    selected["failure"] = "protection-denied"
    partial = worker("apply", token=proposed["token"])
    repair = worker("preview", operation="protect-default", receipt=partial["receipt"])
    second = worker("apply", operation="protect-default", receipt=partial["receipt"], token=repair["token"])
    assert second["status"] == "incomplete" and len(mutations(selected)) == 3
    with pytest.raises(ConfigurationError, match="changed"):
        worker("apply", operation="protect-default", receipt=partial["receipt"], token=repair["token"])
    assert len(mutations(selected)) == 3
    fresh = worker("preview", operation="protect-default", receipt=partial["receipt"])
    assert fresh["token"] != repair["token"]
    selected["failure"] = ""
    complete = worker("apply", operation="protect-default", receipt=partial["receipt"], token=fresh["token"])
    assert complete["status"] == "protected" and len(mutations(selected)) == 4


def test_selection_change_after_creation_stops_before_protection_without_losing_creation(native, selected, monkeypatch):
    proposed = worker("preview")
    original = creation.provider.create

    def changed(account):
        result = original(account)
        source = repos._repos_yaml_path()
        source.write_text(source.read_text() + "additional_owner_field: changed\n", encoding="utf-8")
        return result

    monkeypatch.setattr(creation.provider, "create", changed)
    result = worker("apply", token=proposed["token"])
    assert result["status"] == "incomplete" and result["creation"] == "confirmed"
    assert len(mutations(selected)) == 1


def test_waiting_protection_confirmation_rechecks_the_receipt_under_its_writer_lock(native, selected, monkeypatch):
    proposed = worker("preview")
    selected["failure"] = "protection-denied"
    partial = worker("apply", token=proposed["token"])
    repair = worker("preview", operation="protect-default", receipt=partial["receipt"])
    selected["failure"] = ""
    original = creation.registry_lock

    @contextmanager
    def competed(*args, **kwargs):
        with original(*args, **kwargs):
            newer = receipts.read("sample", partial["receipt"])
            newer["protection_attempts"] += 1
            receipts.save("sample", newer)
            yield

    monkeypatch.setattr(creation, "registry_lock", competed)
    with pytest.raises(ConfigurationError, match="receipt changed"):
        worker("apply", operation="protect-default", receipt=partial["receipt"], token=repair["token"])
    assert len(mutations(selected)) == 2


def test_durable_receipt_survives_runtime_update_and_rollback_but_preview_does_not(native, selected, monkeypatch):
    proposed = worker("preview")
    original_target = creation.target
    with monkeypatch.context() as changed:
        changed.setattr(creation, "target", lambda *args: {
            **original_target(*args), "installation": "agent-worktrees:updated-runtime",
        })
        with pytest.raises(ConfigurationError, match="changed"):
            worker("apply", token=proposed["token"])
        assert mutations(selected) == []
    completed = worker("apply", token=proposed["token"])
    reference = completed["receipt"]
    with monkeypatch.context() as changed:
        changed.setattr(creation, "target", lambda *args: {
            **original_target(*args), "installation": "agent-worktrees:updated-runtime",
        })
        observed = worker("preview", operation="inspect", receipt=reference)
        assert observed["receipt"] == reference and observed["creation"] == "confirmed"
        selected["protected"] = False
        protection = worker("preview", operation="protect-default", receipt=reference)
        result = worker("apply", operation="protect-default", receipt=reference, token=protection["token"])
        assert result["status"] == "protected" and result["receipt"] == reference
        selected["created"] = False
        with pytest.raises(ConfigurationError, match="existing native receipt"):
            worker("preview")
        selected["created"] = True
    assert worker("preview", operation="inspect", receipt=reference)["creation"] == "confirmed"
    assert sum(call[0][-3:] == ["https://api.github.com/user/repos", "--input", "-"]
               for call in mutations(selected)) == 1


@pytest.mark.parametrize("interrupted", [False, True])
def test_protection_repair_never_inherits_a_previously_successful_status(native, selected, monkeypatch, interrupted):
    completed = worker("apply", token=worker("preview")["token"])
    assert completed["status"] == "created"
    selected["protected"] = False
    reference = completed["receipt"]
    proposed = worker("preview", operation="protect-default", receipt=reference)
    retained = OwnedCleanupError(None, object(), cleanup_resources=(("reader", object()),))

    def unverified(*args):
        if interrupted:
            raise retained

    monkeypatch.setattr(creation.provider, "protect", unverified)
    if interrupted:
        with pytest.raises(OwnedCleanupError) as caught:
            worker("apply", operation="protect-default", receipt=reference, token=proposed["token"])
        assert caught.value is retained
    else:
        result = worker("apply", operation="protect-default", receipt=reference, token=proposed["token"])
        assert result["status"] == "incomplete" and result["safeguards"]["status"] == "missing"
        assert "remains incomplete" in result["message"]
    persisted = receipts.read("sample", reference)
    assert persisted["status"] == "incomplete" and persisted["creation"] == "confirmed"


@pytest.mark.parametrize("failure", ["repository-404", "policy-denied", "credential-missing"])
def test_read_only_inspection_retains_creation_and_available_observations_on_failure(
    native, selected, monkeypatch, failure,
):
    completed = worker("apply", token=worker("preview")["token"])
    reference = completed["receipt"]
    before = tree(native)
    posts = len(mutations(selected))

    def unavailable(*args):
        raise creation.CredentialObservationError(
            "repository" if failure == "repository-404" else "policy",
            "access-unconfirmed", http_status=404 if failure == "repository-404" else 403,
        )

    if failure == "repository-404":
        monkeypatch.setattr(creation.provider, "repository", unavailable)
    elif failure == "policy-denied":
        monkeypatch.setattr(creation.provider, "safeguards", unavailable)
    else:
        monkeypatch.setattr(credentials.shutil, "which", lambda _: None)
    result = worker("preview", operation="inspect", receipt=reference)
    assert result["status"] == "incomplete" and result["prior_status"] == "created"
    assert result["receipt"] == reference and result["creation"] == "confirmed"
    assert result["repository"] == completed["repository"]
    assert result["observed_repository"] == (completed["repository"] if failure == "policy-denied" else None)
    assert result["observation_status"] == "unavailable"
    assert result["observation_failure"]["code"] in {"access-unconfirmed", "missing-command"}
    assert tree(native) == before and len(mutations(selected)) == posts


def test_read_only_inspection_distinguishes_prior_success_from_current_missing_protection(native, selected):
    completed = worker("apply", token=worker("preview")["token"])
    selected["protected"] = False
    before = tree(native)
    observed = worker("preview", operation="inspect", receipt=completed["receipt"])
    assert observed["status"] == "incomplete" and observed["prior_status"] == "created"
    assert observed["observation_status"] == "observed"
    assert observed["observed_safeguards"]["status"] == "missing"
    assert receipts.read("sample", completed["receipt"])["status"] == "created"
    assert tree(native) == before and len(mutations(selected)) == 2


@pytest.mark.parametrize("drift", ["id", "default_branch"])
def test_read_only_inspection_retains_original_identity_when_current_repository_drifts(native, selected, monkeypatch, drift):
    completed = worker("apply", token=worker("preview")["token"])
    original = creation.provider.repository

    def changed(*args, **kwargs):
        return {**original(*args, **kwargs), drift: 999 if drift == "id" else "new-default"}

    monkeypatch.setattr(creation.provider, "repository", changed)
    before = tree(native)
    result = worker("preview", operation="inspect", receipt=completed["receipt"])
    assert result["status"] == "incomplete" and result["prior_status"] == "created"
    assert result["repository"] == completed["repository"]
    assert result["observed_repository"][drift] != result["repository"][drift]
    assert result["observation_failure"]["code"] == "creation-target-changed"
    assert tree(native) == before and len(mutations(selected)) == 2


def test_equivalent_github_url_casing_remains_inspectable_and_repairable(native, selected):
    completed = worker("apply", token=worker("preview")["token"])
    selected["protected"] = False
    alternate = "https://github.com/Selected/Knowledge"
    reference = completed["receipt"]
    inspected = worker("preview", operation="inspect", receipt=reference, url=alternate)
    assert inspected["observed_repository"] == completed["repository"]
    proposed = worker("preview", operation="protect-default", receipt=reference, url=alternate)
    result = worker("apply", operation="protect-default", receipt=reference, url=alternate, token=proposed["token"])
    assert result["status"] == "protected" and result["receipt"] == reference
    assert result["repository"] == completed["repository"] and len(mutations(selected)) == 3


def test_confirmed_receipt_can_be_inspected_and_repaired_after_normal_knowledge_binding(native, selected):
    from agent_worktrees import config as cfg
    from agent_worktrees.repos_knowledge_registration import context_selection

    proposed = worker("preview")
    selected["failure"] = "protection-denied"
    partial = worker("apply", token=proposed["token"])
    assert partial["creation"] == "confirmed" and partial["status"] == "incomplete"
    selected["failure"] = ""
    source = repos._repos_yaml_path()
    value = yaml.safe_load(source.read_text())
    value["repos"]["knowledge"] = {
        "class": "knowledge", "agent": False, "remote": URL, "account": "selected",
        "default_branch": "provider-default", repos._current_platform(): str(native / "knowledge"),
    }
    source.write_text(yaml.safe_dump(value), encoding="utf-8")
    configuration = cfg.project_dir("sample") / "config.yaml"
    configuration.parent.mkdir(parents=True, exist_ok=True)
    configuration.write_text("knowledge_repo: knowledge\n", encoding="utf-8")
    before_registry, before_binding = source.read_bytes(), configuration.read_bytes()
    with pytest.raises(ConfigurationError, match="unbound"):
        context_selection("sample")
    with pytest.raises(ConfigurationError, match="unbound"):
        worker("preview")
    observation = worker("preview", operation="inspect", receipt=partial["receipt"])
    assert observation["creation"] == "confirmed" and observation["observed_repository"] == partial["repository"]
    assert observation["observation_status"] == "observed"
    repair = worker("preview", operation="protect-default", receipt=partial["receipt"])
    completed = worker("apply", operation="protect-default", receipt=partial["receipt"], token=repair["token"])
    assert completed["status"] == "protected" and len(mutations(selected)) == 3
    assert source.read_bytes() == before_registry and configuration.read_bytes() == before_binding


def test_actual_worker_timeout_reaps_console_descendant_and_never_replays(native, monkeypatch):
    marker = native / "late-descendant"
    calls = []
    original = creation.run_caller_owned

    def slow(argv, **kwargs):
        calls.append(True)
        descendant = f"import pathlib,time;time.sleep(3);pathlib.Path({str(marker)!r}).touch()"
        script = (
            "import subprocess,sys,time;"
            f"subprocess.Popen([sys.executable,'-c',{descendant!r}],"
            "**({'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}));"
            "time.sleep(20)"
        )
        kwargs["budget"] = OwnedExecutionBudget.from_timeout(2, cleanup_reserve=1)
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(creation, "run_caller_owned", slow)
    started = time.monotonic()
    result = creation.invoke("sample", URL, "create", token="a" * 64)
    assert time.monotonic() - started < 4
    assert result["status"] == "incomplete" and result["creation"] == "unknown"
    assert calls == [True]
    time.sleep(3)
    assert not marker.exists()


@pytest.mark.parametrize("mode", ["preview", "apply"])
def test_missing_worker_receipt_is_unknown_without_replay_or_raw_diagnostics(native, monkeypatch, mode):
    calls = []

    def lost(*args, **kwargs):
        calls.append(True)
        raise RuntimeError(SECRET)

    monkeypatch.setattr(creation, "run_caller_owned", lost)
    if mode == "preview":
        with pytest.raises(ConfigurationError, match="did not complete"):
            creation.invoke("sample", URL, "create")
    else:
        result = creation.invoke("sample", URL, "create", token="a" * 64)
        assert result["status"] == "incomplete" and result["creation"] == "unknown"
        assert result["cleanup"] == "unknown" and SECRET not in json.dumps(result)
    assert calls == [True]


@pytest.fixture
def process_provider(native, monkeypatch):
    local_knowledge(native)
    registry = repos._repos_yaml_path()
    value = yaml.safe_load(registry.read_text())
    value["account_map"] = {"selected": "selected"}
    registry.write_text(yaml.safe_dump(value), encoding="utf-8")
    state = native / "provider-state.json"
    state.write_text(json.dumps({
        "created": False, "protected": False, "posts": [], "failure": "", "metadata": metadata(),
    }), encoding="utf-8")
    executable_dir = native / "provider-bin"
    executable_dir.mkdir()
    script = executable_dir / "provider.py"
    script.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "path=Path(os.environ['CE_CREATION_FIXTURE']); state=json.loads(path.read_text())\n"
        "args=sys.argv[1:]\n"
        "assert os.environ.get('GH_PROMPT_DISABLED')=='1'\n"
        "if args[:2]==['auth','token']:\n"
        " assert args==['auth','token','--hostname','github.com','--user','selected']\n"
        " assert 'GH_TOKEN' not in os.environ\n"
        " print('fixture-token');sys.exit(0)\n"
        "assert os.environ['GH_TOKEN']=='fixture-token'\n"
        "route=next(arg for arg in args if arg.startswith('https://api.github.com/'))\n"
        "status,body=200,{}\n"
        "if route.endswith('/user'): body={'login':'selected'}\n"
        "elif 'POST' in args:\n"
        " body=json.loads(sys.stdin.read());state['posts'].append([route,body])\n"
        " if route.endswith('/user/repos'):\n"
        "  assert body=={'name':'knowledge','private':True,'auto_init':True}\n"
        "  state['created']=True;status,body=201,state['metadata']\n"
        " else:\n"
        "  assert route.endswith('/selected/knowledge/rulesets')\n"
        "  if state['failure']=='protection-denied':status=403\n"
        "  else:state['protected']=True;status,body=201,{'id':7}\n"
        " path.write_text(json.dumps(state))\n"
        "elif route.endswith('/protection'):status=404\n"
        "elif '/rules/branches/' in route:\n"
        " body=[{'type':kind,'ruleset_id':7,'ruleset_source_type':'Repository'} "
        "for kind in ['deletion','non_fast_forward']] if state['protected'] else []\n"
        "elif route.endswith('/rulesets/7'):\n"
        " body={'id':7,'target':'branch','source_type':'Repository','source':'selected/knowledge',"
        "'enforcement':'active','bypass_actors':[],"
        "'rules':[{'type':'deletion'},{'type':'non_fast_forward'}]}\n"
        "elif route.endswith('/repos/selected/knowledge'):\n"
        " status,body=(200,state['metadata']) if state['created'] else (404,{})\n"
        "else:raise AssertionError('unexpected fixture route')\n"
        "print('HTTP/2 '+str(status)+' Response\\r\\n\\r\\n'+json.dumps(body))\n"
        "sys.exit(0 if status in [200,201] else 1)\n",
        encoding="utf-8",
    )
    shim = executable_dir / ("gh.cmd" if sys.platform == "win32" else "gh")
    if sys.platform == "win32":
        shim.write_text(f'@echo off\n"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        shim.write_text(f'#!{sys.executable}\nexec(compile(open({str(script)!r}).read(),{str(script)!r},"exec"))\n')
        shim.chmod(0o700)
    monkeypatch.setenv("PATH", str(executable_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CE_CREATION_FIXTURE", str(state))
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "GH_REPO", "GH_DEBUG"):
        monkeypatch.setenv(key, "hostile-ambient")
    return state


def owner_cli(operation, *, token=None, receipt=""):
    arguments = [
        sys.executable, "-B", "-m", "agent_worktrees", "repos", "configuration", operation,
        "--context-project", "sample", "--repository-url", URL,
    ]
    if token is not None:
        arguments += ["--preview-token", token]
    if receipt:
        arguments += ["--creation-receipt", receipt]
    result = run_caller_owned(
        arguments, timeout=48, max_stdout_bytes=32768, max_stderr_bytes=2048,
    )
    assert SECRET not in result.stdout + result.stderr and "fixture-token" not in result.stdout + result.stderr
    return result.returncode, json.loads(result.stdout)


@pytest.mark.timeout(90)
def test_real_cli_worker_and_credential_processes_complete_separate_protection_repair(native, process_provider):
    before = tree(native)
    code, proposal = owner_cli("knowledge-create-preview")
    assert code == 0 and proposal["scope"] == "remote-repository"
    assert tree(native) == before
    state = json.loads(process_provider.read_text())
    state["failure"] = "protection-denied"
    process_provider.write_text(json.dumps(state), encoding="utf-8")
    code, partial = owner_cli("knowledge-create-apply", token=proposal["token"])
    assert code == 1 and partial["creation"] == "confirmed"
    assert partial["safeguards"]["status"] == "unknown"
    assert partial["failure"]["http_status"] == 403
    state = json.loads(process_provider.read_text())
    assert len(state["posts"]) == 2 and state["created"] is True and state["protected"] is False
    code, observation = owner_cli("knowledge-create-inspect", receipt=partial["receipt"])
    assert code == 1 and observation["observed_repository"]["id"] == partial["repository"]["id"]
    assert len(json.loads(process_provider.read_text())["posts"]) == 2
    code, protection = owner_cli("knowledge-protect-preview", receipt=partial["receipt"])
    assert code == 0 and protection["operation"] == "protect-default"
    state["failure"] = ""
    process_provider.write_text(json.dumps(state), encoding="utf-8")
    code, complete = owner_cli("knowledge-protect-apply", receipt=partial["receipt"], token=protection["token"])
    assert code == 0 and complete["status"] == "protected"
    state = json.loads(process_provider.read_text())
    assert len(state["posts"]) == 3 and state["protected"] is True
    assert sum(route.endswith("/user/repos") for route, _ in state["posts"]) == 1
    assert owner_cli("knowledge-create-apply", token=proposal["token"])[0] == 1
    assert len(json.loads(process_provider.read_text())["posts"]) == 3
    for key, value in before.items():
        if key != str(process_provider.relative_to(native)):
            assert tree(native)[key] == value
