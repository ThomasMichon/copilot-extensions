"""Real reconciliation decisions stop before writes on changed or unknown gates."""

from __future__ import annotations

import importlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest

from test_maintainer_completion_policy import CONFIG, HEAD, ready

if TYPE_CHECKING:
    from test_maintainer_completion_workflow import ControllerModule
    from maintainer_pr_completion import Settings
    from maintainer_pr_completion_policy import CompletionPolicy, CompletionSnapshot
    from maintainer_pr_completion_github import GitHubEvidence

    class ReconcileClient(Protocol):
        repository: str
        host: str
        write_attempted: bool

        def snapshot(self, number: int, policy: CompletionPolicy) -> CompletionSnapshot: ...
        def scope_snapshot(self, number: int) -> tuple[CompletionSnapshot, dict[str, Any]]: ...
        def require_live_base(self, snapshot: CompletionSnapshot) -> None: ...
        def invited_collaborator(self, author: str) -> bool: ...
        def merge_invoke(self, argv: list[str]) -> subprocess.CompletedProcess[str]: ...
        def metadata(self, number: int) -> dict[str, Any]: ...
        def write(self, endpoint: str, *fields: str) -> object: ...
        def pages(self, endpoint: str) -> list[dict[str, Any]]: ...


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def controller(monkeypatch: pytest.MonkeyPatch) -> ControllerModule:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    return cast("ControllerModule", importlib.import_module("maintainer_pr_completion"))


@pytest.fixture
def admitted(monkeypatch: pytest.MonkeyPatch, controller: ControllerModule) -> Settings:
    config = controller.Settings("enabled", CONFIG, "completion-example", 17)
    for key, value in {
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": CONFIG.repository,
        "GITHUB_REF": "refs/heads/main", "GITHUB_WORKFLOW_SHA": "d" * 40,
        "GITHUB_WORKFLOW_REF": (
            f"{CONFIG.repository}/.github/workflows/maintainer-pr-completion.yml@refs/heads/main"
        ),
        "COMPLETION_APP_SLUG": "completion-example", "COMPLETION_APP_ID": "17",
        "COMPLETION_INSTALLATION_ID": "23",
    }.items():
        monkeypatch.setenv(key, value)
    return config


class FakeGitHub:
    repository = CONFIG.repository
    host = "github.com"

    def __init__(
        self, snapshots: list[CompletionSnapshot], *, comments: list[dict[str, Any]] | None = None,
    ) -> None:
        self.snapshots = list(snapshots)
        self.comments = comments or []
        self.writes: list[tuple[str, list[str] | tuple[str, ...]]] = []
        self.write_attempted = False
        self.result_head = "c" * 40
        self.write_error: Exception | None = None
        self.scope_override: CompletionSnapshot | None = None
        self.invited = True
        self.merge_codes = [0]
        self.metadata_result: dict[str, Any] = {
            "number": 7, "node_id": "PR_example", "state": "closed", "merged": True,
            "head": {"sha": HEAD}, "base": {"ref": "dev"},
            "merged_by": {"login": "completion-example[bot]", "type": "Bot"},
        }

    def snapshot(self, number: int, policy: CompletionPolicy) -> CompletionSnapshot:
        return self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]

    def pages(self, endpoint: str) -> list[dict[str, Any]]:
        return self.comments

    def scope_snapshot(self, number: int) -> tuple[CompletionSnapshot, dict[str, Any]]:
        return self.scope_override or self.snapshots[-1], {"user": {"login": "author"}}

    def require_live_base(self, snapshot: CompletionSnapshot) -> None:
        return None

    def invited_collaborator(self, author: str) -> bool:
        return self.invited

    def merge_invoke(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.write_attempted = True
        self.writes.append(("merge", argv))
        code = self.merge_codes.pop(0) if len(self.merge_codes) > 1 else self.merge_codes[0]
        return subprocess.CompletedProcess(argv, code, "", "review gate" if code else "")

    def metadata(self, number: int) -> dict[str, Any]:
        return self.metadata_result

    def write(self, endpoint: str, *fields: str) -> dict[str, Any]:
        self.write_attempted = True
        self.writes.append((endpoint, fields))
        if self.write_error is not None:
            raise self.write_error
        if endpoint == "graphql":
            return {"data": {"updatePullRequestBranch": {"pullRequest": {
                "id": "PR_example", "headRefOid": self.result_head, "baseRefName": "dev",
            }}}}
        body = next(value.removeprefix("body=") for value in fields if value.startswith("body="))
        comment = {"id": 31, "body": body,
                   "user": {"login": "completion-example[bot]", "type": "Bot"}}
        self.comments[:] = [comment]
        return comment


if TYPE_CHECKING:
    def _verify_reconcile_clients(real: GitHubEvidence, fake: FakeGitHub) -> None:
        _real: ReconcileClient = real
        _fake: ReconcileClient = fake


def test_disabled_configuration_never_reads_or_writes(controller: ControllerModule) -> None:
    client = FakeGitHub([])
    result = controller.reconcile(
        cast("GitHubEvidence", client), 7, controller.Settings("disabled", CONFIG, None, None), apply=True,
    )
    assert result == {"pr": 7, "action": "disabled", "applied": False}
    assert client.writes == []


def test_report_only_never_constructs_a_writer(controller: ControllerModule) -> None:
    client = FakeGitHub([replace(ready(), update_required=True)])
    result = controller.reconcile(
        cast("GitHubEvidence", client), 7, controller.Settings("report-only", CONFIG, None, None), apply=False,
    )
    assert result["action"] == "rebase" and result["applied"] is False
    assert client.writes == []


@pytest.mark.parametrize("changes", [
    {"head_sha": "c" * 40},
    {"base_sha": "c" * 40},
    {"base_ref": "main"},
    {"labels": ()},
    {"labels": ("auto-merge", "hold")},
    {"draft": True},
    {"required_checks_verified": False},
    {"invited_collaborator": None},
    {"root_codeowner": False},
])
def test_fresh_gate_or_identity_change_prevents_native_update(
    controller: ControllerModule, admitted: Settings, changes: dict[str, Any],
) -> None:
    initial = replace(ready(), update_required=True)
    client = FakeGitHub([initial, replace(initial, **changes)])
    result = controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    assert result["action"] == "wait" and result["applied"] is False
    assert client.writes == []


def test_new_reviewer_negative_between_reads_prevents_update(controller: ControllerModule, admitted: Settings) -> None:
    initial = replace(ready(), update_required=True)
    changed = replace(initial, reviews=replace(initial.reviews, change_requests=("human",)))
    client = FakeGitHub([initial, changed])
    assert controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)["action"] == "wait"
    assert client.writes == []


