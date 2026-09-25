"""Transport-agnostic Copilot CLI session-liveness probe.

Vendored per copilot-extensions.session-rescue-parity Phase 2: the
``inuse.*.lock`` + ``/proc/<pid>`` liveness-probe technique is a property of
the Copilot CLI's own session-state layout (``~/.copilot/session-state``),
not of any one transport. This module owns the two pure, transport-agnostic
pieces -- the shell script and its output parser -- so both a synchronous
``docker exec`` caller (agent-containers) and an asynchronous SSH caller
(agent-codespaces) can share the exact same probe logic while supplying
their own transport around it.

This module is deliberately pure stdlib and synchronous: it never imports
``docker``/``ssh``/``asyncio``, and it never itself executes the script. A
caller is responsible for running :func:`build_probe_script`'s output
through its own transport (sync or async) and handing the raw
``(returncode, stdout, stderr)`` to :func:`parse_probe_output`.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

_LOCK_LINE_RE = re.compile(
    r"^LOCK\t([0-9a-fA-F-]{36})\t([1-9][0-9]*)\t(live|stale)$"
)


@dataclass
class SessionLiveness:
    """Non-cooperative in-venue session-state probe result."""

    state: str
    active_sessions: list[str] = field(default_factory=list)
    stale_sessions: list[str] = field(default_factory=list)
    reason: str | None = None
    session_state: str = "unknown"


def build_probe_script() -> str:
    """Return the transport-agnostic probe shell script.

    Scans ``~/.copilot/session-state`` for ``inuse.*.lock`` marker files,
    checks each marker's PID against ``/proc/<pid>``, and backstops the
    result against a ``*copilot*``/``--acp`` process/cmdline scan. Runs
    identically over ``docker exec`` or an SSH session -- it only needs a
    POSIX shell and ``/proc``.
    """
    return r"""
set -o pipefail
root="$HOME/.copilot/session-state"
test -d /proc
if [ ! -e "$root" ]; then
  printf 'ROOT\tabsent\n'
else
  test -d "$root"
  printf 'ROOT\tpresent\n'
  find "$root" -mindepth 2 -maxdepth 2 -type f -name 'inuse.*.lock' -print |
  while IFS= read -r path; do
    session="${path%/*}"
    session="${session##*/}"
    marker="${path##*/}"
    pid="${marker#inuse.}"
    pid="${pid%.lock}"
    case "$pid" in
      ''|*[!0-9]*) printf 'INVALID\t%s\t%s\n' "$session" "$marker"; continue ;;
    esac
    if [ -d "/proc/$pid" ]; then state=live; else state=stale; fi
    printf 'LOCK\t%s\t%s\t%s\n' "$session" "$pid" "$state"
  done
fi
scan=ok
for proc in /proc/[0-9]*; do
  pid="${proc##*/}"
  [ "$pid" = "$$" ] && continue
  [ "$pid" = "$PPID" ] && continue
  if [ ! -r "$proc/cmdline" ]; then
    [ -d "$proc" ] && scan=partial
    continue
  fi
  command=$(tr '\000' ' ' < "$proc/cmdline") || {
    [ -d "$proc" ] && scan=partial
    continue
  }
  case "$command" in
    *copilot*|*Copilot*|*--acp*) printf 'PROCESS\t%s\n' "$pid" ;;
  esac
done
printf 'PROCESS_SCAN\t%s\n' "$scan"
""".strip()


def parse_probe_output(
    returncode: int, stdout: str, stderr: str
) -> SessionLiveness:
    """Parse the probe script's transport result into a `SessionLiveness`.

    Pure function: takes the raw exit code / stdout / stderr a caller's own
    transport produced and returns the same non-cooperative liveness
    verdict regardless of whether the transport was `docker exec` or SSH.
    """
    if returncode != 0:
        detail = (stderr or stdout).strip()
        return SessionLiveness("unknown", reason=detail or "session-state probe failed")
    lines = stdout.splitlines()
    if not lines or lines[0] not in {"ROOT\tabsent", "ROOT\tpresent"}:
        return SessionLiveness(
            "unknown", reason="session-state probe returned an invalid header"
        )
    active: list[str] = []
    stale: list[str] = []
    processes: list[str] = []
    scan_state = None
    session_state = lines[0].split("\t", 1)[1]
    for line in lines[1:]:
        if line.startswith("PROCESS\t"):
            pid = line.split("\t", 1)[1]
            if not pid.isdigit():
                return SessionLiveness(
                    "unknown",
                    active,
                    stale,
                    "process backstop returned an invalid pid",
                    session_state,
                )
            processes.append(pid)
            continue
        if line.startswith("PROCESS_SCAN\t"):
            scan_state = line.split("\t", 1)[1]
            if scan_state not in {"ok", "partial"}:
                return SessionLiveness(
                    "unknown",
                    active,
                    stale,
                    "process backstop returned invalid status",
                    session_state,
                )
            continue
        match = _LOCK_LINE_RE.fullmatch(line)
        if not match:
            return SessionLiveness(
                "unknown",
                active,
                stale,
                "session-state probe returned an invalid marker",
                session_state,
            )
        session_id, _pid, marker_state = match.groups()
        try:
            session_id = str(uuid.UUID(session_id))
        except ValueError:
            return SessionLiveness(
                "unknown",
                active,
                stale,
                "session-state probe found a non-UUID session marker",
                session_state,
            )
        if marker_state == "live":
            active.append(session_id)
        else:
            stale.append(session_id)
    if active:
        return SessionLiveness(
            "active", sorted(set(active)), sorted(set(stale)), session_state=session_state
        )
    if processes:
        return SessionLiveness(
            "unknown",
            [],
            sorted(set(stale)),
            "Copilot-like process has no matching live session marker",
            session_state,
        )
    if scan_state != "ok":
        return SessionLiveness(
            "unknown",
            [],
            sorted(set(stale)),
            "process backstop was incomplete",
            session_state,
        )
    return SessionLiveness("idle", [], sorted(set(stale)), session_state=session_state)


__all__ = ["SessionLiveness", "build_probe_script", "parse_probe_output"]
