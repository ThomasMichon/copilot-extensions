"""Delegate/caller graph helpers and CLI (fleet-flows Phase 3, #2740).

Builds a programmatic host<->delegate graph from ``caller_worktree`` and
surfaces the manual finalize-inheritance rule the operator previously had to
apply by hand: when a delegate's host is finalized/completed/gone, the
delegate becomes finalizable too.

Like Phase 1/2's ``fleet``/``reconcile`` verbs, this module is manually
dispatched from ``__main__.py`` so it owns its own argparse and keeps the
already-at-ceiling ``__main__.py`` small.
"""

from __future__ import annotations

import argparse
import base64
import json
import shlex
import subprocess
import sys
from collections import defaultdict
from typing import Any

from . import config as cfg
from . import list_views_cli
from . import tracking

_HOST_TERMINAL_STATUSES = frozenset({"complete", "completed", "finalized"})


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Extract one top-level JSON object, tolerating banner noise."""
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _caller_lookup_tokens(
    raw: str | None,
    *,
    default_project: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Normalize ``caller_worktree`` into ``(machine, project, worktree_id)``."""
    if not raw:
        return None, default_project, None
    parsed = tracking.parse_claim_ref(raw)
    if parsed is None:
        return None, default_project, raw
    return parsed.machine, (parsed.project or default_project), parsed.worktree_id


def _expected_host_key(
    worktree_id: str,
    reachable_hosts: dict[tuple[str, str], bool],
) -> tuple[str, str] | None:
    """Infer the host machine/environment from a machine-stamped worktree id."""
    matches: list[tuple[int, tuple[str, str]]] = []
    for machine, platform in reachable_hosts:
        prefix = f"{machine}-{platform}-" if platform else f"{machine}-"
        if worktree_id.startswith(prefix):
            matches.append((len(prefix), (machine, platform)))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


def annotate_delegate_graph(
    worktrees: list[dict[str, Any]],
    *,
    reachable_hosts: dict[tuple[str, str], bool] | None = None,
) -> list[dict[str, Any]]:
    """Annotate ``list``/``fleet`` rows with caller/host + delegate state."""
    id_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    qualified_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in worktrees:
        worktree_id = row.get("id")
        if not isinstance(worktree_id, str) or not worktree_id:
            continue
        id_index[worktree_id].append(row)
        machine = row.get("machine")
        project = row.get("repo")
        if isinstance(machine, str) and machine and isinstance(project, str) and project:
            qualified_index[(machine, project, worktree_id)] = row

    for row in worktrees:
        row.pop("caller_state", None)
        row.pop("delegate_finalizable", None)
        row.pop("delegate_finalizable_reason", None)
        row.pop("delegates", None)

    for row in worktrees:
        raw_caller = row.get("caller_worktree")
        if not isinstance(raw_caller, str) or not raw_caller:
            continue

        machine_hint, project_hint, caller_id = _caller_lookup_tokens(
            raw_caller,
            default_project=(
                row.get("repo") if isinstance(row.get("repo"), str) else None
            ),
        )
        if not caller_id:
            continue

        caller_state: dict[str, Any] = {
            "state": "unresolved",
            "worktree_id": caller_id,
        }
        if machine_hint:
            caller_state["machine"] = machine_hint
        if project_hint:
            caller_state["repo"] = project_hint

        matches: list[dict[str, Any]] = []
        if machine_hint and project_hint:
            candidate = qualified_index.get((machine_hint, project_hint, caller_id))
            if candidate is not None:
                matches = [candidate]
        if not matches:
            matches = id_index.get(caller_id, [])

        if len(matches) == 1:
            host = matches[0]
            caller_state = {
                "state": "resolved",
                "worktree_id": caller_id,
                "machine": host.get("machine"),
                "platform": host.get("platform"),
                "repo": host.get("repo"),
                "path": host.get("path"),
                "status": host.get("status"),
            }
            host_status = str(host.get("status") or "")
            if host_status in _HOST_TERMINAL_STATUSES:
                row["delegate_finalizable"] = True
                row["delegate_finalizable_reason"] = f"host-{host_status}"
            else:
                row["delegate_finalizable"] = False
                row["delegate_finalizable_reason"] = (
                    f"host-{host_status}" if host_status else "host-unknown"
                )
            host.setdefault("delegates", []).append({
                "worktree_id": row.get("id"),
                "machine": row.get("machine"),
                "platform": row.get("platform"),
                "path": row.get("path"),
                "status": row.get("status"),
                "finalizable": bool(row.get("delegate_finalizable")),
                "reason": row.get("delegate_finalizable_reason"),
            })
        elif len(matches) > 1:
            caller_state["state"] = "ambiguous"
            caller_state["candidates"] = [
                {
                    "worktree_id": candidate.get("id"),
                    "machine": candidate.get("machine"),
                    "platform": candidate.get("platform"),
                    "repo": candidate.get("repo"),
                    "path": candidate.get("path"),
                    "status": candidate.get("status"),
                }
                for candidate in matches
            ]
            row["delegate_finalizable"] = False
            row["delegate_finalizable_reason"] = "host-ambiguous"
        else:
            expected = (
                _expected_host_key(caller_id, reachable_hosts or {})
                if reachable_hosts
                else None
            )
            if expected is not None:
                caller_state["machine"] = expected[0]
                if expected[1]:
                    caller_state["platform"] = expected[1]
                if reachable_hosts.get(expected) is True:
                    caller_state["state"] = "gone"
                    row["delegate_finalizable"] = True
                    row["delegate_finalizable_reason"] = "host-gone"
                else:
                    caller_state["state"] = "unreachable"
                    row["delegate_finalizable"] = False
                    row["delegate_finalizable_reason"] = "host-unreachable"
            else:
                row["delegate_finalizable"] = False
                row["delegate_finalizable_reason"] = "host-unresolved"

        row["caller_state"] = caller_state

    return worktrees


