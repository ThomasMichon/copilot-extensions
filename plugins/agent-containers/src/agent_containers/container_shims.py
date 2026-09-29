"""In-container credential shims deployed at the bridge connection phase.

agent-containers is generic: rather than baking auth into the image, it deploys
thin shims into the running container that fetch tokens on-demand from the host
relay through the trusted venue's SSH ``-R`` loopback forward. The per-container
secret remains request authorization for the shared relay's Azure-token gate;
it is no longer a defense for a host-network-exposed endpoint. The patched Azure
CLI / rush ``AdoCodespacesAuthCredential`` call
``azure-auth-helper get-access-token <scope>`` on PATH; that resolves to our
shim, which relays the request to the host.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

from agent_procutil import no_window_flags

log = logging.getLogger("agent-containers.shims")

_BIN = "/usr/local/bin"
RELAY_CLIENT_PATH = f"{_BIN}/credential-relay-client"
AZURE_HELPER_PATH = f"{_BIN}/azure-auth-helper"
ADO_HELPER_PATH = f"{_BIN}/ado-auth-helper"
AGENT_WORKTREES_PATH = f"{_BIN}/agent-worktrees"
_AW_RUNTIME_REL = ".agent-worktrees"
_AW_PAYLOAD_PARENT_REL = f"{_AW_RUNTIME_REL}/payload-src"
_AW_PAYLOAD_READY_MARKER = ".payload-sync-complete"
_PATH_SPEC = re.compile(r'path\s*=\s*"([^"]+)"')

# Generic relay client: speaks the credential-relay wire protocol to the host,
# reading endpoint + token from the environment injected by the exec wrapper.
RELAY_CLIENT = r"""#!/usr/bin/env python3
import os, socket, sys

HOST = os.environ.get("LC_GIT_CREDENTIAL_RELAY_HOST", "127.0.0.1")
PORT = int(os.environ.get("LC_GIT_CREDENTIAL_RELAY", "9857"))
TOKEN = os.environ.get("LC_GIT_CREDENTIAL_RELAY_TOKEN", "")


def _send(request):
    s = socket.create_connection((HOST, PORT), timeout=10)
    try:
        s.sendall(request.encode("utf-8"))
        s.settimeout(30)
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\n\n" in buf:
                break
        return buf.decode("utf-8", "replace")
    finally:
        s.close()


def _normalize_resource(scope):
    # Retained for back-compat with the (legacy) resource= request form; the
    # azure path now forwards the scope verbatim (see main()).
    res = scope.split("/.default")[0]
    if res and not res.endswith("/"):
        res += "/"
    return res


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    action = sys.argv[2] if len(sys.argv) > 2 else ""
    if action == "get-access-token":
        if kind == "azure":
            # Faithfully forward the official `azure-auth-helper get-access-token
            # "<scope>"` contract: pass the AAD scope through VERBATIM (e.g.
            # https://storage.azure.com/.default) as `scope=`, so the relay's
            # AzLoginSource mints it via `az ... --scope <scope>` -- the same
            # token the official managed-identity broker would. Do NOT downgrade
            # the scope to a `resource=` (dropping `/.default`): the `.default`
            # form requests the principal's full consented permission set, which
            # the resource form does not, and some storage data-plane operations
            # (e.g. user-delegation-key issuance for dev-deploy SAS) depend on it.
            scope = sys.argv[3] if len(sys.argv) > 3 else ""
            resp = _send(
                "get-azure-token\nauth=%s\nscope=%s\n\n" % (TOKEN, scope)
            )
            for line in resp.split("\n"):
                if line.startswith("token="):
                    sys.stdout.write(line[len("token="):])
                    return 0
            return 1
        # ADO PAT (ungated)
        resp = _send("get-access-token\nauth=%s\n\n" % TOKEN)
        tok = resp.strip()
        if not tok or "quit=1" in resp:
            return 1
        sys.stdout.write(tok)
        return 0
    if action == "get":
        data = sys.stdin.read()
        if not data.endswith("\n\n"):
            data = data.rstrip("\n") + "\n\n"
        fields = {}
        for line in data.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                fields[key] = value
        # Containers bootstrap GitHub identity explicitly through GH_TOKEN.
        # Serve that token locally instead of asking the host relay/GCM, whose
        # active account may differ from the token selected for this launch.
        if (
            kind == "ado"
            and fields.get("host", "").lower() == "github.com"
            and os.environ.get("GH_TOKEN")
        ):
            sys.stdout.write(
                "protocol=https\n"
                "host=github.com\n"
                "username=x-access-token\n"
                "password=%s\n\n" % os.environ["GH_TOKEN"]
            )
            return 0
        sys.stdout.write(_send(data))
        return 0
    return 0  # store / erase / unknown


