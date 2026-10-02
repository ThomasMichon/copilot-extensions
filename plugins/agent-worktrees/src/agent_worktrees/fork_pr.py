"""``pr.fork``'s confirmation gate + durable per-(repo, account) consent
registry for ``create_pr`` (split out of ``pr_ops.py`` to respect its
shrink-only module-size baseline, mirroring ``record_cache.py`` /
``process_table_cache.py``'s own splits out of ``tracking.py`` /
``reclaim.py``).

Supersedes the simpler, repo-only-keyed ``fork_consent.yaml`` registry from
ThomasMichon/copilot-extensions#4824 with a (repo, account)-scoped one: a
confirmation recorded under one identity must not silently authorize a
fork/push under a DIFFERENT identity the account mapping (or ambient ``gh``
auth) later resolves to. This is a one-time, deliberate reset of the durable
state -- confirming again after upgrading is expected, not a regression.

``create_pr`` calls :func:`resolve_fork_publish` once, right after it has a
resolved ``default_pr_repo`` and ``prcfg`` -- see that function's docstring
for the full contract.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import registry_paths


# ---------------------------------------------------------------------------
# Durable (repo, account) fork-confirmation registry (forks.yaml)
# ---------------------------------------------------------------------------


def _quote(value: str) -> str:
    """Render *value* as an always-safe YAML double-quoted scalar -- never
    misparsed as a bool/null/number, and every character YAML's own
    double-quoted-scalar grammar requires escaping (backslash, double
    quote, every C0 control character AND the C1 range U+0080-U+009F --
    not just newline/tab/carriage-return) is escaped, so a value containing
    e.g. an embedded ESC (``\\x1b``) or a C1 control (``\\x9b``) can never
    corrupt the hand-written file or make it unparsable. An unparsable file
    would make ``read_registry`` return an EMPTY catalog, silently losing
    every previously stored confirmation on the very next write.
    """
    out = []
    for ch in value:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
            out.append(f"\\x{code:02x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


_LOCK_ACQUIRE_TIMEOUT_S = 10.0
_LOCK_RETRY_INTERVAL_S = 0.1

if sys.platform == "win32":
    import msvcrt

    def _lock_file(fh) -> None:
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        deadline = time.time() + _LOCK_ACQUIRE_TIMEOUT_S
        while True:
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.time() >= deadline:
                    raise
                time.sleep(_LOCK_RETRY_INTERVAL_S)

    def _unlock_file(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_file(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _unlock_file(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def _locked_registry_file():
    """Interprocess lock guarding read-modify-write access to forks.yaml --
    without it, two concurrent create_pr calls (e.g. two parallel worktrees)
    could each add their own entry and the second writer's save would
    silently drop the first writer's just-added one."""
    path = _forks_yaml_path()
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as fh:
        _lock_file(fh)
        try:
            yield
        finally:
            _unlock_file(fh)


@dataclass
class ForkEntry:
    """A single confirmed fork-publish target in the catalog.

    ``owner`` is the login used to build the PR head (``<owner>:<branch>``)
    -- when ``pr.fork.owner`` overrides it, this is the OVERRIDE value, not
    necessarily who actually authenticated. ``real_owner`` is the actual
    authenticated identity ``provider.resolve_fork_owner``/``ensure_fork``
    returned BEFORE any override was applied -- equal to ``owner`` whenever
    no override is configured. Tracked separately so the live-owner
    pre-check can validate the identity that actually forks/pushes even
    when an override makes the PR-head login static.
    """

    repo: str
    owner: str
    remote: str = "fork"
    account: str = ""
    confirmed_at: str = ""
    notes: str = ""
    real_owner: str = ""


@dataclass
class ForkRegistry:
    """The full forks.yaml content, keyed by (normalized repo, account)."""

    forks: dict[tuple[str, str], ForkEntry] = field(default_factory=dict)


def _forks_yaml_path() -> Path:
    return registry_paths.registry_path("forks.yaml")


def _normalize_repo(repo_slug: str) -> str:
    """Case-fold a repo slug (``Owner/Name``) for lookup/storage keys --
    GitHub owner/repo names are case-insensitive."""
    return repo_slug.strip().casefold()


