"""Deterministic native remediation contracts; no backend writes."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from agent_worktrees.pr_ado_remediation import Ledger, RemediationError
from agent_worktrees.providers.azure_observation import BUILD_POLICY
from agent_worktrees.providers.azure_observation_reads import Collection
from agent_worktrees.providers.azure_remediation import (
    AdoMutator, Remediator, thread_fingerprint,
)


HEAD = "a" * 40
EVALUATION = "11111111-1111-1111-1111-111111111111"
AT = "2026-10-18T01:00:00.1234567Z"


def policy():
    return {
        "evaluationId": EVALUATION, "startedDate": AT, "status": "rejected",
        "configuration": {"id": 12, "revision": 1, "isEnabled": True,
                          "isBlocking": True, "type": {"id": BUILD_POLICY, "displayName": "Build"},
                          "settings": {"displayName": "Validation"}},
        "context": {"sourceCommitId": HEAD, "isExpired": True},
    }


def thread():
    return {
        "id": 7, "status": "active", "lastUpdatedDate": AT,
        "pullRequestThreadContext": {"iterationContext": {
            "firstComparingIteration": 1, "secondComparingIteration": 2}},
        "comments": [{"id": 3, "parentCommentId": 0, "author": {"id": "reviewer"},
                      "commentType": 1, "publishedDate": AT, "lastUpdatedDate": AT,
                      "content": "Please clarify the boundary."}],
    }


class Fake:
    def __init__(self):
        self.metadata = {"status": "active", "repository": {
            "id": "repo-guid", "name": "repository", "project": {"id": "project-guid", "name": "Project"}},
            "targetRefName": "refs/heads/main",
            "lastMergeSourceCommit": {"commitId": HEAD}}
        self.policies = [policy()]
        self.discussions = [thread()]
        self.writes = []
        self.policy_reads = 0
        self.before_policy_read = None
        self.fail = ""
        self.incomplete = False
        self.after_comment = None
        self.live_configs = None

    def pr(self):
        return deepcopy(self.metadata)

    def evaluations(self, *args):
        self.policy_reads += 1
        if self.before_policy_read:
            self.before_policy_read(self)
        return Collection(tuple(deepcopy(self.policies)))

    def threads(self):
        return Collection(tuple(deepcopy(self.discussions)), "incomplete" if self.incomplete else "")

    def configurations(self, *args):
        if self.live_configs is not None:
            return Collection(tuple(deepcopy(self.live_configs)))
        configs = {row["configuration"]["id"]: deepcopy(row["configuration"]) for row in self.policies}
        return Collection(tuple(configs.values()))

    def iterations(self):
        return Collection(({"id": 1}, {"id": 2}, {"id": 3}))

    def requeue_evaluation(self, evaluation):
        self.writes.append(("evaluation", evaluation, {"status": "queued"}))
        if self.fail == "evaluation":
            raise RuntimeError("uncertain")
        next(row for row in self.policies if row["evaluationId"] == evaluation)["status"] = "queued"

    def evidence_thread(self, text):
        self.writes.append(("evidence", text))
        if self.fail == "evidence":
            raise RuntimeError("uncertain")
        note = {"id": 8, "status": "closed", "comments": [{"id": 1, "content": text}]}
        self.discussions.append(note)
        return note

    def reply_comment(self, tid, parent, text):
        self.writes.append(("reply", tid, parent, text))
        row = self.discussions[0]
        row["comments"].append({"id": 4, "parentCommentId": parent, "author": {"id": "operator"},
                                "commentType": 1, "content": text,
                                "publishedDate": AT, "lastUpdatedDate": AT})
        if self.after_comment:
            self.after_comment(self)
        if self.fail == "reply":
            raise RuntimeError("response lost after POST")

    def thread_status(self, tid, status):
        self.writes.append(("status", tid, status))
        if self.fail == "status":
            raise RuntimeError("response lost before PATCH")
        self.discussions[0]["status"] = status


@pytest.fixture
def setup(tmp_path):
    fake = Fake()
    provider = SimpleNamespace(_split_repo=lambda repo: repo.split("/", 1))
    operation = Remediator(provider, "Project/repository", 42, api_base="https://dev.azure.com/example",
                           token="not-a-secret", state_dir=tmp_path,
                           reader_factory=lambda *a: fake)
    return operation, fake, tmp_path


def test_existing_evaluation_only_and_once_per_target_head(setup):
    op, fake, _ = setup
    assert op.requeue("validation", expected_head=HEAD, reason="expired")["state"] == "verified"
    assert fake.writes == [("evaluation", EVALUATION, {"status": "queued"})]
    fake.policies[0]["status"] = "rejected"
    fake.policies[0]["evaluationId"] = "22222222-2222-2222-2222-222222222222"
    with pytest.raises(RemediationError, match="consumed"):
        op.requeue("12", expected_head=HEAD, reason="expired")
    assert len(fake.writes) == 1


@pytest.mark.parametrize("optional_id", [11, 13])
@pytest.mark.parametrize("optional_first", [False, True])
@pytest.mark.parametrize("selector", ["12", "validation"])
def test_selected_build_ignores_unrelated_optional_policy(setup, optional_id, optional_first, selector):
    op, fake, _ = setup
    required = fake.policies[0]
    optional = deepcopy(required)
    optional["configuration"].update(id=optional_id, isBlocking=False)
    optional["configuration"]["settings"]["displayName"] = "Optional validation"
    optional["evaluationId"] = "22222222-2222-2222-2222-222222222222"
    fake.policies = [optional, required] if optional_first else [required, optional]
    result = op.requeue(selector, expected_head=HEAD, reason="expired")
    assert result["state"] == "verified"
    assert result["policy_id"] == "12"
    assert fake.writes == [("evaluation", EVALUATION, {"status": "queued"})]
    assert optional["status"] == "rejected"


@pytest.mark.parametrize("optional_first", [False, True])
def test_required_optional_shared_name_is_ambiguous_before_eligibility(setup, optional_first):
    op, fake, _ = setup
    required = fake.policies[0]
    optional = deepcopy(required)
    optional["configuration"].update(id=11, isBlocking=False)
    optional["evaluationId"] = "22222222-2222-2222-2222-222222222222"
    fake.policies = [optional, required] if optional_first else [required, optional]
    with pytest.raises(RemediationError, match="selector is missing or ambiguous"):
        op.requeue("validation", expected_head=HEAD, reason="expired")
    assert not fake.writes
    assert op.requeue("12", expected_head=HEAD, reason="expired")["state"] == "verified"
    assert fake.writes == [("evaluation", EVALUATION, {"status": "queued"})]


@pytest.mark.parametrize("edit", [
    lambda row: row["context"].clear(),
    lambda row: row["context"].update(sourceCommitId="b" * 40),
    lambda row: row["context"].update(lastMergeSourceCommitId="b" * 40),
    lambda row: row.pop("evaluationId"),
    lambda row: row.update(evaluationId="bad"),
    lambda row: row["configuration"].pop("isBlocking"),
    lambda row: row["configuration"].update(isBlocking=False),
    lambda row: row["configuration"].update(isEnabled=False),
    lambda row: row["configuration"].pop("isEnabled"),
    lambda row: row["configuration"].pop("revision"),
    lambda row: row["configuration"].update(type={"id": "human", "displayName": "Minimum approval"}),
    lambda row: row.update(startedDate="2026-10-18"),
])
def test_ineligible_policy_fails_without_write(setup, edit):
    op, fake, _ = setup
    edit(fake.policies[0])
    with pytest.raises((RemediationError, ValueError)):
        op.requeue("12", expected_head=HEAD, reason="expired")
    assert not fake.writes


def test_latest_fractional_offset_evaluation(setup):
    op, fake, _ = setup
    stale = deepcopy(fake.policies[0])
    stale["startedDate"] = "2026-10-18T03:00:00.100000+02:00"
    stale["evaluationId"] = "22222222-2222-2222-2222-222222222222"
    fake.policies.append(stale)
    op.requeue("12", expected_head=HEAD, reason="expired")
    assert fake.writes[0][1] == EVALUATION


@pytest.mark.parametrize("change", ["disabled", "revision", "missing", "optional", "type", "duplicate"])
def test_live_configuration_required(setup, change):
    op, fake, _ = setup
    native = deepcopy(fake.policies[0]["configuration"])
    fake.live_configs = [native]
    if change == "disabled":
        native["isEnabled"] = False
    elif change == "revision":
        native["revision"] = 2
    elif change == "missing":
        fake.live_configs.clear()
    elif change == "optional":
        native["isBlocking"] = False
    elif change == "type":
        native["type"]["id"] = "not-build"
    else:
        fake.live_configs.append(native)
    with pytest.raises(RemediationError, match="configuration"):
        op.requeue("12", expected_head=HEAD, reason="expired")
    assert not fake.writes


def test_ambiguous_policy_name_and_equal_timestamp(setup):
    op, fake, _ = setup
    second = deepcopy(fake.policies[0])
    second["configuration"]["id"] = 13
    fake.policies.append(second)
    with pytest.raises(RemediationError, match="ambiguous"):
        op.requeue("Validation", expected_head=HEAD, reason="expired")
    second["configuration"]["id"] = 12
    second["evaluationId"] = "22222222-2222-2222-2222-222222222222"
    with pytest.raises(ValueError, match="ambiguous"):
        op.requeue("12", expected_head=HEAD, reason="expired")
    assert not fake.writes


@pytest.mark.parametrize("change", ["head", "evaluation", "config", "expiry", "lifecycle"])
def test_immediate_revalidation_consumes_without_patch(setup, change):
    op, fake, root = setup
    def move(f):
        if f.policy_reads == 2:
            if change == "head":
                f.metadata["lastMergeSourceCommit"]["commitId"] = "b" * 40
            elif change == "evaluation":
                f.policies[0]["evaluationId"] = "22222222-2222-2222-2222-222222222222"
            elif change == "config":
                f.policies[0]["configuration"]["revision"] = 2
            elif change == "expiry":
                f.policies[0]["context"]["isExpired"] = False
            else:
                f.metadata["status"] = "abandoned"
    fake.before_policy_read = move
    with pytest.raises(RemediationError):
        op.requeue("12", expected_head=HEAD, reason="expired")
    assert not fake.writes
    assert next(iter(json.loads(next(root.glob("*.json")).read_text())["records"].values()))["state"] == "unknown"


@pytest.mark.parametrize("state,expired", [("running", False), ("queued", False), ("broken", False),
                                          ("approved", False), ("rejected", True)])
def test_flaky_ineligible_states(setup, state, expired):
    op, fake, _ = setup
    fake.policies[0].update(status=state)
    fake.policies[0]["context"]["isExpired"] = expired
    with pytest.raises(RemediationError):
        op.requeue("12", expected_head=HEAD, reason="flaky", evidence="A transient agent outage was verified in the failed task log.")
    assert not fake.writes


def test_flaky_evidence_closed_thread_then_patch(setup):
    op, fake, _ = setup
    fake.policies[0]["context"]["isExpired"] = False
    for evidence in ("", "x" * 20, "!" * 80):
        with pytest.raises(RemediationError, match="evidence"):
            op.requeue("12", expected_head=HEAD, reason="flaky", evidence=evidence)
    op.requeue("12", expected_head=HEAD, reason="flaky",
               evidence="The agent disconnected before execution; logs confirm a transient outage.")
    assert [w[0] for w in fake.writes] == ["evidence", "evaluation"]
    assert "<!-- watch_pr:requeue 12@aaaaaaaaaaaa -->" in fake.writes[0][1]
    assert fake.discussions[-1]["status"] == "closed"


def test_oldest_integer_marker_wins_racing_flaky_reservation(setup):
    op, fake, _ = setup
    fake.policies[0]["context"]["isExpired"] = False
    original = fake.evidence_thread
    def competing(text):
        note = original(text)
        fake.discussions.append({"id": 2, "status": "closed",
                                 "comments": [{"id": 1, "content": text}]})
        return note
    fake.evidence_thread = competing
    with pytest.raises(RemediationError, match="Earlier"):
        op.requeue("12", expected_head=HEAD, reason="flaky",
                   evidence="Confirmed agent outage before any task execution began on this run.")
    assert [w[0] for w in fake.writes] == ["evidence"]


def test_legacy_marker_and_incomplete_read_refuse(setup):
    op, fake, _ = setup
    fake.discussions[0]["comments"][0]["content"] += "\n<!-- watch_pr:requeue 12@aaaaaaaaaaaa -->"
    with pytest.raises(RemediationError, match="marker"):
        op.requeue("12", expected_head=HEAD, reason="expired")
    fake.incomplete = True
    with pytest.raises(RemediationError, match="Complete"):
        op.requeue("12", expected_head=HEAD, reason="expired")
    assert not fake.writes


def test_corrupt_and_interrupted_ledger(setup):
    op, fake, root = setup
    _, target, _ = op.active()
    ledger = Ledger(target, state_dir=root)
    ledger.path.write_text("{bad")
    with pytest.raises(RemediationError, match="corrupt"):
        op.requeue("12", expected_head=HEAD, reason="expired")
    ledger.path.unlink()
    fake.fail = "evaluation"
    with pytest.raises(RuntimeError):
        op.requeue("12", expected_head=HEAD, reason="expired")
    fake.fail = ""
    with pytest.raises(RemediationError, match="consumed"):
        op.requeue("12", expected_head=HEAD, reason="expired")
    assert len(fake.writes) == 1
    data = json.loads(ledger.path.read_text())
    next(iter(data["records"].values()))["state"] = "in_flight"
    ledger.path.write_text(json.dumps(data))
    with pytest.raises(RemediationError, match="consumed"):
        op.requeue("12", expected_head=HEAD, reason="expired")


def test_local_concurrent_calls_only_one_patch(setup):
    op, fake, _ = setup
    def run(_):
        try:
            return op.requeue("12", expected_head=HEAD, reason="expired")["state"]
        except RemediationError:
            return "refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, range(2))) == ["refused", "verified"]
    assert len(fake.writes) == 1


def test_os_lock_serializes_independent_processes(tmp_path):
    import agent_worktrees
    code = """
