"""``pr-watch`` -- block until a pull request moves, then wake the caller.

The provider-generic port of the facility ``tools/pr-watch`` script into the
plugin.  It owns the **network + timing** half of the watcher (poll, retry,
timeout, baseline); the **pure transition logic** lives in
:mod:`agent_worktrees.pr_contract` (``compute_events`` / ``Baseline``), and the
**provider read** (``get_snapshot``) lives in the provider plugins.  The review
backend -- host, token, and (later) verdict vocabulary -- is a facility
**binding** supplied by ``PRConfig``; nothing here hardcodes Gitea.

An agent that just opened a PR fires ``pr-watch wait`` as a background task; the
task polls the provider and blocks until a target transition (a review by
someone other than the author, a mergeability flip, or the PR merging/closing),
then prints the event JSON and exits 0.  The Copilot CLI surfaces the
background-task completion as a new turn, waking the otherwise-idle session so it
can address feedback, re-push, or finalize -- unattended.

Exit codes mirror the facility tool: 0 = a transition fired, 124 = timed out,
3 = provider/auth error, 2 = usage error.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from . import pr_contract as pc
from .providers import ProviderError, get_provider, resolve_token


@dataclass
class WaitResult:
    matched: bool
    payload: dict = field(default_factory=dict)


def decorate_events(
    events: list[dict], repo: str, pr: int, snap: pc.PRSnapshot
) -> dict:
    """Wrap the raw transition list into the final result payload.

    Field-for-field identical to the facility ``tools/pr-watch`` payload so a
    thin shim delegating here is a drop-in (same keys, same JSON shape).
    """
    return {
        "repo": repo,
        "pr": pr,
        "events": events,
        "transitions": [e["event"] for e in events],
        "pr_state": snap.pr_state,
        "merged": snap.merged,
        "mergeable": snap.mergeable,
        "head_sha": snap.head_sha,
        "base_ref": snap.base_ref,
        "cursor": pc.Baseline.from_snapshot(snap).to_cursor(),
    }


def run_wait(
    *,
    repo: str,
    pr: int,
    until: list[str],
    baseline: pc.Baseline | None,
    fetch: Callable[[], pc.PRSnapshot],
    timeout: float,
    interval: float,
    now: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    on_poll: Callable[[pc.PRSnapshot], None] | None = None,
    on_error: Callable[[ProviderError], None] | None = None,
) -> WaitResult:
    """Poll ``fetch`` until a target transition or ``timeout`` seconds elapse.

    ``baseline`` of ``None`` means auto-baseline: the first successful poll
    establishes the reference ("notify me of changes from now on"), except that
    an already-**terminal** state (merged / closed-unmerged) at the first poll
    still fires -- otherwise arming a wait moments after a fast merge baselines
    ON the terminal state and hangs (aperture-labs #1139).  Transient provider
    errors are tolerated (retried next interval); permanent ones propagate so a
    bad token / wrong repo fails fast instead of hanging the full timeout.
    """
    import time as _time

    now = now or _time.monotonic
    sleep = sleep or _time.sleep

    deadline = now() + timeout if timeout > 0 else None
    base = baseline
    while True:
        try:
            snap: pc.PRSnapshot | None = fetch()
        except ProviderError as exc:
            if not exc.transient:
                raise
            if on_error is not None:
                on_error(exc)
            snap = None
        if snap is not None:
            if on_poll is not None:
                on_poll(snap)
            if base is None:
                # Auto-baseline = "changes from here on" -- but diff the first
                # snapshot against a zero TERMINAL baseline so an already-merged
                # / already-closed PR fires immediately (pre-existing reviews and
                # not-yet-computed mergeability do NOT fire; only terminal does).
                first_base = replace(
                    pc.Baseline.from_snapshot(snap), merged=False, closed=False
                )
                events = pc.compute_events(first_base, snap, until)
                if events:
                    return WaitResult(True, decorate_events(events, repo, pr, snap))
                base = pc.Baseline.from_snapshot(snap)
            else:
                # Lazily complete a not-yet-known mergeable baseline: the provider
                # may compute the flag asynchronously (and a --since re-arm starts
                # it unknown), so adopt the first concrete value WITHOUT firing --
                # only a later flip is a real transition.
                if base.mergeable is None and snap.mergeable is not None:
                    base = replace(base, mergeable=snap.mergeable)
                events = pc.compute_events(base, snap, until)
                if events:
                    return WaitResult(True, decorate_events(events, repo, pr, snap))

        if deadline is not None and now() >= deadline:
            return WaitResult(False)
        if deadline is not None:
            sleep(max(0.0, min(interval, deadline - now())))
        else:
            sleep(interval)


def build_fetch(
    prcfg,
    repo: str,
    number: int,
    *,
    api_base: str = "",
    token: str | None = None,
) -> Callable[[], pc.PRSnapshot]:
    """Build a ``() -> PRSnapshot`` fetcher from the repo's PR binding.

    Resolves the provider (``prcfg.provider``), the API base (explicit
    ``api_base`` override else ``prcfg.api_base``), and the token (explicit
    ``token`` override else ``resolve_token(prcfg)`` -- the vault/env binding).
    The provider's ``get_snapshot`` does the actual read; an unsupported provider
    raises :class:`ProviderError` here (fail fast), never a hang.
    """
    provider = get_provider(getattr(prcfg, "provider", "gitea") or "gitea")
    base = (api_base or getattr(prcfg, "api_base", "") or "").strip()
    tok = token if token is not None else resolve_token(prcfg)

    def _fetch() -> pc.PRSnapshot:
        return provider.get_snapshot(repo, number, api_base=base, token=tok)

    return _fetch


__all__ = [
    "WaitResult",
    "build_fetch",
    "decorate_events",
    "run_wait",
]
