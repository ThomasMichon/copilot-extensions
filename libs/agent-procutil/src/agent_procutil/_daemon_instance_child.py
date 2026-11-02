"""Same-process admission for leased Python commands, including source probes."""

from __future__ import annotations

from pathlib import Path
import runpy
import sys

_BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv.pop(1));"
    "from agent_procutil._daemon_instance_child import main;"
    "sys.path.pop(0);main()"
)


def normalize_python_command(command: list[str]) -> tuple[list[str], dict[str, str]]:
    name = Path(command[0]).name.lower()
    if not (name.startswith("python") or name in ("pypy", "pypy3")):
        return command, {}
    from . import direct_python
    executable, environment = direct_python(command[0])
    return [executable, *command[1:]], environment


def leased_python_command(command: list[str]) -> tuple[list[str], dict[str, str]]:
    name = Path(command[0]).name.lower()
    if not (name.startswith("python") or name in ("pypy", "pypy3")):
        raise ValueError("leased opaque executables require their native startup admission")
    index = 1
    while index < len(command):
        part = command[index]
        if part in ("-m", "-c") or not part.startswith("-"):
            break
        if part in ("-W", "-X"):
            index += 2
        elif part in ("-u", "-B", "-E", "-s", "-S", "-I", "-O", "-OO", "-P"):
            index += 1
        else:
            raise ValueError("unsupported leased Python invocation")
    if index >= len(command):
        raise ValueError("leased Python invocation requires an entrypoint")
    normalized, environment = normalize_python_command(command)
    return [
        normalized[0], *command[1:index], "-c", _BOOTSTRAP,
        str(Path(__file__).resolve().parent.parent), *command[index:],
    ], environment


def main() -> None:
    # The absolute helper also works from a source-only package without install.
    if not __package__:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agent_procutil.daemon_instances import admit_daemon_instance

    arguments = sys.argv[1:]
    if not arguments:
        raise ValueError("daemon entrypoint missing")
    with admit_daemon_instance("spawned-daemon"):
        if arguments[0] == "-m":
            sys.argv = arguments[1:]
            runpy.run_module(arguments[1], run_name="__main__", alter_sys=True)
        elif arguments[0] == "-c":
            sys.argv = ["-c", *arguments[2:]]
            exec(compile(arguments[1], "<string>", "exec"), {
                "__name__": "__main__", "__package__": None, "__spec__": None,
            })
        else:
            sys.argv = arguments
            if not getattr(sys.flags, "safe_path", sys.flags.isolated):
                sys.path[0:1] = [str(Path(arguments[0]).absolute().parent)]
            runpy.run_path(arguments[0], run_name="__main__")


if __name__ == "__main__":
    main()
