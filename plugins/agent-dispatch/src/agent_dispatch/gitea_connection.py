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

from .registrar import RegistrarError

GITEA_CONNECTION_KEYS = frozenset({"api_base", "token_env"})

#: Default environment variable a ``GiteaProvider``/``GiteaPRAdapter``
#: reads its API token from when ``forge.token_env`` is not given.
DEFAULT_GITEA_TOKEN_ENV = "GITEA_TOKEN"


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
    if token_env is None:
        token_env = DEFAULT_GITEA_TOKEN_ENV
    elif not isinstance(token_env, str) or not token_env.strip():
        raise RegistrarError(
            "repository-issue-loop forge.token_env: expected a non-empty "
            "string (an environment variable name)"
        )
    return {"api_base": api_base.rstrip("/"), "token_env": token_env}
