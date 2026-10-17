"""Account status must match before and after a session-owned quota read."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from typing import Any

import pytest

from budget_guidance.models import ModelError
from budget_guidance.session_identity import acquire_verified_session_quota
from test_quota import projection


def _status(**changes: Any) -> dict[str, Any]:
    return {
        "isAuthenticated": True, "login": "example-user", "host": "https://github.com",
        "authType": "secret-sentinel", "statusMessage": "secret-sentinel", **changes,
    }


class Session:
    def __init__(self, *statuses: dict[str, Any]) -> None:
        self.session_id = "session-example"
        self.calls: list[str] = []
        self.statuses = list(statuses or [_status(), _status()])
        self.rpc = SimpleNamespace(
            git_hub_auth=SimpleNamespace(get_status=self.status),
            quota=SimpleNamespace(get=self.quota),
        )

    async def status(self, *, timeout: float | None = None) -> SimpleNamespace:
        self.calls.append("status")
        value = self.statuses.pop(0)
        return SimpleNamespace(to_dict=lambda: value)

    async def quota(self, *, timeout: float | None = None) -> SimpleNamespace:
        self.calls.append("quota")
        return SimpleNamespace(to_dict=lambda: deepcopy(projection()))


def _read(session: Session, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(acquire_verified_session_quota(session, **{
        "expected_session_id": "session-example", "expected_login": "example-user",
        "expected_host": "https://github.com", "runtime_version": "runtime-example", **kwargs,
    }))


def test_stable_identity_brackets_only_existing_session_get() -> None:
    session = Session(_status(login="EXAMPLE-user"), _status(host="https://GITHUB.com:443/"))
    result = _read(session)
    assert session.calls == ["status", "quota", "status"]
    assert result["account_identity"] == {
        "status": "matched", "scope": "session-github-auth",
        "host": "https://github.com", "login": "example-user", "quota_binding": "not-atomic",
    }
    assert result["availability"] == "cached"
    assert result["freshness"] == "unknown"
    assert result["provider_observed_at"] is None
    assert result["calculated"] is None
    assert "secret-sentinel" not in json.dumps(result)
    assert result["advice"]["worker_mutation"] is False
    assert result["advice"]["operator_launch_blocked"] is False


@pytest.mark.parametrize(
    ("login", "host"),
    [
        ("mona-cat_octo", "https://github.com"),
        ("octo_admin", "https://github.com"),
        ("managed-user", "https://enterprise.example.ghe.com"),
        ("a" * 35 + "_org", "https://github.com"),
    ],
)
def test_managed_user_and_enterprise_host_identity_can_match(login: str, host: str) -> None:
    session = Session(_status(login=login, host=host), _status(login=login.upper(), host=host))
    result = _read(session, expected_login=login, expected_host=host)
    assert session.calls == ["status", "quota", "status"]
    assert result["account_identity"]["status"] == "matched"
    assert result["account_identity"]["login"] == login
    assert result["availability"] == "cached"


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("status", [
    _status(login="other-user"), _status(host="https://other.example"),
    _status(login=None), _status(host=None), _status(isAuthenticated=False),
    _status(isAuthenticated=1), _status(host="https://user:secret@github.com"),
])
def test_bad_identity_never_returns_even_cached_balances(when: str, status: dict) -> None:
    session = Session(status, _status()) if when == "before" else Session(_status(), status)
    result = _read(session)
    assert session.calls == (["status"] if when == "before" else ["status", "quota", "status"])
    assert result["availability"] == "unavailable"
    assert result["allowance"] is None
    assert result["consumption"] is None
    assert result["account_identity"]["login"] is None
    assert result["account_identity"]["host"] is None
    assert "other-user" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


def test_unsupported_status_does_not_fall_back_to_unverified_quota() -> None:
    session = Session()
    session.rpc.git_hub_auth = SimpleNamespace()
    result = _read(session)
    assert session.calls == []
    assert result["account_identity"]["status"] == "unsupported"
    assert result["allowance"] is None


@pytest.mark.parametrize("missing", ["rpc", "null-rpc", "session-id"])
def test_missing_session_capabilities_never_raise_or_read_quota(missing: str) -> None:
    session = Session()
    if missing == "null-rpc":
        session.rpc = None
    elif missing == "rpc":
        del session.rpc
    else:
        del session.session_id
    result = _read(session)
    assert session.calls == []
    assert result["availability"] == "unavailable"
    assert result["allowance"] is None
    assert result["account_identity"]["status"] == (
        "unverified" if missing == "session-id" else "unsupported"
    )


@pytest.mark.parametrize("missing", ["session-id", "rpc"])
def test_session_capabilities_disappearing_during_rpc_withhold_values(missing: str) -> None:
    session = Session()
    original = session.status

    async def status(*, timeout: float | None = None) -> SimpleNamespace:
        raw = await original(timeout=timeout)
        if missing == "session-id":
            del session.session_id
        else:
            del session.rpc
        return raw

    session.rpc.git_hub_auth.get_status = status
    result = _read(session)
    assert session.calls == ["status"]
    assert result["account_identity"]["status"] == "changed"
    assert result["consumption"] is None


@pytest.mark.parametrize("session_id", ["", "different"])
def test_requester_mismatch_precedes_any_status_or_quota_rpc(session_id: str) -> None:
    session = Session()
    result = _read(session, expected_session_id=session_id)
    assert session.calls == []
    assert result["allowance"] is None


@pytest.mark.parametrize("change", ["session-id", "rpc"])
def test_session_replacement_during_quota_read_withholds_all_values(change: str) -> None:
    session = Session()

    async def quota(*, timeout: float | None = None) -> SimpleNamespace:
        session.calls.append("quota")
        if change == "session-id":
            session.session_id = "replacement"
        else:
            session.rpc = SimpleNamespace()
        return SimpleNamespace(to_dict=lambda: projection())

    session.rpc.quota.get = quota
    result = _read(session)
    assert session.calls == ["status", "quota"]
    assert result["allowance"] is None
    assert result["account_identity"]["status"] == "changed"


@pytest.mark.parametrize("when", ["first", "last"])
def test_rpc_replacement_inside_status_cannot_read_a_replacement_session(when: str) -> None:
    session = Session()
    original = session.status

    async def status(*, timeout: float | None = None) -> SimpleNamespace:
        raw = await original(timeout=timeout)
        if when == "first" or "quota" in session.calls:
            session.rpc = SimpleNamespace()
        return raw

    session.rpc.git_hub_auth.get_status = status
    result = _read(session)
    assert session.calls == (["status"] if when == "first" else ["status", "quota", "status"])
    assert result["account_identity"]["status"] == "changed"
    assert result["consumption"] is None


def test_individually_short_rpcs_share_one_total_deadline(monkeypatch) -> None:
    import budget_guidance.session_identity as module

    clock = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    session = Session()
    original = session.status

    async def status(*, timeout: float | None = None) -> SimpleNamespace:
        assert timeout is not None
        if timeout < 0.02:
            raise asyncio.TimeoutError
        clock[0] += 0.02
        return await original(timeout=timeout)

    session.rpc.git_hub_auth.get_status = status
    result = _read(session, timeout=0.03)
    assert session.calls == ["status", "quota"]
    assert result["allowance"] is None
    assert result["diagnostics"] == ["identity-quota-acquisition-timeout"]


@pytest.mark.guard
@pytest.mark.parametrize("expired_after", ["first-status", "quota", "last-status"])
@pytest.mark.parametrize("completed_at", [1.0, 1.1], ids=["at-deadline", "after-deadline"])
def test_elapsed_total_deadline_refuses_completed_rpc_values(
    monkeypatch, expired_after, completed_at,
):
    import budget_guidance.session_identity as module

    clock = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    session = Session()
    original_status = session.status
    original_quota = session.quota
    budgets = []

    async def status(*, timeout=None):
        budgets.append(timeout)
        value = await original_status(timeout=timeout)
        clock[0] = (
            completed_at
            if expired_after == "first-status" or "quota" in session.calls
            else 0.25
        )
        return value

    async def quota(*, timeout=None):
        budgets.append(timeout)
        value = await original_quota(timeout=timeout)
        clock[0] = completed_at if expired_after == "quota" else 0.5
        return value

    session.rpc.git_hub_auth.get_status = status
    session.rpc.quota.get = quota
    result = _read(session, timeout=1.0)

    assert result["allowance"] is None
    assert result["availability"] == "error"
    assert result["diagnostics"] == ["identity-quota-acquisition-timeout"]
    expected_calls = {
        "first-status": ["status"],
        "quota": ["status", "quota"],
        "last-status": ["status", "quota", "status"],
    }
    assert session.calls == expected_calls[expired_after]
    assert budgets == pytest.approx([1.0, 0.75, 0.5][:len(session.calls)])


def test_not_implemented_status_is_explicit_and_preserves_quota_unsupported() -> None:
    session = Session()

    async def unsupported(*, timeout: float | None = None) -> None:
        raise NotImplementedError

    session.rpc.git_hub_auth.get_status = unsupported
    result = _read(session)
    assert result["account_identity"]["status"] == "unsupported"
    assert session.calls == []

    session = Session()
    session.rpc.quota = SimpleNamespace()
    result = _read(session)
    assert result["account_identity"]["status"] == "matched"
    assert result["diagnostics"] == ["session-quota-rpc-unsupported"]
    assert result["allowance"] is None

    session = Session()
    session.rpc.quota.get = unsupported
    with pytest.raises(NotImplementedError):
        _read(session)
    assert session.calls == ["status"]


@pytest.mark.parametrize("where", ["first-status", "quota", "last-status"])
def test_one_deadline_cancels_owned_work_and_withholds_values(where: str) -> None:
    session = Session()
    original = session.status
    cancelled: list[bool] = []

    async def status(*, timeout: float | None = None) -> SimpleNamespace:
        if where == "first-status" or (where == "last-status" and "quota" in session.calls):
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.append(True)
        return await original(timeout=timeout)

    async def quota(*, timeout: float | None = None) -> SimpleNamespace:
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)
        return SimpleNamespace(to_dict=lambda: projection())

    session.rpc.git_hub_auth.get_status = status
    if where == "quota":
        session.rpc.quota.get = quota
    result = _read(session, timeout=0.01)
    assert cancelled
    assert result["allowance"] is None
    assert result["availability"] == "error"


def test_status_transport_failure_redacted_and_programming_errors_propagate() -> None:
    session = Session()

    async def broken(*, timeout: float | None = None) -> None:
        raise OSError("secret-sentinel")

    session.rpc.git_hub_auth.get_status = broken
    result = _read(session)
    assert result["availability"] == "error"
    assert "secret-sentinel" not in json.dumps(result)

    async def bug(*, timeout: float | None = None) -> None:
        raise RuntimeError("host programming error")

    session.rpc.git_hub_auth.get_status = bug
    with pytest.raises(RuntimeError, match="programming"):
        _read(session)


@pytest.mark.parametrize("kwargs", [
    {"expected_login": "../user"}, {"expected_host": "http://github.com"},
    {"expected_login": "a" * 40}, {"expected_login": "_user"}, {"expected_login": "user_"},
    {"expected_host": "https://github.com/path"}, {"expected_host": "https://github.com?q=x"},
    {"expected_host": "https://git\nhub.com"}, {"expected_host": " https://github.com"},
    {"timeout": True}, {"timeout": float("nan")}, {"timeout": 31},
])
def test_invalid_invocation_is_rejected_before_rpc(kwargs: dict) -> None:
    session = Session()
    with pytest.raises(ModelError):
        _read(session, **kwargs)
    assert session.calls == []
