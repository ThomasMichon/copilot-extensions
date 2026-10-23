"""Durable pending intent follows complete native producer initialization."""

import os

import pytest

from fleet_manager import client_activation as activation
from fleet_manager import client_construction as construction
from fleet_manager import client_frontdoor as frontdoor
from fleet_manager.client_deactivation import deactivate_client_runtime
from fleet_manager.planning import PlanError
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_admission import database_bytes, identity
from test_client_construction import signed_wheel as signed_wheel  # noqa: F401
from test_client_distribution import wheel as wheel  # noqa: F401
from test_client_frontdoor import register, selected as selected, states  # noqa: F401
from test_client_activation import activate


@pytest.mark.parametrize("operation", ["commands", "selectors"])
@pytest.mark.parametrize("boundary", ["empty", "partial", "synced"])
def test_failed_first_header_has_no_pending_owner_and_preserves_abandoned_attempt(
    selected, primitive, monkeypatch, operation, boundary,
):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    if operation == "selectors":
        deactivate_client_runtime(root, identity())
    pending = namespace / (frontdoor.PENDING if operation == "commands" else activation.PENDING)
    publication = primitive._runtime_component("versioned_runtime_publication")
    original = publication.Receipt._append
    floor = database_bytes(root)
    candidate = states([slot / "candidate-bytes"])

    def invoke():
        if operation == "commands":
            return register(root, fixture, staged, trusted, directory)
        return activate(root, fixture, staged, trusted)

    def interrupt(receipt, value):
        assert value["kind"] == "header"
        if boundary == "partial":
            receipt.stream.write(b'{"unfinished":')
            receipt.stream.flush()
            os.fsync(receipt.stream.fileno())
        elif boundary == "synced":
            original(receipt, value)
        raise OSError("injected first-header interruption")

    with monkeypatch.context() as patch:
        patch.setattr(publication.Receipt, "_append", interrupt)
        with pytest.raises(PlanError):
            invoke()
    assert not pending.exists()
    assert states(candidate) == candidate and database_bytes(root) == floor
    history = namespace / (frontdoor.HISTORY if operation == "commands" else activation.HISTORY)
    attempts = list(history.iterdir())
    if operation == "selectors":
        # The existing, explicitly withdrawn activation is retained history.
        attempts = [item for item in attempts if not (item / "publication.jsonl").exists()]
    assert len(attempts) == 1
    evidence = states(item for item in attempts[0].rglob("*") if item.is_file())
    report = invoke()
    assert report["frontdoor_registered"] if operation == "commands" else report["active"]
    assert states(evidence) == evidence and states(candidate) == candidate
    assert database_bytes(root) == floor


@pytest.mark.parametrize("boundary", ["empty", "partial", "synced"])
def test_failed_retry_header_does_not_poison_an_existing_pending_selector(
    selected, primitive, monkeypatch, boundary,
):
    root, fixture, staged, trusted, _commands, namespace, _slot = selected
    deactivate_client_runtime(root, identity())
    original_replace = os.replace

    def stop_second_selector(source, target):
        if target == namespace / "last-known-good":
            raise OSError("injected second-selector interruption")
        return original_replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", stop_second_selector)
        with pytest.raises(PlanError, match="outcome is unknown"):
            activate(root, fixture, staged, trusted)
    pending = namespace / activation.PENDING
    active = construction._read(pending)
    directory = namespace / activation.HISTORY / active["publication"]
    first = directory / "publication.jsonl"
    before = states([first, pending, namespace / "current-version"])
    floor = database_bytes(root)
    publication = primitive._runtime_component("versioned_runtime_publication")
    original_append = publication.Receipt._append

    def interrupt(receipt, value):
        if boundary == "partial":
            receipt.stream.write(b'{"unfinished":')
            receipt.stream.flush()
            os.fsync(receipt.stream.fileno())
        elif boundary == "synced":
            original_append(receipt, value)
        raise OSError("injected retry-header interruption")

    with monkeypatch.context() as patch:
        patch.setattr(publication.Receipt, "_append", interrupt)
        with pytest.raises(PlanError, match="outcome is unknown"):
            activate(root, fixture, staged, trusted)
    assert states(before) == before and database_bytes(root) == floor
    assert not (directory / "publication.1.jsonl").exists()
    abandoned = states(directory.glob(".receipt-*"))
    assert len(abandoned) == 1
    result = activate(root, fixture, staged, trusted)
    assert result["active"] and not result["reused"]
    assert (directory / "publication.1.jsonl").is_file() and not pending.exists()
    assert states(abandoned) == abandoned and states([first]) == {first: before[first]}
    assert database_bytes(root) == floor
