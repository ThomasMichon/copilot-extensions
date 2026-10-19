"""Private tmpfs storage for trusted-container launch inputs."""

from __future__ import annotations

import posixpath
import shlex

RAM_ROOT = "/dev/shm"
EXEC_ROOT = "/tmp"


def launch_directory(user: str) -> str:
    return f"{RAM_ROOT}/agent-containers-{user}/launch"


def executable_root_for_launch_dir(directory: str) -> str:
    if directory.startswith(RAM_ROOT + "/agent-containers-"):
        return EXEC_ROOT + directory[len(RAM_ROOT):]
    return directory


def executable_directory(auth_env: str) -> str:
    owner = auth_env.removesuffix(".d/auth.env")
    return posixpath.join(
        executable_root_for_launch_dir(posixpath.dirname(owner)),
        posixpath.basename(owner) + ".d",
    )


def directory_guard(
    directory: str, *, create: bool = False, require_ram: bool = True,
    missing_ok: bool = False,
) -> str:
    """Validate before reading stdin; pin subsequent relative writes by cwd."""
    if (
        not directory.startswith("/")
        or posixpath.normpath(directory) != directory
        or directory.startswith("//")
    ):
        # POSIX normpath() deliberately preserves an *exact* double leading
        # slash (3+ collapse to one, already caught above): dirname("//")
        # == "//" forever, so the ancestor walk below would never reach "/"
        # and would grow `ancestors` without bound.
        raise RuntimeError("launch storage requires a normalized absolute directory")
    parent = posixpath.dirname(directory)
    base = posixpath.dirname(parent) if create else directory
    ancestors: list[str] = []
    current = base
    while current != "/":
        ancestors.append(current)
        current = posixpath.dirname(current)
    ancestors.append("/")
    quoted = shlex.quote(directory)
    script = (
        "set -eu\numask 077\n"
        "refuse_launch_storage() { "
        "echo 'trusted launch storage must be a private, non-symlink tmpfs directory' >&2; "
        "exit 1; }\n"
        f"for ancestor in {shlex.join(list(reversed(ancestors)))}; do\n"
        'test -d "$ancestor" && test ! -L "$ancestor" || refuse_launch_storage\n'
        "done\n"
    )
    if require_ram:
        script += f'test "$(stat -f -c %T -- {shlex.quote(base)})" = tmpfs || refuse_launch_storage\n'
    if create:
        script += (
            f"for private in {shlex.join([parent, directory])}; do\n"
            'if test ! -e "$private" && test ! -L "$private"; then\n'
            'mkdir -m 700 -- "$private" || test -d "$private" || refuse_launch_storage\n'
            "fi\n"
            'test ! -L "$private" && test -d "$private" || refuse_launch_storage\n'
            'test "$(stat -c %u -- "$private")" = "$(id -u)" || refuse_launch_storage\n'
            'test "$(stat -c %a -- "$private")" = 700 || refuse_launch_storage\n'
            "done\n"
        )
    script += (
        f"cd -- {quoted} || refuse_launch_storage\n"
        f'test "$(pwd -P)" = {quoted} || refuse_launch_storage\n'
        'test "$(stat -c %u -- .)" = "$(id -u)" || refuse_launch_storage\n'
        'test "$(stat -c %a -- .)" = 700 || refuse_launch_storage\n'
    )
    if require_ram:
        script += 'test "$(stat -f -c %T -- .)" = tmpfs || refuse_launch_storage\n'
    if missing_ok:
        script = (
            "set -eu\n"
            f"for ancestor in {shlex.join(list(reversed(ancestors)))}; do\n"
            'test ! -L "$ancestor" || { '
            "echo 'refusing symlinked launch cleanup storage' >&2; exit 1; }\n"
            "done\n"
            f"if test -e {quoted}; then\n" + script + "fi\n"
        )
    return script


def preparation_guard(user: str) -> str:
    directory = launch_directory(user)
    executable = executable_root_for_launch_dir(directory)
    return (
        directory_guard(executable, require_ram=False, missing_ok=True)
        + directory_guard(directory, create=True)
    )
