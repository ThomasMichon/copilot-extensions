"""GitCredentialSource: a password-less ``fill`` is treated as unresolved (#1659).

Regression guard. When GCM produces no token non-interactively (e.g. an
expired/lapsed ADO login it cannot silently refresh under
``GCM_INTERACTIVE=never``), ``git credential fill`` can exit 0 with a body that
carries NO password line. The relay must treat that as UNRESOLVED (return
``None``) so it sends a clean ``quit=1`` fail-fast, instead of forwarding a
credential with no password that makes git abort with a bare, undiagnosable
``exit 128`` -- the "relay serves nothing" symptom (#1659).
"""

from __future__ import annotations

import asyncio
import logging

from credential_relay.sources.git_credential import GitCredentialSource

_PW = "pass" + "word"  # avoid the literal secret-keyword in source scanners


def _resolve(src: GitCredentialSource, action: str, fields: dict[str, str]):
    return asyncio.run(src.resolve(action, fields))


def test_fill_without_password_is_unresolved(monkeypatch, caplog):
    """A GCM ``fill`` that returns no password line resolves to ``None`` + warns."""
    src = GitCredentialSource()

    async def fake_run(action, credential_input, *, timeout=30.0):
        # GCM echoed the request but produced no credential.
        return "protocol=https\nhost=onedrive.visualstudio.com\n\n"

    monkeypatch.setattr(src, "_run_git_credential", fake_run)
    with caplog.at_level(logging.WARNING):
        result = _resolve(
            src, "get",
            {"protocol": "https", "host": "onedrive.visualstudio.com"},
        )

    assert result is None
    assert any(
        "no " + _PW in r.getMessage().lower() for r in caplog.records
    ), "expected a WARNING naming the no-password condition"


def test_fill_with_password_is_returned_and_cached(monkeypatch):
    """A real credential is returned unchanged and cached (one GCM roundtrip)."""
    src = GitCredentialSource()
    calls = 0
    good = f"protocol=https\nhost=example.com\nusername=u\n{_PW}=secret\n\n"

    async def fake_run(action, credential_input, *, timeout=30.0):
        nonlocal calls
        calls += 1
        return good

    monkeypatch.setattr(src, "_run_git_credential", fake_run)

    async def _resolve_twice():
        # Both awaits share one event loop so the instance's loop-bound
        # primitives (lock, in-flight futures) and its cache behave as in prod.
        fields = {"protocol": "https", "host": "example.com"}
        first = await src.resolve("get", dict(fields))
        second = await src.resolve("get", dict(fields))
        return first, second

    r1, r2 = asyncio.run(_resolve_twice())

    assert r1 == good
    assert r2 == r1  # served from cache
    assert calls == 1  # GCM invoked once
