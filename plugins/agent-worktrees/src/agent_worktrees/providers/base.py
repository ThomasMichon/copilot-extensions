"""Pull-request provider plugins -- the interface and shared helpers.

A *provider* owns one job: "create the PR on the hosting service and return
its ``{url, number}``".  Transport is the provider's own CLI (``gh`` for
GitHub, ``az`` for Azure DevOps) or ``curl`` against the REST API (Gitea has
no installed CLI) -- deliberately **no Python HTTP dependency** is added to
the plugin.  The provider is selected per-repo by the existing ``provider``
config value (``gitea`` / ``github`` / ``azure-devops``).

Credentials resolve, in order: ``pr.token_command`` (a shell command that
prints a token -- how the facility points at its vault), then ``pr.token_env``
(an env-var name); GitHub additionally falls back to ``gh`` auth.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class ProviderError(RuntimeError):
    """A provider failed to create or query a pull request."""


@dataclass
class PRScope:
    """Inputs for opening a pull request (built from create_pr's push step)."""

    repo: str                       # target "owner/name"
    head: str                       # the pushed feature branch
    base: str                       # the base (default) branch
    title: str
    body: str = ""
    api_base: str = ""              # provider endpoint (self-hosted gitea / ADO org)
    labels: tuple[str, ...] = ()


@dataclass
class PullResult:
    """The created/queried pull request."""

    url: str = ""
    number: int | None = None
    state: str = "open"
    merged: bool = False
    """True when the PR has been merged (its content is on the base branch).

    Distinct from ``state``: a squash-merged PR reports ``state="closed"`` on
    some providers, so ``merged`` is the authoritative "did the work land"
    signal for prune-safety reconciliation.
    """
    label_error: str = ""
    """Non-empty when the PR opened but one or more configured labels could not
    be applied (lookup/attach failure, or a label absent from the repo).

    The PR creation itself still succeeded -- label trouble is non-fatal -- but
    this is surfaced (as ``pr_label_error`` on create_pr's result) instead of
    being silently swallowed, so a dropped ``auto-merge`` / ``source:<machine>``
    label is visible rather than mysterious.
    """


@runtime_checkable
class PRProvider(Protocol):
    """Protocol every PR provider implements."""

    name: str

    def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
        """Open a PR for ``scope`` and return its url/number."""
        ...

    def get_pull(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> PullResult:
        """Look up an existing PR by number (best-effort; may be unsupported)."""
        ...

    def remove_label(
        self, repo: str, number: int, label: str, *, api_base: str = "",
        token: str | None = None,
    ) -> str:
        """Remove ``label`` from an existing PR; return "" on success."""
        ...


def resolve_token(prcfg) -> str | None:
    """Resolve a provider token from config.

    Order: ``token_command`` (shell, stdout = token) > ``token_env`` (env-var
    name).  Returns None when neither is configured or both yield nothing --
    providers that can fall back to their own CLI auth (e.g. ``gh``) treat
    None as "use the CLI's ambient auth".
    """
    cmd = (getattr(prcfg, "token_command", "") or "").strip()
    if cmd:
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            tok = r.stdout.strip()
            if tok:
                return tok
    env = (getattr(prcfg, "token_env", "") or "").strip()
    if env:
        return os.environ.get(env) or None
    return None


def run_cli(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """Run a provider CLI, returning the completed process (never raises).

    Centralized so providers share one subprocess shape and tests can patch a
    single seam.  The caller inspects ``returncode``/``stdout``/``stderr``.
    """
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        input=input_text,
        env=full_env,
        timeout=timeout,
        check=False,
    )


# Registry -- name -> provider class.  Imported lazily so a missing provider
# module never breaks unrelated commands.
_PROVIDERS = {
    "gitea": ("agent_worktrees.providers.gitea", "GiteaProvider"),
    "github": ("agent_worktrees.providers.github", "GitHubProvider"),
    "azure-devops": ("agent_worktrees.providers.azure_devops", "AzureDevOpsProvider"),
}


def get_provider(name: str) -> PRProvider:
    """Return a provider instance for ``name`` (raises ProviderError if unknown)."""
    import importlib

    entry = _PROVIDERS.get(name)
    if entry is None:
        known = ", ".join(sorted(_PROVIDERS))
        raise ProviderError(
            f"Unknown PR provider '{name}'. Known providers: {known}."
        )
    module_name, cls_name = entry
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:  # pragma: no cover - defensive
        raise ProviderError(f"Provider '{name}' is not available: {e}") from e
    return getattr(module, cls_name)()


def scope_from_create_result(
    result: dict,
    *,
    title: str,
    body: str,
    prcfg,
    machine: str = "",
) -> PRScope:
    """Build a :class:`PRScope` from create_pr's result dict + config.

    ``labels`` are templated with ``{machine}`` so a config entry like
    ``source:{machine}`` becomes ``source:lambda-core``.
    """
    labels = tuple(
        lbl.replace("{machine}", machine) for lbl in (getattr(prcfg, "labels", ()) or ())
    )
    return PRScope(
        repo=str(result.get("repo", "")),
        head=str(result.get("branch", "")),
        base=str(result.get("default_branch", "")),
        title=title,
        body=body,
        api_base=getattr(prcfg, "api_base", "") or "",
        labels=labels,
    )


__all__ = [
    "PRProvider",
    "PRScope",
    "ProviderError",
    "PullResult",
    "get_provider",
    "resolve_token",
    "run_cli",
    "scope_from_create_result",
]
