"""Cross-machine codename reverse lookup (pr-attribution-codenames Phase 3).

Phase 1/2 assign a codename to a worktree and let it be resolved LOCALLY
(``resolve --codename`` on the same machine). A codename published to a
public PR (via ``source_attribution: codename``, Phase 4) is meaningless to
an outside reader -- that is the whole point -- but the AUTHOR also needs to
be able to look it back up when the codename's worktree lives on a
**different machine** than the one they are currently at.

**Design (descoped from the original Phase 3 draft).** The original Phase 3
checklist called for a typed cross-machine codename REGISTRY with atomic
reservation semantics (a shared store every machine writes to at assignment
time, read at lookup time). The operator explicitly steered away from that:
the "cross-machine registry" is exactly what it sounds like -- **SSH into
each known machine and read its own local tracking store** -- not a new
shared atomic-reservation primitive. This module implements that: a
best-effort SSH fan-out to every other known, ssh-ready machine, each asked
(via the ``codename-lookup`` CLI endpoint, mirroring ``claimant.py``'s
``claimant-liveness`` pattern) whether ITS local tracking store has a
worktree with the given codename.

**Accepted tradeoff: collision detection, not collision prevention.** Phase
1's local-only collision-retry (``codename.assign_codename``) cannot prevent
two machines from independently generating the same codename concurrently --
only an atomic shared reservation could, and that is exactly the primitive
this descope rejects. A cross-machine scan can still DETECT a collision
after the fact (more than one machine reports a match) and must never
silently resolve to one of them; see :class:`AmbiguousCodenameError`.

**Remote-embody stays fail-closed, not delegated.** When a codename resolves
to a worktree on a DIFFERENT machine, this module deliberately does not
attempt to auto-delegate through agent-bridge or start anything remotely --
callers must report the resolving machine and worktree id and let the
operator/agent decide how to get there (SSH there directly, or a future
agent-bridge dispatch). This is the explicit choice the Phase 3 checklist
asked for ("either delegate ... or fail closed with a clear message. Define
which explicitly.").
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

from . import claimant
from . import config as cfg

#: Escape hatch: set truthy to disable the cross-machine SSH scan entirely
#: (mirrors ``claimant.py``'s ``AGENT_WORKTREES_NO_REMOTE_CLAIMANT``), so a
#: codename with no local match always resolves to "not found anywhere"
#: without a network call.
NO_REMOTE_ENV = "AGENT_WORKTREES_NO_REMOTE_CODENAME"

#: Default per-machine SSH timeout (seconds). A scan probes every other known
#: machine sequentially, so this is deliberately short -- an unreachable
#: machine must not stall the whole lookup for long.
REMOTE_TIMEOUT = 8.0


@dataclass(frozen=True)
class RemoteCodenameMatch:
    """One machine's local tracking store reporting a codename match."""

    machine: str
    worktree_id: str


class AmbiguousCodenameError(ValueError):
    """More than one machine reports the same codename.

    A genuine cross-machine collision (accepted risk of the descoped
    collision-DETECTION-not-PREVENTION design). Callers must surface this to
    the operator, never silently pick one of the matches.
    """

    def __init__(self, codename: str, matches: list[RemoteCodenameMatch]):
        self.codename = codename
        self.matches = matches
        machines = ", ".join(sorted(m.machine for m in matches))
        super().__init__(
            f"Codename '{codename}' matches worktrees on more than one "
            f"machine: {machines}. This is a genuine cross-machine "
            f"collision (codenames are only collision-checked locally, per "
            f"the accepted Phase 3 design tradeoff) -- resolve manually."
        )


def _known_machine_keys(*, exclude: str | None) -> list[str]:
    """Every Copilot-enabled, ssh-ready machine key from ``machines.yaml``,
    excluding *exclude* (typically the local machine). Empty when the
    registry is unavailable -- never raises."""
    try:
        config = cfg.load_config()
        entries = cfg.load_machines_yaml(config.default_repo.anchor)
    except Exception:
        return []
    keys = []
    for key, entry in entries.items():
        if exclude and key == exclude:
            continue
        if not getattr(entry, "copilot", True) or not entry.ssh_ready:
            continue
        keys.append(key)
    return keys


def _remote_probe_cmd(shell: str, project: str, codename: str) -> str:
    """Build the remote command string that runs ``codename-lookup``.

    Mirrors ``claimant.py``'s ``_remote_probe_cmd`` exactly (pwsh
    EncodedCommand on Windows, ``bash -lc`` elsewhere) for the same reason:
    robust against a cmd.exe default sshd shell.
    """
    inner = f"{project} codename-lookup {codename} --json"
    if shell == "pwsh":
        import base64
        enc = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")
        return f"pwsh -NoProfile -WindowStyle Hidden -EncodedCommand {enc}"
    return f"bash -lc '{inner}'"