def _registry_key(repo_slug: str, account: str) -> tuple[str, str]:
    """The composite (repo, account) key entries are stored/looked up
    under. A repo can legitimately have more than one confirmed account
    over time (an operator switching which identity publishes its PRs) --
    keying by repo alone would let confirming account B silently overwrite
    account A's still-valid approval."""
    return _normalize_repo(repo_slug), (account or "").casefold()


def read_registry() -> ForkRegistry:
    """Load forks.yaml, returning an empty registry if missing/invalid."""
    path = _forks_yaml_path()
    if not path.exists():
        return ForkRegistry()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ForkRegistry()
        raw = data.get("forks", {})
        forks: dict[tuple[str, str], ForkEntry] = {}
        if isinstance(raw, dict):
            for repo, accounts in raw.items():
                if not isinstance(accounts, dict):
                    continue
                for account, entry in accounts.items():
                    if not isinstance(entry, dict):
                        continue
                    owner = str(entry.get("owner", "") or "")
                    if not owner:
                        continue
                    account_str = str(account)
                    forks[_registry_key(str(repo), account_str)] = ForkEntry(
                        repo=str(repo),
                        owner=owner,
                        remote=str(entry.get("remote", "fork") or "fork"),
                        account=account_str,
                        confirmed_at=str(entry.get("confirmed_at", "") or ""),
                        notes=str(entry.get("notes", "") or ""),
                        # Absent in an older (pre-override-tracking) or
                        # hand-edited entry -- falls back to `owner` itself,
                        # matching the no-override case where they're equal.
                        real_owner=str(entry.get("real_owner", "") or "") or owner,
                    )
        return ForkRegistry(forks=forks)
    except Exception:
        return ForkRegistry()


def write_registry(registry: ForkRegistry) -> None:
    """Write forks.yaml with hand-formatted YAML, nested as
    ``forks: { <repo>: { <account>: {...} } }`` so a repo confirmed under
    multiple accounts over time keeps one entry per account."""
    path = _forks_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# forks.yaml -- durable catalog of confirmed fork-based PR publish",
        "# targets, keyed by repo AND resolved account. Once a (repo,",
        "# account) pair is listed here, create-pr's pr.fork confirmation",
        "# gate is skipped for every future call under that SAME resolved",
        "# account -- see fork_pr.py and the 'forks' command's --help.",
        "",
    ]
    by_repo: dict[str, dict[str, ForkEntry]] = {}
    for (repo_key, account_key), e in registry.forks.items():
        by_repo.setdefault(repo_key, {})[account_key] = e
    if by_repo:
        lines.append("forks:")
        for repo_key in sorted(by_repo.keys()):
            accounts = by_repo[repo_key]
            any_entry = next(iter(accounts.values()))
            lines.append(f"  {_quote(any_entry.repo)}:")
            for account_key in sorted(accounts.keys()):
                e = accounts[account_key]
                lines.append(f"    {_quote(e.account)}:")
                lines.append(f"      owner: {_quote(e.owner)}")
                if e.remote and e.remote != "fork":
                    lines.append(f"      remote: {_quote(e.remote)}")
                if e.confirmed_at:
                    lines.append(f"      confirmed_at: {_quote(e.confirmed_at)}")
                if e.notes:
                    lines.append(f"      notes: {_quote(e.notes)}")
                if e.real_owner and e.real_owner != e.owner:
                    lines.append(f"      real_owner: {_quote(e.real_owner)}")
    # Same-directory temp file + os.replace so a lock-FREE reader
    # (is_confirmed/find_fork/list_forks) always observes either the
    # complete old file or complete new one -- never a partial write.
    content = "\n".join(lines) + "\n"
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def list_forks() -> list[ForkEntry]:
    """Return all catalogued fork entries, sorted by repo slug then account."""
    registry = read_registry()
    return sorted(
        registry.forks.values(), key=lambda e: (e.repo.casefold(), e.account.casefold()),
    )


def find_forks_for_repo(repo_slug: str) -> list[ForkEntry]:
    """All confirmed entries for ``repo_slug`` (one per distinct account)."""
    if not repo_slug:
        return []
    registry = read_registry()
    norm = _normalize_repo(repo_slug)
    return sorted(
        (e for (r, _a), e in registry.forks.items() if r == norm),
        key=lambda e: e.account.casefold(),
    )


