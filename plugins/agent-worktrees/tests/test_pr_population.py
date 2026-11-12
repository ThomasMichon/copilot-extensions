"""Synthetic selected native ADO populations, with actual parser/credential processes."""

import json
import shutil
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from pr_filters import Expression

from agent_worktrees import pr_cli, pr_population_cli
from agent_worktrees.providers import observation_population as pop
from agent_worktrees.providers.observation_credentials import ObservationCredentialError

REPO = "Project/repository"
ENDPOINT = "https://dev.azure.com/example"
USER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def expression(where=None):
    return Expression.parse({
        "protocol": "agent-pull-requests.filters/v1", "where": where or {"repo": [REPO]},
    })


def credential(calls, *, provider="azure-devops", repo=REPO, endpoint=ENDPOINT):
    def acquire():
        calls.append("credential")
        return SimpleNamespace(
            token="synthetic-" + str(len(calls)), expires_at=time.time() + 60,
            forge=SimpleNamespace(provider=provider, api_base=endpoint, repository=repo,
                                  permission="observe", authentication="bearer"),
        )
    return acquire


def row(number=7, **overrides):
    return {
        "pullRequestId": number, "status": "active",
        "repository": {"name": "repository", "project": {"name": "Project"}},
        "createdBy": {"id": USER}, "reviewers": [{"id": OTHER}],
        "creationDate": "2026-01-01T00:00:00Z", "isDraft": False,
        "description": "must not escape the provider boundary", **overrides,
    }


def page(rows):
    return {"count": len(rows), "value": rows}


def transport(monkeypatch, replies):
    requests = []
    replies = iter(replies)

    def run(argv, **kwargs):
        requests.append((argv, kwargs))
        value = next(replies)
        if isinstance(value, Exception):
            raise value
        raw = json.dumps(value) if not isinstance(value, str) else value
        return subprocess.CompletedProcess(argv, 0, raw + "\n200", "")

    monkeypatch.setattr(pop, "run_cli", run)
    return requests


def observe(monkeypatch, replies, where=None, **options):
    requests = transport(monkeypatch, replies)
    calls = []
    result = pop.population(
        "azure-devops", REPO, api_base=ENDPOINT, expression=expression(where),
        credential_source=credential(calls), **options,
    )
    return result, calls, requests


def test_union_author_reviewer_deduplicates_and_normalizes_all_available_fields(monkeypatch):
    where = {"any": [
        {"from": {"users": [USER]}}, {"assigned": {"users": [OTHER]}},
    ]}
    result, calls, requests = observe(
        monkeypatch, [page([row()]), page([]), page([row()]), page([]),
                      page([{"name": "ready"}])], where,
    )
    assert result["outcome"] == "complete" and result["pages"] == 4
    assert result["filter_revision"] == expression(where).revision
    assert len(result["pulls"]) == 1
    member = result["pulls"][0]
    assert member["number"] == 7 and member["match"] == "match"
    assert member["facts"] == {
        "repo": REPO, "from": [USER], "assigned": [OTHER],
        "created_at": 1767225600.0, "activity_at": None, "tags": ["ready"],
        "publication": "published",
    }
    queries = [parse_qs(urlsplit(argv[argv.index("--url") + 1]).query) for argv, _ in requests]
    assert queries[0]["searchCriteria.creatorId"] == [USER]
    assert queries[2]["searchCriteria.reviewerId"] == [OTHER]
    assert calls == ["credential"] * 5
    assert "description" not in json.dumps(result)
    assert all("synthetic" not in " ".join(argv) for argv, _ in requests)
    scheme = "Bear" + "er "
    assert requests[0][1]["input_text"] == "Authorization: " + scheme + "synthetic-1\n"
    assert requests[-1][1]["input_text"] == "Authorization: " + scheme + "synthetic-5\n"
    assert all(kwargs["max_stdout_bytes"] == 1_048_584 for _, kwargs in requests)
    assert all(argv[1] == "-q" and "--location" not in argv for argv, _ in requests)


def test_actual_curl_cannot_expand_selected_endpoint_into_multiple_requests(monkeypatch):
    from agent_worktrees.providers.base import run_cli
    if not shutil.which("curl"):
        pytest.skip("native curl unavailable")
    outputs = []

    def invoke(argv, **kwargs):
        # Reserve a private non-listening loopback port and bypass every proxy;
        # no provider or unrelated listener can receive this synthetic request.
        completed = run_cli([*argv, "--noproxy", "*"], **kwargs)
        outputs.append(completed.stdout)
        return completed

    monkeypatch.setattr(pop, "run_cli", invoke)
    calls = []
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        endpoint = f"https://127.0.0.1:{reservation.getsockname()[1]}/{{first,second}}"
        result = pop.population(
            "azure-devops", REPO, api_base=endpoint, expression=expression(),
            credential_source=credential(calls, endpoint=endpoint),
            deadline=time.monotonic() + 5,
        )
    assert result["outcome"] == "unavailable"
    assert calls == ["credential"]
    assert len(outputs) == 1 and outputs[0].count("\n000") == 1


