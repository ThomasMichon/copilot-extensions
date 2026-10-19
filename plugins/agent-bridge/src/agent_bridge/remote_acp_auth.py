"""Adopter approval binds a registered relay source before remote attachment."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from credential_relay.http_auth import HttpAuthContext, SourceHttpAuth
from credential_relay.http_sources import HttpSourceRegistry
from credential_relay.sources.git_credential import GitCredentialSource

from .remote_acp_config import RemoteAcpTarget, RemoteAuthApproval


class RemoteCredentialPolicy:
    def __init__(self, approvals: Mapping[str, RemoteAuthApproval] | None = None) -> None:
        self.approvals = dict(approvals or {})
        self.sources = HttpSourceRegistry()
        self.sources.register("builtin/git-credential", GitCredentialSource(cache_ttl=0))

    def register_sources(self, sources: HttpSourceRegistry) -> None:
        selected = HttpSourceRegistry()
        selected.register("builtin/git-credential", GitCredentialSource(cache_ttl=0))
        selected.include(sources)
        self.sources = selected

    def bind(self, target: RemoteAcpTarget) -> Callable[[HttpAuthContext], SourceHttpAuth] | None:
        if target.auth.source != "injected":
            return None
        reference = target.auth.credential_source
        if reference is None:
            raise PermissionError("Remote HTTP credential source reference required")
        approval = self.approvals.get(reference or "")
        if approval is None:
            raise PermissionError("Remote HTTP credential source approval required")
        actual = (
            target.origin, target.auth.identity, target.auth.resource,
            target.auth.scope, target.auth.scheme,
        )
        expected = (
            approval.origin, approval.identity, approval.resource,
            approval.scope, approval.scheme,
        )
        if actual != expected:
            raise PermissionError("Remote HTTP credential source approval mismatch")
        registered = self.sources.get(reference)
        if (target.auth.resource or target.auth.scope) and not registered.scoped:
            raise PermissionError("Remote HTTP source does not support scoped credentials")

        def factory(context: HttpAuthContext) -> SourceHttpAuth:
            if (
                context.origin, context.identity, context.resource, context.scope,
            ) != actual[:4]:
                raise PermissionError("Remote HTTP credential context mismatch")
            # Git's in-flight coalescing key omits path even with cache_ttl=0.
            source = (
                GitCredentialSource(cache_ttl=0)
                if reference == "builtin/git-credential" else registered.source
            )
            return SourceHttpAuth(
                source, context, target.endpoint,
                scheme=target.auth.scheme, scoped=registered.scoped,
            )

        return factory
