"""Synthetic native transport controls for the actual selected-thread owner."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.client import IncompleteRead
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import pytest

from agent_worktrees import __main__ as cli, config as cfg, output, pr_cli, pr_config
from agent_worktrees import pr_ado_remediation, pr_author_cli
from agent_worktrees.providers import azure_devops as azure, azure_observation_reads as reads
from agent_worktrees.providers.azure_discussions import get_report
from agent_worktrees.providers.azure_remediation import thread_fingerprint
from agent_worktrees.providers.azure_thread_resolution import resolve

REPO = "Project/repository"
ORG = "https://dev.azure.com/example"
PROJECT = "11111111-1111-1111-1111-111111111111"
REPOSITORY = "22222222-2222-2222-2222-222222222222"
ACTOR = "33333333-3333-3333-3333-333333333333"
AT = "2026-11-09T01:00:00Z"


class Response:
    status = 200
    headers: dict = {}

    def __init__(self, value: Any, *, truncated: bool = False) -> None:
        self.data = BytesIO(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.truncated = truncated

    def read(self, size: int) -> bytes:
        if self.truncated:
            raise IncompleteRead(b"partial", 100)
        return self.data.read(size)

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class Backend:
    def __init__(self, root: Path | None = None, *, reopening: bool = False) -> None:
        self.root = root
        self.desired_status = "active" if reopening else "closed"
        self.pull = {
            "pullRequestId": 7, "status": "active", "createdBy": {"id": "author"},
            "repository": {"id": REPOSITORY, "name": "repository",
                           "project": {"id": PROJECT, "name": "Project"}},
            "sourceRefName": "refs/heads/topic", "targetRefName": "refs/heads/main",
            "lastMergeSourceCommit": {"commitId": "a" * 40},
            "lastMergeTargetCommit": {"commitId": "b" * 40},
            "isDraft": True, "reviewers": [{"id": ACTOR, "vote": -10}],
            "labels": ["paused"], "autoCompleteSetBy": {"id": "existing"},
        }
        self.iterations = [{
            "id": 1, "sourceRefCommit": {"commitId": "a" * 40},
            "targetRefCommit": {"commitId": "b" * 40},
            "commonRefCommit": {"commitId": "c" * 40},
        }]
        self.rows = [{
            "id": 9, "status": "active", "lastUpdatedDate": AT,
            "pullRequestThreadContext": {"iterationContext": {
                "firstComparingIteration": 1, "secondComparingIteration": 1}},
            "comments": [{"id": 1, "parentCommentId": 0, "commentType": 1,
                          "content": "Please preserve this text.", "author": {"id": "reviewer"},
                          "publishedDate": AT, "lastUpdatedDate": AT}],
        }]
        self.rows.append({**deepcopy(self.rows[0]), "id": 10})
        if reopening:
            self.rows[0]["status"] = "closed"
        if root is not None and (root / "native.json").exists():
            self.rows = json.loads((root / "native.json").read_text())
        self.actor = ACTOR
        self.writes = []
        self.pr_reads = 0
        self.on_pr = None
        self.fault = ""
        self.allowed = True
        self.count = True
        self.continuation = False
        self.now = 0

    def open(self, request: Any, **kwargs: Any) -> Response:
        method, url = request.get_method(), request.full_url
        path = urlsplit(url).path
        if method == "PATCH":
            assert path.endswith("/pullRequests/7/threads/9")
            body = json.loads(request.data)
            assert body == {"status": self.desired_status}
            self.writes.append((method, path, body))
            if self.root is not None:
                with (self.root / "writes").open("a") as stream:
                    stream.write("PATCH\n")
            if self.fault == "reject":
                raise HTTPError(url, 403, "denied", {}, None)
            if self.fault == "timeout":
                raise TimeoutError("bounded send expired")
            self.rows[0]["status"] = self.desired_status
            self.rows[0]["lastUpdatedDate"] = "2026-11-09T02:00:00Z"
            if self.root is not None:
                (self.root / "native.json").write_text(json.dumps(self.rows))
            if self.fault == "crash-after":
                raise SystemExit(17)
            if self.fault == "lost":
                raise URLError("response lost after apply")
            if self.fault == "malformed":
                return Response(b"[]")
            if self.fault == "late":
                self.now = 121
            if self.fault == "edited-comment":
                self.rows[0]["comments"][0]["lastUpdatedDate"] = "2026-11-09T03:00:00Z"
            response = deepcopy(self.rows[0])
            if self.fault == "wrong-response":
                response["id"] = 10
            if self.fault == "bool-response":
                response["status"] = True
            if self.fault == "float-response":
                response["status"] = 1.0
            if self.fault == "numeric-response":
                response["status"] = 1
            if self.fault == "bool-readback":
                self.rows[0]["status"] = True
            if self.fault == "reclosed":
                self.rows[0]["status"] = "closed"
            return Response(response, truncated=self.fault == "truncated-patch")
        assert method == "GET", "no POST, reply, vote, consent, push or alternate transport"
        if path.endswith("/connectionData"):
            return Response({"authorizedUser": {"id": self.actor}})
        if path.endswith("/pullRequests/7"):
            self.pr_reads += 1
            if self.on_pr:
                self.on_pr(self)
            if self.writes and self.fault == "readback":
                raise URLError("readback unavailable")
            if self.writes and self.fault == "changed-base":
                self.change_base()
            return Response(self.pull, truncated=self.fault == "truncated-readback" and bool(self.writes))
        if path.endswith("/securitynamespaces"):
            namespace = "44444444-4444-4444-4444-444444444444"
            return Response({"count": 1, "value": [{
                "name": "Git Repositories", "namespaceId": namespace,
                "separatorValue": "/", "structureValue": 1, "useTokenTranslator": False,
                "readPermission": 1, "actions": [
                    {"name": name, "bit": bit, "namespaceId": namespace}
                    for name, bit in (("GenericRead", 1), ("PullRequestContribute", 2))
                ],
            }]})
        if "/permissions/" in path:
            return Response({"count": 2, "value": [True, self.allowed]})
        values = self.iterations if path.endswith("/iterations") else self.rows
        assert path.endswith(("/iterations", "/threads"))
        response = Response({"value": values, **({"count": len(values)} if self.count else {})})
        response.headers = {"x-ms-continuationtoken": "unsupported"} if self.continuation else {}
        return response

    def change_base(self) -> None:
        self.pull["lastMergeTargetCommit"]["commitId"] = "d" * 40

    def expectations(self) -> dict[str, str]:
        report = get_report(azure.AzureDevOpsProvider(), REPO, 7, api_base=ORG, token="fixture")
        return {
            "expected_candidate": report["resolution_candidate"]["candidate"]["fingerprint"],
            "expected_thread": thread_fingerprint(self.rows[0]), "operation_id": "fix:thread-9",
        }


def configure(monkeypatch: pytest.MonkeyPatch, root: Path, backend: Backend) -> cfg.Config:
    monkeypatch.setattr(reads, "build_opener", lambda *args: backend)
    monkeypatch.setattr(pr_ado_remediation, "registry_path", lambda name: root / "ledger")
    target = cfg.RepoConfig(anchor="", worktree_root="", pr=cfg.PRConfig(
        enabled=True, provider="azure-devops", repository=REPO, api_base=ORG,
    ))
    caller = cfg.RepoConfig(anchor=str(root), worktree_root="", pr=cfg.PRConfig(provider="github"))
    config = cfg.Config(srcroot=str(root), machine="test", platform="linux", repo_name="caller",
                        repos={"caller": caller}, explicit_pr_target=target)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cli.front_door_cli, "resolve_invocation_project", lambda *a: True)
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: ("claimant", ""))
    from agent_worktrees import providers
    monkeypatch.setattr(providers, "account_token_for_slug",
                        lambda repo, policy: "fixture" if repo == REPO and policy is target.pr else pytest.fail("auth"))
    return config


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Backend:
    server = Backend()
    configure(monkeypatch, tmp_path, server)
    return server


def execute(backend: Backend, **kwargs: Any) -> dict:
    return resolve(azure.AzureDevOpsProvider(), REPO, 7, 9, api_base=ORG, token="fixture", **kwargs)


def status(result: dict) -> str:
    return result["actions"]["resolution"]["status"]


def arguments(expected: dict[str, str]) -> list[str]:
    return [
        "pr-thread-resolve", "--repo", REPO, "--number", "7", "--thread", "9", "--json",
        "--expected-candidate", expected["expected_candidate"],
        "--expected-thread", expected["expected_thread"], "--operation-id", expected["operation_id"],
    ]


def test_actual_cli_closes_only_selected_thread_and_retains_all_independent_state(backend: Backend) -> None:
    before, sibling = deepcopy(backend.pull), deepcopy(backend.rows[1])
    expected = backend.expectations()
    with output.capture_json_output() as capture:
        assert cli.main(arguments(expected)) == 0
    result = json.loads(capture.getvalue())
    assert result["ok"] and status(result) == "confirmed" and result["actor"] == ACTOR
    assert backend.pull == before and backend.rows[1] == sibling
    assert backend.rows[0]["comments"][0]["content"] == "Please preserve this text."
    assert execute(backend, **expected)["reconciled"]
    assert len(backend.writes) == 1


@pytest.mark.parametrize("damage", ["base", "source-ref", "target-ref", "source-repository", "head", "thread"])
def test_full_candidate_and_thread_changes_refuse_even_with_same_head(backend: Backend, damage: str) -> None:
    expected = backend.expectations()
    if damage == "base":
        backend.change_base()
    elif damage.endswith("-ref"):
        backend.pull["sourceRefName" if damage == "source-ref" else "targetRefName"] += "-moved"
    elif damage == "source-repository":
        source = deepcopy(backend.pull["repository"])
        source["id"] = "55555555-5555-5555-5555-555555555555"
        backend.pull["forkSource"] = {"repository": source}
    elif damage == "head":
        backend.pull["lastMergeSourceCommit"]["commitId"] = "e" * 40
        backend.iterations[0]["sourceRefCommit"]["commitId"] = "e" * 40
    else:
        backend.rows[0]["comments"][0]["content"] = "concurrently edited"
    assert status(execute(backend, **expected)) == "not-attempted"
    assert not backend.writes


@pytest.mark.parametrize("damage", [
    "count", "continuation", "duplicate", "missing", "deleted", "closed", "pending",
    "system", "vote", "iteration", "no-iteration", "comments", "author", "identity",
    "permission", "pr-number", "parent",
])
def test_unavailable_or_inapplicable_native_evidence_never_dispatches(backend: Backend, damage: str) -> None:
    expected = backend.expectations()
    row = backend.rows[0]
    if damage == "count":
        backend.count = False
    elif damage == "continuation":
        backend.continuation = True
    elif damage == "duplicate":
        backend.rows.append(deepcopy(row))
    elif damage == "missing":
        backend.rows = backend.rows[1:]
    elif damage == "deleted":
        row["isDeleted"] = True
    elif damage in ("closed", "pending"):
        row["status"] = damage
    elif damage == "system":
        row["comments"][0]["commentType"] = 2
    elif damage == "vote":
        row["properties"] = {"CodeReviewThreadType": {"$value": "VoteUpdate"}}
    elif damage == "iteration":
        row["pullRequestThreadContext"]["iterationContext"]["secondComparingIteration"] = 99
    elif damage == "no-iteration":
        row.pop("pullRequestThreadContext")
    elif damage == "parent":
        row["comments"][0]["parentCommentId"] = 99
    elif damage == "comments":
        row.pop("comments")
    elif damage == "author":
        row["comments"][0]["author"] = []
    elif damage == "identity":
        backend.actor = ""
    elif damage == "permission":
        backend.allowed = False
    else:
        backend.pull["pullRequestId"] = 8
    result = execute(backend, **expected)
    assert status(result) == "not-attempted" and result["error"]
    assert not backend.writes


def test_candidate_movement_during_preflight_and_report_is_explicit(backend: Backend) -> None:
    expected = backend.expectations()
    backend.pr_reads = 0
    backend.on_pr = lambda server: server.change_base() if server.pr_reads >= 3 else None
    assert status(execute(backend, **expected)) == "not-attempted"
    assert not backend.writes
    backend.on_pr = lambda server: server.pull.update(sourceRefName="refs/heads/moved") if server.pr_reads >= 2 else None
    backend.pr_reads = 0
    report = get_report(azure.AzureDevOpsProvider(), REPO, 7, api_base=ORG, token="fixture")
    assert report["complete"] and not report["candidate_changed"]
    assert report["resolution_candidate"]["changed"] and not report["resolution_candidate"]["available"]


def test_stable_target_only_advance_supplies_a_fresh_usable_candidate(backend: Backend) -> None:
    prior = backend.expectations()
    backend.change_base()
    assert backend.iterations[0]["targetRefCommit"]["commitId"] == "b" * 40
    expected = backend.expectations()
    assert expected["expected_candidate"] != prior["expected_candidate"]
    assert status(execute(backend, **prior)) == "not-attempted"
    assert status(execute(backend, **expected)) == "confirmed"
    assert len(backend.writes) == 1


@pytest.mark.parametrize("fault", [
    "reject", "timeout", "lost", "malformed", "wrong-response", "edited-comment", "readback",
    "changed-base", "truncated-patch", "truncated-readback",
])
def test_authoritative_rejection_or_unknown_never_issues_a_second_patch(backend: Backend, fault: str) -> None:
    expected = backend.expectations()
    backend.fault = fault
    result = execute(backend, **expected)
    assert status(result) == ("failed" if fault == "reject" else "unknown")
    backend.fault = ""
    backend.rows[0]["status"] = "closed"
    recovered = execute(backend, **expected)
    assert status(recovered) == status(result) and recovered["reconciled"]
    assert len(backend.writes) == 1
    conflicting = execute(backend, **{**expected, "expected_thread": "sha256:" + "0" * 64})
    assert conflicting["reason"] == "operation-conflict" and conflicting["original_operation"] == result
    assert len(backend.writes) == 1


def test_truncated_consumed_key_read_preserves_original_unknown_envelope(backend: Backend) -> None:
    expected = backend.expectations()
    backend.fault = "lost"
    original = execute(backend, **expected)
    backend.fault = ""
    next_snapshot = backend.pr_reads + 2
    backend.on_pr = lambda server: setattr(
        server, "fault", "truncated-readback",
    ) if server.pr_reads >= next_snapshot else None
    recovered = execute(backend, **expected)
    assert status(recovered) == "unknown" and recovered["error"] == original["error"]
    assert recovered["reconciled"] and len(backend.writes) == 1


@pytest.mark.parametrize("fault", ["truncated-patch", "truncated-readback"])
def test_actual_cli_emits_unknown_json_after_truncated_native_response(backend: Backend, fault: str) -> None:
    expected = backend.expectations()
    backend.fault = fault
    with output.capture_json_output() as capture:
        assert cli.main(arguments(expected)) == 1
    result = json.loads(capture.getvalue())
    assert status(result) == "unknown" and result["operation_id"] == expected["operation_id"]
    assert len(backend.writes) == 1


@pytest.mark.parametrize("after_dispatch", [False, True])
def test_interrupted_consumption_recovers_read_only_even_when_later_closed(
    backend: Backend, monkeypatch: pytest.MonkeyPatch, after_dispatch: bool,
) -> None:
    expected = backend.expectations()
    if after_dispatch:
        backend.fault = "crash-after"
    else:
        consume = pr_ado_remediation.Ledger.consume
        def crash(*args: Any, **kwargs: Any) -> None:
            consume(*args, **kwargs)
            raise SystemExit(17)
        monkeypatch.setattr(pr_ado_remediation.Ledger, "consume", crash)
    with pytest.raises(SystemExit):
        execute(backend, **expected)
    backend.fault = ""
    backend.rows[0]["status"] = "closed"
    recovered = execute(backend, **expected)
    assert status(recovered) == "unknown" and recovered["reconciled"]
    assert len(backend.writes) == int(after_dispatch)


def test_native_response_after_deadline_is_unknown_even_if_thread_closed(backend: Backend) -> None:
    from agent_worktrees.providers.azure_remediation import AdoMutator
    expected = backend.expectations()
    backend.fault = "late"
    result = execute(
        backend, **expected,
        reader_factory=lambda *a: AdoMutator(*a, clock=lambda: backend.now),
    )
    assert status(result) == "unknown" and backend.rows[0]["status"] == "closed"
    assert len(backend.writes) == 1


def test_original_consumed_outcome_survives_terminal_pr_and_principal_change(backend: Backend) -> None:
    expected = backend.expectations()
    backend.fault = "lost"
    original = execute(backend, **expected)
    backend.fault = ""
    backend.pull["status"] = "completed"
    assert status(execute(backend, **expected)) == "unknown"
    backend.actor = "55555555-5555-5555-5555-555555555555"
    conflict = execute(backend, **expected)
    assert conflict["reason"] == "operation-conflict" and conflict["original_operation"] == original
    assert len(backend.writes) == 1


@pytest.mark.parametrize("provider", ["github", "gitea"])
def test_unsupported_binding_never_calls_legacy_bulk_or_token_owner(
    backend: Backend, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    from agent_worktrees import pr_thread_resolution, providers
    target = cfg.RepoConfig(anchor="", worktree_root="", pr=cfg.PRConfig(enabled=True, provider=provider))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda *_: pr_config.ForeignRepoResolution(target, REPO))
    monkeypatch.setattr(providers, "get_provider", lambda *_: pytest.fail("legacy bulk fallback"))
    result = pr_thread_resolution.resolve(None, REPO, 7, 9, **backend.expectations())
    assert status(result) == "not-attempted" and result["reason"] == "unsupported"
    assert not backend.writes


def test_claimant_precedes_policy_and_capability_is_additive(
    backend: Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = backend.expectations()
    capabilities = pr_author_cli.capabilities(cfg.load_config(), REPO)
    assert capabilities["operations"]["thread-resolve"] is True
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: (None, "no claimant"))
    monkeypatch.setattr(pr_author_cli, "load_author_config", lambda *a, **k: pytest.fail("policy fallback"))
    with output.capture_json_output() as capture:
        assert cli.main(arguments(expected)) == 1
    assert json.loads(capture.getvalue())["error"] == "no claimant"
    assert not backend.writes


@pytest.mark.parametrize("extra", [["--all"], ["--pr", "7"], ["tracked"], ["--thread", "0"]])
def test_strict_cli_never_expands_selection(backend: Backend, extra: list[str]) -> None:
    argv = arguments(backend.expectations())
    if extra[0] == "--thread":
        with output.capture_json_output() as capture:
            assert cli.main([*argv, *extra]) == 1
        assert status(json.loads(capture.getvalue())) == "not-attempted"
    else:
        with pytest.raises(SystemExit) as caught:
            cli.main([*argv, *extra])
        assert caught.value.code == 2
    assert not backend.writes


def process_main(root: str, argv: list[str]) -> int:
    """Actual source CLI with a fixture-only native transport and private ledger."""
    root = Path(root)
    backend = Backend(root, reopening=argv[0] == "pr-thread-reopen")
    with pytest.MonkeyPatch.context() as monkeypatch:
        configure(monkeypatch, root, backend)
        with output.capture_json_output() as capture:
            code = cli.main(argv)
        print(capture.getvalue())
        return code


@pytest.mark.parametrize("reopening", [False, True])
def test_real_process_concurrent_owners_share_consumed_before_write_ledger(
    backend: Backend, tmp_path: Path, reopening: bool,
) -> None:
    import agent_worktrees
    if reopening:
        backend.rows[0]["status"] = "closed"
    expected = backend.expectations()
    argv = arguments(expected)
    if reopening:
        argv[0] = "pr-thread-reopen"
    code = (
        "import json,sys; from test_ado_thread_resolution import process_main; "
        "raise SystemExit(process_main(sys.argv[1],json.loads(sys.argv[2])))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((
        str(Path(agent_worktrees.__file__).parent.parent), str(Path(__file__).parent),
        env.get("PYTHONPATH", ""),
    ))
    def run(_: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", code, str(tmp_path), json.dumps(argv)],
            env=env, capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert all(result.returncode == 0 for result in results), [r.stderr for r in results]
    assert all(json.loads(result.stdout)["ok"] for result in results)
    assert (tmp_path / "writes").read_text().splitlines() == ["PATCH"]


@pytest.mark.parametrize("reopening", [False, True])
def test_actual_frontend_to_owner_to_native_transport_across_real_process(
    backend: Backend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    reopening: bool,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "agent-pull-requests" / "src"))
    from agent_pull_requests import __main__ as frontend, native_cli
    if reopening:
        backend.rows[0]["status"] = "closed"
    expected = backend.expectations()
    args = frontend.build_parser().parse_args([
        "reopen-thread" if reopening else "resolve-thread",
        *arguments(expected)[1:], "--worktrees-command", "selected-owner",
    ])
    code = (
        "import sys; from test_ado_thread_resolution import process_main; "
        "raise SystemExit(process_main(sys.argv[1],sys.argv[2:]))"
    )
    import agent_worktrees
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((
        str(Path(agent_worktrees.__file__).parent.parent), str(Path(__file__).parent),
        os.environ.get("PYTHONPATH", ""),
    )))
    assert native_cli.run(args, prefix_factory=lambda: [
        sys.executable, "-c", code, str(tmp_path),
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and result["thread"] == 9 and result["actor"] == ACTOR
    assert (tmp_path / "writes").read_text().splitlines() == ["PATCH"]