def test_created_age_changes_without_head_change_and_all_residuals(monkeypatch):
    where = {"repo": [REPO], "from": {"users": [USER]}, "assigned": {"users": [OTHER]},
             "age": {"field": "created", "min_seconds": 10, "max_seconds": 20},
             "tags": {"all": ["ready"]}, "publication": ["published"]}
    monkeypatch.setattr(pop.time, "time", lambda: 1767225610.0)
    replies = [page([row()]), page([]), page([{"name": "ready"}])]
    first, _, _ = observe(monkeypatch, replies, where)
    assert first["pulls"][0]["match"] == "match"
    monkeypatch.setattr(pop.time, "time", lambda: 1767225621.0)
    second, _, _ = observe(monkeypatch, replies, where)
    assert second["pulls"][0]["match"] == "nonmatch"
    assert first["pulls"][0]["facts"] == second["pulls"][0]["facts"]


@pytest.mark.parametrize("changes,where", [
    ({"createdBy": None}, {"from": {"users": [USER]}}),
    ({"reviewers": None}, {"assigned": {"users": [OTHER]}}),
    ({"reviewers": [{"id": OTHER}, {"id": USER, "isContainer": True}]},
     {"assigned": {"users": [OTHER]}}),
    ({"creationDate": None}, {"age": {"field": "created", "min_seconds": 0}}),
    ({"creationDate": "2026-01-01T00:00:00"}, {"age": {"field": "created", "min_seconds": 0}}),
    ({"isDraft": None}, {"publication": ["published"]}),
])
def test_incomplete_native_fields_preserve_unknown(monkeypatch, changes, where):
    result, _, _ = observe(monkeypatch, [page([row(**changes)]), page([]), page([])], where)
    assert result["outcome"] == "partial"
    assert result["pulls"][0]["match"] == "unknown"


def test_label_failure_preserves_candidate_and_does_not_invent_empty_tags(monkeypatch):
    result, calls, _ = observe(
        monkeypatch, [page([row()]), page([]), "bad JSON"], {"tags": {"any": ["ready"]}},
    )
    assert len(calls) == 3 and result["outcome"] == "partial"
    assert result["pulls"][0]["facts"]["tags"] is None
    assert result["pulls"][0]["match"] == "unknown"


@pytest.mark.parametrize("replies,outcome,pages,numbers", [
    ([page([])], "complete", 1, []),
    (["bad JSON"], "unavailable", 0, []),
    ([page([row()]), "bad JSON"], "partial", 1, [7]),
    ([page([row()]), page([row()])], "partial", 1, [7]),
    ([page([row(repository={"name": "other", "project": {"name": "Project"}})])],
     "unavailable", 0, []),
    ([{"value": [], "count": 1}], "unavailable", 0, []),
    ([page([row(), row()])], "unavailable", 0, []),
    (["[" * 10000 + "0" + "]" * 10000], "unavailable", 0, []),
    ([page([row()]), "[" * 10000 + "0" + "]" * 10000], "partial", 1, [7]),
    ([page([row()]), page([]), "[" * 10000 + "0" + "]" * 10000], "partial", 2, [7]),
])
def test_failed_partial_empty_and_out_of_scope_pages(monkeypatch, replies, outcome, pages, numbers):
    result, calls, _ = observe(monkeypatch, replies)
    assert result["outcome"] == outcome and result["pages"] == pages
    assert [member["number"] for member in result["pulls"]] == numbers
    assert len(calls) <= len(replies)


def test_ado_stored_name_casing_preserves_the_admitted_repository_slug(monkeypatch):
    native = row(repository={"name": "REPOSITORY", "project": {"name": "PROJECT"}})
    result, _, _ = observe(monkeypatch, [page([native]), page([]), page([])])
    assert result["outcome"] == "complete"
    assert result["repo"] == result["pulls"][0]["facts"]["repo"] == REPO


