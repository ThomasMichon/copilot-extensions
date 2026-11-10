"""Early entry for explicit context-free reads through the existing PR handler."""

from __future__ import annotations


def select_read(argv):
    if argv[:2] == ["pr-watch", "inventory"] and not any(
        arg in ("--project", "-p") or arg.startswith("--project=") for arg in argv
    ):
        return lambda: _run_read(tuple(argv[1:]))
    if (
        len(argv) < 4 or argv[0] != "pr-watch"
        or argv[1] not in ("snapshot", "status", "threads", "diff")
        or argv[2].startswith("-") or argv[2].count("/") != 1
        or not all(argv[2].split("/")) or not argv[3].isdecimal()
        or any(arg in ("--project", "-p") or arg.startswith("--project=") for arg in argv)
    ):
        return None
    captured = tuple(argv[1:])
    return lambda: _run_read(captured)


def _run_read(argv):
    from . import output
    from .pr_cli import cmd_pr_watch_dispatch

    output.ensure_utf8_stdio()
    try:
        return cmd_pr_watch_dispatch(list(argv))
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
