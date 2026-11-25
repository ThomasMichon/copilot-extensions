"""Effect-free selection of bounded early CLI entries."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict, cast

from .list_arguments import ARGUMENTS
from .configuration_entry import select_configuration_read


def select_read(argv: list[str]) -> Callable[[], int] | None:
    from .pr_read_entry import select_read as select

    return select(argv)


class _ListArgumentOptions(TypedDict, total=False):
    action: str
    choices: list[str]


def select_current_feed(argv: list[str]) -> Callable[[], int] | None:
    if (
        len(argv) < 4 or argv[0] != "--project" or not argv[1].strip()
        or argv[1].startswith("-") or argv[2] != "list"
    ):
        return None
    definitions = dict(cast(tuple[tuple[str, _ListArgumentOptions], ...], ARGUMENTS))
    seen: set[str] = set()
    index = 3
    while index < len(argv):
        flag = argv[index]
        if flag not in definitions or flag in seen:
            return None
        seen.add(flag)
        options = definitions[flag]
        index += 1
        if options.get("action") != "store_true":
            if index == len(argv) or argv[index].startswith("-"):
                return None
            if "choices" in options and argv[index] not in options["choices"]:
                return None
            index += 1
    if "--follow" not in seen:
        return None
    captured = tuple(argv)
    return lambda: _run_current_feed(captured)


def _run_current_feed(argv: tuple[str, ...]) -> int:
    import argparse
    import os
    import sys
    from pathlib import Path
    from . import config as cfg, invocation_project as context, output
    from .list_arguments import add_parsers

    try:
        invocation_cwd = Path.cwd()
    except OSError:
        invocation_cwd = None
    core = sys.modules.get("agent_worktrees.__main__")
    if core is not None:
        setattr(core, "_INVOCATION_CWD", invocation_cwd)
    output.ensure_utf8_stdio()
    extract_project = cast(
        Callable[[list[str]], tuple[list[str], str | None]], context._extract_project_flag,
    )
    args_list, explicit = extract_project(list(argv))
    os.environ.pop("AGENT_WORKTREES_PROJECT_ROUTED", None)
    resolve_project = cast(
        Callable[
            [
                list[str], str | None,
                Callable[[str | None], tuple[str | None, Path | None]],
                Callable[[Path], bool], Callable[[list[str]], bool],
            ],
            bool,
        ],
        context.resolve_invocation_project,
    )
    has_project = resolve_project(
        args_list, explicit, context._resolve_active_project,
        context._cwd_is_inside_project, lambda _: False,
    )
    if not has_project:
        from .front_door_cli import cmd_help_unrouted

        return cmd_help_unrouted(requested="list")
    parser = argparse.ArgumentParser(prog="agent-worktrees")
    add_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(args_list)
    from .worktrees_client import emit_current

    try:
        cfg.project_name()
        return emit_current(args)
    except (FileNotFoundError, ValueError) as exc:
        output.err(str(exc))
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130


def run_module_if_selected() -> None:
    import sys

    selected = (
        select_configuration_read(sys.argv[1:])
        or select_current_feed(sys.argv[1:]) or select_read(sys.argv[1:])
    )
    if selected is not None:
        from ._shutdown_exit import run_and_exit

        run_and_exit(selected)


def run_legacy_console(main: Callable[[], int]) -> None:
    """Keep the existing shutdown policy around the caller's current handler.

    The root compatibility entry supplies its late-bound main callable, rather
    than importing the root here. This keeps legacy callers and the installed
    entry's fallback on the same interrupt, atexit and stream-flush policy.
    The exact private Source setup operation retains that same shutdown owner.
    """
    from ._shutdown_exit import run_and_exit
    import sys

    if sys.argv[1:] == ["__source-setup"]:
        from .application_setup import setup_application

        main = setup_application
    run_and_exit(main)


def console_entry() -> None:
    import sys

    selected = (
        select_configuration_read(sys.argv[1:])
        or select_current_feed(sys.argv[1:]) or select_read(sys.argv[1:])
    )
    if selected is None:
        from .__main__ import console_entry as legacy_entry

        return legacy_entry()
    from ._shutdown_exit import run_and_exit

    return run_and_exit(selected)
