"""Operation-scoped repository discovery without changing the caller directory."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
from typing import Any, TypedDict

_ROOT: ContextVar[str | None] = ContextVar("vault_configuration_root", default=None)
_NATIVE_DEFAULT: ContextVar[bool] = ContextVar("vault_native_default_owner", default=False)
CACHE_TARGET_CAPABILITY = "repository-cache-targets-v1"
CACHE_TARGET_FIELDS = frozenset({"cache_entries", "cache_manifests"})
CACHE_MAINTENANCE_FIELD = "cache_maintenance"


def cache_maintenance_owner(data: dict[str, Any], label: str = "vault configuration") -> str:
    value = data.get(CACHE_MAINTENANCE_FIELD, "local")
    if not isinstance(value, str) or value not in ("local", "selected-service"):
        raise RuntimeError(f"{label}: cache_maintenance must be local or selected-service")
    return value


FIRST_USE_ROOT_ENV = "_AGENT_VAULT_FIRST_USE_ROOT"


def configuration_root(cwd: str | None = None) -> str | None:
    selected = cwd if cwd is not None else _ROOT.get()
    if selected is not None:
        return selected
    if os.environ.get("COPILOT_PLUGIN_INSTALL_STAGED") != "1":
        return None
    retained = os.environ.get(FIRST_USE_ROOT_ENV)
    if retained is None:
        return None
    project = os.environ.get("COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT")
    path = Path(retained)
    if (
        not retained or any(ord(char) < 32 for char in retained)
        or not path.is_absolute() or not path.is_dir()
        or not project or not Path(project).is_absolute()
        or path.resolve() != Path(project).resolve()
    ):
        raise ValueError("first-use configuration root does not match its staged project")
    return str(path)


@contextmanager
def use_configuration_root(root: str | None, *, native_default: bool = False) -> Iterator[None]:
    if root is None:
        yield
        return
    token = _ROOT.set(root)
    provenance = _NATIVE_DEFAULT.set(native_default)
    try:
        yield
    finally:
        _NATIVE_DEFAULT.reset(provenance)
        _ROOT.reset(token)


def uses_native_default() -> bool:
    return _NATIVE_DEFAULT.get()


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


class InvocationContract(TypedDict):
    schema: str
    version: int
    capabilities: list[str]


def invocation_contract() -> InvocationContract:
    contract: InvocationContract = {
        "schema": "agent-vault.invocation",
        "version": 1,
        "capabilities": [
            "configuration-root-v1", "batch-get-v1", "ssh-key-install-pin-v1",
            "authenticated-curl-v1", "daemon-bind-default-v1", "preserved-cache-recovery-v1",
            "credential-read-require-running-v1",
            "prepared-application-no-service-v1",
            "windows-machine-cache-v1",
            CACHE_TARGET_CAPABILITY,
            *(["key-attachment-create-only-v1"] if os.name != "nt" else []),
            *(["key-attachment-exclusive-export-v1"] if os.name == "posix" else []),
            "service-cache-maintenance-v1",
        ],
    }
    contract["capabilities"].append("native-default-owner-v1")
    return contract


def invoke_cli(command: Callable[[list[str]], int | None], argv: list[str]) -> int | None:
    root, remaining = parse_invocation(list(argv))
    if root is not None and os.environ.pop("_AGENT_WORKTREES_VAULT_RUNTIME_GUARD", None) == "1":
        os.environ.pop("AGENT_VAULT_NO_SELFPROVISION", None)
    with use_configuration_root(root):
        if remaining == ["invocation-contract", "--json"]:
            print(json.dumps(invocation_contract()))
            return 0
        return command(remaining)
