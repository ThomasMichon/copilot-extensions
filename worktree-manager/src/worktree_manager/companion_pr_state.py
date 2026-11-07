"""Recorded PR associations and one explicit, owner-backed PR point read.

The Worktrees owner optionally declares ``companion-pr-observations-v1`` on its
status bundle. Only then does the Companion request the ``--pr-associations``
form and, on explicit operator selection, read exactly one admitted
association through that same engine's passive ``pr-watch snapshot``. Recorded
lifecycle state is association bookkeeping, never current provider openness;
a point read is last-observed evidence, never consent or merge authority.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from rich.text import Text
from rich.tree import Tree

from . import engine_client as ec

CONTRACT = "companion-pr-observations-v1"
DECLARATION = {
    "contract": CONTRACT,
    "version": 1,
    "option": "--pr-associations",
    "snapshot": {"schema": "agent-worktrees.pr-snapshot", "version": 1},
}
SNAPSHOT_SCHEMA = "agent-worktrees.pr-snapshot"
MAX_ASSOCIATIONS = 24
MAX_SNAPSHOT_BYTES = 256 * 1024
TEXT_LIMIT = 4096
_PROVIDERS = frozenset({"github", "gitea", "azure-devops"})
_CHECKS = frozenset({"success", "failure", "pending", "none", ""})
_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class PointReadError(Exception):
    """A point read whose reply cannot be admitted for the selected association."""


@dataclass(frozen=True)
class Association:
    """One admitted association, bound to the owner's resolved target."""

    pr_id: str
    pr_revision: int
    provider: str
    repo: str
    number: int
    url: str
    recorded_state: str
    endpoint: str

    @property
    def key(self) -> tuple:
        return (self.pr_id, self.pr_revision, self.provider, self.repo, self.number, self.endpoint)

    @property
    def label(self) -> str:
        return f"{self.repo}#{self.number} [{self.provider}]"


@dataclass(frozen=True)
class PointRead:
    """The outcome of one finite snapshot read of one association."""

    association: Association
    state: str
    """open, closed-unmerged, merged or unavailable."""
    observed_at: float
    reason: str = ""
    head_sha: str = ""
    mergeable: bool | None = None
    checks_state: str = ""
    checks_complete: bool | None = None
    draft: bool = False
    reviews: int = 0
    latest_review: str = ""


def _text(value, limit: int = TEXT_LIMIT) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= limit and value == value.strip()


def supported(bundle) -> bool:
    """True only when the original response declares the exact version-1 contract."""
    return isinstance(bundle, dict) and bundle.get("companion_pr_observations") == DECLARATION


def read_associations(worktree_id: str, *, runner=None) -> dict:
    """Request the declared optional association form for the selected worktree."""
    obj = ec.run_json(
        None,
        ["worktree-status-bundle", "--worktree", worktree_id, "--pr-associations", "--json"],
        runner=runner,
    )
    if obj.get("worktree_id") != worktree_id or not isinstance(obj.get("facts"), dict):
        raise ec.EngineError("The association response does not describe the selected worktree.")
    if obj.get("companion_pr_observations") != DECLARATION:
        raise ec.EngineError("The association response no longer declares the supported contract.")
    fact = obj["facts"].get("pr_associations")
    if not isinstance(fact, dict) or not isinstance(fact.get("value"), dict):
        raise ec.EngineError("The association response omitted its association fact.")
    value = fact["value"]
    if (
        value.get("schema") != "agent-worktrees.pr-associations"
        or type(value.get("version")) is not int or value["version"] != 1
        or value.get("worktree_id") != worktree_id
        or type(value.get("available")) is not bool
        or type(value.get("complete")) is not bool
        or not isinstance(value.get("associations"), list)
        or len(value["associations"]) > MAX_ASSOCIATIONS
        or type(value.get("omitted")) is not int or value["omitted"] < 0
    ):
        raise ec.EngineError("The association fact has an unsupported shape.")
    return fact