def find_fork(repo_slug: str, account: str = "") -> ForkEntry | None:
    """The catalog entry for ``repo_slug``+``account``, or None if unconfirmed."""
    if not repo_slug:
        return None
    registry = read_registry()
    return registry.forks.get(_registry_key(repo_slug, account))


def is_confirmed(repo_slug: str, *, account: str = "") -> bool:
    """Whether ``repo_slug``'s fork-publish target is confirmed for
    ``account`` -- a confirmation recorded under a different account no
    longer counts; callers must treat a truly unresolvable ``""`` account
    as unconfirmable, never look it up as a wildcard."""
    return find_fork(repo_slug, account) is not None


def record_confirmation(
    repo_slug: str,
    owner: str,
    *,
    remote: str = "fork",
    account: str = "",
    notes: str | None = None,
    real_owner: str = "",
) -> ForkEntry:
    """Record (or refresh) a confirmed fork for ``repo_slug``+``account``.

    Idempotent for the SAME repo+account; a *different* account gets its
    OWN entry alongside any existing one for the same repo. Locked (see
    :func:`_locked_registry_file`): safe against a concurrent
    ``create_pr``/``forks set`` call for a different repo clobbering this
    write via an unsynchronized read-modify-write.
    """
    with _locked_registry_file():
        registry = read_registry()
        key = _registry_key(repo_slug, account)
        existing = registry.forks.get(key)
        entry = ForkEntry(
            repo=repo_slug,
            owner=owner,
            remote=remote,
            account=account or "",
            confirmed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            notes=notes if notes is not None else (existing.notes if existing else ""),
            real_owner=real_owner or owner,
        )
        registry.forks[key] = entry
        write_registry(registry)
        return entry


def remove_fork(repo_slug: str, account: str | None = None) -> bool:
    """Remove confirmed-fork entry/entries for ``repo_slug``. With
    ``account`` omitted, removes EVERY account's entry for this repo."""
    with _locked_registry_file():
        registry = read_registry()
        if account is not None:
            keys = [_registry_key(repo_slug, account)]
        else:
            norm = _normalize_repo(repo_slug)
            keys = [k for k in registry.forks if k[0] == norm]
        removed = False
        for key in keys:
            if key in registry.forks:
                del registry.forks[key]
                removed = True
        if removed:
            write_registry(registry)
        return removed


# ---------------------------------------------------------------------------
# Identity / credential resolution
# ---------------------------------------------------------------------------


def _token_scope(token: str) -> str:
    """The durable confirmation scope for an opaque auth token's own value.
    Shared by :func:`_resolve_fork_credential` and ``forks_cli set
    --token-stdin`` so a pre-seeded entry matches what create_pr computes."""
    import hashlib
    return "token:" + hashlib.sha256(token.encode()).hexdigest()[:16]


def _resolve_fork_credential(repo_slug: str, prcfg) -> tuple[str | None, str]:
    """Resolve the (token, scope) pair for this repo's fork operations ONCE.

    Both the confirmation-gate scope and the token actually handed to
    ``provider.ensure_fork`` come from this single resolution (mirroring
    ``providers.account_token_for_slug``'s own priority: an explicit
    ``pr.token_command``/``token_env`` binding first, else the repo's
    resolved account mapping only when a token can actually be minted for
    it, else ambient ``gh`` auth).

    ``scope`` is ``""`` only when neither a configured token nor a
    mapped/ambient account can be resolved at all -- an opaque custom token
    still gets a non-empty hashed scope via :func:`_token_scope`. Callers
    must treat an empty scope as **fail-closed**.
    """
    if getattr(prcfg, "provider", "") != "github":
        return None, ""
    from .providers.base import resolve_token

    token = resolve_token(prcfg)
    if token:
        return token, _token_scope(token)

    from . import git_ops, repos

    account = repos.account_for_github_slug(repo_slug) or ""
    active = git_ops.active_gh_account() or ""
    if not account or (active and active.casefold() == account.casefold()):
        return None, active
    minted = git_ops.gh_token_for_account(account)
    return (minted, account) if minted else (None, active)


