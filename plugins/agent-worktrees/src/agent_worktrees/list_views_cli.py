"""Fleet-wide worktree listing across every reachable machine (agent-
worktrees-fleet-flows Phase 1, aperture-labs #2740).

``agent-worktrees fleet`` runs exactly one ``list --json`` per SSH target
(never per-worktree, per #2732's contract) and merges the results into a
single JSON payload with ``machine``/``env`` tagged on every row. An
unreachable/offline host degrades to a partial result (marked
``reachable: false``) instead of failing the whole call.

Manually dispatched from ``__main__.py`` (mirrors ``lease``/``pr``'s own
top-level verb pattern -- see the ``if args_list[0] == "lease":`` block) so
this module owns its own argparse and never grows the already-at-its-
grandfathered-ceiling ``__main__.py``.

SSH fan-out mirrors ``claimant.py``'s ``_remote_claimant_alive`` /
``codename_reverse_lookup.py``'s ``_probe_machine`` shape (BatchMode SSH,
pwsh-EncodedCommand on Windows, ``bash -lc`` elsewhere) -- reuses
``claimant.resolve_machine_ssh`` is deliberately NOT used here because it
only resolves the *first* ready SSH environment per machine key, whereas a
machine like Lambda-Core carries two independently-reachable environments
(Windows + WSL) that must each be its own fleet row.
"""

from __future__ import annotations

import argparse
import base64
import json
import shlex
import subprocess
from pathlib import Path

from . import config as cfg

#: Default per-host SSH timeout (seconds). Kept short -- an unreachable host
#: must degrade the fleet call to a partial result, never stall it.
DEFAULT_TIMEOUT = 15.0


def _fleet_targets():
    """Yield ``(machine_key, env_name, alias, shell, is_local)`` for every
    Copilot-enabled, ssh-ready machine x environment in ``machines.yaml``,
    plus the current machine's own environment (``is_local=True``, run
    locally via the binstub rather than over a loopback SSH hop -- not
    literally in-process).
    """
    try:
        config = cfg.load_config()
        entries = cfg.load_machines_yaml(config.default_repo.anchor)
    except Exception:
        return
    this_machine = config.machine
    this_platform = config.platform
    for key, entry in entries.items():
        if not getattr(entry, "copilot", True) or not entry.ssh_ready:
            continue
        for env in entry.ssh_environments:
            if not env.alias:
                continue
            env_name = env.name or ""
            shell = env.shell or ("pwsh" if env_name.lower() == "windows" else "bash")
            is_local = key == this_machine and env_name.lower() == this_platform.lower()
            yield key, env_name, env.alias, shell, is_local


def _remote_list_cmd(shell: str, project: str, extra_args: list[str]) -> str:
    """Build the remote command string invoking ``<project> list --json``.

    Mirrors ``claimant.py``'s ``_remote_probe_cmd`` (pwsh EncodedCommand on
    Windows, ``bash -lc`` elsewhere) for the same reason: robust against a
    cmd.exe default sshd shell.

    Builds the inner command as a properly quoted argv (``shlex.join``)
    rather than naive string concatenation -- ``extra_args`` (e.g. a
    ``--tracking-status`` value) is caller-influenced and must never be
    interpolated into a shell string unescaped (review #3134).
    """
    inner = shlex.join([project, "list", "--json", *extra_args])
    if shell == "pwsh":
        enc = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")
        return f"pwsh -NoProfile -WindowStyle Hidden -EncodedCommand {enc}"
    return f"bash -lc {shlex.quote(inner)}"


def _local_binstub(project: str) -> str:
    """Resolve the local project binstub path, falling back to PATH lookup."""
    binstub = Path.home() / ".local" / "bin" / project
    return str(binstub) if binstub.exists() else project


