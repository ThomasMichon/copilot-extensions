"""Fresh-source ownership and repository-scoped credential contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from itertools import permutations
from types import SimpleNamespace

import pytest

from agent_dispatch import procutil
from agent_dispatch import repository_github_auth
from agent_dispatch.issue_loop_markers import _parse_marker, validate_marker_payload
from agent_dispatch.repository_issue_loops import (
    GitHubProvider,
    _forge_provider_for,
    _latest_reservations,
    _marker,
    run_tick,
    validate_config,
)
from agent_dispatch.registrar import RegistrarError
from tests import stalled_probe_fixture as probe_fixture
from tests.test_repository_issue_loops import (
    FakeClient,
    _config,
    _graphql_issue,
    _graphql_page,
    _issue,
)


def _reservation(**overrides):
    return {
        "loop": "backlog",
        "occurrence": 7200,
        "state": "reserved",
        "at": 7300,
        "label": "agent-reserved",
        "issue": 7,
        **overrides,
    }


@pytest.mark.parametrize("field,value", [
    ("at", None), ("at", []), ("at", {}), ("at", "7300"),
    ("at", True), ("at", float("nan")), ("at", float("inf")),
    ("task_id", None), ("task_id", []), ("task_id", {}), ("task_id", 7),
    ("label", None), ("label", []), ("label", {}), ("label", 7),
])
def test_nonorderable_claim_fields_are_rejected_before_ownership_reduction(field, value):
    payload = _reservation(**{"state": "claimed", "task_id": "task-real", field: value})
    assert validate_marker_payload(payload, issue_number=7) is None
    assert _parse_marker(
        _marker(payload), author="issue-bot", expected_author="issue-bot", issue_number=7,
    ) is None


def test_edited_claim_round_trips_into_an_empty_coordinators_selection():
    comment = {"id": "comment-1", "author": {"login": "issue-bot"},
               "body": _marker(_reservation())}
    mutations = []

    def runner(argv, **_kwargs):
        if argv[1:3] == ["api", "user"]:
            value = "issue-bot"
        elif argv[1:3] == ["api", "repos/example/project"]:
            value = "example/project"
        elif argv[1:3] == ["issue", "view"]:
            value = json.dumps({"comments": [comment]})
        elif argv[1:3] == ["api", "graphql"]:
            if any("updateIssueComment" in arg for arg in argv):
                comment["body"] = next(arg[5:] for arg in argv if arg.startswith("body="))
                mutations.append("edit")
                value = "{}"
            else:
                value = json.dumps(_graphql_page([_graphql_issue(7, comments=[comment])]))
        else:
            raise AssertionError(f"Unexpected forge mutation: {argv[:3]}")
        return SimpleNamespace(returncode=0, stdout=value, stderr="")

    GitHubProvider("issue-bot", runner=runner).claim(
        "example/project", _issue(7), _reservation(), "task-on-another-coordinator"
    )
    client = FakeClient()
    fresh = GitHubProvider("issue-bot", runner=runner)
    result = run_tick(client, _config(), provider=fresh, clock=lambda: 2_000_000_000)

    assert mutations == ["edit"]
    assert result["eligible"] == []
    assert result["created"] == []
    assert client.created == []
    assert not result.get("reconciled")


@pytest.mark.parametrize("release_first", [False, True])
@pytest.mark.parametrize("release_label", ["agent-reserved", "previous-label"])
def test_standalone_claim_rejects_conflicting_release_and_old_occurrence(
    release_first, release_label,
):
    claim = _reservation(state="claimed", task_id="task-real")
    release = {**claim, "state": "released", "task_id": "wrong-task",
               "label": release_label, "reason": "not mine"}
    snapshots = (release, claim) if release_first else (claim, release)
    issue = _issue(7, reservations=(
        *snapshots,
        _reservation(occurrence=3600),
    ))
    assert _latest_reservations(issue)["backlog"] == claim


@pytest.mark.parametrize("release_label", ["agent-reserved", "previous-label"])
def test_only_matching_task_and_label_release_still_supersedes_a_claim(release_label):
    claim = _reservation(state="claimed", task_id="task-real")
    release = {**claim, "state": "released", "label": release_label, "reason": "finished"}
    expected = release if release_label == claim["label"] else claim
    assert _latest_reservations(_issue(7, reservations=(claim, release)))["backlog"] == expected
    assert _latest_reservations(_issue(7, reservations=(release, claim)))["backlog"] == expected


@pytest.mark.parametrize("order", list(permutations(range(3))))
@pytest.mark.parametrize("conflicting_task", [
    {"task_id": "wrong-task"}, {}, {"task_id": None},
], ids=["wrong-task", "missing-task", "null-task"])
@pytest.mark.parametrize("release_override", [
    {}, {"label": "previous-label"}, {"occurrence": 3600},
], ids=["matching", "different-label", "older-occurrence"])
def test_matching_release_evidence_survives_other_releases_in_every_order(
    order, conflicting_task, release_override,
):
    claim = _reservation(state="claimed", task_id="task-real")
    conflicting = _reservation(
        state="released", reason="unrelated", **conflicting_task,
    )
    release = {
        **claim, "state": "released", "reason": "finished", **release_override,
    }
    snapshots = (conflicting, release, claim)
    issue = _issue(7, reservations=tuple(snapshots[index] for index in order))

    expected = claim if release_override else release
    assert _latest_reservations(issue)["backlog"] == expected


@pytest.mark.parametrize("order", list(permutations(range(3))))
@pytest.mark.parametrize("second_label", ["agent-reserved", "previous-label"])
@pytest.mark.parametrize("release_override", [
    {}, {"label": "unrelated-label"}, {"occurrence": 3600},
], ids=["matching", "different-label", "older-occurrence"])
def test_release_only_closes_its_own_claim_in_every_order(
    order, second_label, release_override,
):
    claim_a = _reservation(state="claimed", task_id="task-a", at=7201)
    claim_b = {**claim_a, "task_id": "task-b", "label": second_label}
    release_a = {
        **claim_a, "state": "released", "reason": "finished", **release_override,
    }
    snapshots = (claim_a, claim_b, release_a)
    issue = _issue(7, reservations=tuple(snapshots[index] for index in order))

    active = (claim_a, claim_b) if release_override else (claim_b,)
    assert _latest_reservations(issue)["backlog"] in active


@pytest.mark.parametrize("order", list(permutations(range(4))))
@pytest.mark.parametrize("second", [
    {"at": 7301},
    {"at": 7300, "task_id": "task-b"},
    {"at": 7300, "task_id": "task-a", "label": "z-reserved"},
])
def test_newest_unreleased_claim_has_a_stable_ownership_tiebreak(order, second):
    claim_a = _reservation(state="claimed", task_id="task-a", at=7300)
    claim_b = {**claim_a, **second}
    snapshots = (
        claim_a, claim_b,
        _reservation(state="released", task_id="unrelated", reason="finished"),
        _reservation(occurrence=3600),
    )
    issue = _issue(7, reservations=tuple(snapshots[index] for index in order))
    assert _latest_reservations(issue)["backlog"] == claim_b


@pytest.mark.parametrize("order", list(permutations(range(4))))
def test_repeated_claim_identity_keeps_its_latest_snapshot_in_every_order(order):
    old_a = _reservation(state="claimed", task_id="task-a", at=7201)
    new_a = {**old_a, "at": 7301}
    claim_b = {**old_a, "task_id": "task-b", "at": 7250}
    unrelated = _reservation(state="released", task_id="unrelated", reason="finished")
    snapshots = (old_a, new_a, claim_b, unrelated)
    issue = _issue(7, reservations=tuple(snapshots[index] for index in order))
    assert _latest_reservations(issue)["backlog"] == new_a


@pytest.mark.parametrize("order", list(permutations(range(4))))
@pytest.mark.parametrize("second_label", ["agent-reserved", "previous-label"])
def test_all_matching_claims_released_in_every_order(order, second_label):
    claim_a = _reservation(state="claimed", task_id="task-a", at=7201)
    claim_b = {**claim_a, "task_id": "task-b", "label": second_label}
    release_a = {**claim_a, "state": "released", "reason": "finished a"}
    release_b = {**claim_b, "state": "released", "reason": "finished b"}
    snapshots = (claim_a, claim_b, release_a, release_b)
    issue = _issue(7, reservations=tuple(snapshots[index] for index in order))

    assert _latest_reservations(issue)["backlog"] in (release_a, release_b)


@pytest.mark.parametrize("stage,bootstrap_delay", [
    ("account", 0), ("credential", 0), ("identity", 0), ("operation", 0),
    ("account", 1.0),
], ids=["account", "credential", "identity", "operation", "account-cold-bootstrap"])
def test_repository_probe_timeout_reaps_real_stalled_descendant(
    tmp_path, monkeypatch, stage, bootstrap_delay,
):
    root = tmp_path / "probe"
    prefix = probe_fixture.prefix(root, stage, bootstrap_delay)
    capture = procutil.run_background_capture
    completed = []

    def short_capture(argv, **kwargs):
        if argv[0] == "gh":
            argv = [*prefix, *argv[1:]]
        if probe_fixture.operation(argv[len(prefix):]) != stage:
            return capture(argv, **kwargs)
        with probe_fixture.PreparedProbe(
            root, argv, env=kwargs["env"], cwd=kwargs.get("cwd"),
        ) as prepared:
            assert (root / "ready.json").exists(), "real descendant readiness is required"
            assert prepared.readiness_seconds >= bootstrap_delay
            if bootstrap_delay:
                assert prepared.child["bootstrap_elapsed"] > .8
            communications = []
            expired = []
            communicate = prepared.process.communicate

            def observed_communicate(*args, **options):
                communications.append((time.monotonic(), options["timeout"]))
                try:
                    return communicate(*args, **options)
                except subprocess.TimeoutExpired:
                    expired.append(True)
                    raise

            with monkeypatch.context() as patch:
                patch.setattr(procutil, "subprocess", probe_fixture.CaptureModule(prepared))
                patch.setattr(prepared.process, "communicate", observed_communicate)
                result = capture(argv, **{**kwargs, "timeout": 0.8})
            assert result is None, "ready stalled probe must take the actual timeout path"
            assert len(communications) == 1 and expired == [True]
            started, remaining = communications[0]
            assert prepared.child["ready_at"] <= prepared.prepared_at <= prepared.timed_at <= started
            assert 0 < remaining <= .8
            prepared.assert_reaped()
        assert not prepared.startup_stderr, "real probe actor reported an error"
        completed.append(.8)
        return result

    monkeypatch.setattr(procutil, "run_background_capture", short_capture)
    monkeypatch.setattr(
        procutil, "agent_worktrees_launch_prefix",
        lambda: prefix,
    )
    provider = GitHubProvider("issue-bot", repository="example/project", auth="repository")
    with pytest.raises(RuntimeError):
        provider._gh("issue", "edit", "7")
    assert completed == [.8]
    assert repository_github_auth._TIMEOUT_SECONDS == 30


@pytest.mark.skipif(os.name != "nt", reason="Windows file-only actor bootstrap")
def test_cold_probe_readiness_does_not_import_the_unrelated_process_runtime(tmp_path):
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "agent_procutil.py").write_text(
        "raise AssertionError('file-only actor imported the process runtime')\n",
        encoding="utf-8",
    )
    env = procutil.agent_worktrees_environment()
    env["PYTHONPATH"] = str(shadow)
    root = tmp_path / "probe"
    with probe_fixture.PreparedProbe(
        root, [*probe_fixture.prefix(root, "account", 1), "repos", "account-for", "example/project"],
        env=env,
    ) as prepared:
        assert prepared.child["bootstrap_elapsed"] >= 1
        assert prepared.readiness_seconds <= probe_fixture.READINESS_SECONDS
    assert prepared.startup_stderr == ""


def test_prepared_probe_refuses_unknown_foreign_identity_and_unexpected_requests(tmp_path):
    from agent_dispatch import companion

    root = tmp_path / "probe"
    argv = [*probe_fixture.prefix(root, "account"), "repos", "account-for", "example/project"]
    with probe_fixture.PreparedProbe(
        root, argv, env=procutil.agent_worktrees_environment(),
    ) as prepared:
        foreign_token = companion.process_start_token(os.getpid())
        assert foreign_token
        for owner in (None, foreign_token):
            with pytest.raises(ValueError, match="ownership"):
                prepared.require_owned_root(owner)
        for record in (
            {**prepared.child, "start_token": None},
            {"pid": os.getpid(), "start_token": foreign_token},
        ):
            with pytest.raises(ValueError, match="identity"):
                prepared.pin_child(record)
        for request, options in (
            ([*argv, "--unexpected"], prepared.expected),
            (argv, {**prepared.expected, "cwd": str(tmp_path / "another-cwd")}),
            (argv, {**prepared.expected, "env": {}}),
        ):
            with pytest.raises(ValueError, match="request"):
                prepared.adopt(request, **options)
        assert not prepared.adopted and not (root / "timed").exists()
        prepared.require_owned_root(prepared.root_token)
        handle = prepared.pin_child(prepared.child)
        if handle is not None:
            prepared.api.CloseHandle(handle)


def _exit_observation_probe(initial, final=None, *, completion=0, age=0):
    # Policy-only unit states: no PIDs, creation metadata, signals or cleanup substitutes.
    prepared = object.__new__(probe_fixture.PreparedProbe)
    prepared.timed_at = time.monotonic() - age
    prepared.child_handle = object()
    phases, waits = [], []

    def observe(phase, **details):
        phases.append(phase)
        return {**(initial if len(phases) == 1 else final or initial), **details}

    def wait(milliseconds):
        waits.append(milliseconds)
        return completion, 6 if completion == 0xFFFFFFFF else None

    prepared.child_exit_snapshot = observe
    prepared.wait_pinned_child = wait
    return prepared, phases, waits


def _exit_state(wait):
    return {
        "identity_matches": True, "exit_query_ok": True,
        "custody_open": True, "stop_requested": False,
        "wait_result": wait, "exit_code": 259 if wait == 258 else 1,
    }


def test_known_pending_exit_confirms_same_handle_once_before_watchdog():
    prepared, phases, waits = _exit_observation_probe(_exit_state(258), _exit_state(0))
    handle = prepared.child_handle
    state = prepared.confirm_child_exit()
    assert state["wait_result"] == 0
    assert prepared.child_handle is handle
    assert phases == ["assert-entry", "after-confirmation"]
    assert len(waits) == 1 and 0 < waits[0] <= 1000
    assert time.monotonic() - prepared.timed_at < probe_fixture.ACTIVE_SECONDS


@pytest.mark.parametrize("changes", [
    {"wait_result": 0xFFFFFFFF, "wait_error": 6},
    {"identity_matches": False},
    {"exit_query_ok": False},
    {"exit_code": None},
    {"custody_open": False},
    {"stop_requested": True},
    {"wait_result": 999},
])
def test_failed_or_unknown_exit_observation_never_waits(changes):
    state = {**_exit_state(258), **changes}
    prepared, _phases, waits = _exit_observation_probe(state)
    with pytest.raises(AssertionError):
        prepared.confirm_child_exit()
    assert waits == []


@pytest.mark.parametrize("completion", [258, 0xFFFFFFFF])
def test_exit_confirmation_cannot_credit_pending_or_failed_wait(completion):
    prepared, _phases, waits = _exit_observation_probe(
        _exit_state(258), _exit_state(258), completion=completion,
    )
    with pytest.raises(AssertionError):
        prepared.confirm_child_exit()
    assert len(waits) == 1


def test_exit_confirmation_refuses_insufficient_watchdog_margin():
    prepared, _phases, waits = _exit_observation_probe(
        _exit_state(258), _exit_state(0), age=probe_fixture.ACTIVE_SECONDS - .5,
    )
    with pytest.raises(AssertionError):
        prepared.confirm_child_exit()
    assert waits == []


def test_exit_confirmation_cannot_credit_signal_after_watchdog():
    prepared, _phases, waits = _exit_observation_probe(_exit_state(258), _exit_state(0))
    wait = prepared.wait_pinned_child

    def late_observation(milliseconds):
        result = wait(milliseconds)
        prepared.timed_at = time.monotonic() - probe_fixture.ACTIVE_SECONDS
        return result

    prepared.wait_pinned_child = late_observation
    with pytest.raises(AssertionError, match="watchdog"):
        prepared.confirm_child_exit()
    assert len(waits) == 1


@pytest.mark.parametrize("changes", [
    {"identity_matches": False}, {"custody_open": False}, {"stop_requested": True},
])
def test_exit_confirmation_requires_identity_and_custody_after_signaling(changes):
    prepared, _phases, waits = _exit_observation_probe(
        _exit_state(258), {**_exit_state(0), **changes},
    )
    with pytest.raises(AssertionError):
        prepared.confirm_child_exit()
    assert len(waits) == 1


@pytest.mark.parametrize("error", [
    subprocess.TimeoutExpired(["synthetic-command"], 1, output="synthetic-secret"),
    OSError("synthetic-secret"),
    RuntimeError("synthetic-secret"),
])
def test_credential_probe_errors_do_not_expose_captured_credentials(monkeypatch, error):
    def runner(*_args, **_kwargs):
        raise error

    provider = _scoped_provider(monkeypatch, runner)
    with pytest.raises(RuntimeError) as failure:
        provider._gh("issue", "edit", "7")
    assert "synthetic-secret" not in str(failure.value)
    assert failure.value.__suppress_context__


@pytest.mark.parametrize("stage,description", [
    ("account", "account selection"),
    ("credential", "credential retrieval"),
    ("identity", "credential identity verification"),
])
@pytest.mark.parametrize("failure", ["unavailable", "os-error", "subprocess-error"])
def test_default_probe_failures_retain_safe_stage_context(
    monkeypatch, stage, description, failure,
):
    monkeypatch.setattr(procutil, "agent_worktrees_launch_prefix", lambda: ["scoped-owner"])
    operations = []

    def capture(argv, **_kwargs):
        current = probe_fixture.operation(argv[1:])
        operations.append(current)
        if current == stage:
            if failure == "unavailable":
                return None
            error = OSError if failure == "os-error" else subprocess.SubprocessError
            raise error("synthetic-secret")
        assert current in {"account", "credential", "identity"}, "forge mutation reached"
        value = "synthetic-token" if current == "credential" else "issue-bot"
        return subprocess.CompletedProcess(argv, 0, value, "")

    monkeypatch.setattr(procutil, "run_background_capture", capture)
    provider = GitHubProvider("issue-bot", repository="example/project", auth="repository")
    with pytest.raises(RuntimeError) as error:
        provider._gh("issue", "edit", "7")
    assert str(error.value) == f"Repository GitHub authentication failed during {description}"
    assert error.value.__suppress_context__ and error.value.__cause__ is None
    assert operations == ["account", "credential", "identity"][:operations.index(stage) + 1]


def test_scoped_operation_failure_does_not_echo_credentials(monkeypatch):
    runner = ScopedRunner()
    provider = _scoped_provider(monkeypatch, runner)
    original = provider.runner

    def failure(argv, **kwargs):
        if argv[:2] == ["gh", "issue"]:
            return SimpleNamespace(returncode=1, stdout="", stderr=runner.tokens[-1])
        return original(argv, **kwargs)

    provider.runner = failure
    with pytest.raises(RuntimeError, match="operation failed") as error:
        provider._gh("issue", "edit", "7")
    assert runner.tokens[-1] not in str(error.value)


def test_default_scoped_operation_uses_contained_capture(monkeypatch):
    monkeypatch.setattr(
        "agent_dispatch.repository_issue_loops.repository_environment",
        lambda *_args, **_kwargs: {"GH_TOKEN": "synthetic-only"},
    )
    calls = []

    def capture(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(procutil, "run_background_capture", capture)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_kw: pytest.fail("uncontained spawn"))
    provider = GitHubProvider("issue-bot", repository="example/project", auth="repository")
    assert provider._gh("issue", "edit", "7").stdout == "{}"
    assert calls[0][0] == ["gh", "issue", "edit", "7"]
    assert calls[0][1]["env"] == {"GH_TOKEN": "synthetic-only"}
    assert calls[0][1]["timeout"] == repository_github_auth._TIMEOUT_SECONDS == 30


@pytest.mark.parametrize("error_type", [RuntimeError, OSError, subprocess.SubprocessError])
def test_scoped_forge_capture_failures_suppress_runtime_details(monkeypatch, error_type):
    monkeypatch.setattr(
        "agent_dispatch.repository_issue_loops.repository_environment",
        lambda *_args, **_kwargs: {"GH_TOKEN": "synthetic-only"},
    )

    def capture(*_args, **_kwargs):
        raise error_type("synthetic-secret")

    monkeypatch.setattr(procutil, "run_background_capture", capture)
    provider = GitHubProvider("issue-bot", repository="example/project", auth="repository")
    with pytest.raises(RuntimeError) as failure:
        provider._gh("issue", "edit", "7")
    assert str(failure.value) == "Repository GitHub command failed"
    assert failure.value.__suppress_context__ and failure.value.__cause__ is None


def test_repository_command_preserves_real_child_cwd_and_fixed_capture_options(tmp_path):
    completed = repository_github_auth.run_repository_command(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        cwd=tmp_path, capture_output=True, text=True, check=False,
        **procutil.no_window_kwargs(),
    )
    assert completed.returncode == 0
    assert os.path.samefile(completed.stdout.strip(), tmp_path)
    assert completed.stderr == ""


@pytest.mark.parametrize("options", [
    {"capture_output": False}, {"text": False}, {"check": True},
    {"creationflags": -1}, {"stdin": "synthetic-input"},
])
def test_repository_command_refuses_unsupported_options_before_capture(monkeypatch, options):
    monkeypatch.setattr(
        procutil, "run_background_capture",
        lambda *_a, **_kw: pytest.fail("captured unsupported process options"),
    )
    with pytest.raises(ValueError, match="unsupported process options"):
        repository_github_auth.run_repository_command(["gh", "api", "user"], **options)


@pytest.mark.parametrize("old_state", ["reserved", "claimed", "released"])
@pytest.mark.parametrize("new_state", ["claimed", "released"])
def test_newer_in_place_snapshot_does_not_require_an_obsolete_predecessor(old_state, new_state):
    old = _reservation(occurrence=3600, state=old_state)
    if old_state in {"claimed", "released"}:
        old.update(task_id="old-task", reason="old outcome")
    snapshot = _reservation(state=new_state, task_id="new-task")
    if new_state == "released":
        snapshot["reason"] = "new outcome"
    issue = _issue(7, reservations=(old, snapshot, old))
    assert _latest_reservations(issue)["backlog"] == snapshot


@pytest.mark.parametrize("mode", [None, "ambient", "repository"])
def test_github_auth_mode_survives_declaration_normalization(mode):
    forge = {"provider": "github", "producer_login": "issue-bot"}
    if mode is not None:
        forge["auth"] = mode
    normalized = validate_config(_config(forge=forge))
    assert normalized["forge"].get("auth", "ambient") == (mode or "ambient")


@pytest.mark.parametrize("mode", ["", True, [], "unknown"])
def test_invalid_github_auth_mode_is_refused(mode):
    with pytest.raises(RegistrarError, match="forge.auth"):
        validate_config(_config(forge={
            "provider": "github", "producer_login": "issue-bot", "auth": mode,
        }))


def test_repository_auth_is_not_accepted_by_another_forge():
    with pytest.raises(RegistrarError, match="forge.auth"):
        validate_config(_config(forge={
            "provider": "azure-devops", "producer_login": "issue-bot",
            "auth": "repository",
        }))


class ScopedRunner:
    def __init__(self, *, account="issue-bot", token_code=0, token_error="", token=None,
                 identity="issue-bot"):
        self.account = account
        self.token_code = token_code
        self.token_error = token_error
        self.token = token
        self.identity = identity
        self.calls = []
        self.tokens = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[0] == "scoped-owner":
            assert kwargs["timeout"] > 0
            assert "COPILOT_PLUGIN_ROOT" not in kwargs["env"]
            if argv[1:3] == ["repos", "account-for"]:
                value, code, error = self.account, 0, ""
            else:
                assert argv[1:] == [
                    "repos", "gh", "example/project", "--", "auth", "token",
                ]
                value = self.token if self.token is not None else f"synthetic-{len(self.tokens)}"
                self.tokens.append(value)
                code, error = self.token_code, self.token_error
        else:
            assert argv[0] == "gh"
            assert kwargs["env"]["GH_TOKEN"] == self.tokens[-1]
            assert kwargs["timeout"] > 0
            value, code, error = self.identity, 0, ""
        return SimpleNamespace(returncode=code, stdout=value, stderr=error)


def _scoped_provider(monkeypatch, runner):
    monkeypatch.setattr(procutil, "agent_worktrees_launch_prefix", lambda: ["scoped-owner"])
    return GitHubProvider(
        "issue-bot", runner=runner, repository="example/project", auth="repository",
    )


class ScopedOperationRunner(ScopedRunner):
    def __init__(self):
        super().__init__()
        self.comments = []
        self.on_view = self.on_comment = None
        self.repository_name = "example/project"

    def __call__(self, argv, **kwargs):
        if argv[0] != "gh":
            return super().__call__(argv, **kwargs)
        self.calls.append((argv, kwargs))
        assert kwargs["env"]["GH_TOKEN"] in self.tokens
        assert kwargs["timeout"] > 0
        if argv[1:3] == ["api", "user"]:
            value = self.identity
        elif argv[1:3] == ["api", "repos/example/project"]:
            value = self.repository_name
        elif argv[1:3] == ["issue", "view"]:
            callback, self.on_view = self.on_view, None
            if callback:
                callback()
            value = json.dumps({"comments": self.comments})
        elif argv[1:3] == ["issue", "edit"]:
            value = "{}"
        elif argv[1:3] == ["issue", "comment"] or any(
            "updateIssueComment" in argument for argument in argv
        ):
            body = (
                argv[argv.index("--body") + 1] if "--body" in argv
                else next(arg[5:] for arg in argv if arg.startswith("body="))
            )
            self.comments = [{
                "id": "comment-node", "author": {"login": "issue-bot"}, "body": body,
            }]
            if self.on_comment:
                self.on_comment()
            value = "{}"
        elif argv[1:3] == ["api", "graphql"]:
            page = _graphql_page([_graphql_issue(7, comments=self.comments)])
            if not any(argument.startswith("cursor=") for argument in argv):
                page["data"]["repository"]["issues"]["pageInfo"] = {
                    "hasNextPage": True, "endCursor": "next-page",
                }
            value = json.dumps(page)
        else:
            raise AssertionError(f"Unexpected command group: {argv[:3]}")
        return SimpleNamespace(returncode=0, stdout=value, stderr="")


def _public_operation(provider, operation):
    arguments = ("example/project", _issue(7), _reservation())
    if operation == "list_open_issues":
        return provider.list_open_issues(arguments[0])
    extra = ("task-real",) if operation == "claim" else (
        ("finished",) if operation == "release" else ()
    )
    return getattr(provider, operation)(*arguments, *extra)


@pytest.mark.parametrize("operation", ["list_open_issues", "reserve", "claim", "release"])
@pytest.mark.parametrize("existing_comment", [False, True])
def test_scoped_public_operation_resolves_once_and_renews_next_operation(
    monkeypatch, operation, existing_comment,
):
    monkeypatch.setenv("GH_TOKEN", "ambient-only")
    runner = ScopedOperationRunner()
    if existing_comment:
        runner.comments = [{
            "id": "comment-node", "author": {"login": "issue-bot"},
            "body": _marker(_reservation()),
        }]
    provider = _scoped_provider(monkeypatch, runner)
    environments = []
    for number in range(2):
        start = len(runner.calls)
        _public_operation(provider, operation)
        assert len(runner.tokens) == number + 1
        children = [kwargs["env"] for argv, kwargs in runner.calls[start:] if argv[0] == "gh"]
        assert children and all(env is children[0] for env in children)
        environments.append(children[0])
        if operation in {"reserve", "release"}:
            assert sum(argv[:3] == ["gh", "api", "user"] for argv, _ in runner.calls[start:]) == 3
            assert sum(argv[:3] == ["gh", "api", "repos/example/project"] for argv, _ in runner.calls[start:]) == 2
    assert environments[0] is not environments[1]
    assert environments[0]["GH_TOKEN"] != environments[1]["GH_TOKEN"]
    assert os.environ["GH_TOKEN"] == "ambient-only"
    assert not any(isinstance(value, dict) and "GH_TOKEN" in value for value in vars(provider).values())


@pytest.mark.parametrize("change", ["identity", "repository_name"])
def test_scoped_reserve_reverifies_before_label_and_failure_does_not_cache_env(
    monkeypatch, change,
):
    runner = ScopedOperationRunner()
    runner.on_comment = lambda: setattr(runner, change, "another-value")
    provider = _scoped_provider(monkeypatch, runner)
    with pytest.raises(RuntimeError, match="identity mismatch"):
        _public_operation(provider, "reserve")
    assert len(runner.tokens) == 1
    assert all(argv[:3] != ["gh", "issue", "edit"] for argv, _ in runner.calls)
    runner.on_comment = None
    setattr(runner, change, "issue-bot" if change == "identity" else "example/project")
    _public_operation(provider, "reserve")
    assert len(runner.tokens) == 2


def test_scoped_nested_operation_cannot_replace_callers_child_environment(monkeypatch):
    runner = ScopedOperationRunner()
    provider = _scoped_provider(monkeypatch, runner)
    runner.on_view = lambda: provider.list_open_issues("example/project")
    _public_operation(provider, "reserve")
    assert len(runner.tokens) == 2
    edited = [kwargs["env"] for argv, kwargs in runner.calls if argv[:3] == ["gh", "issue", "edit"]]
    assert len(edited) == 1 and edited[0]["GH_TOKEN"] == runner.tokens[0]


@pytest.mark.parametrize("arguments,group", [
    (("issue", "edit", "7", "--body", "synthetic-marker-secret"), "issue edit"),
    (("api", "graphql", "-f", "query=synthetic-query-secret"), "api graphql"),
    (("api", "repos/synthetic-endpoint-secret"), "api"),
    (("synthetic-command-secret", "synthetic-argument-secret"), "other"),
])
def test_scoped_error_reports_only_fixed_group_and_returncode(monkeypatch, arguments, group):
    runner = ScopedRunner()
    provider = _scoped_provider(monkeypatch, runner)

    def failed_operation(argv, **kwargs):
        if argv == ["gh", *arguments]:
            return SimpleNamespace(
                returncode=17, stdout="synthetic-stdout-secret",
                stderr="synthetic-stderr-secret " + runner.tokens[-1],
            )
        return runner(argv, **kwargs)

    provider.runner = failed_operation
    with pytest.raises(RuntimeError) as failure:
        provider._gh(*arguments)
    assert str(failure.value) == f"Repository GitHub operation failed ({group}, exit 17)"
    assert "secret" not in str(failure.value) and runner.tokens[-1] not in str(failure.value)


def test_scoped_credentials_are_renewed_and_never_change_parent_environment(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ambient-other-account")
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "dispatch-payload")
    runner = ScopedRunner()
    provider = _scoped_provider(monkeypatch, runner)
    provider._gh("api", "user")
    provider._gh("api", "user")
    assert runner.tokens == ["synthetic-0", "synthetic-1"]
    assert os.environ["GH_TOKEN"] == "ambient-other-account"
    assert os.environ["COPILOT_PLUGIN_ROOT"] == "dispatch-payload"
    children = [kwargs["env"] for argv, kwargs in runner.calls if argv[0] == "gh"]
    assert children[0] is not children[-1]


@pytest.mark.parametrize("settings", [
    {"account": "another-account"},
    {"account": ""},
    {"token_code": 1},
    {"token_error": "could not mint a gh token; using ambient auth"},
    {"token": ""},
    {"token": "two\nlines"},
])
def test_scoped_auth_failure_never_reaches_a_forge_mutation(monkeypatch, settings):
    runner = ScopedRunner(**settings)
    provider = _scoped_provider(monkeypatch, runner)
    with pytest.raises(RuntimeError):
        provider._gh("issue", "edit", "7", "--add-label", "claimed")
    assert all(argv[0] != "gh" for argv, _kwargs in runner.calls)


def test_missing_optional_sibling_fails_only_the_opted_in_source(monkeypatch):
    monkeypatch.setattr(procutil, "agent_worktrees_launch_prefix", lambda: None)
    provider = GitHubProvider("issue-bot", repository="example/project", auth="repository")
    with pytest.raises(RuntimeError, match="agent-worktrees"):
        provider._gh("issue", "edit", "7")
    calls = []
    ambient = GitHubProvider("issue-bot", runner=lambda argv, **kw: (
        calls.append((argv, kw)) or SimpleNamespace(returncode=0, stdout="{}", stderr="")
    ))
    ambient._gh("api", "user")
    assert calls[0][0] == ["gh", "api", "user"]
    assert "env" not in calls[0][1]


def test_minted_credential_identity_is_checked_before_mutation(monkeypatch):
    runner = ScopedRunner(identity="another-account")
    provider = _scoped_provider(monkeypatch, runner)
    with pytest.raises(RuntimeError, match="credential identity"):
        provider._gh("issue", "edit", "7")
    assert all(argv[:3] != ["gh", "issue", "edit"] for argv, _kwargs in runner.calls)


def test_scoped_provider_refuses_another_repository_before_credentials(monkeypatch):
    runner = ScopedRunner()
    provider = _scoped_provider(monkeypatch, runner)
    with pytest.raises(RuntimeError, match="scoped authentication target"):
        provider.list_open_issues("another/project")
    assert runner.calls == []


def test_provider_factory_preserves_repository_auth():
    provider = _forge_provider_for(validate_config(_config(forge={
        "provider": "github", "producer_login": "issue-bot", "auth": "repository",
    })))
    assert provider.repository == "example/project"
    assert provider.auth == "repository"


def test_repository_auth_crosses_real_child_environment_boundary(tmp_path, monkeypatch):
    script = tmp_path / "credential_boundary.py"
    script.write_text(
        "import os, sys\n"
        "a = sys.argv[1:]\n"
        "if a[:2] == ['repos', 'account-for']:\n"
        "    print('issue-bot')\n"
        "elif a[:2] == ['repos', 'gh']:\n"
        "    print('synthetic-child-credential')\n"
        "else:\n"
        "    assert os.environ['GH_TOKEN'] == 'synthetic-child-credential'\n"
        "    print('issue-bot')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GH_TOKEN", "ambient-other-account")
    monkeypatch.setattr(
        procutil, "agent_worktrees_launch_prefix", lambda: [sys.executable, str(script)]
    )

    def runner(argv, **kwargs):
        if argv[0] == "gh":
            argv = [sys.executable, str(script), *argv[1:]]
        return subprocess.run(argv, **kwargs)

    provider = GitHubProvider(
        "issue-bot", runner=runner, repository="example/project", auth="repository",
    )
    assert provider._gh("api", "user").stdout.strip() == "issue-bot"
    assert os.environ["GH_TOKEN"] == "ambient-other-account"
