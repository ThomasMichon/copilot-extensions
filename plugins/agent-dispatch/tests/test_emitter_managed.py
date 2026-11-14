"""Credential-free source output -> existing managed-create HTTP receiver."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.producers import emitter
from agent_dispatch.queue_producer_receipts import task_producer_receipt
from tests._helpers import TEST_REPO, RepoDefaultingQueue
from tests.test_task_relevance_transport import transport


@pytest.fixture
def venue(tmp_path, monkeypatch):
    queue = RepoDefaultingQueue(tmp_path / "tasks.db")
    spec = {
        "id": "review-source",
        "source": "emitter",
        "command": ["source-reader", "discover"],
        "task_output": "json",
        "interval_seconds": 30,
        "evaluator_ref": "source-evaluator",
        "managed_producer": {"grant_command": ["grant-owner", "read"]},
    }
    grant = queue.handoff_producer_scope(
        TEST_REPO,
        "emitter",
        producer_id=spec["id"],
        expected_generation=0,
        required_label="reviews",
    ).as_dict()
    monkeypatch.setattr(
        emitter,
        "receipts_path",
        lambda name: tmp_path / "receipts" / name / "receipts.jsonl",
    )
    with TestClient(
        create_app(
            queue,
            token="data",
            control_token="control",
            enable_mcp=False,
            verification_interval=0,
        )
    ) as api:
        calls = []
        with DispatchClient(
            "http://testserver",
            token="data",
            transport=transport(api, calls),
        ) as client:
            yield queue, spec, grant, client, calls


def row(**overrides):
    return {
        "title": "Review object 7",
        "repo": TEST_REPO,
        "labels": ["reviews"],
        "dedup_key": "review:7",
        "producer_request_id": "occurrence:1",
        **overrides,
    }


def author(venue, value=None, *, grant=None):
    _, spec, original, client, _ = venue
    return emitter._author_tasks(
        client,
        spec,
        json.dumps([row() if value is None else value]),
        runner=lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(original if grant is None else grant),
        ),
    )


def test_managed_emitter_records_real_receipt_observes_changes_and_allows_next_episode(venue):
    queue, _, grant, _, calls = venue
    first = author(venue)[0]
    with queue._connect() as conn:
        receipt = task_producer_receipt(conn, queue.get(first["id"]))
    assert receipt["request_id"] == "occurrence:1"
    assert receipt["producer_id"] == "review-source"
    before = len(calls)
    observed = author(venue, row(title="New candidate", producer_request_id="occurrence:2"))[0]
    assert observed["id"] == first["id"] and observed["title"] == first["title"]
    assert not any(method == "POST" for method, _ in calls[before:])
    queue.abandon(first["id"], permitted=True, reason="Source closed")
    second = author(venue, row(producer_request_id="occurrence:2"))[0]
    assert second["id"] != first["id"]
    assert second["producer_fence"]["request_id"] == "occurrence:2"
    assert grant["producer_capability"] not in json.dumps([first, observed, second])


@pytest.mark.parametrize(
    "change",
    [
        {"active_producer": "foreign"},
        {"current_generation": True},
        {"scope": {"repo": "other/project", "source": "emitter"}},
        {"scope": {"repo": TEST_REPO, "source": "foreign"}},
        {"producer_capability": ""},
    ],
)
def test_grant_mismatch_refuses_before_create(venue, change):
    _, _, grant, _, calls = venue
    with pytest.raises(ValueError, match="grant"):
        author(venue, grant={**grant, **change})
    assert not any(method == "POST" for method, _ in calls)


def test_retired_grant_cannot_observe_or_create_under_new_selection(venue):
    queue, _, _, _, calls = venue
    author(venue)
    queue.handoff_producer_scope(
        TEST_REPO,
        "emitter",
        producer_id="review-source",
        expected_generation=1,
    )
    before = len(calls)
    with pytest.raises(ValueError, match="selected generation"):
        author(venue)
    assert not any(method == "POST" for method, _ in calls[before:])


def test_foreign_dedup_occupant_is_neither_adopted_nor_replaced(venue):
    queue, _, _, _, calls = venue
    foreign = queue.create("Unrelated", repo=TEST_REPO, source="manual", dedup_key="review:7")
    with pytest.raises(ValueError, match="cannot adopt"):
        author(venue)
    assert queue.get(foreign.id).source == "manual"
    assert not any(method == "POST" for method, _ in calls)


@pytest.mark.parametrize(
    "change",
    [
        {"producer_request_id": ""},
        {"dedup_key": None},
        {"repo": "other/project"},
        {"producer_capability": "untrusted"},
        {"producer_scope": {"repo": TEST_REPO, "source": "emitter"}},
    ],
)
def test_source_cannot_supply_grant_or_omit_occurrence_identity(venue, change):
    *_, calls = venue
    with pytest.raises(ValueError, match="occurrence"):
        author(venue, row(**change))
    assert not any(method == "POST" for method, _ in calls)


@pytest.mark.parametrize("failure", ["exit", "json", "timeout"])
def test_grant_resolver_diagnostics_never_include_output_or_argv(venue, failure):
    _, spec, _, client, calls = venue
    secret = "private-resolver-output"
    spec["managed_producer"]["grant_command"].append(secret)

    def run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args[0], 30, output=secret, stderr=secret)
        return SimpleNamespace(
            returncode=1 if failure == "exit" else 0,
            stdout=secret,
            stderr=secret,
        )

    with pytest.raises(ValueError) as error:
        emitter._author_tasks(client, spec, json.dumps([row()]), runner=run)
    assert str(error.value) == "managed producer grant resolution failed"
    assert secret not in str(error.value)
    assert not any(method == "POST" for method, _ in calls)


def test_prior_receiver_without_exact_lookup_refuses_before_post(venue):
    _, spec, grant, _, _ = venue
    calls = []

    def respond(request):
        calls.append(request.method)
        if request.url.path == "/producer-scopes/status":
            return httpx.Response(200, json={**grant, "producer_capability": None})
        assert request.url.path == "/health"
        return httpx.Response(200, json={})

    with DispatchClient("http://prior", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DispatchUpgradeRequired, match="exact repo-scoped"):
            emitter._author_tasks(
                client,
                spec,
                json.dumps([row()]),
                runner=lambda *args, **kwargs: SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(grant),
                ),
            )
    assert calls == ["GET", "GET"]


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"grant_command": "shell expression"},
        {"grant_command": ["reader"], "extra": True},
        {"grant_command": ["reader"], "timeout_seconds": float("inf")},
        {"grant_command": ["reader"], "timeout_seconds": True},
    ],
)
def test_invalid_managed_config_is_rejected(venue, config):
    _, spec, *_ = venue
    with pytest.raises(emitter.EmitterError, match="managed_producer"):
        emitter.validate_spec({**spec, "managed_producer": config})


def test_empty_source_does_not_resolve_credentials(venue):
    _, spec, _, client, calls = venue
    assert (
        emitter._author_tasks(
            client,
            spec,
            "[]",
            runner=lambda *args, **kwargs: pytest.fail("unneeded grant"),
        )
        == []
    )
    assert calls == []


def test_missing_grant_resource_is_redacted_before_any_command_or_create(
    venue, tmp_path, monkeypatch
):
    from agent_dispatch import repository_resources

    _, spec, _, client, calls = venue
    spec["managed_producer"]["grant_command"] = ["reader", "repo:fixtures:private-grant-missing"]
    monkeypatch.setattr(
        repository_resources,
        "_registered_repository",
        lambda *args, **kwargs: (tmp_path, "worktree"),
    )
    with pytest.raises(ValueError) as error:
        emitter._author_tasks(
            client,
            spec,
            json.dumps([row()]),
            runner=lambda *args, **kwargs: pytest.fail("missing resource ran a command"),
        )
    assert str(error.value) == "managed producer grant resolution failed"
    assert error.value.__suppress_context__
    assert calls == []


def test_malformed_grant_resource_is_redacted_during_spec_validation(venue):
    _, spec, *_ = venue
    spec["managed_producer"]["grant_command"] = ["reader", "repo:fixtures:../private-grant"]
    with pytest.raises(emitter.EmitterError) as error:
        emitter.validate_spec(spec)
    assert str(error.value) == ("managed_producer.grant_command has invalid repository resources")


@pytest.mark.parametrize("script_operand", [False, True])
def test_supplier_grant_operands_follow_registered_root_relocation(
    venue,
    tmp_path,
    monkeypatch,
    script_operand,
):
    from agent_dispatch import repository_resources
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.registrar_document import qualify_supplier_context

    _, spec, grant, client, _ = venue
    old = tmp_path / "original-supplier"
    old.mkdir()
    (old / "grant.py").write_text(json.dumps(grant), encoding="utf-8")
    (old / "discover.py").write_text("[]", encoding="utf-8")
    command = [str(old / "grant.py"), "literal-operand"]
    if script_operand:
        command.insert(0, sys.executable)
    declaration = load_declaration(
        {
            "kind": "emitter",
            "name": "source",
            "spec": {
                **spec,
                "cwd": str(old),
                "command": [sys.executable, str(old / "discover.py")],
                "managed_producer": {"grant_command": command},
            },
        }
    )
    qualified = qualify_supplier_context(declaration, "source-tools", old)
    persisted = json.loads(json.dumps(dict(qualified.spec)))
    assert str(old) not in json.dumps(persisted)
    new = tmp_path / "moved-supplier"
    old.rename(new)
    monkeypatch.setattr(
        repository_resources,
        "_registered_repository",
        lambda *args, **kwargs: (new, "worktree"),
    )
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        assert kwargs["cwd"] == str(new)
        assert argv[-1] == "literal-operand"
        script = Path(argv[1] if script_operand else argv[0])
        assert script == new / "grant.py"
        return SimpleNamespace(returncode=0, stdout=script.read_text(encoding="utf-8"))

    [created] = emitter._author_tasks(client, persisted, json.dumps([row()]), runner=run)
    assert created["producer_fence"]["producer_id"] == spec["id"]
    assert len(seen) == 1


@pytest.mark.parametrize("side_load", [False, True])
def test_tick_and_side_load_keep_grants_out_of_domain_output_and_receipts(venue, side_load):
    _, spec, grant, client, _ = venue
    spec["side_load"] = {"command": ["source-reader", "discover", "{change_ref}"]}
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        assert grant["producer_capability"] not in json.dumps(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(grant if command[0] == "grant-owner" else [row()]),
        )

    if side_load:
        result = emitter.run_side_load(
            client,
            {"id": "source-registration", "kind": "emitter", "spec": spec},
            "object:7",
            runner=run,
        )
    else:
        result = emitter.run_tick(client, spec, holder="test-host", runner=run)
    receipts = emitter.read_receipts(spec["id"])
    assert len(result["created"]) == 1
    assert receipts["receipts"][0]["task_id"] == result["created"][0]["id"]
    assert [command[0] for command in seen] == ["source-reader", "grant-owner"]
    assert grant["producer_capability"] not in json.dumps([result, receipts])