def _fleet_snapshot(
    *,
    project: str,
    timeout: float,
    include_all: bool,
) -> list[dict[str, Any]]:
    config = cfg.load_config()
    extra_args = ["--all"] if include_all else []
    rows = [
        list_views_cli._probe_host(
            machine_key,
            env_name,
            alias,
            shell,
            is_local,
            project=project,
            extra_args=extra_args,
            timeout=timeout,
        )
        for machine_key, env_name, alias, shell, is_local
        in list(list_views_cli._fleet_targets(config))
    ]
    reachable_hosts = {
        (str(row.get("machine") or ""), str(row.get("env") or "")): bool(row.get("reachable"))
        for row in rows
    }
    worktrees = [
        dict(worktree)
        for row in rows
        for worktree in row.get("worktrees", [])
        if isinstance(worktree, dict)
    ]
    annotate_delegate_graph(worktrees, reachable_hosts=reachable_hosts)
    worktrees_by_id = {
        str(worktree.get("id")): worktree
        for worktree in worktrees
        if isinstance(worktree.get("id"), str)
    }
    for row in rows:
        resolved = []
        for worktree in row.get("worktrees", []):
            if not isinstance(worktree, dict):
                continue
            worktree_id = worktree.get("id")
            if isinstance(worktree_id, str) and worktree_id in worktrees_by_id:
                resolved.append(worktrees_by_id[worktree_id])
        row["worktrees"] = resolved
    return rows


