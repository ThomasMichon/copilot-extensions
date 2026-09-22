"""``agent-codespaces check``/``doctor <name>`` -- venue readiness probe and
best-effort remediation for the CLI-mode `copilot` verb's own preflight
(agent-bridge-cli-mode-sessions Phase 4's "preflight already documented, not
yet enforced in code" gap).

A freshly-provisioned or long-dormant CodeSpace routinely has: no ``tmux``,
an ``agent-worktrees`` binstub that is only "lean-staged" (self-provisions on
first use, to a version that may be far behind the marketplace), or no
``agent-worktrees`` at all. Before this module, closing that gap meant
several manual SSH round-trips (confirmed live this session against a real
odsp-web CodeSpace: probing toolchain presence, installing tmux by hand,
running the binstub once to trigger self-provisioning, then ``update``).

``check`` is read-only (safe to run repeatedly, never mutates the venue).
``doctor <name>`` runs the same probe and, opt-in via ``--fix``, applies the
safely-idempotent remediations this module knows how to do: installing
``tmux`` via the venue's own package manager, nudging ``agent-worktrees`` to
(re)provision/update itself, and installing the ``agent-bridge`` Copilot
plugin itself. Anything it cannot safely fix (no ``copilot`` binary, no
package manager, no passwordless sudo) is reported, never guessed at.

The ``agent-bridge`` plugin gap is a deliberate, narrow exception to "never
install a plugin on the operator's behalf": unlike ``agent-worktrees``
(a project-level policy choice this generic, project-agnostic command has
no business making), ``agent-bridge`` is the exact mechanism the CLI-mode
`copilot` verb exists to serve -- without it loaded in the remote Copilot
process, that process can never self-register back to the host daemon, so
every CLI-mode session silently never claims its own reservation (confirmed
live, agent-bridge-cli-mode-sessions Phase 4: a real CodeSpace ran the
interactive session correctly for 5+ minutes with zero registration, purely
because the plugin was never installed there). Because this is a precondition
of the verb's own contract rather than a project preference, ``cmd_copilot``
applies this one fix **implicitly**, with no ``--fix`` needed -- see its own
docstring.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

ExecCommand = Callable[[str, str], Awaitable[Any]]

# A single batched remote probe -- one round trip, not one per fact. Emits
# plain ``KEY=value`` lines (never hand-rolled JSON): the one field genuinely
# free-form -- ``agent-worktrees --version``'s output -- could otherwise
# break naive JSON quoting.
_PROBE_SCRIPT = r"""bash -lc '
echo "COPILOT=$(command -v copilot || true)"
echo "TMUX=$(command -v tmux >/dev/null 2>&1 && echo yes || echo no)"
echo "NODE=$(command -v node >/dev/null 2>&1 && echo yes || echo no)"
echo "PYTHON3=$(command -v python3 >/dev/null 2>&1 && echo yes || echo no)"
echo "UV=$(command -v uv >/dev/null 2>&1 && echo yes || echo no)"
echo "APT=$(command -v apt-get >/dev/null 2>&1 && echo yes || echo no)"
echo "SUDO_NOPASSWD=$(sudo -n true >/dev/null 2>&1 && echo yes || echo no)"
echo "AGENT_BRIDGE_PLUGIN=$(command -v copilot >/dev/null 2>&1 && copilot plugin list 2>/dev/null | grep -q "agent-bridge@" && echo yes || echo no)"
aw=$(command -v agent-worktrees || true)
if [ -z "$aw" ]; then
  echo "AGENT_WORKTREES_STATE=absent"
else
  if [ -d "$HOME/.agent-worktrees/versions" ] && [ -n "$(ls -A "$HOME/.agent-worktrees/versions" 2>/dev/null)" ]; then
    echo "AGENT_WORKTREES_STATE=full"
    echo "AGENT_WORKTREES_VERSION=$("$aw" --version 2>/dev/null | head -1)"
  else
    echo "AGENT_WORKTREES_STATE=lean"
  fi
fi
'"""


