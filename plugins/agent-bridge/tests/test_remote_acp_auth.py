"""The daemon binds approved relay sources before connecting or persisting."""

from __future__ import annotations

from dataclasses import replace

import pytest
from credential_relay.http_auth import HttpAuthContext
from credential_relay.http_sources import HttpSourceRegistry

from agent_bridge.models import ServiceConfig
from agent_bridge.remote_acp_auth import RemoteCredentialPolicy
from agent_bridge.remote_acp_config import RemoteAcpTarget, RemoteAuthApproval
from agent_bridge.remote_acp_sessions import connect_remote_session
from agent_bridge.session_manager import session_manager_from_config

REFERENCE = "synthetic@example/grant"


class Source:
    def __init__(self):
        self.calls = []

    def supports(self, action, fields):
        self.calls.append(("supports", action, fields))
        return action == "get"

    async def resolve(self, action, fields, *, timeout):
        self.calls.append(("resolve", action, fields))
        return "\n".join(("username=synthetic", "password=" + "synthetic-credential", ""))


def target(**auth_changes):
    return RemoteAcpTarget(
        endpoint="https://example.com/acp", transport="streamable-http",
        auth={
            "source": "injected", "identity": "synthetic",
            "credential_source": REFERENCE, "scheme": "Example",
            "resource": "https://example.com/resource", "scope": "read",
            **auth_changes,
        },
    )


def policy():
    approval = RemoteAuthApproval(
        origin="https://example.com", identity="synthetic", scheme="Example",
        resource="https://example.com/resource", scope="read",
    )
    result = RemoteCredentialPolicy({REFERENCE: approval})
    source = Source()
    result.sources.register(REFERENCE, source, scoped=True)
    return result, source


@pytest.mark.asyncio
async def test_approved_source_builds_real_custom_header_and_receives_exact_scope():
    selected, source = policy()
    spec = target()
    factory = selected.bind(spec)
    assert source.calls == []
    context = HttpAuthContext(spec.origin, spec.auth.identity, "generation", spec.auth.resource, spec.auth.scope)
    auth = factory(context)
    headers = await auth.headers(context)
    assert headers["Authorization"].split(" ", 1) == ["Example", "synthetic-credential"]
    fields = source.calls[-1][2]
    assert fields == {
        "protocol": "https", "host": "example.com", "path": "acp",
        "username": "synthetic", "resource": spec.auth.resource, "scope": "read",
    }
    for wrong in (
        replace(context, origin="https://other.example.com"),
        replace(context, identity="other"), replace(context, generation="other"),
        replace(context, resource="other"), replace(context, scope="write"),
    ):
        with pytest.raises(PermissionError, match="context"):
            await auth.headers(wrong)
    assert len(source.calls) == 2


@pytest.mark.parametrize("changes", [
    {"identity": "other"}, {"scheme": "Other"}, {"scope": "write"},
    {"resource": "https://other.example.com"}, {"credential_source": "unknown@example/grant"},
    {"credential_source": None},
])
def test_auth_approval_mismatch_refuses_before_source_lookup(changes):
    selected, source = policy()
    with pytest.raises(PermissionError):
        selected.bind(target(**changes))
    assert source.calls == []


def test_auth_approval_binds_origin_and_registered_scope_support():
    selected, source = policy()
    spec = RemoteAcpTarget.model_validate({
        **target().model_dump(), "endpoint": "https://other.example.com/acp",
    })
    with pytest.raises(PermissionError, match="mismatch"):
        selected.bind(spec)
    selected.sources = HttpSourceRegistry()
    selected.sources.register(REFERENCE, source)
    with pytest.raises(PermissionError, match="scoped"):
        selected.bind(target())
    selected.sources.register(REFERENCE, source, scoped=True)
    with pytest.raises(PermissionError, match="ambiguous"):
        selected.bind(target())
    assert source.calls == []


@pytest.mark.asyncio
async def test_missing_approval_refuses_before_session_or_network(tmp_db):
    manager = session_manager_from_config(tmp_db, ServiceConfig(remote_acp={"peer": target()}))
    rows = manager._remote_acp_catalog.rows()
    assert not rows[0]["spawnable"]
    assert rows[0]["unavailable_reason"] == "credential-source-binding-required"
    with pytest.raises(ValueError, match="binding"):
        await connect_remote_session(manager, "peer")
    assert manager.list_sessions() == []


def test_explicit_source_registry_refresh_does_not_create_false_ambiguity():
    selected, source = policy()
    registry = HttpSourceRegistry()
    registry.register(REFERENCE, source, scoped=True)
    selected.register_sources(registry)
    selected.register_sources(registry)
    assert selected.bind(target())
    assert source.calls == []
