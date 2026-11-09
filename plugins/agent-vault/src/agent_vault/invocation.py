"""Operation-scoped repository discovery without changing the caller directory."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path

_ROOT: ContextVar[str | None] = ContextVar("vault_configuration_root", default=None)


def configuration_root(cwd: str | None = None) -> str | None:
    return cwd if cwd is not None else _ROOT.get()


@contextmanager
def use_configuration_root(root: str | None) -> Iterator[None]:
    if root is None:
        yield
        return
    token = _ROOT.set(root)
    try:
        yield
    finally:
        _ROOT.reset(token)


def _directory(value: str) -> str:
    path = Path(value)
    try:
        valid = path.is_absolute() and path.is_dir()
    except (OSError, ValueError):
        valid = False
    if not valid:
        raise argparse.ArgumentTypeError(
            "--configuration-root must name an existing absolute directory"
        )
    return str(path)


def parse_invocation(argv: list[str]) -> tuple[str | None, list[str]]:
    parser = argparse.ArgumentParser(prog="agent-vault", add_help=False, allow_abbrev=False)
    parser.add_argument("--configuration-root", type=_directory)
    selectors = argv[:argv.index("--")] if "--" in argv else argv
    if sum(
        value == "--configuration-root" or value.startswith("--configuration-root=")
        for value in selectors
    ) > 1:
        parser.error("--configuration-root may be supplied only once")
    args, remaining = parser.parse_known_args(argv)
    return args.configuration_root, remaining


def invocation_contract() -> dict:
    return {
        "schema": "agent-vault.invocation",
        "version": 1,
        "capabilities": [
            "configuration-root-v1", "batch-get-v1", "ssh-key-install-pin-v1",
            "authenticated-curl-v1",
        ],
    }


def invoke_cli(command: Callable[[list[str]], int | None], argv: list[str]) -> int | None:
    root, remaining = parse_invocation(list(argv))
    if root is not None and os.environ.pop("_AGENT_WORKTREES_VAULT_RUNTIME_GUARD", None) == "1":
        os.environ.pop("AGENT_VAULT_NO_SELFPROVISION", None)
    with use_configuration_root(root):
        if remaining == ["invocation-contract", "--json"]:
            print(json.dumps(invocation_contract()))
            return 0
        return command(remaining)
