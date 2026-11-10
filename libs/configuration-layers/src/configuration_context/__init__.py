"""Read-data transport for an already-qualified configuration owner.

The caller owns qualification of the command prefix and optionality. This module
does not discover registries, trust repositories, resolve peers or bind knowledge.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict, cast

from configuration_layers import ConfigurationError, plan_layers

MAX_REPORT_BYTES = 64 * 1024
CONTEXT_ENV = "AGENT_CONFIGURATION_CONTEXT"


class RepositoryContextRequired(ConfigurationError):
    """The qualified owner cannot select a repository from this directory."""

    def __init__(self) -> None:
        super().__init__(
            "Repository context is missing. Run from an adopted repository "
            "or use its '<repo> <subject> ...' command from any directory."
        )


class _ProcessOptions(TypedDict, total=False):
    creationflags: int


def ready_owner_prefix(
    runtime_root: str | os.PathLike[str] | None, module: str, *,
    resolver: Callable[[Path], str | os.PathLike[str] | None],
) -> list[str]:
    """Use the caller's canonical completion-gated resolver; never bootstrap."""
    if not isinstance(module, str) or not all(
        part.isidentifier() for part in module.split(".")
    ):
        raise ConfigurationError("configuration owner module must be an import name")
    if not callable(resolver):
        raise ConfigurationError("configuration owner requires a qualified runtime resolver")
    if runtime_root is None:
        return []
    root = Path(runtime_root)
    if not root.is_absolute():
        raise ConfigurationError("configuration owner runtime root must be absolute")
    try:
        python = resolver(root)
    except (OSError, ValueError, RuntimeError) as error:
        raise ConfigurationError("configuration owner readiness read failed") from error
    if python is None:
        return []
    executable = Path(python)
    if not executable.is_absolute():
        raise ConfigurationError("configuration owner resolver returned a relative interpreter")
    return [str(executable), "-m", module]


def context_from_owner(report: object) -> dict[str, Any]:
    """Detach the composer context from the native owner's read-side report."""
    if (
        not isinstance(report, dict)
        or report.get("schema") != "agent-worktrees.configuration"
        or type(report.get("version")) is not int
        or report["version"] != 1
    ):
        raise ConfigurationError("configuration owner returned an invalid report")
    context = report.get("context")
    plan_layers(context, [])
    root = report.get("repository_machine_root")
    if root is not None and (not isinstance(root, str) or not Path(root).is_absolute()):
        raise ConfigurationError("configuration owner returned an invalid machine root")
    return cast(dict[str, Any], json.loads(json.dumps(context)))


def delivered_context(
    environment: Mapping[str, str] | None = None, *,
    repository_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """Read the native launcher's data projection, without owner discovery.

    Data is not authority: consumers must still apply their own source, trust
    and operation gates. The launcher reserves this name after user env merge.
    """
    raw = (os.environ if environment is None else environment).get(CONTEXT_ENV, "")
    if not raw:
        return None
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ConfigurationError("configuration projection exceeds the read bound")
    try:
        report = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise ConfigurationError("configuration projection is not JSON") from error
    context = context_from_owner(report)
    if repository_root is not None and (
        "base_root" not in context
        or Path(context["base_root"]).resolve() != Path(repository_root).resolve()
    ):
        return None
    return cast(dict[str, Any], report)


def read_owner_context(
    prefix: Sequence[str], repository_root: str | os.PathLike[str], *,
    required: bool = False, environment: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Read Worktrees data through a caller-qualified native command prefix.

    A namespaced caller must supply its existing same-cell peer launch prefix,
    never an ambient executable. Missing optional legacy owners return None;
    present-but-invalid reports and explicit refusals always fail.
    """
    if not prefix:
        if required:
            raise ConfigurationError("required configuration owner is unavailable")
        return None
    if not isinstance(prefix, (list, tuple)) or not all(
        isinstance(item, str) and item for item in prefix
    ):
        raise ConfigurationError("configuration owner prefix must be native argv")
    options: _ProcessOptions = (
        {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    )
    try:
        result = subprocess.run(
            [*prefix, "state-root", "--configuration", "--json"],
            cwd=repository_root,
            env=environment,
            capture_output=True,
            timeout=20,
            check=False,
            **options,
        )
    except FileNotFoundError as error:
        if required:
            raise ConfigurationError("required configuration owner is unavailable") from error
        return None
    except (OSError, subprocess.SubprocessError) as error:
        raise ConfigurationError("configuration owner read failed") from error
    if result.returncode:
        diagnostic = result.stderr[:MAX_REPORT_BYTES].decode("utf-8", errors="replace")
        unsupported = result.returncode == 2 and (
            "unrecognized arguments: --configuration" in diagnostic
            or "unknown option: --configuration" in diagnostic
        )
        if unsupported and not required:
            return None
        output = result.stdout[:MAX_REPORT_BYTES].decode("utf-8", errors="replace")
        if "Could not resolve a project for 'state-root'." in output or (
            "Could not resolve a project for 'state-root'." in diagnostic
        ):
            raise RepositoryContextRequired()
        raise ConfigurationError("configuration owner refused the selected context")
    if len(result.stdout) > MAX_REPORT_BYTES:
        raise ConfigurationError("configuration owner report exceeds the read bound")
    try:
        report = json.loads(result.stdout)
    except (ValueError, UnicodeError) as error:
        raise ConfigurationError("configuration owner report is not JSON") from error
    context_from_owner(report)
    return cast(dict[str, Any], report)