def _delegate_candidates(hosts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for host in hosts:
        for worktree in host.get("worktrees", []):
            if not isinstance(worktree, dict):
                continue
            if "caller_worktree" not in worktree:
                continue
            candidates.append(worktree)
    return candidates


def _remote_finalize_cmd(project: str, worktree_id: str, shell: str) -> str:
    inner = shlex.join([
        project, "finalize", "--json", "--worktree-id", worktree_id,
    ])
    if shell == "pwsh":
        encoded = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")
        return f"pwsh -NoProfile -WindowStyle Hidden -EncodedCommand {encoded}"
    return f"bash -lc {shlex.quote(inner)}"


def _finalize_target(
    target: dict[str, Any],
    *,
    project: str,
    timeout: float,
    host_index: dict[tuple[str, str], tuple[str, str, bool]],
) -> dict[str, Any]:
    worktree_id = str(target.get("id") or "")
    machine = str(target.get("machine") or "")
    platform = str(target.get("platform") or "")
    result: dict[str, Any] = {
        "worktree_id": worktree_id,
        "machine": machine,
        "platform": platform,
    }
    host = host_index.get((machine, platform))
    if host is None:
        result["ok"] = False
        result["error"] = "no fleet target matched this delegate host"
        return result
    alias, shell, is_local = host
    try:
        if is_local:
            proc = subprocess.run(
                [
                    list_views_cli._local_binstub(project),
                    "finalize",
                    "--json",
                    "--worktree-id",
                    worktree_id,
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 4,
            )
        else:
            proc = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    f"ConnectTimeout={max(1, int(timeout))}",
                    alias,
                    _remote_finalize_cmd(project, worktree_id, shell),
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 4,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        result["ok"] = False
        result["error"] = str(exc)[:500]
        return result
    if proc.returncode != 0:
        result["ok"] = False
        result["error"] = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()[:500]
        return result
    payload = _parse_json_object(proc.stdout)
    if payload is None:
        result["ok"] = False
        result["error"] = "unparseable finalize output"
        return result
    result.update(payload)
    result["ok"] = bool(payload.get("success", payload.get("ok")))
    return result


def run_delegates(argv: list[str]) -> int:
    """Entry point for the ``delegates`` verb (manual dispatch)."""
    parser = argparse.ArgumentParser(
        prog="agent-worktrees delegates",
        description="Resolve delegate/caller host state across the fleet and "
        "surface which delegate worktrees became finalizable because their "
        "host is finalized/completed/gone.",
    )
    parser.add_argument("--json", action="store_true", help="JSON output (default: human summary)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=list_views_cli.DEFAULT_TIMEOUT,
        help=f"Per-host SSH connect timeout in seconds, > 0 (default {list_views_cli.DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Finalize every currently finalizable delegate worktree. Dry-run/list by default.",
    )
    args = parser.parse_args(argv)

    if not (args.timeout > 0) or args.timeout == float("inf"):
        parser.error(f"--timeout must be a finite number > 0 (got {args.timeout!r})")

    try:
        project = cfg.project_name()
    except Exception:
        project = "agent-worktrees"

    try:
        hosts = _fleet_snapshot(project=project, timeout=args.timeout, include_all=True)
    except Exception as exc:
        print(f"agent-worktrees delegates: could not build fleet snapshot: {exc}", file=sys.stderr)
        return 1

    candidates = _delegate_candidates(hosts)
    eligible = [
        row for row in candidates
        if bool(row.get("delegate_finalizable"))
        and str(row.get("status") or "") != "finalized"
    ]
    blocked = [row for row in candidates if row not in eligible]
    payload: dict[str, Any] = {
        "version": 1,
        "checked": len(candidates),
        "eligible": eligible,
        "blocked": blocked,
        "hosts": hosts,
    }

    if args.execute:
        try:
            config = cfg.load_config()
            host_index = {
                (machine_key, env_name): (alias, shell, is_local)
                for machine_key, env_name, alias, shell, is_local
                in list(list_views_cli._fleet_targets(config))
            }
        except Exception as exc:
            print(f"agent-worktrees delegates: could not load fleet targets: {exc}", file=sys.stderr)
            return 1
        executed = [
            _finalize_target(
                row,
                project=project,
                timeout=args.timeout,
                host_index=host_index,
            )
            for row in eligible
        ]
        payload["executed"] = executed
        payload["errors"] = [row for row in executed if not row.get("ok")]
        payload["changed"] = [row for row in executed if row.get("ok")]

    if args.json:
        print(json.dumps(payload, default=str))
        return 0

    print(
        f"delegates: checked {len(candidates)} delegate worktree(s); "
        f"{len(eligible)} finalizable, {len(blocked)} not finalizable."
    )
    for row in eligible:
        caller = row.get("caller_state") or {}
        print(
            f"  + {row.get('id')} ({row.get('machine')}/{row.get('platform')}): "
            f"{row.get('delegate_finalizable_reason')} via "
            f"{caller.get('worktree_id')} [{caller.get('state')}]"
        )
    for row in blocked:
        caller = row.get("caller_state") or {}
        print(
            f"  - {row.get('id')} ({row.get('machine')}/{row.get('platform')}): "
            f"{row.get('delegate_finalizable_reason')} via "
            f"{caller.get('worktree_id')} [{caller.get('state')}]"
        )
    if args.execute:
        changed = payload.get("changed", [])
        errors = payload.get("errors", [])
        print(
            f"\nfinalize: executed {len(changed) + len(errors)} worktree(s); "
            f"{len(changed)} succeeded, {len(errors)} failed."
        )
        for row in changed:
            print(f"  ✓ {row['worktree_id']}")
        for row in errors:
            print(f"  ! {row['worktree_id']}: {row.get('error', 'finalize failed')}")
    return 0