@pytest.mark.parametrize("changes", [
    {"base_ref": "main"}, {"head_sha": "c" * 40}, {"labels": ()},
    {"labels": ("auto-merge", "hold")}, {"draft": True},
])
def test_scope_is_fenced_again_after_the_full_evidence_read(
    controller: ControllerModule, admitted: Settings, changes: dict[str, Any],
) -> None:
    initial = replace(ready(), update_required=True)
    client = FakeGitHub([initial])
    client.scope_override = replace(initial, **changes)
    with pytest.raises(controller.EvidenceUnavailable, match="before the write"):
        controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    assert client.writes == [] and not client.write_attempted


def test_revoked_invitation_is_checked_at_the_write_boundary(controller: ControllerModule, admitted: Settings) -> None:
    client = FakeGitHub([replace(ready(), update_required=True)])
    client.invited = False
    with pytest.raises(controller.EvidenceUnavailable, match="invitation changed"):
        controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    assert client.writes == [] and not client.write_attempted


@pytest.mark.parametrize("key,value", [
    ("GITHUB_ACTIONS", ""), ("GITHUB_REPOSITORY", "example/other"),
    ("GITHUB_REF", "refs/heads/dev"), ("GITHUB_WORKFLOW_SHA", "short"),
    ("GITHUB_WORKFLOW_REF", "example/project/workflow@refs/pull/7/head"),
    ("COMPLETION_APP_SLUG", "other"), ("COMPLETION_APP_ID", "99"),
    ("COMPLETION_INSTALLATION_ID", ""), ("COMPLETION_INSTALLATION_ID", "0"),
])
def test_unapproved_or_head_controlled_writer_context_never_mutates(
    controller: ControllerModule, admitted: Settings, monkeypatch: pytest.MonkeyPatch, key: str, value: str,
) -> None:
    client = FakeGitHub([replace(ready(), update_required=True)])
    monkeypatch.setenv(key, value)
    with pytest.raises(controller.EvidenceUnavailable):
        controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    assert client.writes == []


def test_native_update_is_expected_head_rebase_and_never_merges(controller: ControllerModule, admitted: Settings) -> None:
    client = FakeGitHub([replace(ready(), update_required=True)])
    result = controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    assert result["action"] == "rebased" and result["head"] == "c" * 40
    assert len(client.writes) == 1
    endpoint, fields = client.writes[0]
    assert endpoint == "graphql" and f"head={HEAD}" in fields and "id=PR_example" in fields
    query = next(field for field in fields if field.startswith("query="))
    assert "expectedHeadOid:$head" in query and "updateMethod:REBASE" in query
    assert "mergePullRequest" not in query


