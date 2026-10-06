"""Gitea connection config for repository-issue-loops and reviewer PR
adapters.

Unlike GitHub (``gh``, ambient auth) and Azure DevOps (``az``, ambient
auth), Gitea has no installed CLI and is typically self-hosted, so a
declaration must name which instance to talk to and how to authenticate --
there is no "ambient" default. Kept in its own module (mirroring
``ado_discovery_scope.py``'s extraction) purely to stay under
``repository_issue_loops.py``'s module-size cap.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from .registrar import RegistrarError

GITEA_CONNECTION_KEYS = frozenset({"api_base", "token_env"})

#: Default environment variable a ``GiteaProvider``/``GiteaPRAdapter``
#: reads its API token from when ``forge.token_env`` is not given.
DEFAULT_GITEA_TOKEN_ENV = "GITEA_TOKEN"


def normalize_gitea_api_base(value: str, *, field: str) -> str:
    """Validate and normalize a Gitea instance base URL.

    Accepts an absolute ``http``/``https`` URL with a real hostname,
    preserving any path component so a reverse-proxied, path-hosted
    instance (e.g. ``https://host.example.com/gitea``) still validates.
    Rejects anything that is merely non-empty text but not a usable
    authority -- a bare word, a non-HTTP(S) scheme, embedded
    credentials, or a query/fragment -- all of which would otherwise
    "validate" successfully and then either fail on every real request
    or silently target an unintended authority (e.g. credentials meant
    for one host leaking into the URL of another).
    """
    stripped = value.strip().rstrip("/")
    if not stripped:
        raise ValueError(f"{field}: expected a non-empty string")
    parts = urlsplit(stripped)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(
            f"{field}: expected an absolute http(s) URL with a hostname "
            f"(got {value!r})"
        )
    if parts.username or parts.password:
        raise ValueError(
            f"{field}: must not embed credentials in the URL (got {value!r})"
        )
    if parts.query or parts.fragment:
        raise ValueError(
            f"{field}: must not include a query string or fragment "
            f"(got {value!r})"
        )
    return stripped


def validate_gitea_connection(
    forge: Mapping[str, Any], *, provider: Any
) -> dict[str, Any] | None:
    """Validate ``forge.api_base``/``forge.token_env`` -- gitea-only.

    ``api_base`` (the instance base URL, e.g. ``https://gitea.example.com``)
    is required for ``provider == "gitea"``; raising elsewhere keeps a
    declaration from silently naming connection details a non-gitea
    provider would never read. Returns ``None`` for every other provider.
    """
    api_base = forge.get("api_base")
    token_env = forge.get("token_env")
    if provider != "gitea":
        if api_base is not None or token_env is not None:
            raise RegistrarError(
                "repository-issue-loop forge.api_base/token_env: only "
                "supported for forge.provider 'gitea'"
            )
        return None
    if not isinstance(api_base, str) or not api_base.strip():
        raise RegistrarError(
            "repository-issue-loop forge.api_base: expected a non-empty "
            "string (the Gitea instance base URL) when forge.provider is "
            "'gitea'"
        )
    # Normalize BEFORE validating the final shape: a value that only
    # *looks* non-empty before trimming (surrounding whitespace, or a bare
    # "/" that rstrip("/") would otherwise collapse to "") must never slip
    # through as accepted and then fail forever on the adapter's first
    # real request.
    try:
        normalized_base = normalize_gitea_api_base(
            api_base, field="repository-issue-loop forge.api_base"
        )
    except ValueError as exc:
        raise RegistrarError(str(exc)) from exc
    if token_env is None:
        token_env = DEFAULT_GITEA_TOKEN_ENV
    elif not isinstance(token_env, str) or not token_env.strip():
        raise RegistrarError(
            "repository-issue-loop forge.token_env: expected a non-empty "
            "string (an environment variable name)"
        )
    else:
        token_env = token_env.strip()
    return {"api_base": normalized_base, "token_env": token_env}
