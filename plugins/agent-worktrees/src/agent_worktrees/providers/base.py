"""Pull-request provider plugins -- the interface and shared helpers.

A *provider* owns one job: "create the PR on the hosting service and return
its ``{url, number}``".  Transport is the provider's own CLI (``gh`` for
GitHub, ``az`` for Azure DevOps) or ``curl`` against the REST API (Gitea has
no installed CLI) -- deliberately **no Python HTTP dependency** is added to
the plugin.  The provider is selected per-repo by the existing ``provider``
config value (``gitea`` / ``github`` / ``azure-devops``).

Credentials resolve, in order: ``pr.token_command`` (a shell command that
prints a token -- how the multi-machine system points at its vault), then ``pr.token_env``
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
    draft: bool = False             # open as a draft (not-yet-ready-for-review)


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
    head_sha: str = ""
    """The PR head commit SHA (when the provider reports it) -- lets a reconcile
    check whether the head is already contained in the base (a zombie
    open-but-merged PR, #1375/#1703)."""
    base_ref: str = ""
    """The PR base (target) branch ref, e.g. ``master``."""
    observed_at: str = ""
    """Provider-server timestamp captured while observing ``head_sha``."""
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

    def observe_head(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> PullResult:
        """Observe the exact PR head with a timestamp from the provider clock."""
        ...

    def authority_endpoint(self, api_base: str = "") -> str:
        """Canonical provider endpoint that scopes review authority."""
        ...

    def publish_source_marker(
        self,
        repo: str,
        number: int,
        marker: str,
        *,
        api_base: str = "",
        token: str | None = None,
    ) -> str:
        """Publish a managed source-attribution PR comment; "" on success."""
        ...

    def remove_label(
        self, repo: str, number: int, label: str, *, api_base: str = "",
        token: str | None = None,
    ) -> str:
        """Remove ``label`` from an existing PR; return "" on success."""
        ...

    def mark_ready(
        self, repo: str, number: int, *, api_base: str = "",
        token: str | None = None, title: str = "",
        wip_title_prefixes: tuple[str, ...] = (),
    ) -> str:
        """Move a PR out of draft (draft -> ready-for-review); "" on success.

        The un-draft primitive behind ``pr-ready``.  *How* a provider un-drafts
        is an implementation detail:

        - **gitea** has no native draft flag (<= 1.26): a draft is a WIP title
          prefix, so this strips the prefix by editing the title.  ``title`` (the
          current PR title, if the caller already read it) and
          ``wip_title_prefixes`` (the repo binding) let it strip without a
          re-fetch; with ``title`` empty it reads the title itself.
        - **github** has native drafts: this runs ``gh pr ready``.
        - **azure-devops** has no draft concept exposed here (unsupported).

        Returns "" on success, or a human-readable error string -- including when
        the PR is **not** a draft (so ``pr-ready`` errors rather than reporting a
        false success on a no-op).
        """
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

    def merge_pull(
        self, repo: str, number: int, *, squash: bool = True, admin: bool = False,
        api_base: str = "", token: str | None = None,
    ) -> str:
        """Directly merge a PR **now** (the submitter-direct merge primitive).

        The mechanism behind ``pr-merge <#> --now``: a first-class, provider-generic
        "merge this PR" for a **submitter-direct** repo, so an agent never has to
        fall back to a raw provider CLI. Distinct from
        :meth:`request_auto_complete`, which signals *consent* and lets a review
        gate merge later; ``merge_pull`` performs the merge itself.

        - **github** runs ``gh pr merge <n> --squash`` (``--admin`` when ``admin``
          is set for the owner's sanctioned merge past a non-blocking gate).
          Blocking review policy must use ``admin=False``.
          Deliberately does **not** delete the source branch, so ``finalize`` can
          still affirm the merge against the (still-present) head.
        - **gitea / azure-devops** are unsupported today (return a message).

        Returns "" on success, or a human-readable error string. ``--now`` is only
        offered where the repo's flow profile is ``pr-self-merge``; other profiles
        refuse with a reminder before ever calling this.
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

    def enable_auto_merge(
        self, repo: str, number: int, *, squash: bool = True,
        api_base: str = "", token: str | None = None,
    ) -> str:
        """Enable the provider's **native CI-gated auto-merge** on a PR (#225).

        The mechanism behind ``pr-merge --now`` when the repo policy sets
        ``prefer_auto_merge`` (the default): rather than merging immediately,
        request the provider's native auto-merge so the PR merges on its own once
        required checks pass -- letting an agent stop watching attentively.

        - **github** runs ``gh pr merge <n> --squash --auto`` (no ``--admin``: it
          must wait on the checks, not bypass them). The source branch is not
          deleted, so ``finalize`` can still affirm the eventual merge.
        - **gitea / azure-devops** are unsupported here today (they merge via
          their own consent/auto-complete flow); they return a message so the
          caller falls back to a direct merge.

        Returns "" on success (auto-merge is now armed -- the PR is NOT yet
        merged), or a human-readable error string (so the caller falls back to an
        immediate :meth:`merge_pull`).
        """
        ...

    def get_repo_policy(
        self, repo: str, *, default_branch: str = "", api_base: str = "",
        token: str | None = None,
    ):
        """Read a repo's live PR-relevant settings (#225) as a ``RepoPolicy``.

        The adopt-time research primitive: inspect the provider's ACTUAL settings
        (allowed merge methods, native auto-merge availability, delete-branch-on-
        merge, required approving reviews, required status checks) so ``register``
        / ``pr-research`` can prepare the config policy matrix to match reality.

        - **github** reads ``gh api repos/<repo>`` + the default branch's
          protection.
        - **gitea / azure-devops** are unsupported today (return a
          ``RepoPolicy(supported=False)``).

        Never raises: a failed read yields ``RepoPolicy(supported=False, error=...)``.
        """
        ...

    def head_contained_in_base(
        self, repo: str, base: str, head_sha: str, *, api_base: str = "",
        token: str | None = None,
    ) -> bool | None:
        """Is ``head_sha`` already contained in ``base`` (i.e. its content merged)?

        The zombie-PR self-heal probe (#1375/#1703): a PR whose merge *content*
        landed on the default branch but whose PR object was never flipped
        (Gitea merge non-atomic under load / an AI-reviewer squash that didn't close
        the object) lingers ``state=open`` and shows as an open PR in the Picker.
        A head that is 0 commits *ahead* of the base means the base already
        contains it -- the PR is effectively merged and can be reconciled to a
        terminal state.

        - **gitea** compares ``base...head`` and returns True when head is 0
          commits ahead.
        - other providers are unsupported (return ``None``).

        Returns True (contained), False (still ahead), or ``None`` (unknown /
        unsupported / read failed) -- so a caller only self-heals on a definite
        True and otherwise leaves the state untouched.
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


def _unsupported_merge(name: str) -> str:
    """The default ``merge_pull`` result for a provider that cannot self-merge."""
    return (
        f"Provider '{name}' does not support a direct merge (pr-merge --now is "
        "GitHub-only today; gitea/azure-devops merge via their own flow)."
    )


def _unsupported_auto_merge(name: str) -> str:
    """The default ``enable_auto_merge`` result for a provider without native,
    CLI-drivable auto-merge (so the caller falls back to a direct merge)."""
    return (
        f"Provider '{name}' does not support native auto-merge here (GitHub-only "
        "today; gitea/azure-devops use their own consent/auto-complete flow)."
    )


def _unsupported_repo_policy(name: str):
    """The default ``get_repo_policy`` for a provider that can't read settings."""
    from ..pr_contract import RepoPolicy

    return RepoPolicy(
        supported=False,
        error=(f"Provider '{name}' does not support settings reads (adopt-time "
               "research is GitHub-only today)."),
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


def account_token_for_slug(slug: str | None, prcfg) -> str | None:
    """Resolve the provider token for a repo, honoring its repo-scoped account.

    The gh-ops half of the repo-scoped identity layer.  Order:

    1. an explicitly configured ``pr.token_command`` / ``pr.token_env`` (the
       vault/env binding) always wins -- unchanged from :func:`resolve_token`;
    2. else, for the **github** provider only, when the repo's resolved account
       differs from the **active** ``gh`` account, mint that account's token
       (``gh auth token --user <account>`` via ``git_ops.gh_token_for_account``)
       so a cross-account PR authenticates as the owning identity;
    3. else None -- the provider uses its ambient CLI auth exactly as before.

    **Owner == active account: no override.** When the repo's account *is* the
    active ``gh`` account, do NOT mint and inject a ``--user`` token: ``gh``
    already authenticates as that user via its active credential (``gh auth
    token`` / GCM), and the separately-addressed ``--user`` token can be a
    stale/rotated value that returns 401 while the active token is valid. So we
    fall through to None and let the provider use gh's dynamic ambient auth.
    This mirrors the git auth-args path, which skips injection for the same
    reason (git_ops, #900) -- keeping PR auth dynamic rather than pinned to a
    possibly-stale minted token.

    v1 is GitHub-only: non-github providers (and github repos with no
    resolvable account) fall straight through to today's behavior, so this is
    additive and safe.
    """
    tok = resolve_token(prcfg)
    if tok:
        return tok
    if (getattr(prcfg, "provider", "") or "") != "github":
        return None
    from .. import git_ops, repos

    account = repos.account_for_github_slug(slug)
    if not account:
        return None
    # Owner == active gh account -> use ambient auth, not a stale minted token.
    active = git_ops.active_gh_account()
    if active and active.casefold() == account.casefold():
        return None
    return git_ops.gh_token_for_account(account)


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

    Three guards keep the "never raises" contract:

    - **PATHEXT resolution.** ``args[0]`` is resolved via ``shutil.which`` so a
      batch shim (``az`` -> ``az.cmd``, ``gh`` -> ``gh.cmd``) is found. Bare
      ``CreateProcess`` only appends ``.exe``, so an unresolved ``az`` would
      otherwise raise ``FileNotFoundError`` (WinError 2).
    - **Spawn failures become results, not exceptions.** A missing executable /
      spawn error is surfaced as ``returncode=127`` so it never aborts an
      unrelated command (e.g. ``create-pr``'s git work that already succeeded);
      the caller turns the non-zero result into a ``ProviderError`` it handles.
    - **Timeouts become sanitized results.** ``TimeoutExpired`` retains and
      formats its complete argv, which may include provider authentication
      headers. Convert it to ``returncode=124`` at this boundary and never retain
      secret-bearing command metadata in the returned result.
    """
    full_env = {**os.environ, **(env or {})}
    exe = shutil.which(args[0], path=full_env.get("PATH")) or args[0]
    resolved = [exe, *args[1:]]
    sanitized = _sanitize_cli_args(resolved)
    try:
        result = subprocess.run(
            resolved,
            capture_output=True,
            text=True,
            input=input_text,
            env=full_env,
            timeout=timeout,
            check=False,
        )
        return subprocess.CompletedProcess(
            args=sanitized,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=sanitized,
            returncode=124,
            stdout="",
            stderr=f"provider command timed out after {timeout}s",
        )
    except (FileNotFoundError, OSError) as exc:
        return subprocess.CompletedProcess(
            args=sanitized, returncode=127, stdout="", stderr=str(exc),
        )


def _sanitize_cli_args(args: list[str]) -> list[str]:
    """Return argv safe for result metadata, logs, and exception formatting."""
    sanitized: list[str] = []
    redact_next = False
    secret_flags = {"--api-key", "--password", "--token"}
    for arg in args:
        if redact_next:
            sanitized.append("[REDACTED]")
            redact_next = False
            continue

        lower = arg.casefold()
        if lower in secret_flags:
            sanitized.append(arg)
            redact_next = True
        elif lower.startswith("authorization:"):
            sanitized.append("Authorization: [REDACTED]")
        elif lower.startswith("authorization="):
            sanitized.append("authorization=[REDACTED]")
        elif lower.startswith("http.extraheader="):
            sanitized.append("http.extraheader=[REDACTED]")
        elif any(lower.startswith(f"{flag}=") for flag in secret_flags):
            sanitized.append(f"{arg.split('=', 1)[0]}=[REDACTED]")
        else:
            sanitized.append(arg)
    return sanitized


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
    ``source:{machine}`` becomes ``source:anomalous-potato``.
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
    "account_token_for_slug",
    "get_provider",
    "resolve_token",
    "run_cli",
    "scope_from_create_result",
]