@dataclass
class VenueReadiness:
    """Toolchain presence/state for one CodeSpace, as of one probe."""

    copilot_path: str | None = None
    tmux: bool = False
    node: bool = False
    python3: bool = False
    uv: bool = False
    apt_available: bool = False
    sudo_nopasswd: bool = False
    agent_bridge_plugin: bool = False
    agent_worktrees_state: str = "absent"  # absent | lean | full
    agent_worktrees_version: str | None = None
    raw_stdout: str = ""
    raw_stderr: str = ""
    exit_code: int = 0

    @property
    def copilot_present(self) -> bool:
        return bool(self.copilot_path)

    @property
    def agent_worktrees_full(self) -> bool:
        return self.agent_worktrees_state == "full"

    @property
    def ready(self) -> bool:
        """Every precondition the venue `copilot` verb actually needs."""
        return (
            self.copilot_present and self.tmux and self.agent_worktrees_full
            and self.agent_bridge_plugin
        )

    @property
    def gaps(self) -> list[str]:
        gaps: list[str] = []
        if not self.copilot_present:
            gaps.append("copilot CLI not found on PATH")
        if not self.tmux:
            gaps.append("tmux not installed")
        if self.agent_worktrees_state == "absent":
            gaps.append("agent-worktrees not installed")
        elif self.agent_worktrees_state == "lean":
            gaps.append("agent-worktrees is only lean-staged (not fully provisioned)")
        if not self.agent_bridge_plugin:
            gaps.append(
                "agent-bridge Copilot plugin not installed (CLI-mode session "
                "would never self-register)"
            )
        return gaps

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "copilot_path": self.copilot_path,
            "tmux": self.tmux,
            "node": self.node,
            "python3": self.python3,
            "uv": self.uv,
            "apt_available": self.apt_available,
            "sudo_nopasswd": self.sudo_nopasswd,
            "agent_bridge_plugin": self.agent_bridge_plugin,
            "agent_worktrees_state": self.agent_worktrees_state,
            "agent_worktrees_version": self.agent_worktrees_version,
            "gaps": self.gaps,
        }


def _parse_bool(value: str) -> bool:
    return value.strip().lower() == "yes"


def parse_probe_output(stdout: str, *, exit_code: int = 0, stderr: str = "") -> VenueReadiness:
    """Parse ``_PROBE_SCRIPT``'s ``KEY=value`` line output."""
    readiness = VenueReadiness(raw_stdout=stdout, raw_stderr=stderr, exit_code=exit_code)
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "COPILOT":
            readiness.copilot_path = value or None
        elif key == "TMUX":
            readiness.tmux = _parse_bool(value)
        elif key == "NODE":
            readiness.node = _parse_bool(value)
        elif key == "PYTHON3":
            readiness.python3 = _parse_bool(value)
        elif key == "UV":
            readiness.uv = _parse_bool(value)
        elif key == "APT":
            readiness.apt_available = _parse_bool(value)
        elif key == "SUDO_NOPASSWD":
            readiness.sudo_nopasswd = _parse_bool(value)
        elif key == "AGENT_BRIDGE_PLUGIN":
            readiness.agent_bridge_plugin = _parse_bool(value)
        elif key == "AGENT_WORKTREES_STATE":
            readiness.agent_worktrees_state = value or "absent"
        elif key == "AGENT_WORKTREES_VERSION":
            readiness.agent_worktrees_version = value or None
    return readiness


async def check_remote_venue(
    exec_command: ExecCommand, host: str, *, timeout: float = 30.0,
) -> VenueReadiness:
    """Read-only: probe ``host``'s toolchain readiness in one round trip.

    Never mutates the venue. ``exec_command`` is
    ``ConnectionManager.exec_command`` (or a fake in tests) -- the caller
    owns establishing the connection beforehand.
    """
    result = await exec_command(host, _PROBE_SCRIPT)
    return parse_probe_output(
        getattr(result, "stdout", ""),
        exit_code=getattr(result, "exit_code", 0),
        stderr=getattr(result, "stderr", ""),
    )


@dataclass
class RemediationResult:
    """What :func:`remediate_remote_venue` actually attempted/achieved."""

    attempted: list[str] = field(default_factory=list)
    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
        }