import sys
from pathlib import Path
from types import SimpleNamespace
from test_ado_pr_remediation import Fake, HEAD
from agent_worktrees.providers.azure_remediation import Remediator
from agent_worktrees.pr_ado_remediation import RemediationError
fake = Fake()
native = fake.requeue_evaluation
def write(evaluation):
    path = Path(sys.argv[1]) / 'native-writes'
    with path.open('a') as out:
        out.write('PATCH\\n')
    native(evaluation)
fake.requeue_evaluation = write
operation = Remediator(SimpleNamespace(_split_repo=lambda x: x.split('/', 1)),
    'Project/repository', 42, api_base='https://dev.azure.com/example',
    token=None, state_dir=Path(sys.argv[1]), reader_factory=lambda *a: fake)
try:
    operation.requeue('12', expected_head=HEAD, reason='expired')
except RemediationError:
    pass
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(Path(agent_worktrees.__file__).parent.parent),
                                        str(Path(__file__).parent), env.get("PYTHONPATH", "")))
    def run(_):
        return subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=env,
                              capture_output=True, text=True, timeout=20,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert all(result.returncode == 0 for result in results), [r.stderr for r in results]
    assert (tmp_path / "native-writes").read_text().splitlines() == ["PATCH"]


def test_ledger_scope_full_head_and_full_target(setup):
    op, fake, _ = setup
    op.requeue("12", expected_head=HEAD, reason="expired")
    new_head = "a" * 12 + "b" * 28
    fake.metadata["lastMergeSourceCommit"]["commitId"] = new_head
    fake.policies[0]["context"]["sourceCommitId"] = new_head
    fake.policies[0]["status"] = "rejected"
    op.requeue("12", expected_head=new_head, reason="expired")
    op.number += 1
    fake.policies[0]["status"] = "rejected"
    op.requeue("12", expected_head=new_head, reason="expired")
    assert len(fake.writes) == 3


