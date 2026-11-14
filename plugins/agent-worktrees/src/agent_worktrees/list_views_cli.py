"""Fleet-wide worktree listing across every reachable machine (agent-
worktrees-fleet-flows Phase 1, the downstream tracker).

``agent-worktrees fleet`` runs exactly one ``list --json`` per SSH target
(never per-worktree, per #2732's contract) and merges the results into a
single JSON payload with ``machine``/``env`` tagged on every row. An
unreachable/offline host degrades to a partial result (marked
``reachable: false``) instead of failing the whole call.

Each row is the host's own ``list --json`` record, so it carries both the
lifecycle ``status`` (active/complete/finalized/...) and the derived
``state`` (active/wip/dirty/convo/unused/completed). Live work is
``state == "active"``; many ``status == "active"`` rows are idle. The call
covers the selected project only (each host runs ``agent-worktrees
--project <project> list``), so an all-projects inventory runs it once per
project.

Manually dispatched from ``__main__.py`` (mirrors ``lease``/``pr``'s own
top-level verb pattern -- see the ``if args_list[0] == "lease":`` block) so
this module owns its own argparse and never grows the already-at-its-
grandfathered-ceiling ``__main__.py``.

SSH fan-out mirrors ``claimant.py``'s ``_remote_claimant_alive`` /
``codename_reverse_lookup.py``'s ``_probe_machine`` shape (BatchMode SSH,
pwsh-EncodedCommand on Windows, ``bash -lc`` elsewhere) -- reuses
``claimant.resolve_machine_ssh`` is deliberately NOT used here because it
only resolves the *first* ready SSH environment per machine key, whereas a
machine like Atlas-Core carries two independently-reachable environments
(Windows + WSL) that must each be its own fleet row.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import time

from agent_procutil import no_window_kwargs

from . import config as cfg

#: Default per-host SSH timeout (seconds). Kept short -- an unreachable host
#: must degrade the fleet call to a partial result, never stall it.
DEFAULT_TIMEOUT = 15.0

#: Process-local monotonic time a host's probe started. A resident measured
#: ``refreshed_age_s`` no earlier, so its age plus the time elapsed since this
#: stamp bounds the roster's age now. Never serialized (:func:`public_host_rows`).
PROBE_STARTED_KEY = "_probe_started_monotonic"


def public_host_rows(rows: list[dict]) -> list[dict]:
    """Fleet host rows without the process-local probe clock."""
    return [{key: value for key, value in row.items() if key != PROBE_STARTED_KEY} for row in rows]


def _fleet_targets(config):
    """Yield ``(machine_key, env_name, alias, shell, is_local)`` for every
    Copilot-enabled, ssh-ready machine x environment in ``machines.yaml``,
    plus the current machine's own environment (``is_local=True``, run
    locally via :func:`_local_project_argv` rather than over a loopback SSH
    hop -- a child process, not literally in-process).

    A machine with no per-environment alias falls back to its top-level
    ``alias`` (one row, ``env_name=""``) -- mirrors
    ``claimant.resolve_machine_ssh``'s own fallback, so a machine relying
    on that shape isn't silently omitted from the fleet (review #3134).

    Takes an already-loaded ``config`` (rather than loading it itself) so a
    genuine config/registry load failure propagates to ``run_fleet`` as a
    reported error, instead of this generator silently yielding zero hosts
    (review #3134) -- ``machines.yaml`` itself may still be missing/
    malformed, which is a real, reportable condition, not swallowed here.
    """
    entries = cfg.load_machines_yaml(config.default_repo.anchor)
    this_machine = config.machine
    this_platform = config.platform
    for key, entry in entries.items():
        if not getattr(entry, "copilot", True) or not entry.ssh_ready:
            continue
        yielded = False
        for env in entry.ssh_environments:
            if not env.alias:
                continue
            env_name = env.name or ""
            shell = env.shell or ("pwsh" if env_name.lower() == "windows" else "bash")
            is_local = key == this_machine and env_name.lower() == this_platform.lower()
            yield key, env_name, env.alias, shell, is_local
            yielded = True
        if not yielded and entry.alias:
            is_local = key == this_machine
            yield key, "", entry.alias, "bash", is_local


#: Set for every child ``list`` so a host whose runtime is missing reports a
#: gap instead of self-provisioning one -- ``fleet`` stays a passive read.
NO_SELFPROVISION_ENV = "AGENT_WORKTREES_NO_SELFPROVISION"

#: The owner command the installer always deploys on every host; project
#: binstubs are thin ``<owner> --project <project>`` wrappers that a
#: registered repository need not have.
OWNER_COMMAND = "agent-worktrees"


def _remote_list_cmd(shell: str, project: str, extra_args: list[str]) -> str:
    """Build the remote command running
    ``agent-worktrees --project <project> list --json``.

    Mirrors ``claimant.py``'s ``_remote_probe_cmd`` (pwsh EncodedCommand on
    Windows, ``bash -lc`` elsewhere) for the same reason: robust against a
    cmd.exe default sshd shell. Uses the owner command with an explicit
    ``--project`` rather than a ``<project>`` binstub, which a registered
    repository on the remote host may not have.

    Every argument is quoted for the target shell (PowerShell single quotes
    or ``shlex``) rather than concatenated -- ``extra_args`` (e.g. a
    ``--tracking-status`` value) and the project name are caller-influenced
    and must never be interpolated into a shell string unescaped (review
    #3134). Both shapes set :data:`NO_SELFPROVISION_ENV` (matching
    ``pr_sweep_transport``) and propagate the command's exit status, so a
    missing runtime or project surfaces as a nonzero exit.
    """
    argv = [OWNER_COMMAND, "--project", project, "list", "--json", *extra_args]
    if shell == "pwsh":
        def quoted(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        # A missing owner command exits 127 with bash's wording, so both
        # shells report it the same way; otherwise the exit status of
        # `list` is propagated unchanged.
        script = (
            "$ProgressPreference='SilentlyContinue';"
            f"$env:{NO_SELFPROVISION_ENV}='1';"
            f"if (-not (Get-Command {quoted(OWNER_COMMAND)} -ErrorAction SilentlyContinue))"
            f" {{ [Console]::Error.WriteLine({quoted(OWNER_COMMAND + ': command not found')}); exit 127 }};"
            f"& {' '.join(map(quoted, argv))};exit $LASTEXITCODE"
        )
        enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return f"pwsh -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand {enc}"
    inner = f"{NO_SELFPROVISION_ENV}=1 " + shlex.join(argv)
    return f"bash -lc {shlex.quote(inner)}"


def _local_project_argv(project: str) -> list[str]:
    """Return the argv prefix that runs a project-scoped command locally.

    Invokes this same runtime as ``python -m agent_worktrees --project
    <project>`` -- what the owner command and every project binstub
    ultimately exec -- instead of a launcher file. Launchers are
    platform-shaped (an extensionless script on POSIX, ``.cmd``/``.ps1``
    shims on Windows), and an argv-list ``subprocess.run`` cannot launch a
    ``.ps1`` or reliably resolve PATHEXT, so a binstub lookup made the local
    fleet row fail on Windows. ``sys.executable`` keeps the child on the
    same interpreter as the caller on every platform, and needs no
    ``<project>`` binstub at all. ``-I`` keeps this passive read from
    importing a shadowing ``agent_worktrees`` out of the caller's working
    directory or a ``PYTHON*`` variable, matching the package's other
    self-invocations.
    """
    return [sys.executable, "-I", "-X", "utf8", "-m", "agent_worktrees", "--project", project]


def _parse_list_envelope(raw: str) -> dict | None:
    """Extract the enveloped ``list --json`` payload (``{"worktrees": [...],
    "projection": {...}, "availability": ...}`` -- see ``emit_current``), or
    None on any parse/shape failure.

    Tolerates surrounding shell/login-banner noise by scanning for the
    JSON **object** boundaries first (``{`` .. last ``}``) -- exactly
    ``claimant.py``'s ``_parse_alive`` / ``codename_reverse_lookup.py``'s
    ``_parse_lookup`` pattern, which every other cross-machine SSH probe
    in this plugin already relies on (review #3134): an SSH session's
    MOTD/profile output would otherwise make a perfectly reachable host
    look unreachable. Deliberately dict-only (not also scanning for a
    top-level ``[``/``]``) -- a stray ``[``/``]`` in banner text (e.g. a
    login timestamp's "[UTC]") would otherwise widen the scanned range
    into the noise itself and break parsing, and the real payload is
    never a bare top-level array.
    """
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("worktrees"), list):
        return None
    return data


def _parse_list_payload(raw: str) -> list | None:
    """The ``worktrees`` array of :func:`_parse_list_envelope`, or None."""
    envelope = _parse_list_envelope(raw)
    return None if envelope is None else envelope["worktrees"]


def _probe_host(
    machine_key: str, env_name: str, alias: str, shell: str, is_local: bool,
    *, project: str, extra_args: list[str], timeout: float,
) -> dict:
    """Run ``list --json`` on one host (locally or over SSH), returning a
    fleet row. Every failure mode (unreachable, timeout, unparseable
    output) degrades to ``reachable: false`` with an ``error`` message --
    never raises, so one bad host never aborts the whole fleet call.

    ``worktrees`` is always present (``[]`` when unreachable) so a
    consumer never has to special-case a missing key alongside
    ``reachable: false`` (review #3134)."""
    row: dict = {
        "machine": machine_key, "env": env_name,
        "reachable": False, "worktrees": [],
    }
    # stdin is closed so neither child can read (or block on) the caller's
    # stdin. A local timeout ends only the local client: some SSH servers
    # keep the remote command running after the client disconnects.
    started = time.monotonic()
    try:
        if is_local:
            cmd = [*_local_project_argv(project), "list", "--json", *extra_args]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout + 4,
                stdin=subprocess.DEVNULL, env={**os.environ, NO_SELFPROVISION_ENV: "1"},
                **no_window_kwargs(),
            )
        else:
            remote_cmd = _remote_list_cmd(shell, project, extra_args)
            proc = subprocess.run(
                ["ssh", "-o", "BatchMode=yes",
                 "-o", f"ConnectTimeout={max(1, int(timeout))}",
                 alias, remote_cmd],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout + 4,
                stdin=subprocess.DEVNULL, **no_window_kwargs(),
            )
    except (subprocess.SubprocessError, OSError) as exc:
        row["error"] = str(exc)[:500]
        return row
    if proc.returncode != 0:
        row["error"] = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()[:500]
        return row
    envelope = _parse_list_envelope(proc.stdout)
    if envelope is None:
        row["error"] = "unparseable or unexpected `list --json` output"
        return row
    if "reporting_context" in envelope:
        from . import worktrees_reporting

        try:
            worktrees_reporting.validate(envelope["reporting_context"])
        except ValueError:
            row["error"] = "malformed `list --json` reporting context"
            return row
        row["reporting_context"] = envelope["reporting_context"]
    row["reachable"] = True
    row[PROBE_STARTED_KEY] = started
    row["worktrees"] = envelope["worktrees"]
    # Provenance stays with the rows: `availability` is set (status
    # "advisory") when the host served its saved roster instead of a
    # current resident publication; `projection.observed_at` dates it. Any
    # availability label is kept so delegate inference fails closed on it.
    if isinstance(envelope.get("projection"), dict):
        row["projection"] = envelope["projection"]
    if envelope.get("availability") is not None:
        row["availability"] = envelope["availability"]
    return row


def run_fleet(argv: list[str]) -> int:
    """Entry point for the ``fleet`` verb (manual dispatch from ``__main__.py``)."""
    parser = argparse.ArgumentParser(
        prog="agent-worktrees fleet",
        description="Aggregate one project's `list --json` across every "
        "reachable machine x environment -- one invocation per SSH target, "
        "never per-worktree (#2732). Covers the selected project only (the "
        "current or `--project <project>` one), not every project on a host. "
        "A host that is unreachable, times out, lacks the owner command or "
        "exits nonzero degrades to a partial row (`reachable: false` with an "
        "`error`), which means unknown, not zero worktrees. Each host is read "
        "with `list --json --passive`: one bounded, demand-free resident "
        "attempt that never starts, waits for or wakes a host's status-monitor, "
        "otherwise that host's "
        "saved roster labeled by the row's `availability` (status advisory) "
        "and `projection.observed_at`; a resident roster not republished "
        "within the resident's freshness bound (`projection.freshness`) is "
        "labeled `availability` status stale; a host without a saved roster, or whose "
        "older runtime rejects `--passive`, is a partial row. Registration is "
        "not checked per host: confirm a host's empty list against that "
        "host's `repos list --strict --json`. Rows carry both the lifecycle "
        "`status` and the derived `state`; live work is `state == \"active\"`.",
    )
    parser.add_argument("--json", action="store_true", help="JSON output (default: human summary)")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT,
        help=f"Per-host timeout in seconds, > 0 (default {DEFAULT_TIMEOUT}): the SSH "
        "connect timeout, after which the local client also gives up shortly. "
        "It does not stop a remote command on SSH servers that keep it running "
        "after the client disconnects",
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
    # Fail fast on a bad --timeout (0/negative/NaN/inf) -- an uncaught one
    # would otherwise surface much later as a confusing int()/subprocess
    # error instead of a clear usage message (review #3134).
    if not (args.timeout > 0) or args.timeout == float("inf"):
        parser.error(f"--timeout must be a finite number > 0 (got {args.timeout!r})")

    try:
        config = cfg.load_config()
    except Exception as exc:
        # A genuinely broken config/registry is a reportable failure, not a
        # silent "0 hosts" fleet result (review #3134).
        print(f"agent-worktrees fleet: could not load config: {exc}", file=sys.stderr)
        return 1

    try:
        project = cfg.project_name()
    except Exception:
        project = "agent-worktrees"

    # Every host read is passive: it never starts or awaits a status-monitor.
    extra_args: list[str] = ["--passive"]
    if args.tracking_status:
        extra_args += ["--tracking-status", args.tracking_status]
    if args.all:
        extra_args.append("--all")

    try:
        targets = list(_fleet_targets(config))
    except Exception as exc:
        print(f"agent-worktrees fleet: could not load machines.yaml: {exc}", file=sys.stderr)
        return 1

    rows = [
        _probe_host(
            machine_key, env_name, alias, shell, is_local,
            project=project, extra_args=extra_args, timeout=args.timeout,
        )
        for machine_key, env_name, alias, shell, is_local in targets
    ]
    from . import delegate_cli

    # Only a host's current publication can prove a caller gone or terminal.
    delegate_cli.annotate_fleet_hosts(rows)

    if args.json:
        print(json.dumps({"version": 1, "hosts": public_host_rows(rows)}, default=str))
        return 0

    total = 0
    for row in rows:
        label = f"{row['machine']} ({row['env']})" if row["env"] else row["machine"]
        if not row.get("reachable"):
            print(f"  {label:30} UNREACHABLE -- {row.get('error', 'unknown error')}")
            continue
        n = len(row.get("worktrees", []))
        total += n
        availability = row.get("availability")
        status = availability.get("status") if isinstance(availability, dict) else None
        # Anything not provably current (including an older resident's
        # unlabeled reply) is qualified, never shown as a plain count.
        saved = (
            "" if delegate_cli.host_roster_is_current(row)
            else " (saved roster)" if status == "advisory" else " (stale roster)"
        )
        print(f"  {label:30} {n} worktree(s){saved}")
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