def _resolve_live_fork_owner(prcfg, token: str | None) -> str | None:
    """Non-mutating pre-check of the real fork-owner login a publish would
    resolve to, WITHOUT creating/verifying anything. Lets the confirmation
    gate catch a stale/typo'd stored owner BEFORE
    :func:`_ensure_fork_and_remote`'s mutating POST/remote-repoint can run.

    Returns ``None`` ("couldn't check") for an unsupported provider or a
    failed resolution; the caller then proceeds to the normal, mutating
    path, which will surface the same auth/provider failure there instead.
    """
    if getattr(prcfg, "provider", "") != "github":
        return None
    from . import providers
    try:
        provider = providers.get_provider(prcfg.provider)
        return provider.resolve_fork_owner(
            api_base=getattr(prcfg, "api_base", "") or "", token=token,
        )
    except (providers.ProviderError, OSError):
        return None


def _non_default_authority(prcfg) -> str:
    """A non-default GitHub authority (host) that ANY part of the fork-PR
    flow could actually run against -- the resolved ``authority_endpoint``
    (the same ``pr.api_base``-then-``GH_HOST``-then-``github.com``
    precedence every other provider call uses) OR a differing ambient
    ``GH_HOST`` on its own -- or ``""`` only when BOTH are the default
    github.com.

    Rejecting on the resolved authority alone is not enough: an explicit
    ``pr.api_base=github.com`` overriding a non-default ambient ``GH_HOST``
    makes fork creation target github.com, but GitHub PR creation itself
    (``gh pr create --repo <slug>``, no ``--hostname`` override) still
    reads ambient ``GH_HOST`` and would target the Enterprise host instead
    -- leaving the branch pushed to one host while the PR opens against
    (or fails against) an unrelated same-named repo on another. Until that
    full path uniformly honors ``api_base``, a non-default ambient
    ``GH_HOST`` is rejected even when ``api_base`` overrides it back to the
    default, since this registry is keyed by repo+account only, not by
    authority, and the same ``owner/repo`` slug could otherwise identify
    two unrelated repositories across that boundary.
    """
    if getattr(prcfg, "provider", "") != "github":
        return ""
    import os
    from . import providers
    try:
        provider = providers.get_provider(prcfg.provider)
        resolved = provider.authority_endpoint(getattr(prcfg, "api_base", "") or "")
    except (providers.ProviderError, OSError):
        resolved = ""
    ambient = (os.environ.get("GH_HOST") or "").strip().lower()
    for host in (resolved, ambient):
        if host and host != "github.com":
            return host
    return ""


# ---------------------------------------------------------------------------
# Fork/remote bootstrap + the confirmation gate itself
# ---------------------------------------------------------------------------


def _ensure_fork_and_remote(
    worktree_path: str, repo_slug: str, prcfg, *, token: str | None,
) -> dict:
    """Ensure the caller's fork of ``repo_slug`` exists and a local git
    remote (``prcfg.fork.remote``) points at it.

    Returns ``{"owner": <pr-head login>, "real_owner": <actual authenticated
    login>}`` on success, or ``{"error": <message>}`` on any failure (never
    raises) -- GitHub-only. An explicit ``prcfg.fork.owner`` overrides
    ``owner`` (used to build the PR head) but NEVER ``real_owner`` -- the
    actual identity the provider authenticated as and forked/pushed under,
    needed so a later call can validate that identity independently of the
    override (see :func:`resolve_fork_publish`). ``token`` is the SAME
    value :func:`_resolve_fork_credential` resolved for the confirmation
    scope.
    """
    if prcfg.provider != "github":
        return {"error": (
            f"pr.fork is only supported for provider 'github' today "
            f"(this repo is configured for provider {prcfg.provider!r})."
        )}
    non_default_authority = _non_default_authority(prcfg)
    if non_default_authority:
        return {"error": (
            f"pr.fork does not support a non-default GitHub authority "
            f"('{non_default_authority}') today -- its durable confirmation "
            f"registry is not scoped by authority. Use the default "
            f"github.com (no pr.api_base override, no non-default GH_HOST) "
            f"to use pr.fork for this repo."
        )}
    from . import git_ops, providers
    api_base = getattr(prcfg, "api_base", "") or ""
    try:
        provider = providers.get_provider(prcfg.provider)
        fork = provider.ensure_fork(repo_slug, api_base=api_base, token=token)
    except (providers.ProviderError, OSError) as exc:
        return {"error": f"Could not create/verify a fork of '{repo_slug}': {exc}"}
    if fork is None:
        return {"error": (
            f"Could not create/verify a fork of '{repo_slug}' (no 'gh' auth, "
            f"an API error, or an unsupported provider)."
        )}
    real_owner, clone_url = fork
    owner = prcfg.fork.owner or real_owner
    if not git_ops.ensure_remote(prcfg.fork.remote, clone_url, cwd=worktree_path):
        return {"error": (
            f"Could not point local git remote '{prcfg.fork.remote}' at "
            f"'{clone_url}'."
        )}
    return {"owner": owner, "real_owner": real_owner}


