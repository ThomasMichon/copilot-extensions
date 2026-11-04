"""Bind guarded wheel filtering to the resolver's native interpreter."""
from __future__ import annotations

import os
import platform
import sys

import installer_dependency_policy as dependencies
import installer_native_feed as native_feed
from packaging.tags import sys_tags


def require_native_target(transaction, projection: str, target_tags: list[str]) -> None:
    try:
        pairs = list(dependencies._argument_pairs(transaction.argv[3:]))
        pythons = [value for option, value in pairs if option in ("--python", "-p")]
        versions = [value for option, value in pairs if option == "--python-version"]
        outer = native_feed._projection(projection, "PipCompileSettings")
        settings = native_feed._fields(native_feed._body(outer["settings"], "PipSettings"))
        reported = native_feed._optional(settings["python"])
        current = os.path.normcase(os.path.realpath(sys.executable))
        if (
            target_tags != [str(tag) for tag in sys_tags()]
            or len(pythons) != 1 or os.path.normcase(os.path.realpath(pythons[0])) != current
            or versions != [platform.python_version()]
            or reported is None or os.path.normcase(os.path.realpath(reported)) != current
            or settings.get("python_platform") != "None" or settings.get("universal") != "false"
        ):
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError("guarded resolver target differs from the native interpreter") from None