def test_caps_are_total_across_branches_and_never_complete(monkeypatch):
    where = {"any": [{"from": {"users": [USER]}}, {"assigned": {"users": [OTHER]}}]}
    result, calls, _ = observe(
        monkeypatch, [page([row()]), page([]), page([])], where, max_pages=1,
    )
    assert result["outcome"] == "partial" and result["pages"] == 1
    assert result["error"] == "page_limit" and len(calls) == 2  # one label read
    result, calls, _ = observe(
        monkeypatch, [page([row(), row(8)]), page([])], max_pulls=1,
    )
    assert result["outcome"] == "partial" and result["error"] == "pull_limit"
    assert [member["number"] for member in result["pulls"]] == [7]
    assert len(calls) == 2


def test_deadline_and_mismatched_binding_prevent_provider_reads(monkeypatch):
    monkeypatch.setattr(pop, "run_cli", lambda *_a, **_kw: pytest.fail("unexpected provider"))
    calls = []
    expired = pop.population(
        "azure-devops", REPO, api_base=ENDPOINT, expression=expression(),
        credential_source=credential(calls), deadline=time.monotonic() - 1,
    )
    assert expired["outcome"] == "unavailable" and calls == []
    result = pop.population(
        "azure-devops", REPO, api_base=ENDPOINT, expression=expression(),
        credential_source=credential(calls, repo="Project/other"),
    )
    assert result["outcome"] == "unavailable" and calls == ["credential"]


def test_credential_loss_never_retries_or_reacquires_for_labels(monkeypatch):
    requests = transport(monkeypatch, [page([row()])])
    calls = []

    def acquire():
        if calls:
            calls.append("lost")
            raise ObservationCredentialError("synthetic loss")
        return credential(calls)()

    result = pop.population(
        "azure-devops", REPO, api_base=ENDPOINT, expression=expression(),
        credential_source=acquire,
    )
    assert result["outcome"] == "partial" and len(result["pulls"]) == 1
    assert len(requests) == 1 and calls == ["credential", "lost"]


@pytest.mark.parametrize("status", ["401", "403", "302"])
def test_http_loss_keeps_previous_page_without_retry(monkeypatch, status):
    requests = []

    def run(argv, **kwargs):
        requests.append(argv)
        body = json.dumps(page([row()])) + "\n200" if len(requests) == 1 else "{}\n" + status
        return subprocess.CompletedProcess(argv, 0, body, "")

    monkeypatch.setattr(pop, "run_cli", run)
    calls = []
    result = pop.population(
        "azure-devops", REPO, api_base=ENDPOINT, expression=expression(),
        credential_source=credential(calls),
    )
    assert len(requests) == len(calls) == 2 and result["outcome"] == "partial"
    assert result["pulls"][0]["facts"]["tags"] is None


def test_expiry_during_credential_acquisition_never_starts_provider(monkeypatch):
    monkeypatch.setattr(pop, "run_cli", lambda *_a, **_kw: pytest.fail("late provider read"))
    ticks = [1.0]
    monkeypatch.setattr(pop.time, "monotonic", lambda: ticks[0])

    def acquire():
        ticks[0] = 3.0
        return credential([])()

    result = pop.population(
        "azure-devops", REPO, api_base=ENDPOINT, expression=expression(),
        credential_source=acquire, deadline=2.0,
    )
    assert result["outcome"] == "unavailable"


def test_overlap_with_changed_metadata_retains_uncertainty(monkeypatch):
    where = {"any": [{"from": {"users": [USER]}}, {"assigned": {"users": [OTHER]}}]}
    result, _, _ = observe(
        monkeypatch, [page([row()]), page([]), page([row(reviewers=[])]), page([]), page([])],
        where,
    )
    assert result["outcome"] == "partial"
    assert result["pulls"][0]["facts"]["assigned"] is None


@pytest.mark.parametrize("max_pages,max_pulls", [(0, 200), (21, 200), (4, 0), (4, 1001)])
def test_invalid_caps_refuse_before_credentials(monkeypatch, max_pages, max_pulls):
    with pytest.raises(ValueError):
        pop.population(
            "azure-devops", REPO, api_base=ENDPOINT, expression=expression(),
            credential_source=lambda: pytest.fail("invalid bounded acquisition"),
            max_pages=max_pages, max_pulls=max_pulls,
        )


def test_request_budget_includes_label_reads(monkeypatch):
    monkeypatch.setattr(pop, "MAX_REQUESTS", 4)
    result, calls, requests = observe(
        monkeypatch, [page([row(7), row(8), row(9)]), page([]), page([]), page([])],
    )
    assert len(calls) == len(requests) == 4 and result["outcome"] == "partial"
    assert [member["facts"]["tags"] for member in result["pulls"]] == [[], [], None]


