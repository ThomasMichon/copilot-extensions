"""Resolve CodeSpace rows back to their driving worktrees."""

from __future__ import annotations

from collections.abc import Iterable
import json
from typing import Any


def _matches_workstream_repo(repo: str, repository: str | None) -> bool:
    from .config import _repo_matches_codespace

    if not repository:
        return False
    owner = repo.strip().casefold().rpartition("/")[0]
    return (
        (not owner or repository.strip().casefold().rpartition("/")[0] == owner)
        and _repo_matches_codespace(repo, repository)
    )


def _choose_owner(owners: list[dict]) -> str:
    if len(owners) == 1:
        return str(owners[0].get("worktree_id") or "")
    active = [o for o in owners if o.get("status") == "active"]
    if len(active) == 1:
        return str(active[0].get("worktree_id") or "")
    return ""


def codespace_claim_owner_worktrees(codespace_names: Iterable[str]) -> dict[str, str]:
    """Resolve ``codespace`` claim refs to their owning local worktree ids.

    This is a soft cross-plugin integration: agent-codespaces has no hard
    dependency on agent-worktrees, so missing imports, old versions, and bad
    records all degrade to an empty map.
    """
    names = {str(name) for name in codespace_names if str(name)}
    if not names:
        return {}
    try:
        from agent_worktrees import claims_owner
    except ImportError:
        return {}
    try:
        by_ref = claims_owner.find_claim_owners_for_refs("codespace", names)
    except Exception:
        return {}
    return {
        name: worktree_id
        for name, owners in by_ref.items()
        if (worktree_id := _choose_owner(owners))
    }


def _qualified_claim_refs(holder: str) -> set[str]:
    return {
        name for name, state in _qualified_claim_states(holder).items()
        if state != "released"
    }


def _qualified_claim_states(holder: str) -> dict[str, str]:
    """Read one complete qualified owner's ledger, never treating failure as absence."""
    from . import coordination

    if holder:
        parts = holder.split("#", 1)[0].split("/")
        if len(parts) != 3 or any(not part for part in parts):
            raise RuntimeError("Workstream ownership identity is malformed")
        machine, project, worktree = parts
        result = coordination._run(["--project", project, "claims", worktree, "--json"])
        if result is None or result.returncode:
            detail = (result.stderr or "").strip()[:500] if result is not None else "selected runtime unavailable"
            raise RuntimeError(f"Workstream ownership lookup is unavailable: {detail}")
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            raise RuntimeError("Workstream ownership response is not JSON") from error
        if (
            not isinstance(payload, dict)
            or payload.get("worktree_id") != worktree or payload.get("repo") != project
            or payload.get("execution_machine", payload.get("machine")) != machine
            or ("version" in payload and (
                type(payload["version"]) is not int or payload["version"] != 1
            ))
            or not isinstance(payload.get("outbound"), list)
        ):
            raise RuntimeError("Workstream ownership response is incomplete or names another owner")
        readiness = payload.get("coordination_readiness")
        if readiness is not None and (
            not isinstance(readiness, dict) or readiness.get("ready") is not True
        ):
            raise RuntimeError("Workstream ownership coordination is not ready")
        states: dict[str, str] = {}
        for claim in payload["outbound"]:
            if (
                not isinstance(claim, dict) or not isinstance(claim.get("kind"), str)
                or not claim["kind"]
            ):
                raise RuntimeError("Workstream ownership contains malformed claims")
            if claim["kind"] != "codespace":
                continue
            if (
                not isinstance(claim.get("ref"), str) or not claim["ref"].strip()
                or claim.get("state") not in ("active", "at-rest", "released")
                or claim["ref"] in states
            ):
                raise RuntimeError("Workstream ownership contains an unknown CodeSpace claim")
            states[claim["ref"]] = claim["state"]
        return states
    raise RuntimeError("Workstream ownership identity is missing")


def _workstream_claim_refs(owner: str | None) -> set[str]:
    from . import coordination

    holder = coordination.owner_ref()
    if holder:
        return _qualified_claim_refs(holder)
    if not owner:
        return set()
    try:
        from agent_worktrees import claims_owner

        return set(claims_owner.claims_of_owner(owner, "codespace"))
    except Exception:
        return set()


def _boxes_from_refs(
    members: Iterable[Any], repo: str, refs: set[str], account: str | None = None,
) -> tuple[str, ...]:
    by_name = {member.name: member for member in members}
    candidates = sorted(
        name for name in refs
        if (member := by_name.get(name)) is not None
        and _matches_workstream_repo(repo, member.repository)
    )
    if not account or not refs:
        return tuple(candidates)
    from .account_binding import list_bindings

    # A missing inventory row does not settle a recorded acquisition attempt.
    bindings = list_bindings(strict=True)
    if any(
        not isinstance(value, str)
        for binding in bindings for value in (binding.codespace, binding.account, binding.repo)
    ):
        raise RuntimeError("Malformed workstream account binding")
    missing = sorted(
        binding.codespace for binding in bindings
        if binding.codespace in refs and binding.codespace not in by_name
        and binding.account.casefold() == account.casefold()
        and _matches_workstream_repo(repo, binding.repo)
    )
    return tuple(sorted(set(candidates + missing)))


