"""Tests for the CodeSpace account binding store (state redirected to tmp)."""

from __future__ import annotations

import pytest

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


def test_bound_account_or_raise_corrupt_file_still_degrades_to_none(monkeypatch, tmp_path):
    """A corrupt/missing FILE is this store's own documented, intentional
    degrade for the legacy bound readers. Only lock CONTENTION propagates
    from this lock-strict variant; content-strict admission is opt-in."""
    _redirect_store(monkeypatch, tmp_path)
    binding_mod.BINDINGS_FILE.write_text("{not json", encoding="utf-8")
    assert binding_mod.bound_account_or_raise("cs-one") is None


def test_bound_account_or_raise_propagates_lock_contention(monkeypatch, tmp_path):
    """Unlike ``bound_account`` (which swallows this into ``None``), the
    strict variant propagates lock contention -- a caller feeding a
    destructive reclaim decision must not treat an UNAVAILABLE binding
    the same as a confirmed absence of one."""
    _redirect_store(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("Could not acquire account binding lock (held by another process)")
    monkeypatch.setattr(binding_mod, "_binding_lock", _boom)
    assert binding_mod.bound_account("cs-one") is None
    with pytest.raises(RuntimeError, match="lock"):
        binding_mod.bound_account_or_raise("cs-one")


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


def test_bound_accounts_or_raise_propagates_lock_contention(monkeypatch, tmp_path):
    """Unlike ``bound_accounts`` (which swallows this into ``()``), the
    strict variant propagates lock contention -- a caller building the
    candidate list for a destructive reclaim's fallback scan must not
    treat an UNAVAILABLE binding store the same as a confirmed-empty
    one."""
    _redirect_store(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("Could not acquire account binding lock (held by another process)")
    monkeypatch.setattr(binding_mod, "_binding_lock", _boom)
    assert binding_mod.bound_accounts() == ()
    with pytest.raises(RuntimeError, match="lock"):
        binding_mod.bound_accounts_or_raise()


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
    assert binding_mod.list_bindings(strict=True)[0].account == "acct-a"


@pytest.mark.parametrize("data", ["{corrupt", "[]", '{"cs-one": []}', '{"cs-one": {"account": "acct-a"}}'])
def test_strict_admission_read_rejects_bad_store_without_replacing_it(monkeypatch, tmp_path, data):
    _redirect_store(monkeypatch, tmp_path)
    binding_mod.BINDINGS_FILE.write_text(data, encoding="utf-8")
    assert binding_mod.list_bindings() == []
    with pytest.raises(RuntimeError, match="binding"):
        binding_mod.list_bindings(strict=True)
    assert binding_mod.BINDINGS_FILE.read_text(encoding="utf-8") == data


def test_strict_admission_read_preserves_missing_store_as_empty(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    assert binding_mod.list_bindings(strict=True) == []
    assert not binding_mod.BINDINGS_FILE.exists()


def test_strict_admission_read_distinguishes_permission_failure(monkeypatch, tmp_path):
    from pathlib import Path

    _redirect_store(monkeypatch, tmp_path)
    binding_mod.BINDINGS_FILE.write_text("{}", encoding="utf-8")
    original = Path.read_text

    def unreadable(path, *args, **kwargs):
        if path == binding_mod.BINDINGS_FILE:
            raise PermissionError("fixture permission denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert binding_mod.list_bindings() == []
    with pytest.raises(RuntimeError, match="binding"):
        binding_mod.list_bindings(strict=True)


@pytest.mark.parametrize("data,error", [(b'["bad"]', AttributeError), (b"\xff", UnicodeDecodeError)])
def test_strict_reader_does_not_change_legacy_unhandled_format_errors(monkeypatch, tmp_path, data, error):
    _redirect_store(monkeypatch, tmp_path)
    binding_mod.BINDINGS_FILE.write_bytes(data)
    with pytest.raises(error):
        binding_mod.list_bindings()
    with pytest.raises(RuntimeError, match="binding"):
        binding_mod.list_bindings(strict=True)
    assert binding_mod.BINDINGS_FILE.read_bytes() == data


def test_strict_admission_read_normalizes_lock_access_failure_only_when_opted_in(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)

    def denied():
        raise PermissionError("fixture lock access denied")

    monkeypatch.setattr(binding_mod, "_binding_lock", denied)
    with pytest.raises(PermissionError):
        binding_mod.list_bindings()
    with pytest.raises(RuntimeError, match="binding"):
        binding_mod.list_bindings(strict=True)
