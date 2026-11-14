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
import copy
import json
import math
import shlex
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Collection
from typing import Any

from agent_procutil import no_window_kwargs

from . import config as cfg
from . import fleet_reporting
from . import list_views_cli
from . import tracking
from .worktree_machine_identity import execution_machine_from_fields, projected_machine_fields

_HOST_TERMINAL_STATUSES = frozenset({"complete", "completed", "finalized"})
_ROSTER_ADVISORY_REASON = "host-roster-advisory"
_UNNAMED_REASON = "host-reporting-unavailable"
_UNPROVEN_ROSTER_REASON = "roster not proven current within its freshness bound when evaluated"


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


def _qualified_host_key(
    machine: str | None,
    reachable_hosts: dict[tuple[str, str], bool],
) -> tuple[str, str] | None:
    """Locate an explicit caller selector; allocation IDs carry no host proof."""
    matches = [key for key in reachable_hosts if key[0] == machine] if machine else []
    return matches[0] if len(matches) == 1 else None


def annotate_delegate_graph(
    worktrees: list[dict[str, Any]],
    *,
    reachable_hosts: dict[tuple[str, str], bool] | None = None,
    current_only: bool = False,
    authoritative_hosts: Collection[tuple[str, str]] | None = None,
    advisory_rows: Collection[int] = frozenset(),
    unnamed_rows: Collection[int] = frozenset(),
    incomplete_unnamed_sources: bool = False,
    row_hosts: dict[int, tuple[tuple[str, str], ...]] | None = None,
) -> list[dict[str, Any]]:
    """Annotate ``list``/``fleet`` rows with caller/host + delegate state.

    ``authoritative_hosts`` (fleet reads) names the reachable hosts whose
    roster is a complete current publication; any other reachable host can omit a
    caller that exists now, so a caller expected there is ``unknown``, never
    ``gone``. ``advisory_rows`` holds the ``id()`` of rows read from such a
    host; a terminal caller resolved to one never authorizes finalization."""
    id_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    qualified_index: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in worktrees:
        worktree_id = row.get("id")
        if not isinstance(worktree_id, str) or not worktree_id:
            continue
        id_index[worktree_id].append(row)
        project = row.get("repo")
        if isinstance(project, str) and project:
            machines = {
                name for name in (execution_machine_from_fields(row), row.get("machine"))
                if isinstance(name, str) and name
            }
            for machine in machines:
                qualified_index[(machine, project, worktree_id)].append(row)

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

        uncertainty = _UNNAMED_REASON if machine_hint and (
            incomplete_unnamed_sources or any(
                id(candidate) in unnamed_rows and candidate.get("repo") in (None, "", project_hint)
                for candidate in id_index.get(caller_id, [])
            )
        ) else None
        matches: list[dict[str, Any]] = []
        if machine_hint and project_hint:
            matches = qualified_index.get((machine_hint, project_hint, caller_id), [])
        else:
            matches = id_index.get(caller_id, [])

        if (
            uncertainty is None and machine_hint and len(matches) == 1
            and authoritative_hosts is not None and row_hosts is not None
        ):
            observed = row_hosts.get(id(matches[0]), ())
            if any(
                key[0] == machine_hint and key not in observed
                and (reachable is not True or key not in authoritative_hosts)
                for key, reachable in (reachable_hosts or {}).items()
            ):
                uncertainty = _ROSTER_ADVISORY_REASON
        if uncertainty is not None:
            caller_state["state"] = "unknown"
            row["caller_state"] = caller_state
            row["delegate_finalizable"] = False
            row["delegate_finalizable_reason"] = uncertainty
            continue

        if len(matches) == 1:
            host = matches[0]
            caller_state = {
                "state": "resolved",
                "worktree_id": caller_id,
                **projected_machine_fields(host),
                "repo": host.get("repo"),
                "path": host.get("path"),
                "status": host.get("status"),
            }
            host_status = str(host.get("status") or "")
            if host_status in _HOST_TERMINAL_STATUSES and id(host) in advisory_rows:
                row["delegate_finalizable"] = False
                row["delegate_finalizable_reason"] = _ROSTER_ADVISORY_REASON
            elif host_status in _HOST_TERMINAL_STATUSES:
                row["delegate_finalizable"] = True
                row["delegate_finalizable_reason"] = f"host-{host_status}"
            else:
                row["delegate_finalizable"] = False
                row["delegate_finalizable_reason"] = (
                    f"host-{host_status}" if host_status else "host-unknown"
                )
            delegate = {
                "worktree_id": row.get("id"),
                **projected_machine_fields(row),
                "path": row.get("path"),
                "status": row.get("status"),
                "finalizable": bool(row.get("delegate_finalizable")),
                "reason": row.get("delegate_finalizable_reason"),
            }
            if not current_only:
                host.setdefault("delegates", []).append(delegate)
        elif len(matches) > 1:
            caller_state["state"] = "ambiguous"
            caller_state["candidates"] = [
                {
                    "worktree_id": candidate.get("id"),
                    **projected_machine_fields(candidate),
                    "repo": candidate.get("repo"),
                    "path": candidate.get("path"),
                    "status": candidate.get("status"),
                }
                for candidate in (matches[:8] if current_only else matches)
            ]
            if current_only:
                caller_state["candidate_count"] = len(matches)
            row["delegate_finalizable"] = False
            row["delegate_finalizable_reason"] = "host-ambiguous"
        else:
            expected = (
                _qualified_host_key(machine_hint, reachable_hosts or {})
                if reachable_hosts
                else None
            )
            if expected is not None:
                caller_state["machine"] = expected[0]
                if expected[1]:
                    caller_state["platform"] = expected[1]
                if reachable_hosts.get(expected) is not True:
                    caller_state["state"] = "unreachable"
                    row["delegate_finalizable"] = False
                    row["delegate_finalizable_reason"] = "host-unreachable"
                elif project_hint != row.get("repo") or (
                    authoritative_hosts is not None and expected not in authoritative_hosts
                ):
                    caller_state["state"] = "unknown"
                    row["delegate_finalizable"] = False
                    row["delegate_finalizable_reason"] = _ROSTER_ADVISORY_REASON
                else:
                    caller_state["state"] = "gone"
                    row["delegate_finalizable"] = True
                    row["delegate_finalizable_reason"] = "host-gone"
            else:
                row["delegate_finalizable"] = False
                row["delegate_finalizable_reason"] = "host-unresolved"

        row["caller_state"] = caller_state

    return worktrees


