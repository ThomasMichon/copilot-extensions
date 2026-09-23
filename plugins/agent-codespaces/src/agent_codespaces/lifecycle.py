"""CodeSpace lifecycle management -- create, delete, list, status.

Wraps ``gh codespace`` commands with configuration from
``.copilot-extensions/agent-codespaces/config.yaml``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from enum import Enum

from agent_procutil import no_window_flags

from .config import RUNTIME_DIR, CodespacesConfig, RepoConfig
from .worktrees import ContextRefused, validate_context

log = logging.getLogger("agent-codespaces")


class WaitOutcome(str, Enum):
    """Result of waiting for a CodeSpace to become usable."""

    AVAILABLE = "available"
    FAILED = "failed"
    TIMEOUT = "timeout"


# States (from ``gh codespace list``) that mean the CodeSpace will NOT reach
# ``Available`` on its own -- waiting longer is pointless, so a waiter returns
# FAILED immediately instead of burning its whole budget. Everything else
# (Provisioning/Queued/Starting/Awaiting/Rebuilding/Updating/Shutdown/Unknown/...)
# is treated as *pending*: still in progress, keep waiting patiently. This is
# what lets a slow boot never be mistaken for a dead CodeSpace.
_TERMINAL_FAILED_STATES = frozenset({
    "Failed", "Unavailable", "Deleted", "Moved", "Archived",
})
_AVAILABLE_STATE = "Available"
_SHUTDOWN_STATE = "Shutdown"


def classify_state(state: str) -> str:
    """Bucket a raw ``gh`` state into ``available`` | ``failed`` | ``pending``."""
    if state == _AVAILABLE_STATE:
        return "available"
    if state in _TERMINAL_FAILED_STATES:
        return "failed"
    return "pending"


@dataclass
class CodespaceInfo:
    """Summary of a CodeSpace from ``gh codespace list``."""

    name: str
    display_name: str
    repository: str
    branch: str
    state: str
    machine: str
    # The gh account login under which this CodeSpace was discovered. Empty
    # when listed under the ambient active account (no account_map). Per-name
    # ops (stop/delete/ssh) run under this account's token. See gh_account.
    account: str = ""
    # ISO-8601 last-used timestamp (from ``gh``); the freshness signal the pool
    # disposition model uses to age an idle CodeSpace toward "stale".
    last_used_at: str = ""


def _creation_flags() -> int:
    return no_window_flags()


def list_codespaces() -> list[CodespaceInfo]:
    """List CodeSpaces across all candidate gh accounts, merged by name.

    ``gh codespace list`` only returns the *active* account's CodeSpaces, so a
    CodeSpace owned by another account is invisible. To discover + operate them
    (#195/#190) we list under each account in the agent-worktrees
    ``account_map`` and each persisted account binding -- pinning ``gh`` via
    ``GH_TOKEN`` -- plus the ambient account, tagging each entry with the account
    it came from and de-duping by name.

    When no candidate accounts exist this collapses to a single ambient
    ``gh codespace list`` (today's behavior) -- additive and safe.
    """
    from . import gh_account

    validate_context()
    try:
        from . import account_binding

        bound_accounts = account_binding.bound_accounts()
    except Exception:
        bound_accounts = ()
    accounts_seen: list[str] = []
    for login in (*gh_account.mapped_accounts(), *bound_accounts):
        if login and login not in accounts_seen:
            accounts_seen.append(login)
    accounts = tuple(accounts_seen)
    if not accounts:
        return _list_codespaces_under(None)

    merged: dict[str, CodespaceInfo] = {}
    errors: list[str] = []
    # Each candidate account, then the ambient active account (which may own
    # CodeSpaces under an owner that isn't in the map).
    for login in (*accounts, None):
        try:
            for cs in _list_codespaces_under(login):
                merged.setdefault(cs.name, cs)
        except ContextRefused:
            raise
        except RuntimeError as exc:
            errors.append(str(exc))
    if not merged and errors:
        raise RuntimeError("; ".join(dict.fromkeys(errors)))
    return list(merged.values())


def _list_codespaces_under(login: str | None) -> list[CodespaceInfo]:
    """Run ``gh codespace list`` under one account (or ambient when None)."""
    from . import gh_account

    args = [
        "gh", "codespace", "list",
        "--json",
        "name,displayName,repository,gitStatus,state,machineName,lastUsedAt",
        "--limit", "50",
    ]
    env = gh_account.env_for_account(login) if login else None

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=30,
            creationflags=_creation_flags(), env=env,
        )
    except FileNotFoundError:
        raise RuntimeError("gh CLI not found") from None

    if result.returncode != 0:
        who = f" (account {login})" if login else ""
        raise RuntimeError(
            f"gh codespace list failed{who}: {result.stderr.strip()}"
        )

    try:
        entries = json.loads(result.stdout) if result.stdout.strip() else []
    except (ValueError, TypeError):
        # A malformed/unexpected payload for one account must not sink the
        # whole cross-account merge; treat it as "no CodeSpaces here".
        return []
    codespaces = []
    for e in entries:
        git_status = e.get("gitStatus", {})
        codespaces.append(CodespaceInfo(
            name=e.get("name", ""),
            display_name=e.get("displayName", ""),
            repository=e.get("repository", ""),
            branch=git_status.get("ref", "") if isinstance(git_status, dict) else "",
            state=e.get("state", ""),
            machine=e.get("machineName", ""),
            account=login or "",
            last_used_at=e.get("lastUsedAt", ""),
        ))
    return codespaces


def account_for_codespace(name: str) -> str | None:
    """Return the gh account that owns CodeSpace ``name``, or None.

    Resolved first from the persisted account binding, then from the
    cross-account listing so per-name ops (stop/delete/ssh) can pin ``gh`` to the
    owning account. None => use ambient auth. Any failure degrades to ambient
    rather than propagating.
    """
    validate_context()
    try:
        from . import account_binding

        bound = account_binding.bound_account(name)
        if bound:
            return bound
        for cs in list_codespaces():
            if cs.name == name:
                if cs.account:
                    try:
                        account_binding.bind(name, cs.account, cs.repository)
                    except Exception:
                        pass
                return cs.account or None
    except ContextRefused:
        raise
    except Exception:
        return None
    return None


#: The claim-provider registry's own default STATUS callback budget is 15s
#: (``agent_worktrees.claim_providers._CALLBACK_TIMEOUT_SECONDS``), and
#: ``get_codespace_status`` may try this per-account call SERIALLY across
#: several candidate accounts -- a single sub-call therefore needs a much
#: tighter budget than a one-shot operation would, or a hung backend lets
#: the registry kill the whole callback (and possibly outlive it as an
#: orphaned ``gh`` process) before this code ever returns its own
#: controlled 404-vs-error verdict.
_STATUS_LOOKUP_TIMEOUT_SECONDS = 6.0

#: Overall wall-clock budget for the WHOLE serial account loop in
#: :func:`get_codespace_status`, strictly under the registry's own 15s
#: callback timeout (leaving margin for JSON parsing/return overhead).
#: Without this, two mapped accounts plus the ambient fallback could each
#: consume up to ``_STATUS_LOOKUP_TIMEOUT_SECONDS`` (6s x 3 = 18s), already
#: exceeding the registry's budget on its own -- the registry would then
#: kill this callback mid-loop, reporting a live CodeSpace unavailable
#: instead of this function's own controlled verdict.
_STATUS_OVERALL_BUDGET_SECONDS = 12.0


def _get_codespace_status_under(
    name: str, account: str | None, *, timeout: float = _STATUS_LOOKUP_TIMEOUT_SECONDS,
) -> tuple[bool, str | None]:
    """Single-account attempt for :func:`get_codespace_status`. Raises
    :class:`RuntimeError` for any failure that is NOT an unambiguous 404 --
    including an explicit ``HTTP 404`` marker only, never the looser phrase
    "not found" (which a non-404 auth/API error could also happen to
    mention).

    When ``account`` is given, this is a STRICT provider path whose verdict
    feeds a destructive reclaim decision -- it requires a genuine
    account-specific token, never a silent ambient-auth fallback.
    ``gh_account.env_for_account`` returns the environment UNCHANGED
    (ambient) when it cannot mint a token for the named account (its own
    documented, deliberately permissive contract for its other, non-strict
    callers) -- reusing it here would let a failed-to-authenticate named
    candidate silently query (and later reclaim!) under whatever account
    happens to be ambient, misreporting that as a confirmed result for the
    NAMED candidate (claim-provider-pattern effort review finding: "Require
    account-specific authentication for strict lookups"). Minting also
    consumes its own time (up to its internal 10s timeout) that must count
    against THIS candidate's own ``timeout`` budget, not run on top of it,
    or a single candidate could blow well past its allotted share (review
    finding: "...include credential resolution in the end-to-end
    deadline")."""
    from . import gh_account

    env = None
    if account is not None:
        mint_start = time.monotonic()
        token = gh_account.token_for_account(account)
        timeout = max(timeout - (time.monotonic() - mint_start), 0.5)
        if not token:
            raise RuntimeError(
                f"could not mint an authenticated gh token for account {account} "
                "-- refusing to fall back to ambient auth for this strict lookup"
            )
        env = dict(os.environ)
        env["GH_TOKEN"] = token
        env.pop("GITHUB_TOKEN", None)

    args = ["gh", "api", f"/user/codespaces/{name}"]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            creationflags=_creation_flags(), env=env,
        )
    except FileNotFoundError:
        raise RuntimeError("gh CLI not found") from None
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"gh api codespace lookup for {name} timed out after {timeout:.0f}s"
        ) from exc
    if result.returncode != 0:
        combined = f"{result.stdout}\n{result.stderr}".lower()
        if "http 404" in combined:
            return False, None
        raise RuntimeError(
            f"gh api codespace lookup for {name} failed: {result.stderr.strip()}"
        )
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"gh api returned invalid JSON for {name}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"gh api returned an unexpected shape for {name}")
    return True, data.get("state")


def get_codespace_status(name: str, account: str | None = None) -> tuple[bool, str | None]:
    """Strict, targeted single-CodeSpace existence check via ``gh api
    /user/codespaces/<name>``.

    Unlike :func:`list_codespaces` -- a paginated (``--limit 50``),
    best-effort listing across candidate accounts that silently drops rows
    on malformed JSON or a single failed account -- this hits exactly one
    CodeSpace by name and returns an unambiguous verdict: ``(exists,
    state)``. Raises :class:`RuntimeError` for any failure that is NOT an
    unambiguous 404 (auth error, network failure, malformed response), so a
    caller never mistakes a backend outage or incomplete listing for
    confirmed absence.

    When ``account`` is not given explicitly, this does NOT trust
    :func:`account_for_codespace`'s own single best-effort guess (itself
    backed by the same lossy ``--limit 50`` listing) as authoritative --
    a CodeSpace beyond that listing's first page, or owned by an account
    ``account_for_codespace`` failed to resolve, would otherwise be queried
    under the wrong (or ambient) credentials and misreported absent. Instead
    it tries every candidate account (the ``account_map``, each persisted
    binding, then ambient) exactly like :func:`list_codespaces` does,
    stopping at the first confirmed existence. Only when EVERY candidate
    account confirms an unambiguous 404 is the CodeSpace reported absent;
    a real backend error on any candidate raises (never a false negative).

    See :func:`get_codespace_status_with_account` for a variant that also
    reports WHICH account confirmed existence, for a caller that must then
    reuse that same account for a follow-up operation (rather than letting
    it re-resolve independently and possibly disagree).
    """
    exists, state, _account = get_codespace_status_with_account(name, account)
    return exists, state


def get_codespace_status_with_account(
    name: str, account: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Like :func:`get_codespace_status`, but also returns the account that
    confirmed existence (or ``None`` when ``account`` was explicit, or no
    candidate exists).

    A caller that must perform a FOLLOW-UP operation on the same CodeSpace
    (e.g. ``sync_codespace_sessions``/``delete_codespace`` during a
    claim-provider reclaim) should thread this resolved account through
    rather than letting the follow-up re-resolve independently via
    ``account_for_codespace``'s own limited listing/ambient fallback, which
    can disagree for a CodeSpace found only under a non-ambient or
    beyond-first-page account (claim-provider-pattern effort review
    finding: "Preserve the resolved account through CodeSpace
    reclamation").

    When no explicit ``account`` is given, the EXACT per-name binding
    (``account_binding.bound_account(name)``) is tried FIRST, ahead of the
    generic multi-candidate scan -- :func:`account_for_codespace` already
    treats that binding as authoritative for the same reason: two DIFFERENT
    GitHub accounts can each have a CodeSpace with the identical name, and
    the generic scan would otherwise confirm (and a reclaim would then
    delete!) whichever account's same-named CodeSpace it happened to reach
    first, not necessarily the one this name is actually bound to
    (claim-provider-pattern effort review finding: "Resolve exact
    CodeSpace binding before scanning candidate accounts"). ANY outcome of
    that bound lookup -- existing, a genuine 404, OR an ambiguous
    lookup FAILURE -- is final and is propagated immediately, NEVER
    falling through to the generic scan: falling through on ANY of these
    (not just a confirmed 404) would risk that exact same name-collision
    (a DIFFERENT account's same-named CodeSpace getting reported/
    reclaimed -- possibly DELETED -- under this identity, when the bound
    account was never actually verified), contradicting the binding's own
    authority (review findings: "Do not scan other accounts after a bound
    lookup returns 404" and "Binding lookup errors incorrectly fall
    through to other accounts"). The scan is the fallback ONLY when there
    is NO binding at all -- a missing binding must not make an otherwise-
    discoverable CodeSpace misreport absent. Reading the binding STORE
    itself (as opposed to a confirmed absence of a binding for this name)
    can also fail (lock contention) -- that failure propagates too, via
    ``account_binding.bound_account_or_raise`` for the exact-name lookup
    AND ``account_binding.bound_accounts_or_raise`` for the fallback
    scan's own candidate-list setup, rather than either one degrading to
    "no binding"/an empty candidate set (review findings: "Fail closed
    when account binding cannot be read" and "Propagate binding read
    failures instead of scanning accounts"). Likewise, once ANY candidate
    in the fallback scan has produced an ambiguous error, a LATER
    candidate's success is no longer trusted blindly -- it also raises,
    rather than risk selecting (and reclaiming!) a same-named CodeSpace
    under an unverified account while the true owner's lookup remains
    unresolved (review finding: "Reject later candidates after earlier
    lookup errors")."""
    from . import gh_account

    validate_context()
    if account is not None:
        exists, state = _get_codespace_status_under(name, account)
        return exists, state, account

    deadline = time.monotonic() + _STATUS_OVERALL_BUDGET_SECONDS
    errors: list[str] = []

    from . import account_binding

    bound = account_binding.bound_account_or_raise(name)
    if bound:
        remaining = deadline - time.monotonic()
        # ANY outcome under the authoritative binding is final -- see the
        # docstring above for why even an ambiguous lookup FAILURE must
        # propagate rather than silently fall through to the generic scan
        # for this destructive-reclaim-feeding path.
        exists, state = _get_codespace_status_under(
            name, bound, timeout=min(_STATUS_LOOKUP_TIMEOUT_SECONDS, max(remaining, 0.5)),
        )
        return exists, state, (bound if exists else None)

    bound_accounts = account_binding.bound_accounts_or_raise()
    accounts_seen: list[str] = []
    for login in (*gh_account.mapped_accounts(), *bound_accounts):
        if login and login != bound and login not in accounts_seen:
            accounts_seen.append(login)

    for candidate in (*accounts_seen, None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append(
                f"overall status budget ({_STATUS_OVERALL_BUDGET_SECONDS:.0f}s) "
                f"exhausted before checking account {candidate or '(ambient)'}"
            )
            break
        try:
            exists, state = _get_codespace_status_under(
                name, candidate, timeout=min(_STATUS_LOOKUP_TIMEOUT_SECONDS, remaining),
            )
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        if exists:
            # A confirmed success here is no longer trustworthy on its own
            # if an EARLIER candidate errored ambiguously -- the true
            # owner might be the one that errored, and blindly trusting
            # this candidate risks reclaiming a same-named CodeSpace under
            # the WRONG (unverified) account.
            if errors:
                raise RuntimeError("; ".join(dict.fromkeys(errors)))
            return True, state, candidate
    if errors:
        raise RuntimeError("; ".join(dict.fromkeys(errors)))
    return False, None, None


def list_devcontainers(repo: str) -> list[str]:
    """Return the discoverable devcontainer config paths for a repo.

    Queries ``gh api repos/{repo}/codespaces/devcontainers`` -- the same set
    ``gh codespace create`` would otherwise prompt over. Returns an empty list
    on any failure (missing API, auth, network) so callers degrade gracefully
    to "don't pass ``--devcontainer-path``" (today's behavior).
    """
    args = [
        "gh", "api", f"repos/{repo}/codespaces/devcontainers",
        "--jq", ".devcontainers[].path",
    ]
    from . import gh_account
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=30,
            creationflags=_creation_flags(), env=gh_account.env_for_repo(repo),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        log.debug(
            "Could not enumerate devcontainers for %s: %s",
            repo, result.stderr.strip(),
        )
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def resolve_devcontainer_path(
    repo: str,
    config: CodespacesConfig,
    override: str | None = None,
) -> str | None:
    """Pick the ``--devcontainer-path`` to pass ``gh codespace create``, or None.

    ``gh codespace create`` prompts (and hard-fails headless with ``failed to
    prompt: no terminal``) when a repo exposes MORE THAN ONE discoverable
    ``devcontainer.json``. This resolves which config to build from so creation
    stays non-interactive, and is self-healing: a repo (or a newly-added
    devcontainer) never re-breaks headless create without any config change.

    Returns ``None`` -- meaning *don't pass the flag* -- when the repo has 0 or
    1 devcontainer (the common case; no prompt, no risk for other repos) or when
    enumeration is unavailable. When there are multiple, the config to use is
    chosen by precedence (most specific first):

    1. ``override`` -- an explicit ``--devcontainer-path`` from the caller/agent
       (the "do the right thing" escape hatch, e.g. to pick an alternate config).
    2. ``repos.<repo>.devcontainer_path`` -- the operator's per-repo choice.
    3. ``defaults.devcontainer_path`` -- the global fallback, if it is actually
       one of the repo's configs.
    4. The canonical ``.devcontainer/devcontainer.json`` if present.
    5. The first config reported (deterministic last resort).

    The chosen path and how to override it are logged so a headless caller can
    see (and change) what was picked.
    """
    if override:
        log.info("Using devcontainer path (explicit override): %s", override)
        return override

    paths = list_devcontainers(repo)
    if len(paths) <= 1:
        # 0 or 1 config -> gh won't prompt; passing the flag would risk naming a
        # path that doesn't exist for repos whose sole config lives elsewhere.
        return None

    repo_cfg = config.repos.get(repo, RepoConfig())
    candidates = [
        repo_cfg.devcontainer_path,
        config.default_devcontainer_path
        if config.default_devcontainer_path in paths
        else None,
        ".devcontainer/devcontainer.json"
        if ".devcontainer/devcontainer.json" in paths
        else None,
        paths[0],
    ]
    chosen = next(c for c in candidates if c)
    log.info(
        "Repo %s has %d devcontainer configs %s; building from %r "
        "(override with --devcontainer-path or repos.%s.devcontainer_path)",
        repo, len(paths), paths, chosen, repo,
    )
    return chosen


# Substrings in `gh codespace create` stderr that mean it aborted on an
# interactive prompt it could not show in a headless (no-TTY) shell -- the
# org-billing consent gate on org-paid repos (#151). Matched case-insensitively.
_INTERACTIVE_PROMPT_MARKERS = ("failed to prompt", "no terminal")


def _is_interactive_prompt_failure(stderr: str) -> bool:
    """Whether ``gh codespace create`` stderr indicates a headless prompt abort."""
    low = (stderr or "").lower()
    return any(marker in low for marker in _INTERACTIVE_PROMPT_MARKERS)


def _create_codespace_via_rest(
    repo: str,
    machine: str,
    location: str,
    account: str | None,
    *,
    branch: str | None = None,
    display_name: str | None = None,
    devcontainer_path: str | None = None,
) -> str:
    """Create a CodeSpace via the REST API (no TTY gate) -- #151 fallback.

    ``POST /repos/{repo}/codespaces`` has no interactive billing/permissions
    prompt, so it succeeds headlessly where ``gh codespace create`` aborts.
    ``multi_repo_permissions_opt_out=true`` skips the multi-repo permissions
    consent (the REST analogue of ``--default-permissions``). Returns the
    created CodeSpace name (state ``Queued``); the caller's wait loop handles
    provisioning. Raises ``RuntimeError`` if the REST call fails or returns no
    name.
    """
    from . import gh_account

    args = [
        "gh", "api", "--method", "POST",
        f"repos/{repo}/codespaces",
        "-f", f"machine={machine}",
        "-f", f"location={location}",
        "-F", "multi_repo_permissions_opt_out=true",
        "--jq", ".name",
    ]
    if branch:
        args.extend(["-f", f"ref={branch}"])
    if display_name:
        args.extend(["-f", f"display_name={display_name}"])
    if devcontainer_path:
        args.extend(["-f", f"devcontainer_path={devcontainer_path}"])

    log.info("Creating codespace via REST API (#151 fallback): %s", " ".join(args))
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=300,
        creationflags=_creation_flags(),
        env=gh_account.env_for_account(account) if account else None,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "gh codespace create hit an interactive prompt headlessly and the "
            f"REST API fallback also failed: {result.stderr.strip()}"
        )
    name = result.stdout.strip()
    if not name:
        raise RuntimeError(
            f"REST API codespace create returned no name (stdout={result.stdout!r})"
        )
    return name


def create_codespace(
    repo: str,
    config: CodespacesConfig,
    branch: str | None = None,
    display_name: str | None = None,
    devcontainer_path: str | None = None,
) -> CodespaceInfo:
    """Create a CodeSpace for the given repo using config defaults.

    Dotfiles are applied automatically by GitHub from the account-level
    dotfiles setting -- there is no ``--dotfiles`` flag on ``gh codespace
    create``. ``--default-permissions`` avoids an interactive prompt.

    ``--devcontainer-path`` is passed when the repo has multiple devcontainer
    configs (see ``resolve_devcontainer_path``) so creation stays headless.
    """
    repo_config = config.repos.get(repo, RepoConfig())
    machine_type = repo_config.machine_type or config.default_machine_type
    location = repo_config.location or config.default_location
    resolved_devcontainer = resolve_devcontainer_path(
        repo, config, override=devcontainer_path,
    )

    args = [
        "gh", "codespace", "create",
        "--repo", repo,
        "--machine", machine_type,
        "--location", location,
        "--default-permissions",
    ]
    if resolved_devcontainer:
        args.extend(["--devcontainer-path", resolved_devcontainer])
    if branch:
        args.extend(["--branch", branch])
    if display_name:
        args.extend(["--display-name", display_name])

    log.info("Creating codespace: %s", " ".join(args))

    from . import gh_account

    account = gh_account.account_for_repo(repo)
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=300,
        creationflags=_creation_flags(),
        env=gh_account.env_for_account(account) if account else None,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # #151: on org-paid repos, `gh codespace create` emits an interactive
        # org-billing consent gate that hard-fails headless ("failed to prompt:
        # no terminal"). `--default-permissions` suppresses the permissions
        # prompt but NOT that billing gate. The REST endpoint has no TTY gate,
        # so fall back to it rather than hard-failing a headless dispatch.
        if _is_interactive_prompt_failure(stderr):
            log.warning(
                "gh codespace create hit an interactive prompt headlessly "
                "(%s); falling back to the REST API (#151)", stderr,
            )
            name = _create_codespace_via_rest(
                repo, machine_type, location, account,
                branch=branch, display_name=display_name,
                devcontainer_path=resolved_devcontainer,
            )
        else:
            raise RuntimeError(f"gh codespace create failed: {stderr}")
    else:
        # gh codespace create prints the name on stdout
        name = result.stdout.strip()

    if account:
        try:
            from . import account_binding

            account_binding.bind(name, account, repo)
        except Exception:
            pass
    return CodespaceInfo(
        name=name,
        display_name=display_name or name,
        repository=repo,
        branch=branch or "",
        state="Available",
        machine=machine_type,
        account=account or "",
    )


def wait_for_codespace(
    name: str,
    timeout: float = 1200.0,
    interval: float = 10.0,
    on_progress=None,
) -> tuple[WaitOutcome, str]:
    """Patiently poll a CodeSpace until it is usable, fails, or times out.

    Returns ``(outcome, last_state)``. Unlike a naive fixed-timeout poll, a
    genuinely-failed state (Failed/Unavailable/Deleted/Moved/Archived) returns
    ``FAILED`` right away rather than waiting out the whole budget -- so callers
    can distinguish "still provisioning" from "genuinely dead" and never mistake
    a slow boot for a redundant-create trigger.

    The default ``timeout`` is generous (20 min) because CodeSpace
    create/provision is finicky; the caller supplies a finite ceiling and (for a
    background wait) an ``on_progress(last_state, remaining_s)`` callback. Transient
    ``gh codespace list`` errors are tolerated (logged, retried).
    """
    import time

    deadline = time.monotonic() + timeout
    last_state = ""
    while True:
        try:
            found = None
            for cs in list_codespaces():
                if cs.name == name:
                    found = cs
                    break
            if found is not None:
                last_state = found.state
                bucket = classify_state(found.state)
                if bucket == "available":
                    return WaitOutcome.AVAILABLE, found.state
                if bucket == "failed":
                    return WaitOutcome.FAILED, found.state
            else:
                last_state = "not-listed"
        except RuntimeError as exc:
            log.debug("list_codespaces during wait failed: %s", exc)
        if on_progress is not None:
            on_progress(last_state, max(0.0, deadline - time.monotonic()))
        if time.monotonic() + interval >= deadline:
            return WaitOutcome.TIMEOUT, last_state
        time.sleep(interval)


def wait_for_available(name: str, timeout: float = 300.0, interval: float = 10.0) -> bool:
    """Poll until a CodeSpace reaches the ``Available`` state.

    Returns True once Available, or False on timeout OR a terminal-failed state.
    Backward-compatible boolean shim over :func:`wait_for_codespace` (which now
    also fails fast on genuinely-dead states instead of waiting out the timeout).
    Used after ``create_codespace`` before provisioning over SSH.
    """
    outcome, _ = wait_for_codespace(name, timeout=timeout, interval=interval)
    return outcome == WaitOutcome.AVAILABLE


def delete_codespace(
    name: str, force: bool = False, account: str | None = None,
    *, token: str | None = None,
) -> None:
    """Delete a CodeSpace by name.

    ``account`` pins ``gh`` to the account that owns the CodeSpace; when None it
    is resolved from the cross-account listing (falls back to ambient auth).

    ``token`` -- when given, uses this EXACT pre-minted token directly
    instead of re-deriving one via ``gh_account.env_for_account`` (which
    silently falls back to AMBIENT credentials when it cannot mint one for
    ``account``). A caller that already validated the account (e.g. a
    claim-provider reclaim) should pass its own already-minted token here
    to close that gap entirely (claim-provider-pattern effort review
    finding: "Preserve validated credentials during status and reclaim").
    """
    from . import gh_account

    args = ["gh", "codespace", "delete", "-c", name]
    if force:
        args.append("--force")

    log.info("Deleting codespace: %s", name)

    if token is not None:
        env = dict(os.environ)
        env["GH_TOKEN"] = token
        env.pop("GITHUB_TOKEN", None)
    else:
        if account is None:
            account = account_for_codespace(name)
        env = gh_account.env_for_account(account) if account else None
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=60,
        creationflags=_creation_flags(),
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(f"gh codespace delete failed: {result.stderr.strip()}")
    try:
        from . import account_binding

        account_binding.unbind(name)
    except Exception:
        pass


def stop_codespace(name: str, account: str | None = None) -> bool:
    """Gracefully stop (shut down) a CodeSpace, PRESERVING it for later resume.

    The pause-and-keep counterpart to ``delete_codespace`` -- the compute is
    released but the CodeSpace (and its volume) is kept, so it boots again on
    the next connect. Returns ``True`` when a stop was issued, ``False`` when
    the CodeSpace was already ``Shutdown`` (idempotent no-op). Raises
    ``RuntimeError`` on an unexpected failure.

    ``account`` pins ``gh`` to the owning account; when None it is resolved
    from the cross-account listing (falls back to ambient auth).
    """
    from . import gh_account

    # Idempotency: skip the call if the CodeSpace is already shut down. Reuse
    # the listing to also learn the owning account when not supplied.
    try:
        for cs in list_codespaces():
            if cs.name == name:
                if account is None:
                    account = cs.account or None
                if cs.state == _SHUTDOWN_STATE:
                    log.info("CodeSpace %s already Shutdown; nothing to stop", name)
                    return False
                break
    except ContextRefused:
        raise
    except RuntimeError:
        # Can't list (auth/network) -- fall through and let `gh` decide.
        pass

    args = ["gh", "codespace", "stop", "-c", name]
    log.info("Stopping codespace: %s", name)

    result = subprocess.run(
        args, capture_output=True, text=True, timeout=120,
        creationflags=_creation_flags(),
        env=gh_account.env_for_account(account) if account else None,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # Tolerate an "already stopped" race as a no-op rather than an error.
        if "not running" in stderr.lower() or "already" in stderr.lower():
            log.info("CodeSpace %s already stopped: %s", name, stderr)
            return False
        raise RuntimeError(f"gh codespace stop failed: {stderr}")
    return True


def cleanup_stale(
    *,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """Remove local state for codespaces that no longer exist.

    Prunes:
    - SSH config files in ``~/.agent-codespaces/ssh/`` for codespaces
      not in the current ``gh codespace list`` output
    - Socket files in ``~/.agent-codespaces/sockets/``

    Returns a dict of ``{"ssh_configs": [...], "sockets": [...]}``
    listing what was (or would be) removed.
    """
    ssh_dir = RUNTIME_DIR / "ssh"
    socket_dir = RUNTIME_DIR / "sockets"

    # Get live codespace names
    try:
        live = list_codespaces()
    except ContextRefused:
        raise
    except RuntimeError:
        log.warning("Cannot list codespaces; skipping cleanup")
        return {"ssh_configs": [], "sockets": []}

    live_names = {cs.name for cs in live}

    removed: dict[str, list[str]] = {"ssh_configs": [], "sockets": []}

    # Prune SSH config files
    if ssh_dir.exists():
        import re

        for config_file in ssh_dir.glob("*.config"):
            # Reverse the sanitization: underscores may have replaced
            # non-word chars, so we can't perfectly reverse. Instead,
            # check if any live codespace's sanitized name matches.
            stem = config_file.stem
            matched = any(
                re.sub(r"[^\w\-.]", "_", cs.name) == stem
                for cs in live
            )
            if not matched:
                log.info(
                    "%s stale SSH config: %s",
                    "Would remove" if dry_run else "Removing",
                    config_file.name,
                )
                removed["ssh_configs"].append(str(config_file))
                if not dry_run:
                    config_file.unlink(missing_ok=True)

    # Prune socket files
    if socket_dir.exists():
        for socket_file in socket_dir.iterdir():
            if socket_file.is_file() or socket_file.is_socket():
                # Socket names typically contain the codespace name
                stem = socket_file.stem
                matched = any(name in stem for name in live_names)
                if not matched:
                    log.info(
                        "%s stale socket: %s",
                        "Would remove" if dry_run else "Removing",
                        socket_file.name,
                    )
                    removed["sockets"].append(str(socket_file))
                    if not dry_run:
                        try:
                            socket_file.unlink(missing_ok=True)
                        except OSError:
                            log.warning(
                                "Could not remove socket: %s", socket_file,
                            )

    return removed
