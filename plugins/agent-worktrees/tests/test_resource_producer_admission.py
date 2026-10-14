"""Actionable input diagnostics and shared owner-path admission remain strict."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import config as cfg, resource_producer as producer, tracking_producer_write as writer
from test_resource_producer import call, reserve
from test_resource_producer import owner as owner


@pytest.mark.parametrize("operation,required", [
    ("bind", ["identity", "token"]),
    ("complete", ["token"]),
])
@pytest.mark.parametrize("payload", [{}, [], None, {"extra": True}])
def test_rejected_input_enumerates_both_allowed_key_sets(
    operation: str, required: list[str], payload: object,
) -> None:
    result = writer.apply_resource_producer({
        "schema": producer.SCHEMA, "version": 1, "operation": operation, "input": payload,
        "yaml_path": "/unused/owner.yaml", "owner_ref": "m/p/owner",
    })
    assert result["error"] == "rejected"
    assert result["message"] == (
        f"{operation}: expected exactly {required} or {sorted([*required, 'allocation_fence'])}"
    )


@pytest.mark.parametrize("operation", ["unknown", "", None, ["bind"], {"bind": True}])
def test_unknown_operation_is_explicit_before_record_io(operation: object) -> None:
    result = writer.apply_resource_producer({
        "schema": producer.SCHEMA, "version": 1, "operation": operation, "input": {},
        "yaml_path": "/unused/owner.yaml", "owner_ref": "m/p/owner",
    })
    assert result["error"] == "rejected"
    assert result["message"].startswith(f"unsupported producer operation {operation!r}; expected one of ")
    for supported in ("reserve", "begin", "bind", "complete", "inspect", "close"):
        assert supported in result["message"]


def test_owner_path_uses_shared_resolver_and_receiving_authority(
    owner: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = owner
    reservation = reserve(path)
    alternate = path.with_name("custom-owner.yaml")
    alternate.write_bytes(path.read_bytes())
    before = {target: target.read_bytes() for target in (path, alternate)}
    seen: list[str] = []

    def resolve(ref: str, authority: cfg.Config) -> tuple[Path, str, None]:
        assert authority.repo_name == "p" and authority.machine == "m"
        seen.append(ref)
        return alternate, "owner", None

    monkeypatch.setattr(writer, "resolve_owner_record_path", resolve)
    assert call(alternate, "inspect", {"token": reservation["token"]})["ok"]
    result = call(path, "inspect", {"token": reservation["token"]})
    assert result["error"] == "rejected"
    assert "record path does not match owning-project authority" in result["message"]
    assert seen == ["m/p/owner", "m/p/owner"]
    assert all(target.read_bytes() == content for target, content in before.items())


@pytest.mark.parametrize("resolved_path,resolved_id,error", [
    (None, "owner", "shared resolver refused authority"),
    (None, "owner", None),
    ("existing", "other-owner", None),
])
def test_shared_resolver_failure_never_falls_back_to_guessed_path(
    owner: tuple, monkeypatch: pytest.MonkeyPatch,
    resolved_path: str | None, resolved_id: str, error: str | None,
) -> None:
    path, _ = owner
    reservation = reserve(path)
    before = path.read_bytes()
    monkeypatch.setattr(
        writer, "resolve_owner_record_path",
        lambda *args: (path if resolved_path else None, resolved_id, error),
    )
    result = call(path, "inspect", {"token": reservation["token"]})
    assert result["error"] == "rejected"
    assert result["message"] == (
        error or "producer owner ledger cannot be resolved by owning-project authority"
    )
    assert path.read_bytes() == before


def test_owner_mismatch_is_refused_before_shared_path_resolution(
    owner: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = owner
    reservation = reserve(path)
    before = path.read_bytes()
    monkeypatch.setattr(
        writer, "resolve_owner_record_path",
        lambda *args: pytest.fail("mismatched owner reached shared resolver"),
    )
    result = call(path, "inspect", {"token": reservation["token"]}, ref="m/p/consumer")
    assert result["error"] == "rejected"
    assert path.read_bytes() == before