def resolve_fork_publish(
    worktree_path: str, default_pr_repo: str, prcfg, *, confirm_fork: bool,
) -> dict:
    """Resolve ``pr.fork``'s confirmation gate and, once cleared, the actual
    fork/remote bootstrap for one ``create_pr`` call.

    Returns exactly one of:

    - ``{"needs_confirmation": "fork_setup", "repo", "fork_remote", "message"}``
      -- nothing was mutated; relay ``message`` to the human and re-run with
      ``confirm_fork=True`` once they agree.
    - ``{"error": "..."}`` -- a hard failure; nothing further was mutated
      beyond what the error message itself describes.
    - ``{"publish_remote", "fork_owner", "warning": <optional str>}`` on
      success -- the fork/remote are ready; ``warning`` is set only when the
      fork succeeded but persisting the confirmation itself failed.
    """
    # This registry is not scoped by GitHub authority (host) -- the same
    # owner/repo slug can identify unrelated repositories on github.com vs.
    # a GitHub Enterprise host (via an explicit pr.api_base OR ambient
    # GH_HOST), and reusing a confirmation across that boundary would
    # silently authorize a fork/push against a DIFFERENT real repository.
    # This check runs BEFORE any registry lookup, so a confirmation
    # recorded under github.com can never be silently reused once the
    # effective authority later points elsewhere.
    non_default_authority = _non_default_authority(prcfg)
    if non_default_authority:
        return {"error": (
            f"pr.fork does not support a non-default GitHub authority "
            f"('{non_default_authority}') today -- its durable confirmation "
            f"registry is not scoped by authority. Use the default "
            f"github.com (no pr.api_base override, no non-default GH_HOST) "
            f"to use pr.fork for this repo."
        )}

    # Resolve the credential ONCE: the same (token, scope) pair both gates
    # the confirmation decision and authenticates the actual fork operation
    # below -- see _resolve_fork_credential's docstring.
    fork_token, effective_account = _resolve_fork_credential(default_pr_repo, prcfg)
    # Fail closed on an unresolvable identity: never trust (or later
    # persist) a confirmation under an empty scope.
    confirmed_entry = (
        find_fork(default_pr_repo, effective_account) if effective_account else None
    )
    # An explicit pr.fork.owner override deterministically decides the
    # owner login used for the PR head (the actual fork/remote clone_url
    # still comes from the authenticated provider) -- if configured and it
    # doesn't match what was actually confirmed, this is a DIFFERENT
    # approval; re-ask rather than silently publishing there. The approved
    # LOCAL REMOTE NAME is likewise part of what was approved: if
    # pr.fork.remote later changes (including to an existing remote such
    # as 'origin'), reusing the old approval would repoint a DIFFERENT
    # remote than the one actually approved.
    already_confirmed = confirmed_entry is not None and (
        not prcfg.fork.owner or prcfg.fork.owner == confirmed_entry.owner
    ) and prcfg.fork.remote == confirmed_entry.remote
    # The live-owner pre-check validates the ACTUAL AUTHENTICATED identity
    # (real_owner) -- it must run regardless of whether pr.fork.owner is
    # configured. The override only changes which login names the PR head;
    # it does NOT change which account actually authenticates, forks, and
    # pushes. Skipping this check whenever an override is set would let a
    # silent account switch fork/push to an unapproved identity while the
    # PR head still displays the originally-approved (overridden) owner.
    # Validate it NON-MUTATINGLY, before _ensure_fork_and_remote's mutating
    # POST/remote-repoint can run. An explicit confirm_fork=True this call
    # is itself a fresh, live approval and skips this pre-check. A
    # FAILED/inconclusive lookup must ALSO fail closed.
    if already_confirmed and not confirm_fork:
        live_real_owner = _resolve_live_fork_owner(prcfg, fork_token)
        expected_real_owner = confirmed_entry.real_owner or confirmed_entry.owner
        if live_real_owner != expected_real_owner:
            return {
                "needs_confirmation": "fork_setup",
                "repo": default_pr_repo,
                "fork_remote": prcfg.fork.remote,
                "message": (
                    f"Could not verify the previously confirmed fork "
                    f"identity for '{default_pr_repo}' ('{expected_real_owner}') "
                    f"still matches the actual authenticated identity "
                    f"({live_real_owner!r}). Ask the user to confirm "
                    f"publishing there, then re-run create-pr with "
                    f"--confirm-fork (or confirm_fork=True)."
                ),
            }
    if not confirm_fork and not already_confirmed:
        return {
            "needs_confirmation": "fork_setup",
            "repo": default_pr_repo,
            "fork_remote": prcfg.fork.remote,
            "message": (
                f"This repo's resolved PR flow publishes through a personal fork of "
                f"'{default_pr_repo}' rather than a direct push. Ask the user to confirm "
                f"forking it and pushing there, then re-run create-pr with --confirm-fork "
                f"(or confirm_fork=True) -- only needed once per repo+login (or pre-seed "
                f"via 'forks set')."
            ),
        }
    fork_setup = _ensure_fork_and_remote(
        worktree_path, default_pr_repo, prcfg, token=fork_token,
    )
    if fork_setup.get("error"):
        return {"error": fork_setup["error"]}
    fork_owner = fork_setup["owner"]
    real_owner = fork_setup["real_owner"]
    # The non-mutating pre-check above and this mutating bootstrap are two
    # SEPARATE calls -- with ambient `gh` auth (token=None), a concurrent
    # identity switch between them (another process re-authenticating) can
    # make them resolve to two DIFFERENT real identities, even though the
    # pre-check itself passed moments earlier. Treat that divergence as
    # itself requiring a fresh, EXPLICIT confirm_fork=True this call -- a
    # silent skip (already_confirmed, no confirm_fork) must never let such
    # a race silently re-point the stored approval at an identity nobody
    # actually confirmed this call.
    identity_changed = confirmed_entry is not None and (
        confirmed_entry.owner != fork_owner
        or (confirmed_entry.real_owner or confirmed_entry.owner) != real_owner
    )
    if identity_changed and already_confirmed and not confirm_fork:
        return {"error": (
            f"The fork identity for '{default_pr_repo}' resolved to "
            f"'{real_owner}' during setup, which no longer matches the "
            f"previously confirmed identity "
            f"('{confirmed_entry.real_owner or confirmed_entry.owner}') -- "
            f"likely a concurrent identity change. The fork/remote may "
            f"already be set up for '{real_owner}'; re-run create-pr with "
            f"--confirm-fork (or confirm_fork=True) to approve recording "
            f"that as the new confirmed identity."
        )}
    result = {"publish_remote": prcfg.fork.remote, "fork_owner": fork_owner}
    # Re-persist whenever this is the first confirmation OR an EXPLICIT
    # confirm_fork=True call's result differs from what was stored (the
    # self-heal path for a stale entry the pre-check above caught on a
    # PRIOR call) -- never on a silent skip, which the check above already
    # guards against reaching here with identity_changed still True.
    if (not already_confirmed or identity_changed) and effective_account:
        try:
            record_confirmation(
                default_pr_repo, fork_owner,
                remote=prcfg.fork.remote, account=effective_account,
                real_owner=real_owner,
            )
        except OSError as exc:
            result["warning"] = f"Could not persist fork confirmation: {exc}"
    return result