def reply_args(fake):
    return {"text": "Clarified; the original text stays intact.\n",
            "expected_thread": thread_fingerprint(fake.discussions[0]), "idempotency_key": "fix:round-2",
            "status": "fixed"}


def test_reply_round_binding_source_push_allowed_and_dedup(setup):
    op, fake, root = setup
    args = reply_args(fake)
    fake.metadata["lastMergeSourceCommit"]["commitId"] = "b" * 40
    result = op.reply(7, **args)
    assert result["state"] == "verified"
    assert [w[0] for w in fake.writes] == ["reply", "status"]
    assert fake.writes[0][2] == 3
    assert fake.writes[0][3].startswith(args["text"] + "\n\n<!--")
    assert op.reply(7, **args)["reconciled"]
    args["expected_thread"] = thread_fingerprint(fake.discussions[0])
    assert op.reply(7, **args)["reconciled"]
    assert len(fake.writes) == 2
    stored = next(root.glob("*.json")).read_text()
    assert args["text"] not in stored
    with pytest.raises(RemediationError, match="different"):
        op.reply(7, **{**args, "text": "A different answer."})


@pytest.mark.parametrize("field", ["text", "author", "round", "date", "parent"])
def test_thread_fingerprint_freshness(setup, field):
    op, fake, _ = setup
    args = reply_args(fake)
    row = fake.discussions[0]
    if field == "text":
        row["comments"][0]["content"] = "edited"
    elif field == "author":
        row["comments"][0]["author"]["id"] = "another"
    elif field == "round":
        row["pullRequestThreadContext"]["iterationContext"]["secondComparingIteration"] = 3
    elif field == "date":
        row["lastUpdatedDate"] = "2026-10-18T02:00:00Z"
    else:
        row["comments"][0]["parentCommentId"] = 2
    with pytest.raises(RemediationError, match="fingerprint"):
        op.reply(7, **args)
    assert not fake.writes


