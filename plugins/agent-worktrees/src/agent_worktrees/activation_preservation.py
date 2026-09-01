"""Preserve user activation while refreshing Copilot plugin inventory."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from plugin_activation import (
    ActivationSnapshot,
    PluginStateError,
    capture,
    installed_plugin_identities,
    restore,
    run_install_preserving_activation,
)

__all__ = [
    "ActivationSnapshot",
    "PluginStateError",
    "capture",
    "installed_plugin_identities",
    "restore",
    "run_install_preserving_activation",
]


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("identity")
    parser.add_argument("--copilot", default="copilot")
    args = parser.parse_args(argv)
    try:
        result = run_install_preserving_activation(
            [args.copilot, "plugin", "install", args.identity],
            args.identity,
        )
    except PluginStateError as exc:
        parser.error(str(exc))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(_main())
