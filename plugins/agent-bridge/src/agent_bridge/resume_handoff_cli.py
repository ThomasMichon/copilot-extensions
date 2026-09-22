"""CLI target resolution for resume/handoff singleton-anchor flows."""

from __future__ import annotations

import sys
from typing import Any

from .client import BridgeClientError
from .singleton_anchor import (
    SingletonRepo,
    anchor_session_key,
    find_singleton_repo,
    repo_name_from_anchor_key,
)


def _singleton_repo_for_target(
    client: Any,
    target: str,
    *,
    match_agents,
) -> SingletonRepo | None:
    repo_name = repo_name_from_anchor_key(target)
    if repo_name:
        return find_singleton_repo(repo_name)

    candidates = [target]
    try:
        agents = client.list_agents()
    except Exception:
        agents = []
    matches = match_agents(target, agents) if agents else []
    if len(matches) == 1:
        canonical = matches[0]
        for agent in agents:
            if agent.get("name") != canonical:
                continue
            project = str(agent.get("project", "")).strip()
            if project:
                candidates.append(project)
            break
    for candidate in candidates:
        repo = find_singleton_repo(candidate)
        if repo is not None:
            return repo
    return None


def reject_singleton_create(
    *,
    client: Any,
    target: str,
    match_agents,
) -> None:
    """Raise a declared create refusal for singleton-class repo targets."""
    repo = _singleton_repo_for_target(client, target, match_agents=match_agents)
    if repo is None:
        return
    print(
        f"[BLOCKED] '{target}' resolves to singleton repo '{repo.name}'.\n"
        "  `create` is the fresh-worktree gesture; singleton repos have one "
        "anchor checkout, not a second worktree to create.\n"
        f"  Resume the current head:   agent-bridge resume {target}\n"
        f"  Roll it forward in place: agent-bridge handoff {target}",
        file=sys.stderr,
    )
    raise SystemExit(1)


def run_resume(
    *,
    client: Any,
    target: str,
    reclaim: bool,
    as_json: bool,
    json_out,
    match_agents,
    startup_request_timeout,
) -> None:
    """Implementation of ``agent-bridge resume`` with singleton fallback."""

    def _fail(message: str, *, reason: str = "") -> None:
        if as_json:
            payload = {"error": message}
            if reason:
                payload["reason"] = reason
            json_out(payload)
        else:
            print(f"[FAIL] {message}", file=sys.stderr)
        raise SystemExit(1)

    def _worktree_conflict(exc: BridgeClientError, noun: str, label: str) -> None:
        detail = exc.detail
        reason = detail.get("reason") if isinstance(detail, dict) else None
        if exc.status != 409 or reason != "live_cli_holds_worktree":
            _fail(f"Could not resume {noun.lower()} {label}: {exc.detail}",
                  reason=reason or "")
        holder = detail.get("session_id") if isinstance(detail, dict) else None
        if as_json:
            payload = {
                "error": f"a live interactive CLI ({holder}) still holds "
                f"{noun.lower()} {label}",
                "reason": reason,
                "session_id": holder,
            }
            if noun == "Repo":
                payload["requested_target"] = target
                payload["worktree_id"] = anchor_session_key(label)
            json_out(payload)
        else:
            print(
                f"[BREAK-GLASS] A live interactive CLI (session {holder}) "
                f"still holds {noun.lower()} {label}.\n"
                "  Taking it over would run a second controller on the same "
                "checkout.\n"
                "  Stop that CLI first, then re-run with --force to take it "
                "over.",
                file=sys.stderr,
            )
        raise SystemExit(1)

    if not reclaim:
        try:
            result = client.resume_session(
                target,
                request_timeout=startup_request_timeout(resume=True),
            )
            status = result.get("status", "")
            if as_json:
                json_out({"session_id": target, "status": status, "verb": "resumed"})
            else:
                print(f"[OK] Session {target} resumed ({status})")
            return
        except BridgeClientError as exc:
            if exc.status != 404:
                _fail(f"Could not resume session {target}: {exc.detail}")

    attempts: list[tuple[str, str, str]] = [("Worktree", target, target)]
    repo = _singleton_repo_for_target(client, target, match_agents=match_agents)
    if repo is not None:
        anchor = anchor_session_key(repo.name)
        if anchor != target:
            attempts.append(("Repo", repo.name, anchor))

    for noun, label, worktree_id in attempts:
        try:
            result = client.resume_worktree(
                worktree_id,
                reclaim=reclaim,
                request_timeout=startup_request_timeout(
                    resume=True,
                    fresh_fallback=True,
                ),
            )
        except BridgeClientError as exc:
            if exc.status == 404:
                continue
            _worktree_conflict(exc, noun, label)
        else:
            status = result.get("status", "")
            sid = result.get("session_id", "") or worktree_id
            verb = "took over" if reclaim else "loaded"
            payload = {"session_id": sid, "status": status, "verb": verb}
            if noun == "Repo":
                payload["requested_target"] = target
            payload["worktree_id"] = worktree_id
            if as_json:
                json_out(payload)
            else:
                print(f"[OK] {noun} {label} {verb} as owned session {sid} ({status})")
            return

    extra = " or singleton repo" if repo is not None else ""
    _fail(
        f"{target} is neither a bridge-owned session nor a recognized "
        f"worktree{extra}. Pass a worktree handle (e.g. '<machine>-<env>-<ts>-"
        "<id>') or a singleton repo key to load a dormant target."
    )


def run_handoff(
    *,
    client: Any,
    target: str,
    reason: str | None,
    seed: bool,
    match_agents,
) -> None:
    """Implementation of ``agent-bridge handoff`` with singleton fallback."""

    def _report(kind: str, label: str, result: dict[str, Any]) -> None:
        sid = result.get("session_id", "") or "(unknown)"
        status = result.get("status", "")
        print(f"[OK] {kind} {label} handed off -> successor {sid} ({status})")

    try:
        result = client.handoff_session(target, reason=reason, seed=seed)
        _report("Session", target, result)
        return
    except BridgeClientError as exc:
        if exc.status == 404:
            pass
        elif exc.status == 409:
            print(f"[FAIL] Cannot hand off {target}: {exc.detail}", file=sys.stderr)
            raise SystemExit(1)
        else:
            print(
                f"[FAIL] Could not hand off session {target}: {exc.detail}",
                file=sys.stderr,
            )
            raise SystemExit(1)

    repo = _singleton_repo_for_target(client, target, match_agents=match_agents)
    attempts: list[tuple[str, str, str]] = [("Worktree", target, target)]
    if repo is not None:
        anchor = anchor_session_key(repo.name)
        if anchor != target:
            attempts.append(("Repo", repo.name, anchor))

    for kind, label, worktree_id in attempts:
        try:
            result = client.handoff_worktree(worktree_id, reason=reason, seed=seed)
        except BridgeClientError as exc:
            if exc.status == 404:
                continue
            if exc.status == 409:
                print(
                    f"[FAIL] Cannot hand off {kind.lower()} {label}: {exc.detail}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"[FAIL] Could not hand off {kind.lower()} {label}: {exc.detail}",
                    file=sys.stderr,
                )
            raise SystemExit(1)
        else:
            _report(kind, label, result)
            return

    extra = " or singleton repo" if repo is not None else ""
    print(
        f"[FAIL] {target} is neither a bridge-owned session nor a "
        f"worktree{extra} with a current session to hand off.",
        file=sys.stderr,
    )
    raise SystemExit(1)