def test_old_head_in_rebase_ack_is_not_reported_as_completed(controller: ControllerModule, admitted: Settings) -> None:
    client = FakeGitHub([replace(ready(), update_required=True)])
    client.result_head = HEAD
    result = controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    assert result["action"] == "rebase-requested" and result["applied"] is None
    assert len(client.writes) == 1


def test_uncertain_write_is_not_retried_or_reported_as_a_conflict(controller: ControllerModule, admitted: Settings) -> None:
    client = FakeGitHub([replace(ready(), update_required=True)])
    client.write_error = controller.EvidenceUnavailable("request timed out")
    with pytest.raises(controller.MutationUncertain, match="timed out"):
        controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    assert len(client.writes) == 1


def test_conflict_has_one_owned_notice_across_duplicate_events(controller: ControllerModule, admitted: Settings) -> None:
    client = FakeGitHub([replace(ready(), mergeable=False)])
    first = controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    second = controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    assert first["action"] == second["action"] == "conflict"
    assert first["applied"] is True and second["applied"] is False
    assert len(client.writes) == 1
    assert client.comments[0]["body"].startswith(controller.CONFLICT_MARKER)
    assert "original submitter" in client.comments[0]["body"]


def test_conflict_notice_updates_in_place_without_new_comment(controller: ControllerModule, admitted: Settings) -> None:
    snapshot = replace(ready(), mergeable=False)
    client = FakeGitHub([snapshot])
    controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    client.snapshots[:] = [replace(snapshot, base_sha="d" * 40)]
    controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    assert len(client.writes) == 2
    endpoint, fields = client.writes[-1]
    assert endpoint.endswith("/issues/comments/31") and "PATCH" in fields


def test_checked_in_activation_is_off_and_apply_is_a_refusal(
    controller: ControllerModule, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    config = controller.settings()
    assert config.mode == "disabled" and config.app_slug is None and config.app_id is None
    monkeypatch.setattr(controller, "invoke", lambda argv: pytest.fail("disabled mode touched GitHub"))
    monkeypatch.setattr("sys.argv", ["completion", "--apply"])
    assert controller.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "disabled" and payload["applied"] is False


@pytest.fixture
def merge_helper(
    controller: ControllerModule, monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, int, dict[str, Any]]]:
    monkeypatch.syspath_prepend(str(ROOT / "plugins" / "agent-worktrees" / "src"))
    helper = importlib.import_module("agent_worktrees.providers.github_required_checks")
    calls = []

    def merge(repository: str, number: int, **kwargs: Any) -> str:
        calls.append((repository, number, kwargs))
        argv = ["gh", "pr", "merge", str(number), "--match-head-commit", kwargs["expected_head_sha"]]
        result = kwargs["invoke"](argv)
        if result.returncode:
            result = kwargs["invoke"]([*argv, "--admin"])
        return "merge refused" if result.returncode else ""

    monkeypatch.setattr(helper, "merge_pull", merge)
    return calls


def test_merge_delegates_exact_head_and_holds_to_the_shared_effect_boundary(
    controller: ControllerModule, admitted: Settings,
    merge_helper: list[tuple[str, int, dict[str, Any]]],
) -> None:
    client = FakeGitHub([ready()])
    result = controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    assert result["action"] == "merged" and result["applied"] is True
    repository, number, kwargs = merge_helper[0]
    assert repository == CONFIG.repository and number == 7
    assert kwargs["expected_head_sha"] == HEAD and kwargs["squash"] is True
    assert kwargs["hold_labels"] == CONFIG.hold_labels
    assert kwargs["wip_title_prefixes"] == CONFIG.wip_title_prefixes
    assert kwargs["delete_source_branch"] is False and len(client.writes) == 1


def test_scope_fence_also_applies_to_the_shared_helpers_admin_fallback(
    controller: ControllerModule, admitted: Settings,
    merge_helper: list[tuple[str, int, dict[str, Any]]],
) -> None:
    client = FakeGitHub([ready()])
    client.merge_codes = [1, 0]
    original = client.merge_invoke

    def revoke_after_normal(argv: list[str]) -> subprocess.CompletedProcess[str]:
        result = original(argv)
        client.scope_override = replace(ready(), labels=())
        return result

    setattr(client, "merge_invoke", revoke_after_normal)
    with pytest.raises(controller.MutationUncertain, match="scope changed"):
        controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    assert len(client.writes) == 1 and "--admin" not in client.writes[0][1]


