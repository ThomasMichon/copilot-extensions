"""Explicit live controls that delegate configuration and updates to Copilot."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned
from plugin_activation import PluginStateError, read_json_object
from plugin_resolve.execution import windows_batch_command

CONTRACT = "agent-machines.copilot-cli.v1"
OVERRIDES = ("COPILOT_AUTO_UPDATE", "COPILOT_CLI_DIST_DIR")
_VERSION = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


class CopilotControlError(RuntimeError):
    """A refused or incomplete native operation, never permission to replay it."""

    def __init__(self, message: str, *, effects: str = "none") -> None:
        super().__init__(message)
        self.effects = effects


@dataclass(frozen=True)
class CachedVersion:
    version: str
    path: str
    platform: str
    ready: bool
    reason: str = ""


def version_argument(value: str) -> str:
    """Accept an exact semver, never a path or a request for a remote version."""
    value = value.removeprefix("v")
    match = _VERSION.fullmatch(value)
    if match is None or any(
        part.isdigit() and len(part) > 1 and part.startswith("0")
        for part in (match.group(4) or "").split(".")
    ):
        raise argparse.ArgumentTypeError("expected an exact semantic version, such as 1.0.100-1")
    return value


def _settings() -> dict[str, Any]:
    home = Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot")
    return read_json_object(home / "settings.json", jsonc_header=True)[1]


def _executable(selected: str | None) -> str:
    executable = shutil.which(selected or "copilot")
    if executable is None:
        raise CopilotControlError("Copilot is unavailable; install it or pass --copilot PATH.")
    return executable


def _native(
    executable: str,
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
    timeout: float = 60,
    effectful: bool = False,
) -> str:
    environment = dict(os.environ) if environment is None else dict(environment)
    command = [executable, *arguments]
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        command = windows_batch_command(command, environment)
    try:
        result = run_caller_owned(
            command, timeout=timeout, env=environment,
            max_stdout_bytes=512 * 1024, max_stderr_bytes=512 * 1024,
        )
    except (OSError, subprocess.SubprocessError, OwnedCleanupError) as exc:
        raise CopilotControlError(
            f"Native Copilot operation did not settle successfully: {exc}",
            effects="unknown" if effectful else "none",
        ) from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise CopilotControlError(
            f"Native Copilot exited {result.returncode}: {detail}",
            effects="unknown" if effectful else "none",
        )
    return result.stdout.strip()


def _user_overrides() -> dict[str, str]:
    if os.name != "nt":
        return {}
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")
    except FileNotFoundError:
        return {}
    values: dict[str, str] = {}
    with key:
        for name in OVERRIDES:
            try:
                value, _ = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                continue
            if not isinstance(value, str):
                raise CopilotControlError(f"User environment override {name} is not a string.")
            values[name] = value
    return values


def _clear_user_overrides(expected: dict[str, str]) -> list[str]:
    if not expected:
        return []
    import winreg

    cleared: list[str] = []
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as key:
            for name, previous in expected.items():
                current, _ = winreg.QueryValueEx(key, name)
                if current != previous:
                    raise ValueError(f"{name} changed after inspection")
                winreg.DeleteValue(key, name)
                cleared.append(name)
    except (OSError, ValueError) as exc:
        raise CopilotControlError(
            "The live setting was applied, but override removal did not finish: "
            f"{exc}. Already removed: {', '.join(cleared) or 'none'}. Inspect before retrying.",
            effects="partial",
        ) from exc
    return cleared


def _cache_directories() -> list[Path]:
    system = platform.system()
    if system not in {"Windows", "Linux", "Darwin"}:
        raise CopilotControlError("Native Copilot cache inspection is unsupported on this platform.")
    if os.environ.get("COPILOT_CACHE_HOME"):
        root = Path(os.environ["COPILOT_CACHE_HOME"])
    elif system == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise CopilotControlError("LOCALAPPDATA is required for Windows cache inspection.")
        root = Path(local) / "copilot"
    elif system == "Darwin":
        root = Path.home() / "Library" / "Caches" / "copilot"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "copilot"
    target = {"Windows": "win32", "Linux": "linux", "Darwin": "darwin"}[system]
    packages = root / "pkg"
    if not packages.exists():
        return []
    supported = {f"{target}-{architecture}" for architecture in ("x64", "arm64")}
    if system == "Linux":
        supported.update(f"{target}-{architecture}-musl" for architecture in ("x64", "arm64"))
    return [
        path for path in sorted(packages.iterdir(), key=lambda item: item.name)
        if path.name in supported and path.is_dir()
    ]


def _ordinary(path: Path) -> bool:
    return not path.is_symlink() and not getattr(path.lstat(), "st_reparse_tag", 0)


def _cached_version(directory: Path, version: str) -> CachedVersion:
    path = directory / version
    try:
        if (
            not path.is_dir() or not _ordinary(directory) or not _ordinary(path)
            or path.resolve().parent != directory.resolve()
        ):
            raise ValueError("version directory is absent, linked or outside the native cache")
        for name in (".extraction-complete", "index.js", "package.json"):
            member = path / name
            if not member.is_file() or not _ordinary(member):
                raise ValueError(f"native package is incomplete or linked: {name}")
        if (path / "package.json").stat().st_size > 64 * 1024:
            raise ValueError("package metadata exceeds the inspection limit")
        if read_json_object(path / "package.json")[1].get("version") != version:
            raise ValueError("package metadata does not match the requested version")
    except (OSError, ValueError) as exc:
        return CachedVersion(version, str(path), directory.name, False, str(exc))
    return CachedVersion(version, str(path), directory.name, True)


def _versions() -> list[CachedVersion]:
    versions: list[CachedVersion] = []
    for directory in _cache_directories():
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            try:
                version = version_argument(path.name)
            except argparse.ArgumentTypeError:
                continue
            if version == path.name:
                versions.append(_cached_version(directory, version))
    return versions


def _status(args: argparse.Namespace) -> dict[str, Any]:
    settings = _settings()
    configured = settings.get("autoUpdate")
    if configured is not None and not isinstance(configured, bool):
        raise CopilotControlError("The live autoUpdate setting is not a boolean.")
    executable = _executable(args.copilot)
    return {
        "executable": executable,
        "version_in_current_environment": _native(executable, ["--version"]),
        "configured_auto_update": configured,
        "downloads_preference": True if configured is None else configured,
        "configured_channel": settings.get("autoUpdatesChannel"),
        "inherited_overrides": {name: os.environ[name] for name in OVERRIDES if name in os.environ},
        "windows_user_overrides": _user_overrides(),
        "ci_markers": [
            name for name in ("CI", "BUILD_NUMBER", "RUN_ID", "SYSTEM_COLLECTIONURI")
            if os.environ.get(name)
        ],
        "cached_versions": [asdict(version) for version in _versions()],
        "declarative_policy_changed": False,
    }


def _auto_update(args: argparse.Namespace) -> dict[str, Any]:
    desired = args.state == "on"
    settings = _settings()
    overrides = _user_overrides() if args.clear_overrides else {}
    executable = _executable(args.copilot)
    command = ["config", "autoUpdate", str(desired).lower(), "--global"]
    result: dict[str, Any] = {
        "applied": False, "before": settings.get("autoUpdate"), "auto_update": desired,
        "command": [executable, *command],
        "clear_user_overrides": list(overrides),
        "clear_invocation_overrides": list(OVERRIDES) if args.clear_overrides else [],
        "existing_parent_environments_changed": False,
        "declarative_policy_changed": False,
    }
    if not args.apply:
        return result
    environment = dict(os.environ)
    if args.clear_overrides:
        for name in OVERRIDES:
            environment.pop(name, None)
    write_needed = settings.get("autoUpdate") is not desired
    if write_needed:
        try:
            _native(executable, ["config", "--help"], environment=environment)
        except CopilotControlError as exc:
            raise CopilotControlError(
                f"Native configuration is unavailable. Update Copilot first; no settings "
                f"file fallback was attempted. {exc}"
            ) from exc
        _native(executable, command, environment=environment, effectful=True)
    try:
        verified = _settings()
    except (PluginStateError, OSError) as exc:
        raise CopilotControlError(
            f"Native configuration read-back failed: {exc}. Inspect before retrying.",
            effects="unknown" if write_needed else "none",
        ) from exc
    if verified.get("autoUpdate") is not desired:
        raise CopilotControlError("Native configuration read-back did not match.", effects="unknown")
    result.update(
        applied=True,
        cleared_user_overrides=_clear_user_overrides(overrides),
        inherited_overrides_still_in_parent={
            name: os.environ[name] for name in OVERRIDES if name in os.environ
        },
    )
    return result


def _update(args: argparse.Namespace) -> dict[str, Any]:
    executable = _executable(args.copilot)
    command = ["update", *([args.channel] if args.channel else [])]
    result: dict[str, Any] = {"applied": False, "command": [executable, *command]}
    if args.apply:
        result.update(
            applied=True,
            native_output=_native(executable, command, timeout=600, effectful=True),
        )
    return result


def _launch(args: argparse.Namespace) -> dict[str, Any]:
    candidates = [_cached_version(directory, args.version) for directory in _cache_directories()]
    if not any(version.ready for version in candidates):
        reason = "; ".join(version.reason for version in candidates) or "no matching native cache"
        raise CopilotControlError(
            f"Cannot launch cached version {args.version}: {reason}. "
            "Use native /downgrade <version> to acquire an uncached version."
        )
    forwarded = args.arguments
    if any(value == "--prefer-version" or value.startswith("--prefer-version=") for value in forwarded):
        raise CopilotControlError("Do not override the explicitly selected version in forwarded args.")
    executable = _executable(args.copilot)
    if executable.lower().endswith((".cmd", ".bat")):
        raise CopilotControlError("Cached launch requires the standalone Copilot binary, not a batch launcher.")
    command = [executable, "--prefer-version", args.version, *forwarded]
    result = {
        "applied": False, "command": command,
        "cached_candidates": [asdict(version) for version in candidates],
        "unset_environment": ["COPILOT_CLI_DIST_DIR"],
        "persistent_version_pin_changed": False,
    }
    if args.apply:
        environment = dict(os.environ)
        environment.pop("COPILOT_CLI_DIST_DIR", None)
        reported = _native(
            command[0], ["--prefer-version", args.version, "--version"],
            environment=environment,
        )
        if f"GitHub Copilot CLI {args.version}." not in reported.splitlines():
            raise CopilotControlError(
                "The native version probe did not select the requested package; no session launched."
            )
        if not any(
            _cached_version(directory, args.version).ready for directory in _cache_directories()
        ):
            raise CopilotControlError("The selected native cache changed before launch.")
        os.execvpe(command[0], command, environment)
    return result


def _command(args: argparse.Namespace) -> int:
    try:
        if args.operation == "status":
            result = _status(args)
        elif args.operation == "versions":
            result = {"cached_versions": [asdict(version) for version in _versions()]}
        elif args.operation == "auto-update":
            result = _auto_update(args)
        elif args.operation == "update":
            result = _update(args)
        else:
            result = _launch(args)
        payload = {"contract": CONTRACT, "operation": args.operation, "ok": True, **result}
    except (CopilotControlError, PluginStateError, OSError) as exc:
        payload = {
            "contract": CONTRACT, "operation": args.operation, "ok": False,
            "error": str(exc), "effects": getattr(exc, "effects", "none"),
        }
        if not args.json:
            print(f"Copilot CLI control failed: {exc}", file=sys.stderr)
            return 2
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 2


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("copilot-cli", help="Inspect or explicitly control native Copilot.")
    parser.add_argument("--copilot", help="select the native Copilot executable")
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("status", "versions", "auto-update", "update", "launch"):
        current = commands.add_parser(name)
        current.add_argument("--json", action="store_true")
        current.set_defaults(func=_command)
        if name in {"auto-update", "update", "launch"}:
            current.add_argument("--apply", action="store_true", help="perform the previewed operation")
        if name == "auto-update":
            current.add_argument("state", choices=("on", "off"))
            current.add_argument(
                "--clear-overrides", action="store_true",
                help="clear only Copilot update/package user overrides; existing parents stay unchanged",
            )
        elif name == "update":
            current.add_argument("--channel", choices=("stable", "prerelease"))
        elif name == "launch":
            current.add_argument("version", type=version_argument)
            current.add_argument(
                "--arg", dest="arguments", action="append", default=[],
                help="native argument (repeatable; use --arg=--flag for flags)",
            )
