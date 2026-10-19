"""The daemon binds approved relay sources before connecting or persisting."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from credential_relay.http_auth import HttpAuthContext
from credential_relay.http_sources import HttpSourceRegistry
from credential_relay.sources.git_credential import GitCredentialSource

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


@pytest.mark.asyncio
async def test_registered_custom_source_is_reused_across_auth_instances():
    selected, source = policy()
    spec = target()
    factory = selected.bind(spec)
    context = HttpAuthContext(
        spec.origin, spec.auth.identity, "generation", spec.auth.resource, spec.auth.scope,
    )
    first_auth = factory(context)
    second_auth = factory(replace(context, generation="next"))
    headers = await asyncio.gather(
        first_auth.headers(context),
        second_auth.headers(replace(context, generation="next")),
    )
    assert all(
        header["Authorization"].split(" ", 1) == ["Example", "synthetic-credential"]
        for header in headers
    )
    assert [call[:2] for call in source.calls] == [
        ("supports", "get"), ("resolve", "get"),
        ("supports", "get"), ("resolve", "get"),
    ]


@pytest.mark.parametrize("reference", ["builtin/git-credential", "unknown@example/grant"])
def test_builtin_selection_retains_explicit_approval_and_registration(reference):
    spec = target(credential_source=reference, scheme="Bearer", resource="", scope="")
    with pytest.raises(PermissionError, match="approval required"):
        RemoteCredentialPolicy().bind(spec)
    selected = RemoteCredentialPolicy({
        reference: RemoteAuthApproval(origin=spec.origin, identity=spec.auth.identity),
    })
    selected.sources = HttpSourceRegistry()
    with pytest.raises(PermissionError, match="unavailable"):
        selected.bind(spec)


def test_builtin_source_is_fresh_for_each_factory_invocation():
    reference = "builtin/git-credential"
    selected = RemoteCredentialPolicy({
        reference: RemoteAuthApproval(origin="https://example.com", identity="synthetic"),
    })
    spec = target(credential_source=reference, scheme="Bearer", resource="", scope="")
    factory = selected.bind(spec)
    context = HttpAuthContext(spec.origin, spec.auth.identity, "generation")
    first_auth = factory(context)
    second_auth = factory(replace(context, generation="next"))
    assert isinstance(first_auth._source, GitCredentialSource)
    assert isinstance(second_auth._source, GitCredentialSource)
    assert first_auth._source is not second_auth._source


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh_registry", [False, True])
async def test_builtin_git_source_isolates_concurrent_endpoint_paths(monkeypatch, refresh_registry):
    reference = "builtin/git-credential"
    approval = RemoteAuthApproval(origin="https://example.com", identity="synthetic")
    selected = RemoteCredentialPolicy({reference: approval})
    if refresh_registry:
        selected.register_sources(HttpSourceRegistry())
    first_started = asyncio.Event()
    second_requested = asyncio.Event()
    release_first = asyncio.Event()
    resolved = []
    original_resolve = GitCredentialSource.resolve

    async def resolve(self, action, fields, *, timeout):
        if fields["path"] == "second":
            second_requested.set()
        return await original_resolve(self, action, fields, timeout=timeout)

    async def run_git(self, action, credential_input, *, timeout):
        fields = dict(line.split("=", 1) for line in credential_input.splitlines() if "=" in line)
        assert action == "fill"
        resolved.append((self, fields["path"]))
        if fields["path"] == "first":
            first_started.set()
            await release_first.wait()
        credential = "synthetic-" + fields["path"]
        return "\n".join(("username=synthetic", "pass" + "word=" + credential, ""))

    monkeypatch.setattr(GitCredentialSource, "resolve", resolve)
    monkeypatch.setattr(GitCredentialSource, "_run_git_credential", run_git)
    first = target(credential_source=reference, scheme="Bearer", resource="", scope="")
    second = first.model_copy(update={"endpoint": "https://example.com/second"})
    first = first.model_copy(update={"endpoint": "https://example.com/first"})
    context = HttpAuthContext(first.origin, first.auth.identity, "generation")
    first_auth = selected.bind(first)(context)
    second_auth = selected.bind(second)(context)
    first_request = asyncio.create_task(first_auth.headers(context))
    second_request = None
    try:
        await first_started.wait()
        second_request = asyncio.create_task(second_auth.headers(context))
        await second_requested.wait()
        release_first.set()
        headers = await asyncio.gather(first_request, second_request)
        prefix = "Bear" + "er "
        matches = [
            result == {"Authorization": prefix + "synthetic-" + path}
            for result, path in zip(headers, ("first", "second"), strict=True)
        ]
        assert all(matches), "Concurrent endpoint paths must receive their own credentials"
        assert [path for _, path in resolved] == ["first", "second"]
        assert resolved[0][0] is not resolved[1][0]
    finally:
        release_first.set()
        for request in (first_request, second_request):
            if request is not None and not request.done():
                request.cancel()
        await asyncio.gather(
            *(request for request in (first_request, second_request) if request is not None),
            return_exceptions=True,
        )
