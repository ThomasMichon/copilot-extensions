"""Discovery overlaps independent reads without changing account precedence."""
from __future__ import annotations

from threading import Event, Lock
from types import SimpleNamespace

import pytest

from agent_codespaces import (
    account_binding, account_candidates, config, gh_account, lifecycle, picker_auth,
)


@pytest.fixture(autouse=True)
def account_sources(monkeypatch):
    monkeypatch.setattr(lifecycle, "validate_context", lambda: None)
    monkeypatch.setattr(account_binding, "bound_accounts_or_raise", lambda: ())
    monkeypatch.setattr(picker_auth, "authorized_configs", lambda: {})
    monkeypatch.setattr(gh_account, "account_inventory", gh_account.AccountInventory)
    monkeypatch.setattr(gh_account, "active_account", lambda: "active")
    monkeypatch.setattr(gh_account, "account_for_repo", lambda _: "configured")
    monkeypatch.setattr(
        config, "load_merged_config",
        lambda **_: SimpleNamespace(repos={"example/repo": {}}),
    )
    monkeypatch.setattr(config, "scan_config_providers", lambda **_: object())


def space(name, account):
    return lifecycle.CodespaceInfo(
        name, name, "example/repo", "main", "Available", "small", account,
    )


def test_ordinary_listing_retains_serial_execution(monkeypatch):
    def unexpected_executor(*args, **kwargs):
        raise AssertionError("ordinary discovery must not create executor threads")

    monkeypatch.setattr(lifecycle, "ThreadPoolExecutor", unexpected_executor)
    monkeypatch.setattr(account_candidates, "ThreadPoolExecutor", unexpected_executor)
    monkeypatch.setattr(lifecycle, "_list_codespaces_under", lambda account: [space(account, account)])
    assert [row.account for row in lifecycle.list_codespaces()] == ["configured", "active"]


@pytest.mark.asyncio
async def test_namespace_enumeration_opts_into_overlap(monkeypatch):
    from agent_codespaces import resolver

    selected = []

    def listing(*, concurrent=False):
        selected.append(concurrent)
        return []

    monkeypatch.setattr(resolver, "list_codespaces", listing)
    assert await resolver.CodespaceResolver().list_specs() == []
    assert selected == [True]


def test_known_account_starts_before_configuration_finishes(monkeypatch):
    started = Event()
    monkeypatch.setattr(account_binding, "bound_accounts_or_raise", lambda: ("bound",))

    def configured(**_):
        assert started.wait(3), "known account listing waited for configuration"
        return SimpleNamespace(repos={"example/repo": {}})

    def listing(account):
        if account == "bound":
            started.set()
        return [space(f"space-{account}", account)]

    monkeypatch.setattr(config, "load_merged_config", configured)
    monkeypatch.setattr(lifecycle, "_list_codespaces_under", listing)
    assert [row.account for row in lifecycle.list_codespaces(concurrent=True)] == [
        "bound", "configured", "active",
    ]


def test_account_reads_overlap_but_merge_in_source_order(monkeypatch):
    second_finished = Event()
    monkeypatch.setattr(
        gh_account, "account_inventory",
        lambda: gh_account.AccountInventory(("first", "second")),
    )

    def listing(account):
        if account == "first":
            assert second_finished.wait(3), "account reads were serialized"
        if account == "second":
            second_finished.set()
        return [space("shared", account)]

    monkeypatch.setattr(lifecycle, "_list_codespaces_under", listing)
    assert lifecycle.list_codespaces(concurrent=True)[0].account == "first"


def test_sources_overlap_and_keep_priority_and_casefold_dedup(monkeypatch):
    configured = Event()
    monkeypatch.setattr(account_binding, "bound_accounts_or_raise", lambda: ("Bound",))
    monkeypatch.setattr(picker_auth, "authorized_configs", lambda: {"Provider": object()})

    def mapped():
        assert configured.wait(3), "independent metadata sources were serialized"
        return gh_account.AccountInventory(("Mapped", "BOUND"))

    def configured_repos(**_):
        configured.set()
        return SimpleNamespace(repos={"example/repo": {}})

    monkeypatch.setattr(gh_account, "account_inventory", mapped)
    monkeypatch.setattr(config, "load_merged_config", configured_repos)
    monkeypatch.setattr(gh_account, "account_for_repo", lambda _: "mapped")
    monkeypatch.setattr(gh_account, "active_account", lambda: "provider")
    notified = []
    assert account_candidates.candidates(on_account=notified.append, concurrent=True) == (
        "Mapped", "BOUND", "Provider",
    )
    assert sorted(value.casefold() for value in notified) == [
        "bound", "mapped", "provider",
    ]


def test_early_lower_priority_casing_does_not_change_the_selected_owner(monkeypatch):
    started = Event()
    monkeypatch.setattr(account_binding, "bound_accounts_or_raise", lambda: ("Bound",))
    monkeypatch.setattr(config, "load_merged_config", lambda **_: SimpleNamespace(repos={}))
    monkeypatch.setattr(gh_account, "active_account", lambda: "bound")
    called = []

    def inventory():
        assert started.wait(3)
        return gh_account.AccountInventory(("BOUND",))

    def listing(account):
        called.append(account)
        started.set()
        return [space("shared", account)]

    monkeypatch.setattr(gh_account, "account_inventory", inventory)
    monkeypatch.setattr(lifecycle, "_list_codespaces_under", listing)
    assert lifecycle.list_codespaces(concurrent=True)[0].account == "BOUND"
    assert called == ["Bound"]


@pytest.mark.parametrize("providers,expected", [
    ({}, ("configured", None)),
    ({"authorized": object()}, ("authorized", "configured")),
])
def test_ambient_fallback_waits_for_complete_candidate_set(monkeypatch, providers, expected):
    monkeypatch.setattr(picker_auth, "authorized_configs", lambda: providers)
    monkeypatch.setattr(gh_account, "active_account", lambda: None)
    notified = []
    assert account_candidates.candidates(on_account=notified.append, concurrent=True) == expected
    assert (None in notified) == (None in expected)


def test_context_refusal_joins_already_started_reads(monkeypatch):
    finished = Event()
    monkeypatch.setattr(account_binding, "bound_accounts_or_raise", lambda: ("bound",))
    started = Event()

    def configured(**_):
        assert started.wait(3)
        raise lifecycle.ContextRefused("refused")

    def listing(_):
        started.set()
        finished.set()
        return []

    monkeypatch.setattr(config, "load_merged_config", configured)
    monkeypatch.setattr(lifecycle, "_list_codespaces_under", listing)
    with pytest.raises(lifecycle.ContextRefused, match="refused"):
        lifecycle.list_codespaces(concurrent=True)
    assert finished.is_set()


def test_native_concurrency_is_bounded_and_failures_still_surface(monkeypatch):
    monkeypatch.setattr(
        gh_account, "account_inventory",
        lambda: gh_account.AccountInventory(tuple(f"a{i}" for i in range(8))),
    )
    four_started = Event()
    lock = Lock()
    active = peak = 0

    def listing(_):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 4:
                four_started.set()
        try:
            assert four_started.wait(3)
            raise RuntimeError("provider failed")
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(lifecycle, "_list_codespaces_under", listing)
    with pytest.raises(RuntimeError, match="provider failed"):
        lifecycle.list_codespaces(concurrent=True)
    assert peak == 4
    assert active == 0
