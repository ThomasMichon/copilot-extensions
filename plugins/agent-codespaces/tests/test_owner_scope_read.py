"""The owner projects actual read provenance without mutating its producer."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from dropin_registry import EntryDecision, Finding, ScanAuthority

from agent_codespaces import account_candidates, config, gh_account, owner_inventory, worktrees


@pytest.fixture
def read(monkeypatch):
    def report(authority):
        return SimpleNamespace(
            authority=authority, snapshot=SimpleNamespace(decisions={}),
            to_dict=lambda: {"authority": authority.value},
        )

    result = account_candidates.CandidateRead(
        ("a",), gh_account.AccountInventory(("a",)),
        SimpleNamespace(repos={}),
        SimpleNamespace(
            active_plugins=report(ScanAuthority.COMPLETE),
            config_d=report(ScanAuthority.ABSENT),
        ),
        ("a",), (("a", Path("synthetic-provider")),), "a",
    )
    monkeypatch.setattr(account_candidates, "read_candidates", lambda: result)
    monkeypatch.setattr(worktrees, "validate_context", lambda: None)
    monkeypatch.setattr(owner_inventory, "credential", lambda account: "synthetic-credential")
    monkeypatch.setattr(
        config, "load_merged_config",
        lambda **kwargs: pytest.fail("must not load configuration twice"),
    )
    return result


def test_projected_scope_is_immutable_and_retains_selection_changes(read):
    first = owner_inventory.capture_scope()
    read.configuration.repos["synthetic/repo"] = {}
    second = owner_inventory.capture_scope()
    assert first != second
    assert first.accounts == second.accounts == ("a",)
    assert first.configuration != second.configuration
    assert read.bound_accounts == ("a",)
    assert read.provider_configs == (("a", Path("synthetic-provider")),)


def test_same_merged_value_with_indeterminate_provider_is_not_authority(read):
    owner_inventory.capture_scope()
    read.provider_reports.active_plugins.authority = ScanAuthority.INDETERMINATE
    with pytest.raises(RuntimeError, match="provider authority unavailable"):
        owner_inventory.capture_scope()


@pytest.mark.parametrize("provider", ["active_plugins", "config_d"])
def test_complete_scan_with_retained_indeterminate_entry_is_not_authority(read, provider):
    first = owner_inventory.capture_scope()
    report = getattr(read.provider_reports, provider)
    report.authority = ScanAuthority.COMPLETE
    report.snapshot.decisions["synthetic-provider"] = EntryDecision.indeterminate(
        Finding("synthetic", "synthetic-provider", "indeterminate", "target-unusable"),
    )
    for _ in range(2):
        with pytest.raises(RuntimeError, match="provider authority unavailable"):
            owner_inventory.capture_scope()
    assert first.accounts == read.accounts