@pytest.mark.parametrize("changes", [
    {"node_id": "different"}, {"head": {"sha": "b" * 40}},
    {"base": {"ref": "main"}}, {"merged": None}, {"state": "open"},
    {"merged_by": {"login": "human", "type": "User"}},
])
def test_merge_receipt_is_not_completion_without_candidate_and_actor_confirmation(
    controller: ControllerModule, admitted: Settings,
    merge_helper: list[tuple[str, int, dict[str, Any]]], changes: dict[str, Any],
) -> None:
    client = FakeGitHub([ready()])
    client.metadata_result.update(changes)
    with pytest.raises(controller.MutationUncertain):
        controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    assert len(client.writes) == 1


def test_unconfirmed_merge_remains_pending_without_polling_or_another_write(
    controller: ControllerModule, admitted: Settings,
    merge_helper: list[tuple[str, int, dict[str, Any]]],
) -> None:
    client = FakeGitHub([ready()])
    client.metadata_result.update(merged=False, state="open", merged_by=None)
    result = controller.reconcile(cast("GitHubEvidence", client), 7, admitted, apply=True)
    assert result["action"] == "merge-requested" and result["applied"] is None
    assert len(client.writes) == 1


def test_cli_transport_pins_the_same_host_for_api_and_native_merge(
    controller: ControllerModule, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_HOST", "enterprise.example")
    calls = []
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(controller.subprocess, "run", run)
    controller.invoke(["gh", "pr", "merge", "7"])
    assert calls[0][1]["env"]["GH_HOST"] == "github.com"
    assert calls[0][1]["timeout"] == 45 and calls[0][1]["check"] is False


def test_one_unreadable_candidate_does_not_hide_other_observations(
    controller: ControllerModule, admitted: Settings, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(controller, "settings", lambda: replace(admitted, mode="report-only"))
    monkeypatch.setattr("sys.argv", ["completion", "--report-only"])
    monkeypatch.setattr(controller, "candidates", lambda *args: [7, 8])

    def observe(client: GitHubEvidence, number: int, config: Settings, *, apply: bool) -> dict[str, Any]:
        if number == 8:
            raise controller.EvidenceUnavailable("history incomplete")
        return {"pr": number, "action": "wait", "applied": False}

    monkeypatch.setattr(controller, "reconcile", observe)
    assert controller.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and [row["pr"] for row in result["results"]] == [7, 8]
    assert result["results"][1]["action"] == "unavailable"


def test_uncertain_write_stops_the_pass_and_retains_already_confirmed_results(
    controller: ControllerModule, admitted: Settings, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(controller, "settings", lambda: admitted)
    monkeypatch.setattr("sys.argv", ["completion", "--apply"])
    monkeypatch.setattr(controller, "candidates", lambda *args: [7, 8, 9])
    calls = []

    def apply(client: GitHubEvidence, number: int, config: Settings, **kwargs: object) -> dict[str, Any]:
        calls.append(number)
        if number == 8:
            raise controller.MutationUncertain("acknowledgement lost")
        return {"pr": number, "action": "merged", "applied": True}

    monkeypatch.setattr(controller, "reconcile", apply)
    assert controller.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert calls == [7, 8] and result["action"] == "unconfirmed"
    assert result["failed_action_applied"] is None
    assert result["completed_results"] == [{"pr": 7, "action": "merged", "applied": True}]


def test_api_deadline_refuses_a_request_instead_of_spawning_past_its_budget(
    controller: ControllerModule, admitted: Settings, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(controller, "settings", lambda: replace(admitted, mode="report-only"))
    monkeypatch.setattr("sys.argv", ["completion", "--report-only"])
    clock = iter((0, 200))
    monkeypatch.setattr(controller.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(controller, "invoke", lambda argv: pytest.fail("out-of-budget request"))
    monkeypatch.setattr(controller, "GitHubEvidence", lambda repository, invoke: (
        SimpleNamespace(invoke=invoke)
    ))
    monkeypatch.setattr(controller, "candidates", lambda client, *args: (
        client.invoke(["gh", "api", "repos/example/project/pulls"])
    ))
    assert controller.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "unavailable" and "budget" in result["error"]
    assert result["completed_results"] == []


def test_report_only_does_not_read_through_an_ambient_nonworkflow_account(
    controller: ControllerModule, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    config = controller.Settings("report-only", CONFIG, None, None)
    monkeypatch.setattr(controller, "settings", lambda: config)
    monkeypatch.setattr("sys.argv", ["completion", "--report-only"])
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(controller, "invoke", lambda argv: pytest.fail("ambient account used"))
    assert controller.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert "trusted Main" in result["error"] and result["failed_action_applied"] is False