sys.exit(main())
"""

# azure-auth-helper: thin wrapper invoked by rush / the patched az CLI.
AZURE_HELPER = f"""#!/usr/bin/env bash
exec python3 {RELAY_CLIENT_PATH} azure "$@"
"""

# ado-auth-helper: thin wrapper for ADO PAT + git credential mode (optional).
ADO_HELPER = f"""#!/usr/bin/env bash
exec python3 {RELAY_CLIENT_PATH} ado "$@"
"""

def _agent_worktrees_wrapper(payload_root: str | None) -> str:
    lines = ["#!/usr/bin/env bash", "set -euo pipefail"]
    if payload_root:
        lines.extend(
            [
                f"payload_root={payload_root!r}",
                'if [[ -f "$payload_root/plugin.json" ]]; then',
                '    export AGENT_WORKTREES_PAYLOAD_ROOT="$payload_root"',
                "fi",
            ]
        )
    lines.extend(
        [
            'candidate="$HOME/.local/bin/agent-worktrees"',
            'if [[ -x "$candidate" ]]; then',
            '    exec "$candidate" "$@"',
            "fi",
            'echo "agent-worktrees is not installed for HOME=$HOME (expected $candidate)" >&2',
            "exit 127",
        ]
    )
    return "\n".join(lines) + "\n"


def _run_docker(args: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=no_window_flags(),
    )


def _docker_exec(
    container: str,
    command: str,
    *,
    user: str,
    env: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    args = ["docker", "exec", "-u", user]
    for name, value in sorted((env or {}).items()):
        args.extend(["-e", f"{name}={value}"])
    args.extend([container, "bash", "-lc", command])
    return _run_docker(args, timeout=timeout)


def _docker_cp(source: Path, container: str, target_dir: str, *, timeout: float = 120.0) -> None:
    result = _run_docker(
        ["docker", "cp", str(source), f"{container}:{target_dir}"],
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker cp failed for {source}: {result.stderr.strip() or result.stdout.strip()}"
        )


def _docker_exists(container: str, path: str, *, user: str = "0", timeout: float = 30.0) -> bool:
    result = _docker_exec(
        container,
        f"test -e {path!r}",
        user=user,
        timeout=timeout,
    )
    return result.returncode == 0


def _agent_worktrees_ready(container: str, *, user: str, timeout: float = 60.0) -> bool:
    result = _docker_exec(container, "agent-worktrees --version", user=user, timeout=timeout)
    return result.returncode == 0


def _agent_worktrees_payload_paths(home: str) -> tuple[str, str]:
    payload_root = _agent_worktrees_payload_root()
    common_root, _sources = _agent_worktrees_copy_sources(payload_root)
    parent = str(PurePosixPath(home, _AW_PAYLOAD_PARENT_REL))
    payload = _copied_path(parent, payload_root, common_root=common_root)
    return parent, payload


def _agent_worktrees_payload_ready(container: str, *, user: str, home: str) -> bool:
    parent, payload = _agent_worktrees_payload_paths(home)
    return _docker_exists(container, f"{parent}/{_AW_PAYLOAD_READY_MARKER}", user=user) and (
        _docker_exists(container, f"{payload}/plugin.json", user=user)
    )


def _agent_containers_source_root() -> Path:
    env_payload = os.environ.get("COPILOT_PLUGIN_ROOT", "").strip()
    if env_payload:
        candidate = Path(env_payload).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    candidate = Path(__file__).resolve().parents[2]
    if candidate.is_dir() and (candidate / "plugin.json").is_file():
        return candidate
    manifest = Path.home() / ".agent-containers" / "deploy-manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            data = None
        source_path = str((data or {}).get("source", {}).get("path") or "").strip()
        if source_path:
            candidate = Path(source_path).expanduser()
            if candidate.is_dir():
                return candidate.resolve()
    raise RuntimeError("could not resolve the host agent-containers payload root")


def _agent_worktrees_payload_root() -> Path:
    installed = (
        Path.home() / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-worktrees"
    )
    if installed.is_dir() and (installed / "scripts" / "install.sh").is_file():
        return installed.resolve()
    containers_root = _agent_containers_source_root()
    candidate = containers_root.parent / "agent-worktrees"
    if candidate.is_dir() and (candidate / "scripts" / "install.sh").is_file():
        return candidate.resolve()
    raise RuntimeError(
        "could not resolve the host agent-worktrees payload root next to agent-containers"
    )


def _agent_worktrees_copy_sources(payload_root: Path) -> tuple[Path, list[Path]]:
    pyproject = payload_root / "pyproject.toml"
    if not pyproject.is_file():
        raise RuntimeError(f"agent-worktrees payload is missing pyproject.toml: {pyproject}")
    repo_root = payload_root.parents[1] if payload_root.parent.name == "plugins" else None
    deps: list[Path] = []
    if repo_root and (repo_root / "libs").is_dir() and (repo_root / "plugins").is_dir():
        deps.append((repo_root / "libs").resolve())
    in_sources = False
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_sources = stripped == "[tool.uv.sources]"
            continue
        if not in_sources:
            continue
        for match in _PATH_SPEC.finditer(stripped):
            dep = (payload_root / match.group(1)).resolve()
            if (
                dep.is_dir()
                and dep != payload_root
                and not dep.is_relative_to(payload_root)
                and (not repo_root or not dep.is_relative_to(repo_root / "libs"))
            ):
                deps.append(dep)
    unique = sorted({dep for dep in deps}, key=str)
    common_root = Path(os.path.commonpath([str(payload_root), *(str(dep) for dep in unique)]))
    return common_root, [payload_root, *unique]


def _container_home(container: str, *, user: str) -> str:
    result = _docker_exec(
        container,
        f"getent passwd {user!r} | cut -d: -f6",
        user="0",
        timeout=30.0,
    )
    home = (result.stdout or "").strip()
    if result.returncode != 0 or not home.startswith("/"):
        raise RuntimeError(
            f"could not resolve the home directory for '{user}' in '{container}': "
            f"{result.stderr.strip() or result.stdout.strip() or f'exit {result.returncode}'}"
        )
    return home


def _host_uv_index() -> str | None:
    for name in ("UV_DEFAULT_INDEX", "UV_INDEX_URL", "PIP_INDEX_URL"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    candidates = (
        [sys.executable, "-m", "pip", "config", "get", "global.index-url"],
        ["python", "-m", "pip", "config", "get", "global.index-url"],
    )
    if os.name == "nt":
        candidates += (["py", "-3", "-m", "pip", "config", "get", "global.index-url"],)
    for argv in candidates:
        try:
            result = _run_docker(argv, timeout=15.0)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        value = (result.stdout or "").strip()
        if result.returncode == 0 and value:
            return value
    return None


def _to_posix(root: str, relative: Path) -> str:
    return str(PurePosixPath(root, *relative.parts))


def _copied_path(root: str, source: Path, *, common_root: Path) -> str:
    relative = source.relative_to(common_root)
    return str(PurePosixPath(_to_posix(root, relative.parent), source.name))


def _sync_agent_worktrees_payload(container: str, *, user: str, home: str) -> str:
    payload_root = _agent_worktrees_payload_root()
    common_root, sources = _agent_worktrees_copy_sources(payload_root)
    stage_root = str(PurePosixPath(home, _AW_PAYLOAD_PARENT_REL))
    mkdirs = [
        f"rm -rf {stage_root!r}",
        f"install -d -m 755 {stage_root!r}",
    ]
    for source in sources:
        relative = source.relative_to(common_root)
        mkdirs.append(f"install -d -m 755 {_to_posix(stage_root, relative.parent)!r}")
    prepared = _docker_exec(container, "; ".join(mkdirs), user="0", timeout=60.0)
    if prepared.returncode != 0:
        raise RuntimeError(
            f"could not prepare the agent-worktrees staging directory in '{container}': "
            f"{prepared.stderr.strip() or prepared.stdout.strip() or f'exit {prepared.returncode}'}"
        )
    for source in sources:
        relative = source.relative_to(common_root)
        _docker_cp(source, container, _to_posix(stage_root, relative.parent))
    chowned = _docker_exec(
        container,
        (
            f"set -euo pipefail; group=$(id -gn {user!r}); "
            f'chown -R {user!r}:"$group" {stage_root!r}'
        ),
        user="0",
        timeout=120.0,
    )
    if chowned.returncode != 0:
        raise RuntimeError(
            f"could not assign the staged agent-worktrees payload to '{user}' in '{container}': "
            f"{chowned.stderr.strip() or chowned.stdout.strip() or f'exit {chowned.returncode}'}"
        )
    marked = _docker_exec(
        container,
        f"touch {str(PurePosixPath(stage_root, _AW_PAYLOAD_READY_MARKER))!r}",
        user=user,
        timeout=120.0,
    )
    if marked.returncode != 0:
        raise RuntimeError(
            f"could not mark the staged agent-worktrees payload ready in '{container}': "
            f"{marked.stderr.strip() or marked.stdout.strip() or f'exit {marked.returncode}'}"
        )
    return _copied_path(stage_root, payload_root, common_root=common_root)


def _deploy_agent_worktrees_wrapper(container: str, payload_root: str | None = None) -> None:
    _docker_write(container, AGENT_WORKTREES_PATH, _agent_worktrees_wrapper(payload_root))


def ensure_agent_worktrees(container: str, *, user: str) -> None:
    """Ensure ``agent-worktrees`` is runnable on the detached-launch PATH."""
    home = _container_home(container, user=user)
    payload_root = (
        _agent_worktrees_payload_paths(home)[1]
        if _agent_worktrees_payload_ready(container, user=user, home=home)
        else None
    )
    if _agent_worktrees_ready(container, user=user) and payload_root:
        _deploy_agent_worktrees_wrapper(container, payload_root)
        return
    user_binstub = str(PurePosixPath(home, ".local/bin/agent-worktrees"))
    if payload_root is None:
        payload_root = _sync_agent_worktrees_payload(container, user=user, home=home)
    if _docker_exists(container, user_binstub, user=user):
        _deploy_agent_worktrees_wrapper(container, payload_root)
        if _agent_worktrees_ready(container, user=user):
            return
    install_dir = str(PurePosixPath(home, _AW_RUNTIME_REL))
    host_uv_index = _host_uv_index()
    install_env = {"UV_DEFAULT_INDEX": host_uv_index} if host_uv_index else None
    install = _docker_exec(
        container,
        (
            "set -euo pipefail; "
            f"cd {payload_root!r}; "
            f"bash scripts/install.sh provision --install-dir {install_dir!r}"
        ),
        user=user,
        env=install_env,
        timeout=900.0,
    )
    if install.returncode != 0:
        detail = (
            install.stderr.strip() or install.stdout.strip() or f"exit {install.returncode}"
        )[-3000:]
        raise RuntimeError(
            "could not provision agent-worktrees in the container with "
            f"`bash scripts/install.sh provision --install-dir {install_dir}`: {detail}"
        )
    _deploy_agent_worktrees_wrapper(container, payload_root)
    if not _agent_worktrees_ready(container, user=user):
        raise RuntimeError(
            "agent-worktrees finished provisioning but still is not runnable on the "
            "container PATH"
        )


def ensure_agent_worktrees_workspace_registered(
    container: str,
    *,
    user: str,
    workspace_folder: str,
    project_name: str | None = None,
) -> None:
    """Adopt the container workspace repo so detached ``embody`` can run there."""
    folder = PurePosixPath(workspace_folder)
    project = project_name or folder.name
    if not project:
        raise RuntimeError(
            "could not infer an agent-worktrees project name from the workspace folder"
        )
    register = _docker_exec(
        container,
        (
            "set -euo pipefail; "
            f"cd {str(folder)!r}; "
            f"{AGENT_WORKTREES_PATH!r} register {project!r} --repo-dir {str(folder)!r}"
        ),
        user=user,
        timeout=300.0,
    )
    if register.returncode != 0:
        detail = register.stderr.strip() or register.stdout.strip() or f"exit {register.returncode}"
        raise RuntimeError(
            f"could not register workspace repo {workspace_folder!r} as project {project!r}: "
            f"{detail}"
        )


def _docker_write(container: str, path: str, content: str, mode: str = "755") -> None:
    """Write ``content`` to ``path`` in the container (as root) and chmod it."""
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    script = f"echo {b64} | base64 -d > {path} && chmod {mode} {path}"
    res = subprocess.run(
        ["docker", "exec", "-u", "0", container, "bash", "-lc", script],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=no_window_flags(),
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"shim deploy failed for {path}: {res.stderr.strip() or res.stdout.strip()}"
        )


def git_credential_environment() -> dict[str, str]:
    """Launch-only Git config that makes the trusted helper authoritative."""
    return {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": ADO_HELPER_PATH,
        "GIT_TERMINAL_PROMPT": "0",
    }


def deploy(container: str, *, ado: bool = True) -> None:
    """Deploy the relay client + azure-auth-helper (idempotent) into ``container``.

    ``ado`` deploys the trusted launch's Git/ADO credential helper. It defaults
    on because the launch-only Git config returned by
    :func:`git_credential_environment` makes that helper authoritative without
    modifying the container user's persistent Git configuration.
    """
    _docker_write(container, RELAY_CLIENT_PATH, RELAY_CLIENT)
    _docker_write(container, AZURE_HELPER_PATH, AZURE_HELPER)
    if ado:
        _docker_write(container, ADO_HELPER_PATH, ADO_HELPER)
    log.info("Deployed relay shims into container '%s' (ado=%s)", container, ado)
