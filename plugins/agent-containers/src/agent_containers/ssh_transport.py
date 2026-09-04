"""OpenSSH transport for trusted fleet containers.

Docker remains the lifecycle/bootstrap boundary. Agent traffic crosses a real
OpenSSH connection to the container's ``sshd`` in inetd mode. POSIX hosts use a
direct ``ProxyCommand``; Windows uses a plugin-owned loopback byte broker so the
Docker CLI can be launched explicitly without a visible console.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agent_procutil import no_window_flags
from ssh_manager import SSHConfig, build_remote_exec_args

from .config import RUNTIME_DIR

log = logging.getLogger("agent-containers")

_SAFE_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SAFE_USER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_SAFE_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SSH_DIR = RUNTIME_DIR / "ssh"
_ENV_EXCLUDE = {"HOME", "LOGNAME", "PWD", "SHELL", "SHLVL", "USER", "_"}


def _creation_flags() -> int:
    return no_window_flags()


def _is_windows() -> bool:
    return os.name == "nt"


def _validate_target(container: str, user: str) -> None:
    if not _SAFE_CONTAINER.fullmatch(container):
        raise RuntimeError(f"Unsafe container name for SSH transport: {container!r}")
    if not _SAFE_USER.fullmatch(user):
        raise RuntimeError(f"Unsafe container user for SSH transport: {user!r}")


@contextmanager
def _prepare_lock(
    container: str,
    *,
    timeout: float = 15.0,
    poll: float = 0.05,
) -> Iterator[None]:
    """Serialize machine-key creation and per-container config publication."""
    _SSH_DIR.mkdir(parents=True, exist_ok=True)
    lock = _SSH_DIR / f"{container}.prepare.lock"
    deadline = time.monotonic() + timeout
    fd = None
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                try:
                    if time.time() - lock.stat().st_mtime > timeout * 3:
                        lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                raise RuntimeError(
                    f"Timed out preparing SSH transport for '{container}'"
                ) from None
            time.sleep(poll)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        lock.unlink(missing_ok=True)


def _run(
    args: list[str],
    *,
    input_text: str | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess:
    try:
        if input_text is None:
            return subprocess.run(
                args,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=_creation_flags(),
            )
        # Windows text-mode pipes translate LF to CRLF. These payloads are
        # consumed by Linux shell/read and must remain byte-exact; otherwise a
        # trailing CR becomes part of a staged token or authorized key.
        completed = subprocess.run(
            args,
            input=input_text.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            creationflags=_creation_flags(),
        )
        return subprocess.CompletedProcess(
            completed.args,
            completed.returncode,
            completed.stdout.decode("utf-8", "replace"),
            completed.stderr.decode("utf-8", "replace"),
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required transport command not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Transport command timed out: {args[0]}") from exc


def _ensure_host_key() -> tuple[Path, str]:
    """Return the machine-local private key and its OpenSSH public-key text."""
    with _prepare_lock("__host-key__"):
        _SSH_DIR.mkdir(parents=True, exist_ok=True)
        try:
            _SSH_DIR.chmod(0o700)
        except OSError:
            pass

        private_key = _SSH_DIR / "id_ed25519"
        ssh_keygen = shutil.which("ssh-keygen")
        if not ssh_keygen:
            raise RuntimeError("ssh-keygen is required for trusted-container SSH")

        if not private_key.exists():
            temp_key = private_key.with_name(
                f"{private_key.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            temp_public = Path(f"{temp_key}.pub")
            try:
                generated = _run([
                    ssh_keygen,
                    "-q",
                    "-t", "ed25519",
                    "-N", "",
                    "-C", "agent-containers",
                    "-f", str(temp_key),
                ])
                if generated.returncode != 0:
                    raise RuntimeError(
                        "Failed to generate the trusted-container SSH key: "
                        f"{generated.stderr.strip()}"
                    )
                os.replace(temp_key, private_key)
            finally:
                temp_key.unlink(missing_ok=True)
                temp_public.unlink(missing_ok=True)

        public = _run([ssh_keygen, "-y", "-f", str(private_key)])
        if public.returncode != 0 or not public.stdout.strip():
            raise RuntimeError(
                "Failed to read the trusted-container SSH public key: "
                f"{public.stderr.strip()}"
            )
        return private_key, public.stdout.strip()


def _container_id(container: str) -> str:
    inspected = _run(["docker", "inspect", "--format", "{{.Id}}", container])
    container_id = inspected.stdout.strip()
    if inspected.returncode != 0 or not re.fullmatch(
        r"[0-9a-fA-F]{12,64}", container_id
    ):
        raise RuntimeError(
            f"Could not resolve Docker identity for '{container}': "
            f"{inspected.stderr.strip() or inspected.stdout.strip()}"
        )
    return container_id.lower()


def _install_authorized_key(container: str, user: str, public_key: str) -> None:
    """Idempotently authorize the machine-local key for the configured user."""
    probe = _run([
        "docker", "exec", "-u", "root", container,
        "sh", "-c",
        "test -x /usr/sbin/sshd && command -v ssh-keygen >/dev/null && "
        "install -d -m 755 /run/sshd && ssh-keygen -A >/dev/null && "
        "/usr/sbin/sshd -t",
    ])
    if probe.returncode != 0:
        raise RuntimeError(
            f"Trusted container '{container}' is not SSH-ready; its image must "
            "provide sshd, ssh-keygen, host keys, and a valid sshd_config: "
            f"{probe.stderr.strip() or probe.stdout.strip() or f'exit {probe.returncode}'}"
        )

    prepare_script = r"""