def admitted(fact) -> tuple[list[Association], int]:
    """Associations eligible for a point read, plus the count not admitted."""
    if not isinstance(fact, dict) or not isinstance(fact.get("value"), dict):
        return [], 0
    value = fact["value"]
    if value.get("available") is not True or not isinstance(value.get("associations"), list):
        return [], 0
    rows = value["associations"][:MAX_ASSOCIATIONS]
    ids = [row.get("pr_id") for row in rows if isinstance(row, dict)]
    result, rejected = [], 0
    for row in rows:
        binding = row.get("binding") if isinstance(row, dict) else None
        if (
            not isinstance(row, dict) or not isinstance(binding, dict)
            or binding.get("available") is not True
            or not all(_text(row.get(key)) for key in ("pr_id", "provider", "repo", "url"))
            or not isinstance(row.get("recorded_state"), str)
            or ids.count(row["pr_id"]) != 1
            or type(row.get("pr_revision")) is not int
            or type(row.get("number")) is not int or row["number"] <= 0
            or row["provider"] not in _PROVIDERS
            or binding.get("provider") != row["provider"]
            or binding.get("repo") != row["repo"]
            or not _text(binding.get("endpoint"))
        ):
            rejected += 1
            continue
        result.append(Association(
            row["pr_id"], row["pr_revision"], row["provider"], row["repo"], row["number"],
            row["url"], row["recorded_state"], binding["endpoint"],
        ))
    return result, rejected


def snapshot_argv(association: Association) -> list[str]:
    """The existing passive owner read; no host, token or credential overrides."""
    return ["pr-watch", "snapshot", association.repo, str(association.number), "--json"]


def decode_snapshot(obj, association: Association, *, observed_at: float) -> PointRead:
    """Admit one snapshot reply strictly against the selected association."""
    if not isinstance(obj, dict):
        raise PointReadError("non-object reply")
    if obj.get("schema") != SNAPSHOT_SCHEMA or type(obj.get("version")) is not int or obj["version"] != 1:
        raise PointReadError("unsupported snapshot contract/version")
    if obj.get("provider") != association.provider or obj.get("repo") != association.repo:
        raise PointReadError("foreign provider or repository")
    if type(obj.get("pr")) is not int or obj["pr"] != association.number:
        raise PointReadError("foreign PR number")
    if not isinstance(obj.get("endpoint"), str) or not obj["endpoint"]:
        raise PointReadError("endpoint unknown")
    if obj["endpoint"] != association.endpoint:
        raise PointReadError("foreign endpoint")
    pr_state, merged = obj.get("pr_state"), obj.get("merged")
    head_sha, mergeable = obj.get("head_sha"), obj.get("mergeable")
    checks, complete = obj.get("checks_state"), obj.get("checks_complete")
    reviews, draft = obj.get("reviews"), obj.get("draft")
    if (
        pr_state not in ("open", "closed") or type(merged) is not bool
        or not isinstance(head_sha, str) or (head_sha and not _OBJECT_ID.fullmatch(head_sha))
        or not (mergeable is None or type(mergeable) is bool)
        or not isinstance(checks, str) or checks not in _CHECKS or not (complete is None or type(complete) is bool)
        or type(draft) is not bool or not isinstance(reviews, list)
        or any(not isinstance(review, dict) or not isinstance(review.get("state"), str)
               for review in reviews)
    ):
        raise PointReadError("malformed typed state")
    if merged and pr_state != "closed":
        raise PointReadError("contradictory merged/open state")
    state = "merged" if merged else ("closed-unmerged" if pr_state == "closed" else "open")
    return PointRead(
        association, state, observed_at, head_sha=head_sha, mergeable=mergeable,
        checks_state=checks, checks_complete=complete, draft=draft, reviews=len(reviews),
        latest_review=reviews[-1]["state"][:64] if reviews else "",
    )


def point_read(association: Association, *, runner=None, clock=time.time) -> PointRead:
    """Run one finite read; every failure is explicit unavailability, never empty success."""
    try:
        obj = ec.run_json(None, snapshot_argv(association), runner=runner,
                          max_bytes=MAX_SNAPSHOT_BYTES)
        return decode_snapshot(obj, association, observed_at=clock())
    except (ec.EngineError, PointReadError) as exc:
        return PointRead(association, "unavailable", clock(), reason=str(exc)[:240])