def delegate_graph_overlays(
    worktrees: list[dict[str, Any]],
    *,
    reachable_hosts: dict[tuple[str, str], bool] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return only the additive delegate/caller overlay keyed by worktree id."""
    rows = [dict(row) for row in worktrees]
    annotate_delegate_graph(rows, reachable_hosts=reachable_hosts)
    overlays: dict[str, dict[str, Any]] = {}
    for row in rows:
        worktree_id = row.get("id")
        if not isinstance(worktree_id, str) or not worktree_id:
            continue
        overlay = {
            key: row[key]
            for key in (
                "caller_state",
                "delegate_finalizable",
                "delegate_finalizable_reason",
                "delegates",
            )
            if key in row
        }
        if overlay:
            overlays[worktree_id] = overlay
    return overlays


def _finite_seconds(value: Any) -> bool:
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and value >= 0
    )


def host_roster_is_current(host: dict[str, Any], *, now: float | None = None) -> bool:
    """True only for a reachable fleet host row read from a current publication.

    A saved (``availability.status: "advisory"``) or stale-labeled roster, or
    a reply without current-roster provenance (as from an older runtime or a
    cached listing), may omit or misstate a caller that exists now. A passive
    read is also current only while the resident proves it republished the
    roster within its freshness bound (``projection.freshness``): an older
    runtime that cannot prove this stays non-authoritative. That age was
    measured when the host answered, so the local time elapsed since its probe
    started (``now`` minus the row's probe stamp) counts against the bound too;
    a row without that stamp is not provably current."""
    projection = host.get("projection")
    if not (
        host.get("reachable") is True
        and host.get("availability") is None
        and isinstance(projection, dict)
        and projection.get("quality") == "current"
    ):
        return False
    freshness = projection.get("freshness")
    started = host.get(list_views_cli.PROBE_STARTED_KEY)
    if not isinstance(freshness, dict) or type(started) not in (int, float):
        return False
    elapsed = (time.monotonic() if now is None else now) - started
    age, bound = freshness.get("refreshed_age_s"), freshness.get("bound_s")
    return all(_finite_seconds(value) for value in (age, bound, elapsed)) and age + elapsed <= bound


def _host_roster_is_complete(host: dict[str, Any]) -> bool:
    """A filtered or malformed selection cannot prove a caller absent."""
    projection, rows = host.get("projection"), host.get("worktrees")
    if not isinstance(projection, dict) or not isinstance(rows, list) or not all(
        isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"] for row in rows
    ):
        return False
    total, selected = projection.get("roster_count"), projection.get("selected_count")
    return (
        type(total) is int and type(selected) is int and total == selected == len(rows)
        and len({row["id"] for row in rows}) == len(rows)
    )


def annotate_fleet_hosts(
    hosts: list[dict[str, Any]], *, now: float | None = None,
) -> list[dict[str, Any]]:
    """Annotate the delegate graph across fleet host rows, in place.

    Only complete rosters current at ``now`` (default: the monotonic
    clock) prove absence. Observed terminal callers also need current coverage
    from competing qualified sources. A reachable roster that is
    not current and carries no ``availability`` label (unproven, or expired
    while later hosts were probed) is labeled stale here, so serialized rows
    -- which drop the local probe clock -- never pass it off as current."""
    now = time.monotonic() if now is None else now
    reachable_hosts: dict[tuple[str, str], bool] = {}
    authoritative_hosts: set[tuple[str, str]] = set()
    advisory_rows: set[int] = set()
    worktrees: list[dict[str, Any]] = []
    duplicate_hosts: set[tuple[str, str]] = set()
    reporter_rows: dict[tuple[str, str], list[int]] = defaultdict(list)
    unnamed_rows: set[int] = set()
    incomplete_unnamed_sources = False
    row_hosts: dict[int, tuple[tuple[str, str], ...]] = {}
    for host in hosts:
        try:
            keys, copies = fleet_reporting.reported_rows(host)
        except ValueError:
            incomplete_unnamed_sources = True
            host.update(reachable=False, worktrees=[], error="malformed reporting context")
            continue
        for key in keys:
            if key in reachable_hosts:
                duplicate_hosts.add(key)
            reachable_hosts[key] = bool(host.get("reachable"))
            reporter_rows[key].extend(id(row) for row in copies)
        row_hosts.update((id(row), keys) for row in copies)
        current = host_roster_is_current(host, now=now)
        complete = current and _host_roster_is_complete(host)
        if not keys:
            unnamed_rows.update(id(row) for row in copies)
            incomplete_unnamed_sources |= not complete
        if complete:
            authoritative_hosts.update(keys)
        if not current and host.get("reachable") is True and host.get("availability") is None:
            host["availability"] = {"status": "stale", "reason": _UNPROVEN_ROSTER_REASON}
        if not current:
            advisory_rows.update(id(row) for row in copies)
        worktrees.extend(copies)
        host["worktrees"] = copies
    authoritative_hosts.difference_update(duplicate_hosts)
    for key in duplicate_hosts:
        advisory_rows.update(reporter_rows[key])
    annotate_delegate_graph(
        worktrees,
        reachable_hosts=reachable_hosts,
        authoritative_hosts=authoritative_hosts,
        advisory_rows=advisory_rows,
        unnamed_rows=unnamed_rows,
        incomplete_unnamed_sources=incomplete_unnamed_sources,
        row_hosts=row_hosts,
    )
    return hosts


def _fleet_snapshot(
    *,
    project: str,
    timeout: float,
    include_all: bool,
) -> list[dict[str, Any]]:
    config = cfg.load_config()
    # Passive like `fleet`: the snapshot never starts, awaits or wakes a host's
    # status-monitor, and only a passive reply carries the freshness proof that
    # `host_roster_is_current` requires before a caller can authorize finalization.
    extra_args = ["--passive", *(["--all"] if include_all else [])]
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
    annotate_fleet_hosts(rows)
    for row in rows:
        row["worktrees"] = [
            worktree for worktree in row["worktrees"] if isinstance(worktree.get("id"), str)
        ]
    return rows


def _still_finalizable(
    hosts: list[dict[str, Any]], target: dict[str, Any], *, source_key: tuple[str, str],
) -> bool:
    """Re-derive ``target``'s verdict from the snapshot at this instant, just
    before finalizing it: a caller roster that authorized it at annotation may
    have outlived its freshness bound since."""
    if sum(fleet_reporting.source_key(host) == source_key for host in hosts) != 1:
        return False
    return any(
        row.get("id") == target.get("id") and row.get("repo") == target.get("repo")
        and row.get("delegate_finalizable") is True
        for host in annotate_fleet_hosts(copy.deepcopy(hosts))
        if fleet_reporting.source_key(host) == source_key
        for row in host.get("worktrees") or []
    )


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
    source_key: tuple[str, str],
) -> dict[str, Any]:
    worktree_id = str(target.get("id") or "")
    machine = execution_machine_from_fields(target) or ""
    platform = str(target.get("platform") or "")
    result: dict[str, Any] = {
        "worktree_id": worktree_id,
        "machine": machine,
        "platform": platform,
        "source": {"machine": source_key[0], "env": source_key[1]},
    }
    host = host_index.get(source_key)
    if host is None:
        result["ok"] = False
        result["error"] = "no fleet target matched this delegate host"
        return result
    alias, shell, is_local = host
    try:
        if is_local:
            proc = subprocess.run(
                [
                    *list_views_cli._local_project_argv(project),
                    "finalize",
                    "--json",
                    "--worktree-id",
                    worktree_id,
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 4,
                **no_window_kwargs(),
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
                **no_window_kwargs(),
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
    sources = {
        id(row): fleet_reporting.source_key(host)
        for host in hosts for row in host.get("worktrees", [])
    }
    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for row in candidates:
        if bool(row.get("delegate_finalizable")) and str(row.get("status") or "") != "finalized":
            eligible.append(row)
        else:
            blocked.append(row)
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
                source_key=sources[id(row)],
            )
            if _still_finalizable(hosts, row, source_key=sources[id(row)])
            else {
                "worktree_id": row.get("id"),
                "machine": execution_machine_from_fields(row) or "",
                "platform": str(row.get("platform") or ""),
                "ok": False,
                "reason": _ROSTER_ADVISORY_REASON,
                "error": "caller roster is no longer provably current; not finalized",
            }
            for row in eligible
        ]
        payload["executed"] = executed
        payload["errors"] = [row for row in executed if not row.get("ok")]
        payload["changed"] = [row for row in executed if row.get("ok")]

    if args.json:
        print(json.dumps(
            {**payload, "hosts": list_views_cli.public_host_rows(hosts)}, default=str,
        ))
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