def test_byte_bounds_and_total_json_bound(monkeypatch):
    result, _, _ = observe(monkeypatch, [" " * (pop.MAX_BYTES + 1)])
    assert result["outcome"] == "unavailable"
    # Lower the output cap after transport decoding, without weakening its real read bound.
    original_labels = pop._labels

    def labels(value):
        monkeypatch.setattr(pop, "MAX_BYTES", 800)
        return original_labels(value)

    monkeypatch.setattr(pop, "_labels", labels)
    result, _, _ = observe(monkeypatch, [
        page([row(7), row(8), row(9)]), page([]), page([]), page([]), page([]),
    ])
    assert len(json.dumps(result).encode()) <= 800
    assert result["outcome"] == "partial" and result["error"] == "capture_limit"


def inputs(tmp_path, where=None, provider="azure-devops"):
    config = tmp_path / "native.json"
    config.write_text(json.dumps({"repos": {"target": {"pr": {
        "provider": provider, "api_base": ENDPOINT, "repository": REPO,
    }}}}))
    filters = tmp_path / "filter.json"
    filters.write_text(expression(where).canonical)
    return [REPO, "--config", str(config), "--filter-file", str(filters),
            "--credential-command", json.dumps([sys.executable]), "--json"]


@pytest.mark.parametrize("where", [
    {"assigned": {"groups": [USER]}}, {"from": {"groups": [USER]}},
    {"age": {"field": "activity", "min_seconds": 1}},
    {"from": {"users": ["alias@example.test"]}},
    {"any": [{"repo": [REPO]}, {"repo": ["Project/other"]}]},
])
def test_unsupported_filter_refused_before_credential_effect(tmp_path, monkeypatch, where):
    from agent_worktrees import pr_credential_command
    monkeypatch.setattr(pr_credential_command, "command_source",
                        lambda *_a, **_kw: pytest.fail("unsupported credential selection"))
    assert pr_population_cli.dispatch(inputs(tmp_path, where)) == 2


@pytest.mark.parametrize("raw", [
    '{"protocol":"agent-pull-requests.filters/v1","where":{"repo":["Project/repository"],"repo":["bad/repo"]}}',
    '{"protocol":"agent-pull-requests.filters/v1","where":{"unknown":[]}}',
    "x" * 32769, "[]",
])
def test_malformed_filter_refused_before_credentials(tmp_path, monkeypatch, raw):
    from agent_worktrees import pr_credential_command
    argv = inputs(tmp_path)
    (tmp_path / "filter.json").write_text(raw)
    monkeypatch.setattr(pr_credential_command, "command_source",
                        lambda *_a, **_kw: pytest.fail("malformed credential selection"))
    assert pr_population_cli.dispatch(argv) == 2


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_non_utf8_filter_refused_before_credential_selection(tmp_path, monkeypatch, encoding):
    from agent_worktrees import pr_credential_command
    argv = inputs(tmp_path)
    (tmp_path / "filter.json").write_bytes(expression().canonical.encode(encoding))
    monkeypatch.setattr(pr_credential_command, "command_source",
                        lambda *_a, **_kw: pytest.fail("non-UTF8 credential selection"))
    assert pr_population_cli.dispatch(argv) == 2