set -eu
entry="$(getent passwd "$AGENT_CONTAINERS_SSH_USER")"
test -n "$entry"
home="$(printf '%s\n' "$entry" | cut -d: -f6)"
group="$(id -gn "$AGENT_CONTAINERS_SSH_USER")"
if awk -v home="$home" '
    $5 != "/" && (
        home == $5 ||
        index(home, $5 "/") == 1 ||
        index($5, home "/") == 1
    ) { found = 1 }
    END { exit !found }
' /proc/self/mountinfo; then
    echo "refusing SSH key projection across a mounted home path: $home" >&2
    exit 42
fi
test ! -L "$home/.ssh" || {
    echo "refusing symlinked SSH directory: $home/.ssh" >&2
    exit 43
}
install -d -m 700 -o "$AGENT_CONTAINERS_SSH_USER" -g "$group" "$home/.ssh"
test ! -L "$home/.ssh"
chown "$AGENT_CONTAINERS_SSH_USER:$group" "$home/.ssh"
chmod 700 "$home/.ssh"
"""
    prepared = _run([
        "docker", "exec", "-u", "root",
        "-e", f"AGENT_CONTAINERS_SSH_USER={user}",
        container, "sh", "-c", prepare_script,
    ])
    if prepared.returncode != 0:
        detail = (
            prepared.stderr.strip()
            or prepared.stdout.strip()
            or f"exit {prepared.returncode}"
        )
        raise RuntimeError(
            f"Failed to prepare SSH access in '{container}': {detail}"
        )

    install_script = r"""
set -eu
IFS= read -r AGENT_CONTAINERS_SSH_KEY
entry="$(getent passwd "$AGENT_CONTAINERS_SSH_USER")"
test -n "$entry"
home="$(printf '%s\n' "$entry" | cut -d: -f6)"
auth="$home/.ssh/authorized_keys"
test ! -L "$auth" || {
    echo "refusing symlinked authorized_keys: $auth" >&2
    exit 44
}
if test -e "$auth" && ! test -f "$auth"; then
    echo "refusing non-regular authorized_keys: $auth" >&2
    exit 45
