"""Managed initialization contract without a dependency on a sibling runtime."""

from __future__ import annotations

from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
from typing import Any

import pytest

from test_agenda_v2_core import CAPS, STORE, board, context, current, proof, v2

MANAGED = importlib.import_module("agenda_managed")
OWNER_CALL = MANAGED._owner
PROJECT = "fixture-project"
WORKTREE = "machine-20300101-123456-abcd"
NONCE = "0123456789abcdef"
STORE_ID = "a" * 32

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


def initialization() -> dict[str, Any]:
    return v2({
        "operation": "init", "worktree": WORKTREE, "board": board(),
        "baseline_context": context({"content": "initial"}), "resource_caps": CAPS,
    }, 0, "managed-initialization")


@pytest.fixture
def owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "owner"
    root.mkdir()

    def selected_owner(_python: str, project: str, argv: list[str]) -> dict[str, Any]:
        assert project == PROJECT
        if argv[0] == "worktree-data-read":
            return {"capabilities": ["worktree-data-v1"]}
        home = root / "agenda"
        home.mkdir(exist_ok=True)
        return {"project": PROJECT, "worktree_id": WORKTREE, "availability": "registered",
                "data": {"scope": {"project": PROJECT, "repository": PROJECT},
                         "worktree_id": WORKTREE, "creation_nonce": NONCE,
                         "domain": "agendas", "lane": "main", "store_id": STORE_ID,
                         "placement_generation": 1, "state_home": str(home),
                         "state_home_allocated": True, "store_content_validated": False,
                         "live_authority": False}}

    monkeypatch.setattr(MANAGED, "_owner", selected_owner)
    return root


def initialize(request: dict[str, Any] | None = None) -> dict[str, Any]:
    return MANAGED.initialize(
        sys.executable, PROJECT, WORKTREE, NONCE, "main", STORE_ID,
        initialization() if request is None else request, session="fixture-session",
    )


def test_selected_placement_and_agenda_share_identity_across_exact_retry(owner: Path) -> None:
    result = initialize()
    home = Path(result["placement"]["state_home"])
    assert home.is_relative_to(owner) and result["accepted"] is True
    state = STORE.read(home, worktree=WORKTREE)
    assert result["result"]["store_id"] == state["store_id"] == STORE_ID
    assert state["revision"] == 1 and len(state["records"]) > 3
    before = {p.relative_to(owner): p.read_bytes() for p in owner.rglob("*") if p.is_file()}
    retry = initialize()
    assert retry["accepted"] is False and retry["result"] == result["result"]
    assert {p.relative_to(owner): p.read_bytes() for p in owner.rglob("*") if p.is_file()} == before
    frame, item, _attempt = current(home)
    STORE.mutate(home, v2({
        "operation": "wait", "frame_id": frame, "item_id": item,
        "reason_kind": "external", "reason_ref": "fixture",
        "next_action": "Wait for owned evidence", "evidence": proof("waiting evidence"),
    }, 1, "later-update"), session="fixture-session")
    advanced = STORE.read(home)
    assert advanced["revision"] == 2
    retry = initialize()
    assert retry["accepted"] is False and retry["result"] == result["result"]
    assert STORE.read(home) == advanced


@pytest.mark.parametrize("fault", ["wrong-worktree", "mode", "caps", "context", "revision"])
def test_invalid_initialization_never_allocates(
    owner: Path, fault: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = deepcopy(initialization())
    if fault == "wrong-worktree":
        request["change"]["worktree"] = "another-worktree"
    elif fault == "mode":
        request["mode"] = "legacy"
    elif fault == "caps":
        request["change"]["resource_caps"]["max_depth"] = True
    elif fault == "context":
        request["change"]["baseline_context"] = {"facts": []}
    else:
        request["expected_revision"] = True
    monkeypatch.setattr(MANAGED, "_owner", lambda *args: pytest.fail("invalid request reached owner"))
    with pytest.raises(MANAGED.AgendaError):
        initialize(request)
    assert not list(owner.rglob("worktree-state"))


def test_chosen_identity_is_part_of_the_store_retry_fence(tmp_path: Path) -> None:
    request = initialization()
    result = STORE.mutate(tmp_path, request, initial_store_id=STORE_ID)
    assert STORE.mutate(tmp_path, request, initial_store_id=STORE_ID) == (result[0], False)
    before = STORE.read(tmp_path)
    with pytest.raises(MANAGED.AgendaError, match="mismatched"):
        STORE.mutate(tmp_path, request, initial_store_id="b" * 32)
    assert STORE.read(tmp_path) == before
    with pytest.raises(MANAGED.AgendaError, match="requires init"):
        STORE.mutate(tmp_path, v2({"operation": "park", "ref": "fixture", "reason": "discovery"},
                                 1, "not-init"), initial_store_id=STORE_ID)
    assert STORE.read(tmp_path) == before


def test_request_is_detached_before_the_placement_process_wait(
    owner: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = initialization()
    native = MANAGED._owner

    def changed_input(*args: Any) -> dict[str, Any]:
        request["change"]["worktree"] = "changed-during-owner-wait"
        return native(*args)

    monkeypatch.setattr(MANAGED, "_owner", changed_input)
    result = initialize(request)
    state = STORE.read(Path(result["placement"]["state_home"]))
    assert state["documents"]["cursor"]["worktree"] == WORKTREE
    assert request["change"]["worktree"] != WORKTREE


def test_managed_initialization_refuses_unrelated_contents(owner: Path) -> None:
    home = owner / "agenda"
    home.mkdir()
    retained = home / "retained.bin"
    retained.write_bytes(b"not an admitted agenda database")
    with pytest.raises(MANAGED.AgendaError, match="nonempty"):
        initialize()
    assert retained.read_bytes() == b"not an admitted agenda database"
    assert not (home / STORE.DATABASE).exists()


@pytest.mark.parametrize("fault", ["store", "scope", "authority"])
def test_changed_owner_binding_never_initializes_the_domain_store(
    owner: Path, fault: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = MANAGED._owner

    def changed_reply(*args: Any) -> dict[str, Any]:
        reply = native(*args)
        if "data" in reply:
            if fault == "store":
                reply["data"]["store_id"] = "b" * 32
            elif fault == "scope":
                reply["data"]["scope"]["project"] = "another-project"
            else:
                reply["data"]["live_authority"] = True
        return reply

    monkeypatch.setattr(MANAGED, "_owner", changed_reply)
    with pytest.raises(MANAGED.AgendaError):
        initialize()
    assert not list(owner.rglob(STORE.DATABASE))


def test_missing_provider_refuses_without_fallback_or_new_store(
    owner: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MANAGED, "_owner", OWNER_CALL)
    monkeypatch.setattr(MANAGED, "PROVIDER", (
        'print(\'{"ok":false,"error":{"code":"unsupported","message":"old provider"}}\')'
    ))
    with pytest.raises(MANAGED.AgendaError, match="old provider"):
        initialize()
    assert not list(owner.rglob("worktree-state")) and not list(owner.rglob("agenda.sqlite3"))


def test_payload_capability_and_strict_parser_do_not_allocate(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MANAGED, "_owner", lambda *args: pytest.fail("info reached owner"))
    assert MANAGED.main(["info"]) == 0
    assert json.loads(capsys.readouterr().out)["capabilities"] == [MANAGED.CAPABILITY]
    with pytest.raises(SystemExit) as failed:
        MANAGED.main(["info", "--project", PROJECT])
    assert failed.value.code == 2
