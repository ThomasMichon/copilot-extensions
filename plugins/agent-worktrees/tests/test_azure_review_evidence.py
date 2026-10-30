"""Finite exporter controls through the existing credential and HTTP read owners."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_worktrees.providers.azure_observation_reads import AdoReader
from agent_worktrees.providers.azure_review_evidence import acquire_evidence
from agent_worktrees.providers.base import ProviderError
from agent_worktrees.providers.observation_credentials import ObservationCredentialError


ENDPOINT = "https://dev.azure.com/example"
HEAD, COMMON = "a" * 40, "b" * 40


def material(token="synthetic-token"):
    return SimpleNamespace(
        token=token, expires_at=4_000_000_000,
        forge=SimpleNamespace(
            provider="azure-devops", api_base=ENDPOINT, repository="project/repository",
            permission="observe", authentication="bearer",
        ),
    )


class Response:
    status = 200
    headers = {}

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return json.dumps(self.value).encode()[:size]


class Forge:
    def __init__(self):
        self.requests = []
        self.pr_reads = self.iteration_reads = 0
        self.head_after = HEAD
        self.iteration_after = 3
        self.fail_policies = False
        self.fail_final_iterations = False
        self.fail_threads_auth = False
        self.pages = {
            0: {"changeEntries": [{"changeTrackingId": 7, "changeType": "edit",
                                   "item": {"path": "/example.txt"}}],
                "nextSkip": 0, "nextTop": 0},
        }

    def open(self, request, timeout):
        parsed = urlsplit(request.full_url)
        path, query = parsed.path, parse_qs(parsed.query)
        self.requests.append((path, query))
        if path.endswith("/connectionData"):
            actor = "other-actor" if "other-token" in request.get_header("Authorization") else "actor-id"
            return Response({"authorizedUser": {"id": actor}})
        if path.endswith("/pullRequests/42"):
            self.pr_reads += 1
            return Response({
                "pullRequestId": 42,
                "repository": {"id": "repository-id", "project": {"id": "project-id"}},
                "lastMergeSourceCommit": {
                    "commitId": HEAD if self.pr_reads == 1 else self.head_after,
                },
                "autoCompleteSetBy": None, "completionOptions": {"deleteSourceBranch": False},
                "reviewers": [{"id": "actor-id", "vote": 0}],
            })
        if path.endswith("/iterations"):
            self.iteration_reads += 1
            if self.fail_final_iterations and self.iteration_reads > 1:
                raise ProviderError("controlled unavailable iteration read")
            return Response({"value": [{
                "id": 3 if self.iteration_reads == 1 else self.iteration_after,
                "sourceRefCommit": {
                    "commitId": HEAD if self.iteration_reads == 1 else self.head_after,
                },
                "commonRefCommit": {"commitId": COMMON},
            }]})
        if path.endswith("/changes"):
            assert query["$compareTo"] == ["0"]
            return Response(self.pages[int(query["$skip"][0])])
        if path.endswith("/threads"):
            if self.fail_threads_auth:
                raise ObservationCredentialError("controlled auth loss")
            return Response({"value": [{"id": 8, "status": "closed", "comments": []}]})
        if path.endswith("/evaluations"):
            if self.fail_policies:
                raise ProviderError("controlled unavailable policy evaluation")
            assert query["includeNotApplicable"] == ["true"]
            return Response({"value": []})
        pytest.fail("unexpected provider route")

    def factory(self, *args, **kwargs):
        return AdoReader(*args, opener=self, **kwargs)


def acquire(forge, source=material, **kwargs):
    return acquire_evidence(
        "project/repository", 42, ENDPOINT, credential_source=source,
        budget=30, reader_factory=forge.factory, **kwargs,
    )


def test_complete_export_renews_credentials_and_observes_actual_principal():
    forge = Forge()
    calls = []

    def source():
        calls.append(True)
        return material()

    result = acquire(forge, source)
    assert result["scope"]["principal_id"] == "actor-id"
    assert result["scope"]["project_id"] == "project-id"
    assert result["scope"]["repository_id"] == "repository-id"
    assert all(value["complete"] for value in result["sections"].values())
    pull = result["sections"]["pull_request"]["data"]
    assert pull["autoCompleteSetBy"] is None
    assert pull["completionOptions"]["deleteSourceBranch"] is False
    assert result["sections"]["reviewers"]["data"][0]["vote"] == 0
    assert result["acquisition"]["credential_identity_checks"] == 1
    assert len(calls) == 7
    assert len(forge.requests) == 8
    assert result["acquisition"]["effects"] == "none"
    assert result["acquisition"]["snapshot_atomic"] is False
    assert "synthetic-token" not in json.dumps(result)
    assert not any("configurations" in path for path, _ in forge.requests)


def test_push_bracket_keeps_new_head_and_old_changes_bound_to_old_range():
    forge = Forge()
    forge.head_after, forge.iteration_after = "c" * 40, 4
    result = acquire(forge)
    assert result["sections"]["pull_request"]["data"]["lastMergeSourceCommit"]["commitId"] == "c" * 40
    assert result["sections"]["iterations"]["data"][0]["id"] == 4
    assert result["sections"]["changes"]["revision"] == {
        "head_sha": HEAD, "iteration": 3, "common_sha": COMMON,
    }


def test_incomplete_policies_and_final_iteration_read_retain_known_records():
    forge = Forge()
    forge.fail_policies = forge.fail_final_iterations = True
    result = acquire(forge)
    assert result["sections"]["policies"]["complete"] is False
    assert result["sections"]["policies"]["errors"] == ["policies_incomplete"]
    iterations = result["sections"]["iterations"]
    assert iterations["complete"] is False
    assert iterations["data"][0]["id"] == 3


@pytest.mark.parametrize("next_top", [25, 100])
def test_change_pagination_and_credentials_are_bounded_by_actual_pages(next_top):
    forge = Forge()
    first = forge.pages[0]
    first.update(nextSkip=100, nextTop=next_top)
    forge.pages[100] = {
        "changeEntries": [{"changeTrackingId": 9, "item": {"path": "/second.txt"}}],
        "nextSkip": 0, "nextTop": 0,
    }
    result = acquire(forge)
    assert result["sections"]["changes"]["complete"] is True
    assert len(result["sections"]["changes"]["data"]) == 2
    assert sum(path.endswith("/changes") for path, _ in forge.requests) == 2
    queries = [query for path, query in forge.requests if path.endswith("/changes")]
    assert [query["$top"] for query in queries] == [["100"], [str(next_top)]]


@pytest.mark.parametrize("failure", [
    "missing", "repeated", "repeated_terminal", "nonadvancing", "oversize",
])
def test_bad_change_continuation_retains_known_records_as_incomplete(failure):
    forge = Forge()
    if failure == "missing":
        forge.pages[0].pop("nextSkip")
    elif failure == "nonadvancing":
        forge.pages[0].update(nextSkip=0, nextTop=100)
    elif failure == "oversize":
        forge.pages[0].update(nextSkip=100, nextTop=20_001)
    else:
        forge.pages[0].update(nextSkip=100, nextTop=100)
        forge.pages[100] = copy.deepcopy(forge.pages[0])
        forge.pages[100]["nextSkip"] = 200
        if failure == "repeated_terminal":
            forge.pages[100].update(nextSkip=0, nextTop=0)
    result = acquire(forge)
    changes = result["sections"]["changes"]
    assert not changes["complete"]
    assert changes["errors"] == ["changes_incomplete"]
    assert changes["data"][0]["item"]["path"] == "/example.txt"
    assert len(changes["data"]) == 1


def test_new_material_is_identity_checked_and_principal_switch_aborts():
    forge = Forge()
    count = 0

    def source():
        nonlocal count
        count += 1
        return material("synthetic-token" if count == 1 else "other-token")

    with pytest.raises(ObservationCredentialError, match="principal changed") as failure:
        acquire(forge, source)
    assert failure.value.transient is False
    assert not any(path.endswith("/iterations") for path, _ in forge.requests)


def test_same_principal_renewal_and_expiry_are_checked_per_request():
    forge = Forge()
    count = 0

    def source():
        nonlocal count
        count += 1
        return material("synthetic-token-" + str(count))

    result = acquire(forge, source)
    assert result["acquisition"]["credential_identity_checks"] == count == 7
    assert len(forge.requests) == 14
    expired = material()
    expired.expires_at = 0
    expired_forge = Forge()
    with pytest.raises(ObservationCredentialError):
        acquire(expired_forge, lambda: expired)
    assert expired_forge.requests == []


def test_expected_principal_and_auth_loss_never_become_partial_success():
    wrong_principal = Forge()
    with pytest.raises(ObservationCredentialError):
        acquire(wrong_principal, expected_principal="wrong-actor")
    assert not any(path.endswith("/pullRequests/42") for path, _ in wrong_principal.requests)
    forge = Forge()
    forge.fail_threads_auth = True
    with pytest.raises(ObservationCredentialError, match="auth loss"):
        acquire(forge)


def test_budget_exhaustion_and_unsafe_endpoints_fail_without_ambient_fallback():
    ticks = iter([0.0, 100.0])
    with pytest.raises(ProviderError, match="budget exhausted"):
        acquire(Forge(), clock=lambda: next(ticks))
    for endpoint in ("http://example.org", "https://user:password@example.org",
                     "https://user:password@example.org\uff0f"):
        with pytest.raises(ProviderError, match="configured HTTPS"):
            acquire_evidence(
                "project/repository", 42, endpoint, credential_source=material,
                budget=30,
            )


@pytest.mark.parametrize("drift", [False, True])
def test_actual_exporter_output_composes_with_successor_generation_builder(monkeypatch, drift):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins" / "agent-pull-requests" / "src"))
    from agent_pull_requests.review_workspace import build_generation

    forge = Forge()
    if drift:
        forge.head_after, forge.iteration_after = "c" * 40, 4
    result = build_generation(acquire(forge))
    generation = result["generation"]
    assert generation["scope"]["principal_id"] == "actor-id"
    assert generation["complete"] is not drift
    assert generation["sections"]["changes"]["complete"] is not drift
    assert generation["source_attachment"]["state"] == "unsupported"


def test_source_cli_strict_syntax_and_redacted_failures(monkeypatch, capsys):
    from agent_worktrees.pr_review_evidence_cli import main
    from agent_worktrees import pr_credential_command
    from agent_worktrees.providers import azure_review_evidence

    assert main(["capabilities"]) == 0
    assert json.loads(capsys.readouterr().out)["effects"] == "none"
    with pytest.raises(SystemExit) as failure:
        main(["read", "--rep", "project/repository"])
    assert failure.value.code == 2
    def selected_source(_serialized, *, remaining):
        assert remaining() == 1.0
        return material

    monkeypatch.setattr(pr_credential_command, "command_source", selected_source)

    def refused(*args, **kwargs):
        assert kwargs["budget"] == 30
        assert kwargs["expected_principal"] == "actor-id"
        assert kwargs["credential_source_factory"](lambda: 1.0) is material
        raise ObservationCredentialError("synthetic-secret-must-not-be-echoed")

    monkeypatch.setattr(azure_review_evidence, "acquire_evidence", refused)
    assert main([
        "read", "--repo", "project/repository", "--number", "42",
        "--endpoint", ENDPOINT, "--budget", "30",
        "--credential-command", "synthetic-command", "--expected-principal", "actor-id",
    ]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["category"] == "credential"
    assert result["effects"] == "none"
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("mismatch", ["number", "repository_change"])
def test_pr_identity_mismatch_never_publishes_evidence(mismatch):
    forge = Forge()
    original = forge.open

    def changed(request, timeout):
        response = original(request, timeout)
        if urlsplit(request.full_url).path.endswith("/pullRequests/42"):
            if mismatch == "number":
                response.value["pullRequestId"] = 43
            elif forge.pr_reads > 1:
                response.value["repository"]["id"] = "another-repository"
        return response

    forge.open = changed
    with pytest.raises(ProviderError, match="identity"):
        acquire(forge)


def test_total_evidence_budget_is_checked_before_publication(monkeypatch):
    from agent_worktrees.providers import azure_review_evidence

    monkeypatch.setattr(azure_review_evidence, "MAX_EVIDENCE_BYTES", 256)
    with pytest.raises(ProviderError, match="byte budget"):
        acquire(Forge())


def test_credential_factory_receives_the_shared_decreasing_operation_budget():
    forge = Forge()
    ticks = [0.0]
    limits = []

    def factory(remaining):
        def source():
            limits.append(remaining())
            ticks[0] += 1
            return material()
        return source

    result = acquire_evidence(
        "project/repository", 42, ENDPOINT, credential_source_factory=factory,
        budget=30, reader_factory=forge.factory, clock=lambda: ticks[0],
    )
    assert limits == [30, 29, 28, 27, 26, 25, 24]
    assert result["acquisition"]["duration_ms"] == 7000
