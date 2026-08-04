"""Tests for the CodeSpace account binding store (state redirected to tmp)."""

from __future__ import annotations

from agent_codespaces import account_binding as binding_mod


def _redirect_store(monkeypatch, tmp_path):
    monkeypatch.setattr(binding_mod, "BINDINGS_FILE", tmp_path / "account-bindings.json")
    monkeypatch.setattr(binding_mod, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(binding_mod, "_LOCK_FILE", tmp_path / "account-bindings.lock")
    monkeypatch.setattr(binding_mod, "ensure_runtime_dir", lambda: None)


def test_bind_and_bound_account_round_trip(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    rec = binding_mod.bind("cs-one", "acct-a", "owner/repo")
    assert rec is not None
    assert rec.codespace == "cs-one"
    assert rec.account == "acct-a"
    assert rec.repo == "owner/repo"
    assert binding_mod.bound_account("cs-one") == "acct-a"


def test_bind_empty_values_are_noops(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    assert binding_mod.bind("", "acct-a") is None
    assert binding_mod.bind("cs-one", "") is None
    assert binding_mod.list_bindings() == []


def test_bound_account_unknown_is_none(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    assert binding_mod.bound_account("missing") is None


def test_bound_account_corrupt_file_is_none(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    binding_mod.BINDINGS_FILE.write_text("{not json", encoding="utf-8")
    assert binding_mod.bound_account("cs-one") is None


def test_unbind_removes_record(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    binding_mod.bind("cs-one", "acct-a")
    assert binding_mod.unbind("cs-one") is True
    assert binding_mod.bound_account("cs-one") is None
    assert binding_mod.unbind("cs-one") is False


def test_bound_accounts_distinct_non_empty(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    binding_mod.bind("cs-one", "acct-a")
    binding_mod.bind("cs-two", "acct-a")
    binding_mod.bind("cs-three", "acct-b")
    assert binding_mod.bound_accounts() == ("acct-a", "acct-b")


def test_list_bindings(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    binding_mod.bind("cs-one", "acct-a", "owner/repo-a")
    binding_mod.bind("cs-two", "acct-b", "owner/repo-b")
    by_name = {b.codespace: b for b in binding_mod.list_bindings()}
    assert set(by_name) == {"cs-one", "cs-two"}
    assert by_name["cs-one"].repo == "owner/repo-a"
    assert by_name["cs-two"].account == "acct-b"


def test_read_tolerates_unknown_keys(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    binding_mod.BINDINGS_FILE.write_text(
        '{"cs-one": {"codespace": "cs-one", "account": "acct-a", '
        '"repo": "owner/repo", "bound_at": 1.0, "future_field": 42}}',
        encoding="utf-8",
    )
    assert binding_mod.bound_account("cs-one") == "acct-a"