def _box_from_refs(
    members: Iterable[Any], repo: str, refs: set[str], account: str | None = None,
) -> str | None:
    return next(iter(_boxes_from_refs(members, repo, refs, account)), None)


def resolve_workstream_box(
    members: Iterable[Any],
    repo: str,
    owner: str | None,
) -> str | None:
    """The CodeSpace ``owner`` (a worktree id, or a bound effort ref) already
    claims for ``repo``, if any -- the box a Phase 2b venue request should
    resume rather than re-deriving from any idle/clean box in the pool.

    Looks up ``owner``'s own outbound ``codespace`` claims (active or at-rest
    -- an at-rest claim is exactly "mine, just not running right now"), then
    picks the one still present in ``members`` and matching ``repo``. Multiple
    live matches (should not normally happen -- one workstream, one box) picks
    the first deterministically (sorted by name) rather than raising.
    Qualified owners are read through the selected Worktrees runtime, not a
    sibling Python import. A failed or malformed declared-owner lookup raises;
    only the unattributed legacy profile retains best-effort import behavior.
    """
    if not owner:
        return None
    return _box_from_refs(members, repo, _workstream_claim_refs(owner))


def resolve_current_workstream_box(
    members: Iterable[Any],
    repo: str,
    account: str | None = None,
) -> str | None:
    """``resolve_workstream_box`` for THIS process's own calling worktree.

    Combines ``lease.resolve_owner_worktree()`` (who is asking) with
    :func:`resolve_workstream_box` (what they already claim) into the one call
    a venue request needs. A qualified native owner remains readable without
    the sibling Python package. Unknown declared ownership raises rather than
    becoming absence. With a selected account, a matching persisted binding
    keeps an omitted venue's unresolved obligation visible to admission.
    """
    return next(iter(resolve_current_workstream_boxes(members, repo, account)), None)


def resolve_current_workstream_boxes(
    members: Iterable[Any], repo: str, account: str | None = None,
) -> tuple[str, ...]:
    """Return every matching responsibility, including inventory omissions."""
    try:
        from .lease import resolve_owner_worktree

        owner = resolve_owner_worktree()
    except Exception:
        owner = None
    return _boxes_from_refs(members, repo, _workstream_claim_refs(owner), account)


def current_workstream_account(repo: str) -> str | None:
    """Keep an unmapped workstream on its claimed venue's persisted account."""
    try:
        from .lease import resolve_owner_worktree

        owner = resolve_owner_worktree()
    except Exception:
        owner = None
    refs = _workstream_claim_refs(owner)
    if not refs:
        return None
    from .account_binding import list_bindings
    try:
        bindings = list_bindings(strict=True)
        if any(
            not isinstance(value, str)
            for binding in bindings for value in (binding.codespace, binding.account, binding.repo)
        ):
            raise RuntimeError("Malformed workstream account binding")
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("Malformed workstream account bindings") from exc
    accounts = {
        binding.account for binding in bindings
        if binding.codespace in refs and binding.account
        and _matches_workstream_repo(repo, binding.repo)
    }
    if len({account.casefold() for account in accounts}) > 1:
        raise RuntimeError("Workstream obligations span multiple accounts; select a repository account")
    return next(iter(accounts), None)


def same_worktree_family(holder: str, owner: str) -> bool:
    """True when ``holder`` and ``owner`` are the same worktree, or one created
    the other (directly or transitively, via ``owner_ref``).

    A parent worktree that spawned a child to work on its behalf (e.g. a
    dedicated ``copilot-extensions`` worktree carved off a driving harness/task
    worktree) is not a foreign contender for a CodeSpace either of them already
    claimed -- it's the same task lineage. Recognizing that here means neither
    ``agent-codespaces``' own claim path nor ``agent-bridge``'s Session-Host
    dispatch (which shells out to it) needs an explicit force-takeover to keep
    operating on a box its own parent/child worktree already holds. Soft
    cross-plugin integration: no ``agent_worktrees`` / stale version / bad
    records all degrade to ``False`` (today's stricter behavior), never a false
    family match.
    """
    if not holder or not owner:
        return False
    try:
        from agent_worktrees import claims_owner
    except ImportError:
        return False
    try:
        return bool(claims_owner.same_worktree_family(holder, owner))
    except Exception:
        return False
