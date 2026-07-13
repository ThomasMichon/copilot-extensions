"""``pr-merge`` -- signal merge consent on an approved PR (apply the label).

The provider-generic port of the facility ``tools/pr-consent`` script.  The
author, after seeing an approval, runs ``pr-merge`` to **consent** to the merge;
this applies the repo's configured merge-consent label (facility:
``auto-merge``), which is the signal the review gate acts on.  It NEVER merges
anything itself -- it only applies the label; the gate still decides.

Two modes, both driven by the same pure classifier (:func:`pr_contract.classify_state`):

- **single PR** (the author path): classify one PR and, if eligible, apply the
  consent label.
- **sweep** (``--all``, the transition-helper carryover): classify every open PR
  and apply the label to each eligible one, optionally looping.

The merge-consent vocabulary is a facility **binding** on ``PRConfig``
(``automerge_label`` / ``hold_labels`` / ``wip_title_prefixes``); with no binding
the verb is a no-op ("no auto-merge label configured"), never a crash.  Eligibility
also requires the PR to target the repo's default branch.
"""

from __future__ import annotations

from collections.abc import Callable

from . import pr_contract as pc
from .providers import get_provider, resolve_token


def _binding(prcfg) -> dict:
    """Extract the review-vocabulary binding kwargs from a PRConfig."""
    return {
        "automerge_label": getattr(prcfg, "automerge_label", "") or "",
        "hold_labels": tuple(getattr(prcfg, "hold_labels", ()) or ()),
        "wip_title_prefixes": tuple(getattr(prcfg, "wip_title_prefixes", ()) or ()),
        "approval_required": bool(getattr(prcfg, "approval_required", True)),
    }


def classify_pr(snap: pc.PRSnapshot, prcfg) -> pc.PRState:
    """Classify a snapshot against the repo's merge-consent binding."""
    return pc.classify_state(snap, **_binding(prcfg))


def _decide(
    snap: pc.PRSnapshot, prcfg, *, default_branch: str = ""
) -> tuple[pc.PRState, str, str]:
    """Return ``(state, action, reason)`` incl. the default-branch guard.

    The default-branch check is a ``pr-merge`` policy (consent only PRs targeting
    the repo's default branch), layered over the pure classifier so a PR aimed at
    a side branch is skipped even if otherwise eligible.
    """
    state = classify_pr(snap, prcfg)
    if default_branch and snap.base_ref and snap.base_ref != default_branch:
        return state, "skip", f"base {snap.base_ref!r} != {default_branch!r}"
    return state, state.consent_action, state.reason


def merge_one(
    prcfg,
    repo: str,
    number: int,
    *,
    api_base: str = "",
    token: str | None = None,
    apply: bool = False,
    default_branch: str = "",
    provider=None,
) -> dict:
    """Classify PR ``number`` and, if eligible and ``apply``, apply the label.

    Returns a per-PR decision dict: ``{pr, action, reason, title, applied?,
    error?}``.  ``action`` is ``apply`` / ``already`` / ``skip`` (from the
    classifier + default-branch guard).  With ``apply=False`` it is a dry-run
    (classification only).
    """
    provider = provider or get_provider(getattr(prcfg, "provider", "gitea") or "gitea")
    base = (api_base or getattr(prcfg, "api_base", "") or "").strip()
    tok = token if token is not None else resolve_token(prcfg)

    snap = provider.get_snapshot(repo, number, api_base=base, token=tok)
    state, action, reason = _decide(snap, prcfg, default_branch=default_branch)
    row: dict = {
        "pr": number, "action": action, "reason": reason, "title": snap.title,
        "verdict": state.verdict, "merge_state": state.merge_state,
    }
    if action == "apply" and apply:
        # "Request auto-complete" is the first-class concept; the provider
        # decides how (gitea/github apply the automerge_label; ADO sets native
        # auto-complete). Applying the label is an implementation detail here.
        err = provider.request_auto_complete(
            repo, number, api_base=base, token=tok,
            automerge_label=_binding(prcfg)["automerge_label"],
            squash=getattr(prcfg, "squash", True),
            delete_source_branch=getattr(prcfg, "delete_source_branch", True),
            bypass_policy=getattr(prcfg, "bypass_policy", False),
            bypass_reason=getattr(prcfg, "bypass_reason", ""),
        )
        if err:
            row["applied"] = False
            row["error"] = err
        else:
            row["applied"] = True
    return row


def sweep_once(
    prcfg,
    repo: str,
    *,
    api_base: str = "",
    token: str | None = None,
    apply: bool = False,
    only: int | None = None,
    default_branch: str = "",
    provider=None,
) -> dict:
    """One classification pass over the open PRs (or a single ``only`` PR).

    Returns a summary ``{repo, open, eligible, applied, failed, apply,
    decisions}``.  Mirrors the facility ``pr-consent`` summary shape.
    """
    provider = provider or get_provider(getattr(prcfg, "provider", "gitea") or "gitea")
    base = (api_base or getattr(prcfg, "api_base", "") or "").strip()
    tok = token if token is not None else resolve_token(prcfg)

    if only is not None:
        numbers: tuple[int, ...] = (only,)
    else:
        numbers = provider.list_open_pulls(repo, api_base=base, token=tok)

    decisions: list[dict] = []
    applied = failed = eligible = 0
    for number in numbers:
        row = merge_one(
            prcfg, repo, number, api_base=base, token=tok, apply=apply,
            default_branch=default_branch, provider=provider,
        )
        if row["action"] == "apply":
            eligible += 1
            if apply:
                if row.get("applied"):
                    applied += 1
                else:
                    failed += 1
        decisions.append(row)
    return {
        "repo": repo, "open": len(numbers), "eligible": eligible,
        "applied": applied, "failed": failed, "apply": apply, "decisions": decisions,
    }


def run_sweep(
    prcfg,
    repo: str,
    *,
    api_base: str = "",
    token: str | None = None,
    apply: bool = False,
    loop: bool = False,
    interval: float = 30.0,
    max_passes: int = 0,
    default_branch: str = "",
    provider=None,
    sleep: Callable[[float], None] | None = None,
    on_pass: Callable[[dict], None] | None = None,
) -> dict:
    """Run one sweep, or loop until no eligible PRs remain (``loop=True``).

    ``max_passes`` (0 = unbounded) caps the loop.  Returns the last pass summary.
    Transient provider errors during a pass propagate (the caller decides); this
    keeps the sweep honest rather than silently masking a broken backend.
    """
    import time as _time

    sleep = sleep or _time.sleep
    provider = provider or get_provider(getattr(prcfg, "provider", "gitea") or "gitea")
    passes = 0
    summary: dict = {}
    while True:
        summary = sweep_once(
            prcfg, repo, api_base=api_base, token=token, apply=apply,
            default_branch=default_branch, provider=provider,
        )
        passes += 1
        if on_pass is not None:
            on_pass(summary)
        if not loop:
            break
        # Stop when nothing remains to apply (or we hit the pass cap).
        if summary["eligible"] == 0 or (max_passes and passes >= max_passes):
            break
        sleep(interval)
    return summary


__all__ = [
    "classify_pr",
    "merge_one",
    "run_sweep",
    "sweep_once",
]