def _parse_lookup(stdout: str) -> str | None:
    """Extract the matched ``worktree_id`` from a ``codename-lookup`` JSON
    envelope (or None on any parse failure/no-match). Tolerates surrounding
    shell/banner noise by scanning for the JSON object, exactly like
    ``claimant.py``'s ``_parse_alive``."""
    if not stdout:
        return None
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(stdout[start:end + 1])
    except (ValueError, TypeError):
        return None
    if not data.get("found"):
        return None
    worktree_id = data.get("worktree_id")
    return worktree_id if isinstance(worktree_id, str) and worktree_id else None


def _probe_machine(
    machine_key: str, project: str, codename: str, *, timeout: float,
) -> str | None:
    """SSH to *machine_key* and ask its local ``codename-lookup`` endpoint.

    Every failure mode -- unresolvable machine, ssh error, timeout,
    unparseable/negative response -- degrades to ``None`` (no match), never
    raises. Mirrors ``claimant.py``'s ``_remote_claimant_alive`` shape.
    """
    resolved = claimant.resolve_machine_ssh(machine_key)
    if resolved is None:
        return None
    alias, shell = resolved
    remote_cmd = _remote_probe_cmd(shell, project, codename)
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes",
             "-o", f"ConnectTimeout={max(1, int(timeout))}",
             alias, remote_cmd],
            capture_output=True, text=True, timeout=timeout + 4,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return _parse_lookup(proc.stdout)


def resolve_codename_cross_machine(
    codename: str,
    *,
    project: str | None = None,
    timeout: float = REMOTE_TIMEOUT,
) -> list[RemoteCodenameMatch]:
    """Scan every other known, ssh-ready machine's tracking store for
    *codename* under *project*.

    Returns the list of matches found (usually 0 or 1). More than one match
    is a genuine cross-machine collision -- this function reports it as-is;
    callers that need a single resolved worktree must raise
    :class:`AmbiguousCodenameError` on ``len(matches) > 1`` rather than
    picking one (see :func:`resolve_codename_cross_machine_unique`).

    ``project`` defaults to the current active project (``cfg.project_name()``);
    a codename is only assigned/collision-checked within one project's
    tracking directory (Phase 1/2), so a cross-machine lookup for "the same
    codename I have locally" is only meaningful against the SAME project name
    on the other machine. Set ``AGENT_WORKTREES_NO_REMOTE_CODENAME`` to
    disable the scan entirely (returns ``[]`` without any network call).

    ``codename`` is validated against :func:`codename.is_valid_handle`
    before any SSH fan-out. Every valid codename is already constrained to
    that shape (lowercase alnum + hyphens), so this is a defense-in-depth
    gate: ``codename`` is interpolated into a remote shell command
    (``_remote_probe_cmd``'s ``bash -lc '...'``/pwsh EncodedCommand
    payload), and an unvalidated string containing quotes or shell
    metacharacters would otherwise let a caller-supplied value inject
    commands on every scanned machine. An invalid codename can never
    legitimately match anything, so failing this check returns ``[]``
    exactly like "not found." An explicitly supplied ``project`` is
    interpolated into the same remote command and is validated the same
    way, against ``cfg._PROJECT_NAME_RE`` (the same shape
    ``cfg.project_name()``'s own resolution enforces) -- guarded with an
    ``isinstance`` check first since a non-``str`` value (the type hint
    is not runtime-enforced) would otherwise raise inside the regex match
    instead of degrading to ``[]``. The default (``project=None``,
    resolved via ``cfg.project_name()``) is already trusted and is not
    re-validated.
    """
    if not codename or os.environ.get(NO_REMOTE_ENV):
        return []
    from . import codename as codename_mod
    if not codename_mod.is_valid_handle(codename):
        return []
    if project is None:
        try:
            project = cfg.project_name()
        except Exception:
            return []
    elif not isinstance(project, str) or not cfg._PROJECT_NAME_RE.fullmatch(project):
        return []
    try:
        self_machine = cfg.load_config().machine
    except Exception:
        self_machine = None
    matches: list[RemoteCodenameMatch] = []
    for machine_key in _known_machine_keys(exclude=self_machine):
        worktree_id = _probe_machine(machine_key, project, codename, timeout=timeout)
        if worktree_id:
            matches.append(RemoteCodenameMatch(machine=machine_key, worktree_id=worktree_id))
    return matches


def resolve_codename_cross_machine_unique(
    codename: str,
    *,
    project: str | None = None,
    timeout: float = REMOTE_TIMEOUT,
) -> RemoteCodenameMatch | None:
    """Like :func:`resolve_codename_cross_machine`, but resolves to exactly
    one match or ``None`` -- raising :class:`AmbiguousCodenameError` if more
    than one machine reports the same codename. The convenience entry point
    for callers (``resolve``/``embody``) that need a single answer.
    """
    matches = resolve_codename_cross_machine(codename, project=project, timeout=timeout)
    if not matches:
        return None
    if len(matches) > 1:
        raise AmbiguousCodenameError(codename, matches)
    return matches[0]
