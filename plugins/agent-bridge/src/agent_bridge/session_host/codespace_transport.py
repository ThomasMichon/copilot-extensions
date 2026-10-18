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
import json
import logging
import os
import posixpath
import re
import shlex
import subprocess
import time
from contextlib import contextmanager
from urllib.parse import quote

from agent_procutil import no_window_flags
from ssh_manager import CodespaceConfigSource, ConnectionManager

log = logging.getLogger("agent-bridge.session-host.codespace")
# CodeSpace states in which an SSH reconnect would have to start (re-wake) it.
_STOPPED_STATES = frozenset({"Shutdown", "Archived", "Deleted"})


def _creation_flags() -> int:
    return no_window_flags()


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
        self._home_dir: str | None = None

    @property
    def codespace_name(self) -> str:
        """The CodeSpace this transport drives."""
        return self._name

    async def _ensure(self) -> None:
        if not self._connected:
            await self._manager.ensure_connected(self._name, self._source, [])
            self._connected = True

    async def run(
        self, command: str, *, timeout: float = 60.0, input_bytes: bytes | None = None,
    ) -> tuple[int, str, str]:
        await self._ensure()
        kwargs = {"input_bytes": input_bytes} if input_bytes is not None else {}
        res = await self._manager.exec_command(self._name, command, timeout=timeout, **kwargs)
        return (res.exit_code, res.stdout, res.stderr)

    async def run_script(
        self, script: str, *, timeout: float = 60.0,
    ) -> tuple[int, str, str]:
        """Run a bash script fed over stdin (``bash -s``), not argv.

        For payloads too large for one SSH argv element: the provision command
        can exceed the Windows ``CreateProcess`` command-line limit.
        """
        await self._ensure()
        res = await self._manager.exec_command(
            self._name, "bash -s", timeout=timeout, input_bytes=script.encode("utf-8"),
        )
        return (res.exit_code, res.stdout, res.stderr)

    async def path_exists(self, remote_path: str) -> bool:
        _rc, out, _err = await self.run(
            f"test -f {shlex.quote(remote_path)} && echo __EXISTS__ || true",
            timeout=30.0,
        )
        return "__EXISTS__" in (out or "")

    async def home_dir(self) -> str:
        if self._home_dir is not None:
            return self._home_dir
        rc, out, err = await self.run('printf "%s" "$HOME"', timeout=30.0)
        home = (out or "").strip()
        if rc != 0 or not home.startswith("/"):
            raise RuntimeError(
                f"Could not resolve remote home for {self._name}: {err or out}"
            )
        self._home_dir = home
        return home

    async def is_running(self) -> bool:
        """Read the exact target without SSH, scoped to an account that can see it."""
        return await asyncio.to_thread(self._read_state) == "Available"

    async def reconnect_allowed(self) -> bool:
        """Credential-relay reconnect gate: may an SSH reconnect run now?

        ``True`` when Available; ``False`` only for a definitively stopped
        CodeSpace (reconnecting would start it again). A transitional state
        (e.g. Starting, ShuttingDown) is inconclusive and raises, so the relay
        supervisor backs off and re-checks instead of retiring.
        """
        state = await asyncio.to_thread(self._read_state)
        if state == "Available":
            return True
        if state in _STOPPED_STATES:
            return False
        raise RuntimeError(f"CodeSpace {self._name} is {state}; relay reconnect deferred")

    def _read_state(self) -> str:
        if not self._name or self._name.strip() != self._name:
            raise RuntimeError("CodeSpace availability requires an exact target name")
        deadline = time.monotonic() + 30.0

        def run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("CodeSpace availability lookup timed out")
            try:
                return subprocess.run(
                    ["gh", *args], capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    timeout=remaining, env=env, creationflags=_creation_flags(),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError("CodeSpace availability lookup failed") from exc

        def query(env: dict[str, str]) -> str | None:
            result = run([
                "api", f"/user/codespaces/{quote(self._name, safe='')}",
                "--method", "GET", "--hostname", "github.com",
                "--jq", "{name: .name, state: .state}",
            ], env)
            if result.returncode != 0:
                if re.search(r"\(HTTP (401|403|404)\)", result.stderr or ""):
                    return None
                raise RuntimeError("GitHub could not verify CodeSpace availability")
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError("CodeSpace availability returned invalid JSON") from exc
            if (
                not isinstance(data, dict)
                or data.get("name") != self._name
                or not isinstance(data.get("state"), str)
                or not data["state"].strip()
            ):
                raise RuntimeError("CodeSpace availability did not identify the requested target")
            return data["state"]

        pinned_env = getattr(self._source, "_gh_env", None)
        env = dict(pinned_env if pinned_env is not None else os.environ)
        state = query(env)
        if state is not None:
            return state
        if pinned_env is not None:
            raise RuntimeError("The configured account could not verify CodeSpace availability")

        # A denied lookup is not evidence that the target is stopped. Inspect
        # the keyring without inherited tokens, then pin each candidate explicitly.
        env = dict(env)
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        env["NO_COLOR"] = "1"
        result = run(["auth", "status", "--hostname", "github.com"], env)
        # Some supported gh versions lack auth-status JSON. Keep only the
        # account names from its two supported logged-in status formats.
        accounts = dict.fromkeys(re.findall(
            r"Logged in to github\.com (?:account|as) ([A-Za-z0-9_-]+)",
            (result.stdout or "") + "\n" + (result.stderr or ""),
        ))
        if not accounts:
            raise RuntimeError("Could not enumerate GitHub accounts for CodeSpace availability")
        for login in accounts:
            token = run(["auth", "token", "--hostname", "github.com", "--user", login], env)
            if token.returncode != 0 or not token.stdout.strip():
                continue
            state = query({**env, "GH_TOKEN": token.stdout.strip()})
            if state is not None:
                return state
        raise RuntimeError("No authenticated GitHub account could verify CodeSpace availability")

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


def _workspace_environment_probe(argument: str) -> str:
    """Parse only the provider's variable/fallback grammar; never shell-eval it."""
    variables = []
    value = argument
    while value.startswith("${"):
        match = re.match(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", value)
        if match is None:
            raise RuntimeError("Unsupported venue execution checkout expression")
        variables.append(match.group(1))
        tail = value[match.end():]
        if tail == "}":
            value = None
            break
        if not tail.startswith(":-") or not tail.endswith("}"):
            raise RuntimeError("Unsupported venue execution checkout expression")
        value = tail[2:-1]
    if not variables or value is not None and (
        value != "." and not value.startswith("/")
        or any(character in value for character in "$`{};|&\"'\r\n")
    ):
        raise RuntimeError("Unsupported venue execution checkout expression")
    script = (
        "import json,os,pathlib,sys\n"
        "names=json.loads(sys.argv[1]);fallback=json.loads(sys.argv[2])\n"
        "value=next((os.environ[name] for name in names if os.environ.get(name)),fallback)\n"
        "if not value: raise SystemExit('workspace environment is unset')\n"
        "path=pathlib.Path(value).resolve(strict=False)\n"
        "print(path)\n"
    )
    return (
        f"export {' '.join(variables)} && "
        f"python3 -c {shlex.quote(script)} {shlex.quote(json.dumps(variables))} "
        f"{shlex.quote(json.dumps(value))}"
    )


async def resolve_execution_workspace(target: dict, transport) -> str:
    """Resolve a fresh/replacement child's leading `cd` without launching it."""
    leading_cd = re.match(r"^\s*cd\s", str(target.get("acp_command") or "")) is not None
    existing = target.get("workspace_folder")
    if isinstance(existing, str) and existing.startswith("/") and not leading_cd:
        return existing
    if (
        "checkout_provision_command" not in target
        and str(target.get("acp_command") or "").lstrip().startswith("[")
    ):
        await _refresh_checkout_metadata(target)
    existing = target.get("workspace_folder")
    leading_cd = re.match(r"^\s*cd\s", str(target.get("acp_command") or "")) is not None
    if isinstance(existing, str) and existing.startswith("/") and not leading_cd:
        return existing
    clause, separator, command = str(target.get("acp_command") or "").partition("&&")
    parts = shlex.split(clause)
    if not separator or not parts or parts[0] != "cd" or len(parts) not in (2, 3):
        raise RuntimeError("Cannot resolve the venue execution checkout from its launch command")
    if len(parts) == 3 and parts[1] != "--":
        raise RuntimeError("Unsupported venue execution checkout expression")
    argument = parts[-1]
    if argument.startswith("/") and argument == existing:
        path = existing
    else:
        if argument.startswith("/"):
            reader = "import pathlib,sys;print(pathlib.Path(sys.argv[1]).resolve(strict=False))"
            probe = f"python3 -c {shlex.quote(reader)} {shlex.quote(argument)}"
        elif argument.startswith("${"):
            probe = _workspace_environment_probe(argument)
        else:
            raise RuntimeError("Unsupported venue execution checkout expression")
        rc, output, _error = await transport.run(f"bash -lc {shlex.quote(probe)}", timeout=30)
        path = output.strip()
        if rc or not path.startswith("/") or "\n" in path or "\r" in path:
            raise RuntimeError(f"Could not resolve the venue execution checkout (exit {rc})")
    target["workspace_folder"] = path
    target["acp_command"] = f"cd {shlex.quote(path)} && {command.strip()}"
    return path


async def _refresh_checkout_metadata(target: dict) -> None:
    """Ask the provider to refresh legacy clone targets; never parse their shell."""
    provider = target.get("provider_command")
    if not isinstance(provider, list) or not provider or not all(
        isinstance(part, str) and part for part in provider
    ):
        raise RuntimeError("Legacy checkout target requires fresh provider metadata")
    argv = [*provider, "namespace-resolve", target["name"]]
    requested = target.get("requested_repo") or target.get("repo")
    if requested:
        argv += ["--repo", requested]
    if target.get("repo_remote"):
        argv += ["--repo-remote", target["repo_remote"]]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=_creation_flags(),
    )
    try:
        output, _error = await asyncio.wait_for(proc.communicate(), timeout=60)
    except BaseException:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        raise RuntimeError("Could not refresh legacy checkout provider metadata")
    metadata = json.loads(output).get("codespace") or {}
    if metadata.get("name") != target.get("name"):
        raise RuntimeError("Provider returned a different CodeSpace checkout")
    for key in (
        "workspace_folder", "acp_command", "checkout_provision_command",
        "requested_repo", "repo_remote",
    ):
        if key in metadata:
            target[key] = metadata[key]


async def provision_execution_checkout(target: dict, transport, *, prelude: str = "") -> None:
    """Run only the provider's separate clone step, inside the ready relay barrier."""
    if (
        "checkout_provision_command" not in target
        and str(target.get("acp_command") or "").lstrip().startswith("[")
    ):
        previous = target.get("workspace_folder")
        await _refresh_checkout_metadata(target)
        if previous and target.get("workspace_folder") != previous:
            raise RuntimeError("Provider changed the execution checkout during preparation")
    command = target.get("checkout_provision_command")
    if not command:
        return
    if not isinstance(command, str):
        raise RuntimeError("Invalid provider checkout provisioning command")
    rc, _output, _error = await transport.run(
        f"bash -lc {shlex.quote(prelude + command)}", timeout=600,
    )
    if rc:
        raise RuntimeError(f"Required execution checkout provisioning failed (exit {rc})")


def codespace_child_preparer(target, transport, *, prelude, controller_cwd,
                             prepare_plugins, append_plugin_dirs):
    """Share the authenticated checkout/preparation barrier across fresh/recovery."""
    async def prepare_codespace_child(argv):
        await provision_execution_checkout(target, transport, prelude=prelude)
        workspace = target.get("workspace_folder") or ""
        validation = (
            f"test -d {shlex.quote(workspace)} && "
            f"git -C {shlex.quote(workspace)} rev-parse --show-toplevel >/dev/null"
        )
        rc, _output, _error = await transport.run(
            f"bash -lc {shlex.quote(prelude + validation)}", timeout=30,
        )
        if rc:
            raise RuntimeError("Required execution checkout is missing or invalid after provisioning")
        directories = await prepare_plugins(
            target, transport, target.get("workspace_folder") or None,
            prelude=prelude, controller_cwd=controller_cwd,
        )
        return ["bash", "-lc", prelude + append_plugin_dirs(target["acp_command"], directories)]
    return prepare_codespace_child


@contextmanager
def codespace_launch_ownership(manager, session_id, target, owner, core, *,
                              holder_ref=None, replace_session_id=None, retain_on_error=False,
                              claim_metadata=None, persist_claim_metadata=None):
    """Rollback only this start's resources, including preflight/cancellation failures."""
    from ..session_manager import CodespaceClaimConflictError, CodespaceCoordinationRejectedError
    from .spawner import RemoteSpawnCleanupPendingError

    name = target["name"]
    already_held = session_id in manager._codespace_lock_sessions
    locked = claimed = transferred = False
    connect_started = False

    def enter_connect():
        nonlocal connect_started
        connect_started = True

    if claim_metadata is not None and not already_held and not replace_session_id:
        claim_metadata["codespace_claim"] = None
        if persist_claim_metadata is not None:
            persist_claim_metadata()
    try:
        if replace_session_id:
            manager._transfer_codespace_lock(replace_session_id, session_id, name)
            transferred = True
        else:
            manager._acquire_codespace_lock(session_id, name)
        locked = True
        status, detail = core._claim_codespace(
            name, owner, holder_ref=holder_ref,
        )
        if status == "conflict":
            raise CodespaceClaimConflictError(name, owner, detail)
        if status == "coordination-rejected":
            raise CodespaceCoordinationRejectedError(name, owner, detail)
        claimed = status != "skipped"
        if claim_metadata is not None:
            inherited_receipt = (already_held or transferred) and "codespace_claim" in claim_metadata
            if claimed or not inherited_receipt:
                claim_metadata["codespace_claim"] = {"name": name, "owner": owner} if claimed else None
            if persist_claim_metadata is not None:
                persist_claim_metadata()
        yield enter_connect
    except RemoteSpawnCleanupPendingError:
        raise
    except BaseException as failure:
        if not (retain_on_error and connect_started and not isinstance(failure, asyncio.CancelledError)):
            if claimed and not already_held and not transferred:
                detail = "release was not confirmed"
                try:
                    released = core._release_codespace_claim(name, owner) is True
                except Exception as cleanup_error:
                    released = False
                    detail = f"release raised {type(cleanup_error).__name__}"
                if not released:
                    raise RuntimeError(
                        f"CodeSpace claim cleanup is unconfirmed ({detail}); "
                        "retaining the failed session's claim and TargetLock"
                    ) from failure
                if claim_metadata is not None:
                    claim_metadata["codespace_claim"] = None
                    if persist_claim_metadata is not None:
                        persist_claim_metadata()
            if transferred:
                manager._transfer_codespace_lock(session_id, replace_session_id, name)
            elif locked and not already_held:
                manager._release_codespace_lock(session_id)
        raise


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
    unexpected_reap_seconds: float = 60.0,
    active_reap_seconds: float = 0.0,
    provision_prelude: str = "",
):
    """Construct a :class:`CodeSpaceSpawner` wired to a CodeSpace transport.

    ``relay_port`` (the daemon's credential-relay port) is carried on the
    persistent forward's ``-R`` so a detached Host keeps a live relay; omit it for
    an auth-light validation. ``unexpected_reap_seconds`` / ``active_reap_seconds``
    bound how long the detached far-side Host holds a front-less idle / active
    child before letting it go (so a reconnecting front can resume; #145).
    Every spawn first asks the host's launch policy (``venue_launch_policy``).
    """
    from .spawner import CodeSpaceSpawner

    class _PolicyGatedSpawner(CodeSpaceSpawner):
        async def spawn(self, child_argv, **kwargs):
            import asyncio

            from ..venue_launch_policy import ensure_codespace_launch_allowed

            await asyncio.to_thread(ensure_codespace_launch_allowed, codespace_name)
            return await super().spawn(child_argv, **kwargs)

    transport = CodeSpaceTransport(codespace_name, repo, relay_port=relay_port)
    return _PolicyGatedSpawner(
        transport, remote_dir=remote_dir, ready_timeout=ready_timeout,
        unexpected_reap_seconds=unexpected_reap_seconds,
        active_reap_seconds=active_reap_seconds,
        provision_prelude=provision_prelude,
    )