@pytest.mark.parametrize("change", ["round", "author", "date", "vote", "deleted", "parent", "incomplete"])
def test_missing_native_discussion_evidence_refuses(setup, change):
    op, fake, _ = setup
    args = reply_args(fake)
    row = fake.discussions[0]
    if change == "round":
        row["pullRequestThreadContext"]["iterationContext"]["secondComparingIteration"] = 99
        args["expected_thread"] = thread_fingerprint(row)
    elif change == "author":
        row["comments"][0]["author"].clear()
    elif change == "date":
        row["comments"][0]["lastUpdatedDate"] = ""
    elif change == "vote":
        row["properties"] = {"CodeReviewThreadType": {"$value": "VoteUpdate"}}
    elif change == "deleted":
        row["isDeleted"] = True
    elif change == "parent":
        row["comments"][0]["parentCommentId"] = "0"
    else:
        fake.incomplete = True
    with pytest.raises(RemediationError):
        op.reply(7, **args)
    assert not fake.writes


@pytest.mark.parametrize("failure", ["reply", "status"])
def test_reply_uncertain_partial_reconciles_no_post(setup, failure):
    op, fake, _ = setup
    args = reply_args(fake)
    fake.fail = failure
    with pytest.raises(RuntimeError):
        op.reply(7, **args)
    count = len(fake.writes)
    result = op.reply(7, **args)
    assert result["state"] == "partial"
    assert len(fake.writes) == count