async def remediate_remote_venue(
    exec_command: ExecCommand,
    host: str,
    readiness: VenueReadiness,
    *,
    timeout: float = 240.0,
) -> RemediationResult:
    """Best-effort, idempotent-safe fixes for the gaps this module can safely
    close without any project-specific context: a missing ``tmux``, an
    ``agent-worktrees`` binstub that exists but never provisioned (or is
    still lean-staged), and a missing ``agent-bridge`` Copilot plugin.

    Never attempts to install the ``copilot`` CLI itself or the
    ``agent-worktrees`` **plugin** (that needs a marketplace registration
    decision this generic, project-agnostic command has no business making).
    A caller wanting a specific project's ``agent-worktrees`` fully updated
    to the latest marketplace version should still run
    ``agent-worktrees --project <name> update`` themselves -- this only
    nudges the binstub's own documented first-use self-provisioning path.

    ``agent-bridge`` itself is the one deliberate exception: it is not a
    project policy choice but a precondition of the CLI-mode `copilot` verb's
    own contract (see this module's docstring), so installing it is always
    safe and always attempted when missing -- including implicitly, from
    `cmd_copilot` itself, not just an explicit `doctor --fix`.
    """
    result = RemediationResult()

    if not readiness.tmux:
        if readiness.apt_available and readiness.sudo_nopasswd:
            result.attempted.append("install tmux")
            probe = await exec_command(
                host, "sudo -n apt-get install -y tmux 2>&1",
            )
            if getattr(probe, "exit_code", 1) == 0:
                result.succeeded.append("install tmux")
            else:
                result.failed.append("install tmux")
        else:
            reason = (
                "no apt-get" if not readiness.apt_available
                else "no passwordless sudo"
            )
            result.skipped.append(f"install tmux ({reason})")

    if readiness.agent_worktrees_state == "lean":
        # Idempotent: a lean binstub self-provisions on first use. Only
        # attempted when there is an actual gap (state == "lean") -- an
        # already-"full" venue has nothing to fix here, so this never adds
        # an unnecessary remote round trip to an already-ready venue.
        result.attempted.append("provision/refresh agent-worktrees")
        probe = await exec_command(
            host, 'bash -lc "agent-worktrees --version"',
        )
        if getattr(probe, "exit_code", 1) == 0:
            result.succeeded.append("provision/refresh agent-worktrees")
        else:
            result.failed.append("provision/refresh agent-worktrees")
    elif readiness.agent_worktrees_state == "absent":
        result.skipped.append(
            "install agent-worktrees (no binstub -- install the plugin first)"
        )

    if not readiness.agent_bridge_plugin:
        if readiness.copilot_present:
            result.attempted.append("install agent-bridge plugin")
            probe = await exec_command(
                host,
                "bash -lc 'copilot plugin install agent-bridge@copilot-extensions 2>&1'",
            )
            if getattr(probe, "exit_code", 1) == 0:
                result.succeeded.append("install agent-bridge plugin")
            else:
                result.failed.append("install agent-bridge plugin")
        else:
            result.skipped.append(
                "install agent-bridge plugin (no copilot CLI to install it into)"
            )

    return result


def format_report(readiness: VenueReadiness, *, remediation: RemediationResult | None = None) -> str:
    """Human-readable multi-line report (used by both ``check`` and
    ``doctor``'s non-JSON output)."""
    lines: list[str] = []
    status = "READY" if readiness.ready else "NOT READY"
    lines.append(f"[{status}] venue readiness")
    lines.append(f"  copilot:          {readiness.copilot_path or 'MISSING'}")
    lines.append(f"  tmux:             {'yes' if readiness.tmux else 'MISSING'}")
    lines.append(f"  agent-worktrees:  {readiness.agent_worktrees_state}"
                 + (f" ({readiness.agent_worktrees_version})"
                    if readiness.agent_worktrees_version else ""))
    lines.append(f"  agent-bridge:     "
                 f"{'installed' if readiness.agent_bridge_plugin else 'MISSING'}")
    lines.append(f"  node/python3/uv:  "
                 f"{'yes' if readiness.node else 'no'}/"
                 f"{'yes' if readiness.python3 else 'no'}/"
                 f"{'yes' if readiness.uv else 'no'}")
    if readiness.gaps:
        lines.append("  gaps:")
        for gap in readiness.gaps:
            lines.append(f"    - {gap}")
    if remediation is not None:
        lines.append("  remediation:")
        for item in remediation.succeeded:
            lines.append(f"    [OK] {item}")
        for item in remediation.failed:
            lines.append(f"    [FAIL] {item}")
        for item in remediation.skipped:
            lines.append(f"    [SKIP] {item}")
    return "\n".join(lines)
