#!/usr/bin/env python3
"""Inspect and narrow Copilot plugin activation without uninstalling inventory."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


def _load_state_module() -> ModuleType:
    plugin_root = Path(__file__).resolve().parents[3]
    state_path = (
        plugin_root
        / "libs"
        / "plugin-activation"
        / "src"
        / "plugin_activation"
        / "state.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_customizing_copilot_plugin_activation_state",
        state_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load plugin activation state library: {state_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_STATE = _load_state_module()
PluginStateError = _STATE.PluginStateError


def _render(state: dict[str, object]) -> None:
    print(f"Plugin: {state['identity']}")
    print(f"Installed inventory: {'yes' if state['installed'] else 'no'}")
    print(f"User activation: {state['userActivation']}")
    if state["repositoryActivation"] is not None:
        print(f"Repository activation: {state['repositoryActivation']}")
        print(f"Repository trusted: {'yes' if state['repositoryTrusted'] else 'no'}")
    print(
        "Installed but not user-enabled: "
        f"{'yes' if state['installedButNotUserEnabled'] else 'no'}"
    )


def _inspect(args: argparse.Namespace) -> int:
    state = _STATE.inspect_plugin_state(
        args.identity,
        args.copilot_home.expanduser().resolve(),
        args.repo,
    )
    if args.json:
        print(json.dumps(state, indent=2))
    else:
        _render(state)
    return 0


def _remove(args: argparse.Namespace) -> int:
    result = _STATE.remove_user_activation(
        args.identity,
        args.copilot_home.expanduser().resolve(),
        apply=args.apply,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _render(result)
        print(f"Mode: {result['mode']}")
        if result["changes"]:
            for change in result["changes"]:
                print(f"- {change}")
        else:
            print("No changes.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or narrow Copilot plugin activation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, handler in (
        ("inspect", _inspect),
        ("remove-user-activation", _remove),
    ):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("identity")
        subparser.add_argument(
            "--copilot-home",
            type=Path,
            default=Path.home() / ".copilot",
        )
        subparser.add_argument("--repo", type=Path)
        subparser.add_argument("--json", action="store_true")
        if command == "remove-user-activation":
            subparser.add_argument("--apply", action="store_true")
        subparser.set_defaults(handler=handler)
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except PluginStateError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