def test_reply_unknown_absent_readback_no_retry(setup):
    op, fake, _ = setup
    args = reply_args(fake)
    fake.fail = "reply"
    with pytest.raises(RuntimeError):
        op.reply(7, **args)
    fake.discussions[0]["comments"].pop()
    with pytest.raises(RemediationError, match="no blind POST"):
        op.reply(7, **args)
    assert len(fake.writes) == 1


def test_reply_concurrent_edit_never_resolves(setup):
    op, fake, _ = setup
    args = reply_args(fake)
    fake.after_comment = lambda f: f.discussions[0]["comments"][0].update(content="Reviewer edited")
    with pytest.raises(RemediationError, match="changed"):
        op.reply(7, **args)
    assert [w[0] for w in fake.writes] == ["reply"]


def test_reply_cross_venue_marker_reconciles_and_collision_refuses(setup):
    op, fake, root = setup
    args = reply_args(fake)
    op.reply(7, **args)
    next(root.glob("*.json")).unlink()
    assert op.reply(7, **args)["reconciled"]
    with pytest.raises(RemediationError, match="different"):
        op.reply(7, **{**args, "text": "Changed"})
    next(root.glob("*.json")).unlink()
    with pytest.raises(RemediationError, match="collision"):
        op.reply(7, **{**args, "text": "Changed"})


