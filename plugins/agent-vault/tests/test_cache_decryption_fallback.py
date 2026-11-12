"""A selected service can read without changing an unreadable DPAPI cache."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from agent_vault import authenticated_curl, batch_reads, cache, cli, config, credential_reads, kek
from agent_vault.cache_dpapi import MAGIC, CacheProtectionError


@pytest.fixture(params=["native", "wrapped-key"])
def protected_cache(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> tuple[cache.PersistentCache, dict[str, bytes]]:
    monkeypatch.setattr(cache, "IS_WINDOWS", True)
    monkeypatch.setenv(cache.CACHE_ENABLE_ENV, "1")
    monkeypatch.setenv(cache.CACHE_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(config.PINNED_CONTEXT_ENV, raising=False)
    monkeypatch.setattr(config, "resolve_context", lambda **_kw: SimpleNamespace(group="Allowed"))
    monkeypatch.setattr(cache.PersistentCache, "_crypto_available", lambda _self: True)
    ciphertext = MAGIC + b"fixture-ciphertext" if request.param == "native" else b"ciphertext"
    (tmp_path / "credential-cache.enc").write_bytes(ciphertext)
    if request.param == "wrapped-key":
        (tmp_path / "credential-cache.key").write_bytes(kek.WRAP_MAGIC + b"Dfixture-key")
        crypto = ModuleType("agent_vault.protected_crypto")
        crypto.Fernet = lambda _key: pytest.fail("unavailable key reached cipher construction")
        monkeypatch.setitem(sys.modules, "agent_vault.protected_crypto", crypto)

    def unavailable(*_args: object) -> bytes:
        raise kek.KekError("private DPAPI diagnostic")

    monkeypatch.setattr(kek, "_dpapi", unavailable)
    persistent = cache.PersistentCache()
    monkeypatch.setattr(cache, "get_cache", lambda: persistent)
    monkeypatch.setattr(
        persistent, "put", lambda *_a, **_kw: pytest.fail("unreadable cache was written"),
    )
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    return persistent, before


def _arguments(**overrides: object) -> SimpleNamespace:
    return SimpleNamespace(
        **{"entry": "entry", "field": "password", "strict_cache": True, **overrides},
    )


def _assert_preserved(persistent: cache.PersistentCache, before: dict[str, bytes]) -> None:
    assert {
        path.name: path.read_bytes() for path in persistent._base_dir.iterdir()
    } == before


@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("strict_cache", [False, True])
def test_decryption_failure_uses_one_selected_service_read_without_writeback(
    protected_cache,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    pinned: bool,
    strict_cache: bool,
) -> None:
    persistent, before = protected_cache
    if pinned:
        monkeypatch.setenv(config.PINNED_CONTEXT_ENV, "fixture-selected-context")
    requests = []
    monkeypatch.setattr(cli, "ensure_service", lambda: True)

    def read(request: dict, timeout: object = None) -> dict:
        requests.append(request)
        assert request == {"action": "get", "entry": "entry", "field": "password", "refresh": False}
        return {"ok": True, "value": "fixture-live-value"}

    monkeypatch.setattr(cli, "send_command", read)
    assert cli.cmd_get(_arguments(strict_cache=strict_cache)) == 0
    captured = capsys.readouterr()
    assert captured.out == "fixture-live-value\n"
    assert "cache_decryption_unavailable" in captured.err
    assert "private DPAPI diagnostic" not in captured.err
    assert "fixture-live-value" not in captured.err
    assert len(requests) == 1
    _assert_preserved(persistent, before)


@pytest.mark.parametrize("outcome", ["cache-only", "unavailable", "refused"])
def test_cache_only_and_service_failure_remain_authoritative(
    protected_cache, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    persistent, before = protected_cache
    requests = []

    def ensure() -> bool:
        assert outcome != "cache-only"
        return outcome != "unavailable"

    def read(request: dict, timeout: object = None) -> dict:
        assert outcome == "refused"
        requests.append(request)
        return {"ok": False, "error": "selected service refused", "needs_unlock": True}

    monkeypatch.setattr(cli, "ensure_service", ensure)
    monkeypatch.setattr(cli, "send_command", read)
    result = credential_reads.read_credential(_arguments(cache_only=outcome == "cache-only"))
    assert result.code == 1 and result.value is None
    assert len(requests) == (1 if outcome == "refused" else 0)
    if outcome == "cache-only":
        assert result.error.startswith("cache_decryption_unavailable;")
    elif outcome == "refused":
        assert result.error == "selected service refused"
    _assert_preserved(persistent, before)


def test_cache_hit_and_other_protection_errors_do_not_use_fallback(
    protected_cache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistent, before = protected_cache
    monkeypatch.setattr(
        cli, "ensure_service", lambda: pytest.fail("unexpected selected-service access"),
    )
    monkeypatch.setattr(persistent, "get", lambda *_a, **_kw: "fixture-cached-value")
    result = credential_reads.read_credential(_arguments())
    assert result.code == 0 and result.source == "cache" and result.warning is None

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise CacheProtectionError("cache recovery policy refused")

    monkeypatch.setattr(persistent, "get", refuse)
    with pytest.raises(CacheProtectionError):
        credential_reads.read_credential(_arguments())
    _assert_preserved(persistent, before)


def test_authenticated_http_uses_fallback_without_diagnostic_credential_exposure(
    protected_cache, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    persistent, before = protected_cache
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(
        cli, "send_command",
        lambda _request, timeout=None: {"ok": True, "value": "fixture-live-value"},
    )
    binding = SimpleNamespace(entry="entry", scheme="token")
    assert authenticated_curl._credential(binding, "password", False) == "fixture-live-value"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cache_decryption_unavailable" in captured.err
    assert "private DPAPI diagnostic" not in captured.err
    assert "fixture-live-value" not in captured.err
    _assert_preserved(persistent, before)


def test_explicit_refresh_still_requires_authoritative_cache_reconciliation(
    protected_cache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistent, before = protected_cache
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(
        cli, "send_command",
        lambda _request, timeout=None: {"ok": True, "value": "fixture-live-value"},
    )
    result = credential_reads.read_credential(_arguments(refresh=True))
    assert result.code == 2 and result.value is None
    _assert_preserved(persistent, before)


@pytest.mark.parametrize("succeeds", [False, True])
def test_batch_fallback_preserves_results_and_separates_safe_diagnostics(
    protected_cache, monkeypatch: pytest.MonkeyPatch, capsys, succeeds: bool,
) -> None:
    persistent, before = protected_cache
    requests = []
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO('[{"entry":"entry"}]'))

    def read(request: dict, timeout: object = None) -> dict:
        requests.append(request)
        assert request == {"action": "get", "entry": "entry", "field": "password", "refresh": False}
        return (
            {"ok": True, "value": "fixture-live-value"} if succeeds
            else {"ok": False, "error": "fixture-service-refused"}
        )

    monkeypatch.setattr(cli, "send_command", read)
    assert batch_reads.cmd_batch_get(SimpleNamespace(refresh=False)) == (0 if succeeds else 1)
    captured = capsys.readouterr()
    expected = {
        "entry": "entry", "field": "password", "value": "fixture-live-value" if succeeds else None,
        "ok": succeeds, "source": "service",
    }
    if not succeeds:
        expected["error"] = "fixture-service-refused"
    assert json.loads(captured.out) == {"results": [expected]}
    assert captured.err == f"Warning: {credential_reads.CACHE_DECRYPTION_WARNING}\n"
    assert "private DPAPI diagnostic" not in captured.err
    assert "fixture-live-value" not in captured.err
    assert len(requests) == 1
    _assert_preserved(persistent, before)
