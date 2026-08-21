"""Drain-safe cutover handoff mechanism (#743).

Covers the security-critical secret handoff in isolation: building/applying the
minimal warm-state payload, the round-trip that warms a fresh generation with no
re-unlock, and the owner-gated-transport enforcement on the ``handoff-export``
daemon action (the master password is never served over loopback TCP).
"""

from __future__ import annotations

import time

from agent_vault import cutover
from agent_vault.service import VaultService


def _unlocked_service(kpdb: str, password: str) -> VaultService:
    svc = VaultService()
    svc.cli.set_password(kpdb, password)
    svc._password_set_at[kpdb] = time.time()
    return svc


def test_build_payload_carries_unlocked_masters(tmp_path):
    kpdb = str(tmp_path / "v.kdbx")
    svc = _unlocked_service(kpdb, "s3cret")
    svc.ttl_override = 0
    payload = cutover.build_handoff_payload(svc)
    assert payload["master_passwords"] == {kpdb: "s3cret"}
    assert kpdb in payload["password_set_at"]
    assert payload["ttl_override"] == 0


def test_build_payload_empty_when_locked():
    svc = VaultService()  # nothing unlocked
    payload = cutover.build_handoff_payload(svc)
    assert payload["master_passwords"] == {}
    assert payload["password_set_at"] == {}


def test_roundtrip_warms_new_generation_without_reunlock(tmp_path):
    kpdb = str(tmp_path / "v.kdbx")
    old = _unlocked_service(kpdb, "master-pw")
    old.ttl_override = 0

    new = VaultService()
    assert not new.cli.has_password(kpdb)  # cold

    warmed = cutover.apply_handoff_payload(new, cutover.build_handoff_payload(old))
    assert warmed == 1
    # The new generation is unlocked with no re-unlock, same password + TTL policy.
    assert new.cli.has_password(kpdb)
    assert new.cli.get_password(kpdb) == "master-pw"
    assert kpdb in new._password_set_at
    assert new.ttl_override == 0


def test_apply_is_defensive_against_malformed():
    svc = VaultService()
    assert cutover.apply_handoff_payload(svc, {}) == 0
    assert cutover.apply_handoff_payload(svc, {"master_passwords": "nope"}) == 0
    assert cutover.apply_handoff_payload(svc, None) == 0
    # A malformed entry is skipped, a valid sibling still applies.
    n = cutover.apply_handoff_payload(svc, {"master_passwords": {"": "x", "/a.kdbx": 5,
                                                                 "/b.kdbx": "ok"}})
    assert n == 1
    assert svc.cli.get_password("/b.kdbx") == "ok"


def test_apply_ignores_malformed_ttl_override():
    svc = VaultService()
    svc.ttl_override = None
    # A non-int (or bool) ttl_override must not corrupt the daemon's TTL policy.
    for bad in ("300", 1.5, True, [], {}):
        cutover.apply_handoff_payload(svc, {"ttl_override": bad})
        assert svc.ttl_override is None
    # A real int (including 0 = persistent) is adopted.
    cutover.apply_handoff_payload(svc, {"ttl_override": 0})
    assert svc.ttl_override == 0


def test_handoff_export_refused_over_tcp(tmp_path):
    kpdb = str(tmp_path / "v.kdbx")
    svc = _unlocked_service(kpdb, "pw")
    resp = cutover.handoff_export_response(svc, transport="tcp")
    assert resp["ok"] is False and resp["refused"] is True
    assert "handoff" not in resp  # the secret is NOT in the refusal


def test_handoff_export_allowed_over_owner_gated(tmp_path):
    kpdb = str(tmp_path / "v.kdbx")
    svc = _unlocked_service(kpdb, "pw")
    # Only the AF_UNIX socket is a proven owner-gated transport today.
    resp = cutover.handoff_export_response(svc, transport="unix")
    assert resp["ok"] is True
    assert resp["handoff"]["master_passwords"] == {kpdb: "pw"}
    # The named pipe is NOT trusted for the master-secret handoff until its DACL
    # is hardened -- refused, no secret leaked.
    for transport in ("pipe", "tcp", "unknown"):
        refused = cutover.handoff_export_response(svc, transport=transport)
        assert refused["ok"] is False and refused["refused"] is True
        assert "handoff" not in refused


def test_handle_request_gates_handoff_export(tmp_path):
    kpdb = str(tmp_path / "v.kdbx")
    svc = _unlocked_service(kpdb, "pw")
    # Over TCP: refused, no secret leaked.
    tcp = svc.handle_request({"action": "handoff-export"}, transport="tcp")
    assert tcp["ok"] is False and tcp.get("refused") is True
    assert "handoff" not in tcp
    # Over the unix socket: served.
    unix = svc.handle_request({"action": "handoff-export"}, transport="unix")
    assert unix["ok"] is True
    assert unix["handoff"]["master_passwords"] == {kpdb: "pw"}
    # Default transport (unknown) is refused -- fail closed.
    unknown = svc.handle_request({"action": "handoff-export"})
    assert unknown["ok"] is False and unknown.get("refused") is True