fi
touch "$auth"
if ! grep -qxF "$AGENT_CONTAINERS_SSH_KEY" "$auth"; then
    if test -s "$auth"; then
        last_byte="$(tail -c 1 "$auth" | od -An -t u1 | tr -d ' ')"
        test "$last_byte" = "10" || printf '\n' >> "$auth"
    fi
    printf '%s\n' "$AGENT_CONTAINERS_SSH_KEY" >> "$auth"
fi
chmod 600 "$auth"
"""
    installed = _run([
        "docker", "exec", "-i", "-u", user,
        "-e", f"AGENT_CONTAINERS_SSH_USER={user}",
        container, "sh", "-c", install_script,
    ], input_text=public_key + "\n")
    if installed.returncode != 0:
        detail = (
            installed.stderr.strip()
            or installed.stdout.strip()
            or f"exit {installed.returncode}"
        )
        raise RuntimeError(
            f"Failed to authorize SSH access to '{container}': "
            f"{detail}"
        )


def _config_path(path: Path) -> str:
    # OpenSSH config accepts forward slashes on every supported host, including
    # Windows, and avoids treating backslashes as escapes inside quoted values.
    return str(path.resolve()).replace("\\", "/")


def prepare_ssh_config(container: str, user: str) -> SSHConfig:
    """Provision trusted-container SSH access and return its shared config."""
    _validate_target(container, user)
    with _prepare_lock(container):
        private_key, public_key = _ensure_host_key()
        _install_authorized_key(container, user, public_key)
        container_id = _container_id(container)
        alias = f"agent-container-{container}-{container_id[:12]}"
        proxy_port: int | None = None
        if _is_windows():
            from .docker_proxy import ensure_broker

            proxy_port = ensure_broker(
                container,
                container_id,
                _SSH_DIR / f"{container}.proxy.json",
            )
        config_file = _SSH_DIR / f"{container}.config"
        known_hosts = _SSH_DIR / "known_hosts"
        lines = [
            f"Host {alias}",
            f"    HostName {'127.0.0.1' if proxy_port else alias}",
        ]
        if proxy_port:
            lines.extend([
                f"    Port {proxy_port}",
                f"    HostKeyAlias {alias}",
            ])
        lines.extend([
            f"    User {user}",
            "    IdentitiesOnly yes",
            "    BatchMode yes",
            "    StrictHostKeyChecking accept-new",
            f'    UserKnownHostsFile "{_config_path(known_hosts)}"',
        ])
        if not proxy_port:
            lines.append(
                f"    ProxyCommand docker exec -i -u root {container} "
                "/usr/sbin/sshd -i -e -o GatewayPorts=no"
            )
        lines.extend([
            "    LogLevel ERROR",
            "",
        ])
        content = "\n".join(lines)
        try:
            current = config_file.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current != content:
            temp = config_file.with_name(
                f"{config_file.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            temp.write_text(content, encoding="utf-8")
            try:
                temp.chmod(0o600)
            except OSError:
                pass
            os.replace(temp, config_file)
    return SSHConfig(
        host_alias=alias,
        user=user,
        identity_file=str(private_key),
        config_file=str(config_file),
    )


def container_environment(container: str, user: str) -> dict[str, str]:
    """Read the trusted container's effective non-session environment."""
    _validate_target(container, user)
    result = _run(["docker", "exec", "-u", user, container, "env", "-0"])
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not read the environment in '{container}': "
            f"{result.stderr.strip() or f'exit {result.returncode}'}"
        )
    values: dict[str, str] = {}
    for item in result.stdout.split("\0"):
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        if _SAFE_ENV.fullmatch(name) and name not in _ENV_EXCLUDE:
            values[name] = value
    return values


