"""Invocation-local candidate values preserve the existing discovery contract."""
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_codespaces import account_binding, account_candidates, config, gh_account, picker_auth


@pytest.fixture
def sources(monkeypatch):
    calls = Counter()
    routing = gh_account.AccountInventory(
        ("Mapped", "BOUND"), repositories={"example/repo": "Configured"},
    )
    reports = SimpleNamespace(
        active_plugins=SimpleNamespace(authority="indeterminate", findings=("unreadable",)),
        config_d=SimpleNamespace(authority="complete", findings=()),
    )
    effective = SimpleNamespace(repos={"example/repo": {}})
    providers = {"Provider": Path("synthetic-provider")}

    def counted(name, value):
        def read(*args, **kwargs):
            calls[name] += 1
            if name == "configuration":
                assert kwargs == {"include_cwd": False, "provider_reports": reports}
            return value
        return read

    monkeypatch.setattr(picker_auth, "authorized_configs", counted("providers", providers))
    monkeypatch.setattr(account_binding, "bound_accounts_or_raise", counted("bound", ("Bound",)))
    monkeypatch.setattr(gh_account, "account_inventory", counted("routing", routing))
    monkeypatch.setattr(config, "scan_config_providers", counted("reports", reports))
    monkeypatch.setattr(config, "load_merged_config", counted("configuration", effective))
    monkeypatch.setattr(gh_account, "active_account", counted("active", "Active"))
    return SimpleNamespace(
        calls=calls, routing=routing, reports=reports,
        effective=effective, providers=providers,
    )


@pytest.mark.parametrize("concurrent", [False, True])
def test_read_returns_only_the_actual_single_invocation_values(sources, concurrent):
    notified = []
    result = account_candidates.read_candidates(on_account=notified.append, concurrent=concurrent)
    assert result.accounts == ("Mapped", "BOUND", "Provider", "Configured", "Active")
    assert result.routing is sources.routing
    assert result.configuration is sources.effective
    assert result.provider_reports is sources.reports
    assert result.provider_reports.active_plugins.findings == ("unreadable",)
    assert result.bound_accounts == ("Bound",)
    assert result.provider_configs == tuple(sources.providers.items())
    assert result.active_account == "Active"
    assert sources.calls == dict.fromkeys(
        ("providers", "bound", "routing", "reports", "configuration", "active"), 1,
    )
    assert sorted(value.casefold() for value in notified) == [
        "active", "bound", "configured", "mapped", "provider",
    ]
    if not concurrent:
        assert notified == ["Mapped", "BOUND", "Provider", "Configured", "Active"]


@pytest.mark.parametrize("concurrent", [False, True])
def test_candidates_remains_tuple_projection_and_not_a_cache(sources, concurrent):
    assert account_candidates.candidates(concurrent=concurrent) == (
        "Mapped", "BOUND", "Provider", "Configured", "Active",
    )
    assert account_candidates.read_candidates(concurrent=concurrent).accounts == (
        "Mapped", "BOUND", "Provider", "Configured", "Active",
    )
    assert set(sources.calls.values()) == {2}


@pytest.mark.parametrize("concurrent", [False, True])
@pytest.mark.parametrize("source", ["account_inventory", "active_account", "scan_config_providers"])
def test_reader_errors_still_propagate_without_a_partial_success(sources, monkeypatch, concurrent, source):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic source unavailable")

    monkeypatch.setattr(config if source == "scan_config_providers" else gh_account, source, fail)
    with pytest.raises(RuntimeError, match="synthetic source unavailable"):
        account_candidates.read_candidates(concurrent=concurrent)


@pytest.mark.parametrize("concurrent", [False, True])
def test_ambient_projection_and_callback_are_unchanged(sources, monkeypatch, concurrent):
    monkeypatch.setattr(picker_auth, "authorized_configs", lambda: {})
    monkeypatch.setattr(gh_account, "active_account", lambda: None)
    notified = []
    result = account_candidates.read_candidates(on_account=notified.append, concurrent=concurrent)
    assert result.accounts == ("Mapped", "BOUND", "Configured", None)
    assert result.active_account is None
    assert notified[-1] is None
