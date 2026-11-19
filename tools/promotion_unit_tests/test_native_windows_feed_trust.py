"""Windows trust-admission controls with the native store and TLS engine inert."""
from __future__ import annotations

import ssl
import sys
import time
from unittest.mock import Mock

import pytest

import installer_native_feed as native


@pytest.mark.parametrize(("trust", "accepted"), [
    (True, True),
    ({ssl.Purpose.SERVER_AUTH.oid}, True),
    (frozenset({ssl.Purpose.SERVER_AUTH.oid}), True),
    (frozenset({ssl.Purpose.CLIENT_AUTH.oid}), False),
    (frozenset(), False),
    (False, False),
], ids=["all-purposes", "mutable-server-auth", "immutable-server-auth", "client-auth-only", "no-purpose", "untrusted"])
def test_native_windows_tls_preserves_purpose_qualified_roots(
    monkeypatch: pytest.MonkeyPatch, trust: object, accepted: bool,
) -> None:
    certificate = b"synthetic certificate bytes"
    context = Mock(spec=ssl.SSLContext)
    factory = Mock(return_value=context)

    def certificates(store: str) -> list[tuple[bytes, str, object]]:
        return [(certificate, "x509_asn", trust)] if store == "ROOT" else []

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ssl, "SSLContext", factory)
    monkeypatch.setattr(ssl, "enum_certificates", certificates, raising=False)
    result = native._tls(
        {"allow_insecure_host": "[]", "system_certs": "true"},
        environment={}, deadline=time.monotonic() + 10,
    )
    assert result is context
    factory.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
    if accepted:
        context.load_verify_locations.assert_called_once_with(cadata=ssl.DER_cert_to_PEM_cert(certificate))
    else:
        context.load_verify_locations.assert_not_called()