def _parse_list_payload(raw: str) -> list | None:
    """Extract the ``worktrees`` array from a ``list --json`` payload
    (enveloped ``{"worktrees": [...]}`` or a flat list), or None on any
    parse/shape failure."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    worktrees = data.get("worktrees") if isinstance(data, dict) else data
    return worktrees if isinstance(worktrees, list) else None


def _probe_host(
    machine_key: str, env_name: str, alias: str, shell: str, is_local: bool,
    *, project: str, extra_args: list[str], timeout: float,
) -> dict:
    """Run ``list --json`` on one host (locally or over SSH), returning a
    fleet row. Every failure mode (unreachable, timeout, unparseable
    output) degrades to ``reachable: false`` with an ``error`` message --
    never raises, so one bad host never aborts the whole fleet call."""
    row: dict = {"machine": machine_key, "env": env_name, "reachable": False}
    try:
        if is_local:
            cmd = [_local_binstub(project), "list", "--json", *extra_args]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout + 4,
            )
        else:
            remote_cmd = _remote_list_cmd(shell, project, extra_args)
            proc = subprocess.run(
                ["ssh", "-o", "BatchMode=yes",
                 "-o", f"ConnectTimeout={max(1, int(timeout))}",
                 alias, remote_cmd],
                capture_output=True, text=True, timeout=timeout + 4,
            )
    except (subprocess.SubprocessError, OSError) as exc:
        row["error"] = str(exc)[:500]
        return row
    if proc.returncode != 0:
        row["error"] = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()[:500]
        return row
    worktrees = _parse_list_payload(proc.stdout)
    if worktrees is None:
        row["error"] = "unparseable or unexpected `list --json` output"
        return row
    row["reachable"] = True
    row["worktrees"] = worktrees
    return row


def run_fleet(argv: list[str]) -> int:
    """Entry point for the ``fleet`` verb (manual dispatch from ``__main__.py``)."""
    parser = argparse.ArgumentParser(
        prog="agent-worktrees fleet",
        description="Aggregate `list --json` across every reachable machine "
        "x environment -- one invocation per SSH target, never per-worktree "
        "(#2732). An unreachable host degrades to a partial result.",
    )
    parser.add_argument("--json", action="store_true", help="JSON output (default: human summary)")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT,
        help=f"Per-host SSH connect timeout in seconds (default {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--tracking-status", default=None,
        choices=["active", "complete", "finalized", "orphaned", "archived", "all"],
        help="Passed through to each host's own `list --json --tracking-status ...`",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Passed through to each host's own `list --json --all`",
    )
    args = parser.parse_args(argv)

    try:
        project = cfg.project_name()
    except Exception:
        project = "agent-worktrees"

    extra_args: list[str] = []
    if args.tracking_status:
        extra_args += ["--tracking-status", args.tracking_status]
    if args.all:
        extra_args.append("--all")

    rows = [
        _probe_host(
            machine_key, env_name, alias, shell, is_local,
            project=project, extra_args=extra_args, timeout=args.timeout,
        )
        for machine_key, env_name, alias, shell, is_local in _fleet_targets()
    ]

    if args.json:
        print(json.dumps({"version": 1, "hosts": rows}, default=str))
        return 0

    total = 0
    for row in rows:
        label = f"{row['machine']} ({row['env']})" if row["env"] else row["machine"]
        if not row.get("reachable"):
            print(f"  {label:30} UNREACHABLE -- {row.get('error', 'unknown error')}")
            continue
        n = len(row.get("worktrees", []))
        total += n
        print(f"  {label:30} {n} worktree(s)")
    print(f"\n{total} worktree(s) across {len(rows)} host(s).")
    return 0


def cmd_list_glance(records) -> int:
    """Render a compact "at a glance" digest of ACTIVE worktrees for agent /
    sub-agent consumption (situational awareness).

    Title + agent-asserted disposition summary only, ranked by disposition
    recency. Worktrees with no recorded disposition are **named, not hidden** --
    honesty about coverage matters more than a tidy list (a blank worktree is a
    real gap, not an absence). Machine-local by design; :func:`run_fleet` above
    unions the fleet-wide equivalent across machines.

    Moved out of ``__main__.py`` alongside ``run_fleet`` (module-size cap;
    ``__main__.py`` had zero headroom against its grandfathered ceiling).
    """
    from . import __main__ as m

    active = [r for r in records if r.status == "active"]

    def _has_disp(r) -> bool:
        return bool((r.title and r.title != "null") or r.summary)

    disposed = [r for r in active if _has_disp(r)]
    blank = [r for r in active if not _has_disp(r)]
    # Rank by disposition recency (status_note_at desc); undated sort last.
    disposed.sort(key=lambda r: r.status_note_at or "", reverse=True)

    try:
        proj = cfg.project_name() or "?"
    except Exception:
        proj = "?"
    print(
        f"Active worktrees on this machine ({proj}): {len(active)} "
        f"-- {len(disposed)} with a recorded disposition, {len(blank)} without"
    )
    if disposed:
        print()
    for r in disposed:
        sid = r.worktree_id[-8:]
        age = m._activity_age_str(r.status_note_at) or "?"
        title = (r.title if (r.title and r.title != "null") else "").strip()
        summ = (r.summary or "").strip().replace("\n", " ")
        if len(summ) > 220:
            summ = summ[:217] + "..."
        line = f"  {sid}  {age:>7}  {title or '(untitled)'}"
        if summ:
            line += f" -- {summ}"
        if r.follow_up:
            line += "  [follow-up]"
        print(line)
    if blank:
        print()
        ids = ", ".join(r.worktree_id[-8:] for r in blank)
        print(f"  No recorded disposition ({len(blank)}): {ids}")
    return 0