def test_fixed_transport_endpoints_no_build_creation():
    requests = []
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            return b"{}"
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return Response()
    provider = SimpleNamespace(_split_repo=lambda repo: repo.split("/", 1),
                               _auth_header=lambda token: ("Authorization: unit-test", ""))
    reader = AdoMutator(provider, "Project/repository", 42, "https://dev.azure.com/example",
                        None, opener=Opener())
    reader.requeue_evaluation(EVALUATION)
    assert requests[0].method == "PATCH"
    assert requests[0].full_url.endswith("/policy/evaluations/" + EVALUATION + "?api-version=7.1-preview.1")
    assert json.loads(requests[0].data) == {"status": "queued"}
    reader.reply_comment(7, 3, "unchanged")
    reader.thread_status(7, "byDesign")
    reader.evidence_thread("operator evidence")
    assert all("/build/" not in req.full_url and "/reviewers" not in req.full_url for req in requests)
    assert json.loads(requests[1].data)["parentCommentId"] == 3
    with pytest.raises(ValueError):
        reader.requeue_evaluation("../../build/builds")
    class Failed:
        def open(self, *args, **kwargs):
            raise URLError("no network")
    reader.opener = Failed()
    with pytest.raises(Exception, match="unknown"):
        reader.requeue_evaluation(EVALUATION)


def test_cli_dispatch_unknown_and_unsupported_provider(monkeypatch, capsys):
    from agent_worktrees.pr_remediation_cli import dispatch
    from agent_worktrees import pr_config, pr_read_context
    assert dispatch(["threads"]) is None
    monkeypatch.setattr(pr_read_context, "load_read_config", lambda path: object())
    binding = SimpleNamespace(pr=SimpleNamespace(provider="github"))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda *a: SimpleNamespace(resolved=True, repo_config=binding))
    code = dispatch(["requeue", "Project/repository", "42", "--policy", "12", "--reason",
                     "expired", "--expected-head", HEAD, "--json"])
    assert code == 2
    assert "unsupported" in capsys.readouterr().out


def test_cli_binding_account_input_and_partial_exit(setup, monkeypatch, capsys, tmp_path):
    from agent_worktrees.pr_remediation_cli import dispatch
    from agent_worktrees import pr_config, pr_read_context, providers
    op, fake, _ = setup
    seen = []
    binding = SimpleNamespace(provider="azure-devops", api_base="https://dev.azure.com/example")
    monkeypatch.setattr(pr_read_context, "load_read_config", lambda path: "caller-config")
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda config, slug: SimpleNamespace(resolved=True,
                            repo_config=SimpleNamespace(pr=binding)))
    monkeypatch.setattr(providers, "get_provider", lambda provider: "native-provider")
    monkeypatch.setattr(providers, "account_token_for_slug",
                        lambda slug, pr: seen.append((slug, pr)) or "memory-only")
    def factory(provider, repo, number, **kwargs):
        assert provider == "native-provider"
        assert repo == "Project/repository" and number == 42
        assert kwargs["token"] == "memory-only"
        assert kwargs["state_dir"] == tmp_path
        return op
    text = tmp_path / "reply.txt"
    text.write_text("The requested boundary is clarified.", encoding="utf-8")
    args = ["reply", "Project/repository", "42", "--thread", "7", "--text-file", str(text),
            "--expected-thread", thread_fingerprint(fake.discussions[0]),
            "--idempotency-key", "cli-reply", "--status", "fixed", "--json"]
    assert dispatch(args, state_dir=tmp_path, remediator_factory=factory) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "verified"
    assert seen == [("Project/repository", binding)]
    assert text.read_text() in fake.writes[0][3]
    text.write_text("x" * 65537, encoding="utf-8")
    assert dispatch(args, state_dir=tmp_path, remediator_factory=factory) == 2
    assert "64 KiB" in capsys.readouterr().out