def test_passive_contract_and_inventory_compatibility(monkeypatch, capsys):
    from agent_worktrees import __main__ as main
    monkeypatch.setattr(pop, "population", lambda *_a, **_kw: pytest.fail("contract read"))
    monkeypatch.setattr(main, "_ensure_cluster_loaded", lambda: pytest.fail("lifecycle loaded"))
    assert main.main(["pr-watch", "population", "--contract", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["protocol"] == "agent-worktrees.pr-population/v1"
    assert set(value["providers"]) == {"azure-devops"}
    assert value["providers"]["azure-devops"]["groups"] == []
    assert value["providers"]["azure-devops"]["age_fields"] == ["created"]
    assert value["max_requests"] == 64 and value["max_json_bytes"] == 1_048_576
    assert pr_cli.cmd_pr_watch_dispatch(["inventory", "--contract", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["providers"] == ["github", "gitea"]
    assert pr_population_cli.dispatch(["--contract", "--json", "--config", "arbitrary"]) == 2
    assert pr_population_cli.dispatch(["--contract", "--unknown"]) == 2


def test_actual_native_entry_renews_typed_credential_process(tmp_path, monkeypatch, capsys):
    from agent_worktrees import __main__ as main
    argv = inputs(tmp_path)
    material = {
        "schema": "fleet-manager.forge-credential", "version": 1, "token": "synthetic-entry",
        "expires_at": int(time.time()) + 60, "forge": {
            "provider": "azure-devops", "api_base": ENDPOINT, "repository": REPO,
            "permission": "observe", "authentication": "bearer",
        },
    }
    helper = tmp_path / "credential.py"
    helper.write_text("import json\nprint(json.dumps(" + repr(material) + "))\n")
    argv[argv.index("--credential-command") + 1] = json.dumps([sys.executable, str(helper)])
    monkeypatch.setattr(main, "_resolve_active_project", lambda *_a: pytest.fail("project lookup"))
    monkeypatch.setattr(main, "_ensure_cluster_loaded", lambda: pytest.fail("lifecycle loaded"))
    requests = transport(monkeypatch, [page([row()]), page([]), page([])])
    assert main.main(["pr-watch", "population", *argv]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "complete"
    assert len(requests) == 3
    assert all("synthetic-entry" in kw["input_text"] for _, kw in requests)


def test_actual_native_process_passive_contract_and_strict_parse():
    from agent_worktrees.providers.base import run_cli
    front = [sys.executable, "-c",
             "from agent_worktrees.current_roster_entry import console_entry; console_entry()",
             "pr-watch", "population"]
    result = run_cli(front + ["--contract", "--json"], timeout=10,
                     max_stdout_bytes=8192, max_stderr_bytes=8192)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["protocol"] == "agent-worktrees.pr-population/v1"
    result = run_cli(front + ["--contract", "--json", "--filter-file", "not-admitted"],
                     timeout=10, max_stdout_bytes=8192, max_stderr_bytes=8192)
    assert result.returncode == 2 and result.stdout == ""


@pytest.mark.parametrize("provider", ["github", "gitea"])
def test_population_refuses_other_providers_without_changing_inventory(tmp_path, monkeypatch, provider):
    from agent_worktrees import pr_credential_command
    monkeypatch.setattr(pr_credential_command, "command_source",
                        lambda *_a, **_kw: pytest.fail("unsupported provider credentials"))
    assert pr_population_cli.dispatch(inputs(tmp_path, provider=provider)) == 2


def test_query_cover_encodes_scope_and_never_expands_a_conjunction(monkeypatch):
    nested = expression({"all": [
        {"any": [{"from": {"users": [USER]}}, {"assigned": {"users": [OTHER]}}]},
        {"repo": [REPO]}, {"publication": ["draft"]},
    ]})
    assert len(pop.validate("azure-devops", REPO, ENDPOINT, nested, 4, 200)) == 2
    assert pop.validate("azure-devops", REPO, ENDPOINT, expression({"any": [
        {"repo": [REPO]}, {"from": {"users": [USER]}},
    ]}), 4, 200) == [None]
    custom_repo = "Project Space/repo#query"
    transport(monkeypatch, [page([])])
    requests = []

    def run(argv, **_kwargs):
        requests.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{"count":0,"value":[]}\n200', "")

    monkeypatch.setattr(pop, "run_cli", run)
    result = pop.population(
        "azure-devops", custom_repo, api_base=ENDPOINT,
        expression=expression({"repo": [custom_repo]}),
        credential_source=credential([], repo=custom_repo),
    )
    assert result["outcome"] == "complete"
    url = requests[0][requests[0].index("--url") + 1]
    assert "/Project%20Space/_apis/git/repositories/repo%23query/pullRequests?" in url
    assert parse_qs(urlsplit(url).query)["$top"] == ["50"]


def test_query_branch_cap_applies_to_the_selected_cover_not_discarded_conjuncts():
    identities = [f"00000000-0000-0000-0000-{number:012x}" for number in range(80)]
    wide = {"any": [
        {"from": {"users": identities[:40]}},
        {"from": {"users": identities[40:]}},
    ]}
    selected = expression({"all": [{"from": {"users": [USER]}}, wide]})
    assert pop.validate("azure-devops", REPO, ENDPOINT, selected, 4, 200) == [("creatorId", USER)]
    unfiltered = expression({"any": [wide, {"repo": [REPO]}]})
    assert pop.validate("azure-devops", REPO, ENDPOINT, unfiltered, 4, 200) == [None]
    with pytest.raises(ValueError, match="branch limit"):
        pop.population(
            "azure-devops", REPO, api_base=ENDPOINT, expression=expression(wide),
            credential_source=lambda: pytest.fail("over-limit selected query cover"),
        )
