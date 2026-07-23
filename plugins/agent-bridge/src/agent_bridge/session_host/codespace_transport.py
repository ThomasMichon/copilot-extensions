"""Codespace-boundary :class:`RemoteTransport` for the remote Session Host.

Self-contained on ``gh`` (file copy) + ssh-manager (SSH config / exec / forward)
so the **agent-bridge daemon** -- which runs in its own venv and cannot import
``agent-codespaces`` -- can bootstrap a Session Host inside a CodeSpace. The four
seam operations:

* ``push_file`` -- ``gh codespace cp`` (stdin piping through the ssh wrapper hangs,
  so a real file copy is used; validated in the #145 live proof).
* ``run`` -- ssh-manager ``exec_command`` over the codespace SSH config (fast; the
  ProxyCommand rides the gh tunnel). Used for the detached launch + state poll.
* ``ssh_config`` -- the codespace SSH config the ``-L`` forward dials.
* ``reverse_forwards`` -- the optional credential-relay ``-R`` spec, carried on the
  persistent forward so a detached Host keeps a live relay for ADO/git (rush
  build). Omitted for an auth-light validation.
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
import shlex
import subprocess
import sys

from ssh_manager import CodespaceConfigSource, ConnectionManager

log = logging.getLogger("agent-bridge.session-host.codespace")


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


class CodeSpaceTransport:
    """Far-side operations for a Session Host running inside a CodeSpace."""

    boundary = "codespace"

    def __init__(
        self,
        codespace_name: str,
        repo: str = "",
        *,
        relay_port: int | None = None,
        manager: ConnectionManager | None = None,
        source: CodespaceConfigSource | None = None,
    ) -> None:
        self._name = codespace_name
        self._repo = repo
        self._relay_port = relay_port
        self._source = source or CodespaceConfigSource(codespace_name)
        self._manager = manager or ConnectionManager()
        self._connected = False

    async def _ensure(self) -> None:
        if not self._connected:
            await self._manager.ensure_connected(self._name, self._source, [])
            self._connected = True

    async def run(
        self, command: str, *, timeout: float = 60.0,
    ) -> tuple[int, str, str]:
        await self._ensure()
        res = await self._manager.exec_command(self._name, command, timeout=timeout)
        return (res.exit_code, res.stdout, res.stderr)

    async def path_exists(self, remote_path: str) -> bool:
        _rc, out, _err = await self.run(
            f"test -f {shlex.quote(remote_path)} && echo __EXISTS__ || true",
            timeout=30.0,
        )
        return "__EXISTS__" in (out or "")

    async def push_file(self, local_path: str, remote_path: str) -> None:
        # scp (under gh cp) will not create the destination dir -- ensure it.
        parent = posixpath.dirname(remote_path)
        if parent:
            await self.run(f"mkdir -p {shlex.quote(parent)}", timeout=30.0)
        args = [
            "gh", "codespace", "cp", "-e", "-c", self._name,
            local_path, f"remote:{remote_path}",
        ]
        log.debug("gh codespace cp: %s", " ".join(args))
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=_creation_flags(),
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=180.0)
        if proc.returncode != 0:
            raise RuntimeError(
                f"gh codespace cp failed (rc={proc.returncode}): "
                f"{(err or out).decode(errors='replace').strip()}"
            )

    def ssh_config(self):  # -> ssh_manager.SSHConfig
        return self._source.get_ssh_config()

    def endpoint_extra(self) -> dict:
        return {"codespace": self._name, "repo": self._repo}

    def reverse_forwards(self) -> list[str]:
        if self._relay_port:
            return [f"{self._relay_port}:127.0.0.1:{self._relay_port}"]
        return []


def workspace_folder_from_acp_command(acp_command: str) -> str | None:
    """Extract the literal target dir of a leading ``cd <dir> && …`` clause.

    The remote ACP launch string is typically ``cd /workspaces/<repo> && copilot
    --acp --stdio`` (see ``agent_codespaces.config.effective_acp_command_for``).
    Copilot uses the ACP ``session/new`` ``cwd`` as the working directory its
    tools run from -- **not** just the process cwd the ``cd`` sets -- so the
    frontend must pass this same absolute path as the session cwd, or the agent
    operates from ``/home/<user>`` with no repo checkout in view.

    Returns the absolute POSIX path, or ``None`` when the command has no literal
    ``cd`` prefix (e.g. the env-expanded ``cd "${CODESPACE_VSCODE_FOLDER:-…}"``
    fallback, which can't be resolved host-side).
    """
    import shlex

    s = (acp_command or "").strip()
    if not s.startswith("cd "):
        return None
    clause = s.split("&&", 1)[0].strip()  # "cd /workspaces/example-web"
    try:
        parts = shlex.split(clause)
    except ValueError:
        return None
    if len(parts) < 2:
        return None
    target = parts[1]
    # Only a literal absolute POSIX path is usable; skip ${ENV} expansions.
    return target if target.startswith("/") else None


def parse_codespace_target(spawn_command: list[str]) -> dict | None:
    """Recognize a codespace command-target from its ``spawn_command`` shape.

    ``agent-codespaces``'s bridge provider registers each CodeSpace as a command
    agent: ``… agent_codespaces ssh --stdio <name> --repo <repo> --remote-cmd
    <acp_command>``. This extracts ``{name, repo, acp_command, workspace_folder}``
    so the daemon can route the agent through the :class:`CodeSpaceSpawner`
    (Session-Host mode) instead of the front-owns-stdio path -- **without** a
    provider-API change. ``workspace_folder`` is the ``cd`` target parsed from
    ``acp_command`` (used as the ACP session cwd; see
    :func:`workspace_folder_from_acp_command`).
    Returns ``None`` for any command that is not a codespace stdio launch.

    (Structured provider metadata -- a first-class ``codespace`` block on the
    agent config / SpawnTarget -- is the cleaner long-term seam; noted as a
    follow-up. This shape-detection keeps the productionization self-contained.)
    """
    if not spawn_command:
        return None
    toks = list(spawn_command)
    if not any("agent_codespaces" in t or "agent-codespaces" in t for t in toks):
        return None
    if "ssh" not in toks or "--stdio" not in toks:
        return None

    def _opt(flag: str) -> str | None:
        if flag in toks:
            i = toks.index(flag)
            if i + 1 < len(toks):
                return toks[i + 1]
        return None

    # The positional CodeSpace name is the token right after "ssh" that is not a
    # flag and not the "--stdio" switch.
    name = None
    ssh_i = toks.index("ssh")
    for t in toks[ssh_i + 1:]:
        if t.startswith("-"):
            continue
        name = t
        break
    acp_command = _opt("--remote-cmd")
    if not name or not acp_command:
        return None
    return {
        "name": name,
        "repo": _opt("--repo") or "",
        "acp_command": acp_command,
        "workspace_folder": workspace_folder_from_acp_command(acp_command),
    }


def build_codespace_spawner(
    codespace_name: str,
    repo: str = "",
    *,
    relay_port: int | None = None,
    remote_dir: str = "/tmp/agent-bridge",  # noqa: S108 -- remote CS path, not a local temp
    ready_timeout: float = 120.0,
):
    """Construct a :class:`CodeSpaceSpawner` wired to a CodeSpace transport.

    ``relay_port`` (the daemon's credential-relay port) is carried on the
    persistent forward's ``-R`` so a detached Host keeps a live relay; omit it for
    an auth-light validation.
    """
    from .spawner import CodeSpaceSpawner

    transport = CodeSpaceTransport(codespace_name, repo, relay_port=relay_port)
    return CodeSpaceSpawner(
        transport, remote_dir=remote_dir, ready_timeout=ready_timeout,
    )