_STATE_LABELS = {
    "open": "open",
    "closed-unmerged": "closed without merge",
    "merged": "merged",
    "unavailable": "unknown/unavailable",
}


def _plain(value) -> str:
    text = value if isinstance(value, str) else ""
    return " ".join(text.split())[:120] or "unknown"


def render(root: Tree, *, declared: bool, fact, error: str | None,
           selected: Association | None, read: PointRead | None, busy: bool) -> None:
    """Add recorded associations separately from resource claims."""
    group = root.add(Text("Recorded PR associations (record state, not current PR state)"))
    if not declared:
        group.add(Text("Not declared by this engine: claim-only view retained; PR state unknown"))
        return
    if error:
        group.add(Text("Unavailable: " + _plain(error)))
        return
    value = fact.get("value") if isinstance(fact, dict) else None
    if not isinstance(value, dict):
        group.add(Text("Not loaded"))
        return
    if value.get("available") is not True:
        group.add(Text("Unavailable: " + _plain(value.get("reason"))))
        return
    rows = value.get("associations") or []
    if not rows:
        group.add(Text("No recorded PR associations in this observation"))
    associations, rejected = admitted(fact)
    by_id = {item.pr_id: item for item in associations}
    for row in rows[:MAX_ASSOCIATIONS]:
        if not isinstance(row, dict):
            continue
        pr_id = row.get("pr_id")
        item = by_id.get(pr_id) if isinstance(pr_id, str) else None
        label = (f"PR [{_plain(row.get('provider'))}] {_plain(row.get('repo'))}"
                 f"#{row.get('number') if type(row.get('number')) is int else '?'}; "
                 f"recorded lifecycle: {_plain(row.get('recorded_state'))} "
                 "(record, not current openness or approval)")
        entry = group.add(Text(label))
        if item is None:
            binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
            entry.add(Text("Point read unavailable: " + _plain(binding.get("reason") or "not admitted")))
            continue
        if selected is not None and item.key == selected.key:
            _render_read(entry, read, busy)
        else:
            entry.add(Text("Not read: select it to inspect once"))
    omitted = value.get("omitted") if type(value.get("omitted")) is int else 0
    if omitted or value.get("complete") is not True:
        group.add(Text(f"Incomplete: {omitted} association(s) omitted by the owner's bound"))
    if rejected:
        group.add(Text(f"{rejected} association(s) not admitted for point reads"))


def _render_read(entry: Tree, read: PointRead | None, busy: bool) -> None:
    if busy:
        entry.add(Text("Point read in progress"))
    if read is None:
        if not busy:
            entry.add(Text("Selected; no completed point read"))
        return
    stamp = time.strftime("%H:%M:%S", time.localtime(read.observed_at))
    state = entry.add(Text(
        f"Point read: {_STATE_LABELS[read.state]} (last observed {stamp}; "
        "not continuously confirmed)"))
    state.add(Text("Source: agent-worktrees pr-watch snapshot "
                   f"{read.association.repo} #{read.association.number} via "
                   f"{_plain(read.association.endpoint)}"))
    if read.state == "unavailable":
        state.add(Text("Reason: " + _plain(read.reason)))
        return
    diagnostics = state.add(Text("Observed diagnostics only (no consent, gate or action):"))
    diagnostics.add(Text(f"Head: {read.head_sha[:12] or 'unknown'}; draft: {read.draft}"))
    diagnostics.add(Text(
        f"Checks: {read.checks_state or 'unknown'}"
        + ("" if read.checks_complete is True else " (incomplete or unknown)")))
    diagnostics.add(Text(
        "Mergeability: " + ("unknown" if read.mergeable is None else str(read.mergeable))))
    diagnostics.add(Text(
        f"Reviews observed: {read.reviews}"
        + (f"; latest: {_plain(read.latest_review)}" if read.latest_review else "")))
