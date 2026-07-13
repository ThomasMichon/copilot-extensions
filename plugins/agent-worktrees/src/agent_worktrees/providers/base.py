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
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..pr_contract import PRSnapshot, ThreadsResult


class ProviderError(RuntimeError):
    """A provider failed to create or query a pull request.

    ``transient`` distinguishes a retryable hiccup (network blip, timeout, 5xx,
    429/408, or a curl-level failure) from a **permanent** failure (bad/expired
    token, wrong repo or PR, malformed response).  A polling caller (``pr-watch``)
    retries transient errors until its timeout but lets permanent ones propagate
    so it fails fast instead of hanging the full timeout on a guaranteed failure.
    Defaults to ``False`` (permanent) so existing raise sites are unchanged.
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


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

    def get_snapshot(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> PRSnapshot:
        """Fetch a full :class:`~agent_worktrees.pr_contract.PRSnapshot`.

        The review/mergeability/lifecycle view the ``pr-watch`` and ``pr-status``
        verbs diff and classify.  Distinct from :meth:`get_pull` (which returns
        only url/number/state/merged): a snapshot also carries reviews, the
        mergeable flag, author, head sha, labels, title, and draft.  A provider
        that cannot supply it raises :class:`ProviderError` (the base default),
        so ``pr-watch`` fails fast on an unsupported backend rather than hanging.
        """
        ...

    def add_label(
        self, repo: str, number: int, label: str, *, api_base: str = "",
        token: str | None = None,
    ) -> str:
        """Attach ``label`` to an existing PR; return "" on success.

        A label-apply primitive.  On the label-based providers (gitea/github)
        it is the mechanism behind :meth:`request_auto_complete`; on Azure DevOps
        auto-complete is native and does not go through a label.
        """
        ...

    def request_auto_complete(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None,
        automerge_label: str = "", squash: bool = True,
        delete_source_branch: bool = True, bypass_policy: bool = False,
        bypass_reason: str = "",
    ) -> str:
        """Request that the PR **auto-complete** (merge when the gate is satisfied).

        The first-class "signal merge consent" primitive behind ``pr-merge``.
        *How* a provider honors it is an implementation detail:

        - **gitea / github** apply the configured ``automerge_label`` -- the
          review gate watches the label and merges. (The ``squash`` /
          ``delete_source_branch`` / ``bypass_*`` options do not apply.)
        - **Azure DevOps** sets native auto-complete on the PR (``--auto-complete``
          with the given squash / delete-source-branch / policy-bypass options);
          there is no label.

        Returns "" on success, or a human-readable error string.
        """
        ...

    def get_comment_threads(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> ThreadsResult:
        """Return the PR's review comment threads (first-class across providers).

        ``ThreadsResult.supported`` is False when the provider cannot read them.
        """
        ...

    def resolve_threads(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None,
        thread_ids: tuple[int, ...] = (),
    ) -> str:
        """Mark active threads resolved (all active, or the given ``thread_ids``).

        Returns "" on success, or a human-readable error string.
        """
        ...

    def list_open_pulls(
        self, repo: str, *, api_base: str = "", token: str | None = None
    ) -> tuple[int, ...]:
        """Return the numbers of every open PR on ``repo`` (for the sweep mode)."""
        ...


def _unsupported_snapshot(name: str) -> PRSnapshot:
    raise ProviderError(
        f"Provider '{name}' does not support snapshot reads (pr-watch/pr-status "
        "need a provider with get_snapshot; only 'gitea' implements it today)."
    )


def _unsupported_threads(name: str) -> ThreadsResult:
    from ..pr_contract import ThreadsResult as _TR

    return _TR(
        supported=False,
        error=f"Provider '{name}' does not support comment-thread reads.",
    )


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

    Two Windows-robustness guards keep the "never raises" contract:

    - **PATHEXT resolution.** ``args[0]`` is resolved via ``shutil.which`` so a
      batch shim (``az`` -> ``az.cmd``, ``gh`` -> ``gh.cmd``) is found. Bare
      ``CreateProcess`` only appends ``.exe``, so an unresolved ``az`` would
      otherwise raise ``FileNotFoundError`` (WinError 2).
    - **Spawn failures become results, not exceptions.** A missing executable /
      spawn error is surfaced as ``returncode=127`` so it never aborts an
      unrelated command (e.g. ``create-pr``'s git work that already succeeded);
      the caller turns the non-zero result into a ``ProviderError`` it handles.
    """
    full_env = {**os.environ, **(env or {})}
    exe = shutil.which(args[0], path=full_env.get("PATH")) or args[0]
    resolved = [exe, *args[1:]]
    try:
        return subprocess.run(
            resolved,
            capture_output=True,
            text=True,
            input=input_text,
            env=full_env,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        return subprocess.CompletedProcess(
            args=resolved, returncode=127, stdout="", stderr=str(exc),
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