def _remote_home(container: str, user: str) -> str:
    resolved = _run([
        "docker", "exec", "-u", user,
        "-e", f"AGENT_CONTAINERS_SSH_USER={user}",
        container,
        "sh", "-c",
        "getent passwd \"$AGENT_CONTAINERS_SSH_USER\" | cut -d: -f6",
    ])
    home = resolved.stdout.strip()
    if resolved.returncode != 0 or not home.startswith("/") or "\n" in home:
        raise RuntimeError(
            f"Could not resolve the home directory for '{user}' in "
            f"'{container}': {resolved.stderr.strip() or resolved.stdout.strip()}"
        )
    return home


def write_remote_env(container: str, user: str, values: dict[str, str]) -> str | None:
    """Write launch-only environment values through stdin, never argv."""
    if not values:
        return None
    _validate_target(container, user)
    invalid = sorted(name for name in values if not _SAFE_ENV.fullmatch(name))
    if invalid:
        raise RuntimeError(f"Unsafe environment names for SSH launch: {invalid}")
    launch_dir = f"{_remote_home(container, user)}/.agent-containers/launch"
    remote_path = f"{launch_dir}/{uuid.uuid4().hex}.env"
    payload = "".join(
        f"export {name}={shlex.quote(value)}\n"
        for name, value in sorted(values.items())
    )
    written = _run([
        "docker", "exec", "-i", "-u", user, container,
        "sh", "-c",
        f"umask 077; mkdir -p {shlex.quote(launch_dir)}; "
        f"chmod 700 {shlex.quote(launch_dir)}; "
        f"find {shlex.quote(launch_dir)} -maxdepth 1 -type f "
        "-name '*.env' -mmin +10 -delete; "
        f"cat > {shlex.quote(remote_path)}",
    ], input_text=payload)
    if written.returncode != 0:
        raise RuntimeError(
            f"Failed to stage the SSH launch environment in '{container}': "
            f"{written.stderr.strip()}"
        )
    return remote_path


def cleanup_remote_env(container: str, user: str, remote_path: str | None) -> None:
    if not remote_path:
        return
    _validate_target(container, user)
    try:
        result = _run([
            "docker", "exec", "-u", user, container,
            "rm", "-f", remote_path,
        ])
    except RuntimeError as exc:
        log.warning("Could not clean SSH launch environment in %s: %s", container, exc)
        return
    if result.returncode != 0:
        log.warning(
            "Could not clean SSH launch environment in %s: %s",
            container,
            result.stderr.strip(),
        )


def cleanup_remote_envs(container: str, user: str) -> None:
    """Remove abandoned launch-only env files before preparing a new launch."""
    _validate_target(container, user)
    launch_dir = f"{_remote_home(container, user)}/.agent-containers/launch"
    result = _run([
        "docker", "exec", "-u", user, container,
        "sh", "-c",
        f"if test -d {shlex.quote(launch_dir)}; then "
        f"find {shlex.quote(launch_dir)} -maxdepth 1 -type f "
        "-name '*.env' -delete; fi",
    ])
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not clear abandoned SSH launch environments in "
            f"'{container}': {result.stderr.strip() or result.stdout.strip()}"
        )


def build_remote_command(acp_command: str, remote_env: str | None) -> str:
    """Build the remote shell command without embedding credential values."""
    inner = acp_command
    if remote_env:
        path = shlex.quote(remote_env)
        inner = f". {path}; rm -f {path}; {acp_command}"
    # sshd asks the account's login shell to interpret the remote command.
    # Keep that outer command shell-agnostic; all POSIX staging syntax belongs
    # inside the Bash process the fleet contract already requires.
    return f"exec bash -lc {shlex.quote(inner)}"


def build_ssh_command(
    config: SSHConfig,
    remote_command: str,
    *,
    reverse_forwards: list[str] | None = None,
) -> list[str]:
    """Build the shared OpenSSH remote-exec argv for the ACP stdio channel."""
    if not shutil.which("ssh"):
        raise RuntimeError("ssh is required for trusted-container transport")
    return build_remote_exec_args(
        config,
        remote_command,
        reverse_forwards=reverse_forwards,
    )
