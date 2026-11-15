"""Retain the selected Bridge build for origin negotiation and acquisition."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import peer_launch
from .install_paths import install_dir, uses_legacy_install_dir
from .procutil import no_window_kwargs, resolve_runtime_python


def _legacy_owner(own_root: Path) -> Path:
    if not uses_legacy_install_dir(own_root):
        raise ValueError("non-legacy Dispatch requires an installation context")
    root = Path.home() / ".agent-bridge"  # marketplace-isolation: allow legacy-compatibility
    tombstone = root / ".installation-ownership.json"
    if tombstone.exists() or tombstone.is_symlink():
        raise ValueError("legacy Bridge owner has installation-cell ownership")
    return root


def retained_selection() -> tuple[str, ...]:
    own_root = install_dir()
    context = os.environ.get(peer_launch.CONTEXT_ENV, "")
    if context:
        prefix, _python = peer_launch.pinned_launch_prefix(
            "agent-dispatch", own_root, context, "agent-bridge",
        )
        return tuple(prefix)
    root = _legacy_owner(own_root)
    python = resolve_runtime_python(root)
    if python is None:
        raise ValueError("selected Bridge runtime is unavailable")
    identity = peer_launch.retained_runtime_identity(root, python, "agent_bridge")
    bootstrap = Path(sys.executable)
    if bootstrap.name.lower() == "pythonw.exe":
        bootstrap = bootstrap.with_name("python.exe")
    return (
        str(bootstrap), "-I", "-B", "-X", "utf8", "-m",
        "agent_dispatch.bridge_owner_launch", str(own_root),
        "--pinned-runtime", str(python), "--runtime-identity", identity,
    )


def main() -> int:
    try:
        arguments = sys.argv[1:]
        if (
            len(arguments) < 5 or arguments[1] != "--pinned-runtime"
            or arguments[3] != "--runtime-identity"
        ):
            raise ValueError("expected owner root, retained interpreter and original build identity")
        if os.environ.get(peer_launch.CONTEXT_ENV, ""):
            raise ValueError("legacy Bridge launch cannot adopt an installation context")
        root = _legacy_owner(Path(arguments[0]))
        prefix = peer_launch.retained_runtime_prefix(
            root, Path(arguments[2]), "agent_bridge", runtime_identity=arguments[4],
        )
        command = [*prefix, *arguments[5:]]
        environment = peer_launch.legacy_peer_environment(dict(os.environ))
        if os.name != "nt":
            os.execve(command[0], command, environment)
        return subprocess.call(
            command, env=environment, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
            **no_window_kwargs(),
        )
    except (OSError, ValueError, ImportError, subprocess.SubprocessError) as error:
        print(f"bridge owner launch refused: {error}", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
