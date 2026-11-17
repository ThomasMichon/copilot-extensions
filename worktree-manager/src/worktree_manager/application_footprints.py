"""Finite Manager-owned installation of already prepared application footprints."""
from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from graphlib import CycleError, TopologicalSorter
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
from typing import Any
from typing import cast
import uuid

import agent_procutil

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_VERSION = re.compile(r"[0-9][A-Za-z0-9._+-]*")
_BACKEND = "manager-application-footprints"
_MODULE = "worktree_manager.application_footprints"


@dataclass(frozen=True)
class Application:
    name: str
    version: str
    source: Path
    root: Path
    environment: str
    declaration: bytes
    dependencies: tuple[str, ...]

    @property
    def slot(self) -> Path:
        return self.root / "versions" / self.version


def _ordinary(path: Path) -> Path:
    if ".." in path.parts:
        raise ValueError("application footprint paths must not contain parent traversal")
    for member in (path, *path.parents):
        try:
            info = member.lstat()
        except FileNotFoundError:
            continue
        if member.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"application footprint path must not be indirect: {member}")
    return path


def _read(path: Path) -> bytes:
    _ordinary(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
        raise ValueError(f"application declaration must be a bounded ordinary file: {path}")
    with path.open("rb") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError(f"application declaration exceeds its byte bound: {path}")
    return content


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate application footprint field: {key}")
        result[key] = value
    return result


def plan(
    footprint: Path, apps_root: Path, *, selected: tuple[str, ...] = (),
) -> tuple[Application, ...]:
    """Order explicit IPC dependency edges; never resolve or build packages."""
    footprint = _ordinary(footprint.absolute())
    apps_root = _ordinary(apps_root.absolute())
    record = json.loads(_read(footprint / "footprint.json"), object_pairs_hook=_unique)
    if (
        not isinstance(record, dict) or set(record) != {"schema", "version", "apps"}
        or record["schema"] != "copilot-extensions.application-footprint"
        or type(record["version"]) is not int or record["version"] != 1
        or not isinstance(record["apps"], list) or not 0 < len(record["apps"]) <= 64
    ):
        raise ValueError("unsupported application footprint declaration")
    applications: dict[str, Application] = {}
    commands: dict[str, str] = {}
    environments: set[str] = set()
    for row in record["apps"]:
        if (
            not isinstance(row, dict) or set(row) != {"name", "version", "dependencies"}
            or not isinstance(row["name"], str) or not _NAME.fullmatch(row["name"])
            or not isinstance(row["version"], str) or not _VERSION.fullmatch(row["version"])
            or row["version"].endswith(".")
            or not isinstance(row["dependencies"], list) or len(row["dependencies"]) > 64
            or any(not isinstance(name, str) or not _NAME.fullmatch(name)
                   for name in row["dependencies"])
            or len(set(row["dependencies"])) != len(row["dependencies"])
            or row["name"] in applications
        ):
            raise ValueError("invalid or duplicate application footprint entry")
        name, version = row["name"], row["version"]
        source = footprint / "apps" / name
        root = apps_root / name
        if source.is_relative_to(root) or root.is_relative_to(source):
            raise ValueError("application source and installation paths must not overlap")
        if any(os.path.lexists(source / entry) for entry in (".venv", "state")):
            raise ValueError("application footprint must not contain a prepared environment or runtime state")
        declaration = _read(source / "application.json")
        app = json.loads(declaration, object_pairs_hook=_unique)
        if (
            not isinstance(app, dict) or app.get("name") != name
            or not isinstance(app.get("construction"), dict)
            or app["construction"].get("runtime_version") != version
            or not isinstance(app.get("root_environment"), str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*(?:_ROOT|_HOME|_INSTALL_DIR)", app["root_environment"])
            or not isinstance(app.get("commands"), list) or not app["commands"]
            or any(not isinstance(command, str) or not _NAME.fullmatch(command)
                   for command in app["commands"])
            or name not in app["commands"]
            or len(set(app["commands"])) != len(app["commands"])
        ):
            raise ValueError(f"application differs from its footprint entry: {name}")
        if app["root_environment"] in environments:
            raise ValueError("application root-environment declarations conflict")
        environments.add(app["root_environment"])
        for command in app["commands"]:
            if command in commands:
                raise ValueError(f"application command has multiple owners: {command}")
            commands[command] = name
        for required in ("pyproject.toml", "uv.lock", "bin/setup.ps1", "bin/setup.sh"):
            path = _ordinary(source / required)
            if not path.is_file():
                raise ValueError(f"application setup input is unavailable: {path}")
        applications[name] = Application(
            name, version, source, root, app["root_environment"],
            declaration, tuple(row["dependencies"]),
        )
    graph = {name: app.dependencies for name, app in applications.items()}
    if any(dependency not in applications for edges in graph.values() for dependency in edges):
        raise ValueError("application footprint is missing an IPC dependency")
    try:
        ordered = tuple(TopologicalSorter(graph).static_order())
    except CycleError as error:
        raise ValueError("application footprint contains an IPC dependency cycle") from error
    if any(name not in applications for name in selected):
        raise ValueError("requested application is absent from the footprint")
    wanted = set(selected or ordered)
    for name in reversed(ordered):
        if name in wanted:
            wanted.update(graph[name])
    return tuple(applications[name] for name in ordered if name in wanted)


def _verify_copy(path: Path, application: Application) -> None:
    if _read(path / "application.json") != application.declaration:
        raise ValueError(f"application declaration changed before setup: {application.name}")
    _verify_tree(path, application)


def _verify_tree(path: Path, application: Application) -> None:
    def unreadable(error: OSError) -> None:
        raise error
    for directory, children, files in os.walk(path, followlinks=False, onerror=unreadable):
        for name in (*children, *files):
            member = _ordinary(Path(directory) / name)
            mode = member.lstat().st_mode
            if not (stat.S_ISDIR(mode) if name in children else stat.S_ISREG(mode)):
                raise ValueError(f"application input is not an ordinary file or directory: {member}")
        if Path(directory) == path and path == application.slot:
            children[:] = [name for name in children if name not in {".venv", "state"}]


def select_generation(application: Application, current: str | None) -> Application:
    """Reuse the selected repair or allocate a distinct slot for damaged metadata."""
    from .self_install import _runtime_installer

    _verify_copy(application.source, application)
    _runtime_installer()
    validate = importlib.import_module("runtime_application_install")._validate_application
    validate(json.loads(application.declaration, object_pairs_hook=_unique))
    base = application.version
    if current and re.fullmatch(re.escape(base) + r"\.repair\.[0-9a-f]{32}", current):
        application = replace(application, version=current)
    _ordinary(application.slot)
    if not os.path.lexists(application.slot):
        return application
    _verify_tree(application.slot, application)
    try:
        installed = _read(application.slot / "application.json")
    except (FileNotFoundError, ValueError):
        installed = None
    if installed == application.declaration:
        return application
    if installed is not None:
        try:
            validate(json.loads(installed, object_pairs_hook=_unique))
        except (ValueError, UnicodeError):
            pass
        else:
            raise ValueError("application declaration conflicts with its existing generation")
    return replace(application, version=base + ".repair." + uuid.uuid4().hex)


def _selection_key(footprint: Path, applications: tuple[Application, ...]) -> str:
    digest = hashlib.sha256(_read(footprint / "footprint.json"))
    for application in applications:
        digest.update(application.name.encode("utf-8") + b"\0" + application.declaration)
    return "footprint:" + digest.hexdigest()


def _preflight(applications: tuple[Application, ...]) -> tuple[Any, str]:
    """Reject known no-effect failures before admitting execution custody."""
    from .self_install import _runtime_installer

    if len({app.environment for app in applications}) != len(applications):
        raise ValueError("application root-environment declarations conflict")
    for application in applications:
        _verify_copy(application.source, application)
        _ordinary(application.slot)
        if os.path.lexists(application.slot):
            _verify_copy(application.slot, application)
    installer_factory: Callable[[], Any] = _runtime_installer
    installer = installer_factory()
    shell = shutil.which("pwsh" if os.name == "nt" else "sh")
    if shell is None:
        raise ValueError("application setup requires PowerShell 7 or a POSIX shell")
    return installer, shell


def install(
    applications: tuple[Application, ...], *, stage: bool, deadline: float,
    prepared: tuple[Any, str] | None = None,
) -> None:
    """App setup owns environment construction, selection and actual readiness."""
    from update_driver import participant_environment

    installer, shell = prepared if prepared is not None else _preflight(applications)
    runtime = installer.runtime
    construction = runtime._runtime_component("versioned_runtime_construction")
    process = installer.engine
    roots = {app.environment: str(app.root) for app in applications}

    def remaining() -> float:
        value = deadline - time.monotonic()
        if not math.isfinite(value) or value <= 0:
            raise TimeoutError("application installation deadline expired")
        return value

    with ExitStack() as retained:
        descriptors = {}
        for application in applications:
            remaining()
            _verify_copy(application.source, application)
            _ordinary(application.slot)
            application.slot.parent.mkdir(parents=True, exist_ok=True)
            with runtime.construction_lease(application.root, application.version, timeout=remaining()):
                construction.publish_source_snapshot(
                    application.source, application.slot.parent, application.version,
                    runtime=runtime, verify=lambda path, app=application: _verify_copy(path, app),
                    on_progress=remaining,
                )
                _slot, descriptors[application.name] = retained.enter_context(runtime.retention_custody(
                    application.root, application.version,
                    require_installed=False, timeout=remaining(),
                ))
        for staging in ((True,) if stage else (True, False)):
            for application in applications:
                remaining()
                _verify_copy(application.slot, application)
                entry = application.slot / "bin" / ("setup.ps1" if os.name == "nt" else "setup.sh")
                command = (
                    [shell, "-NoProfile", "-File", str(entry)] if os.name == "nt"
                    else [shell, str(entry)]
                )
                if staging:
                    command.append("--stage")
                environment = {
                    key: value for key, value in os.environ.items()
                    if key.upper() not in {
                        *roots, "PYTHONPATH", "PYTHONHOME",
                        "VIRTUAL_ENV", "__PYVENV_LAUNCHER__",
                    }
                }
                environment.update(roots)
                environment.update(participant_environment())
                result = process.run_owned(
                    command, cwd=Path(application.slot.anchor) if os.name == "nt" else application.slot,
                    env=environment, timeout=remaining(),
                    deadline=deadline, procutil=agent_procutil,
                    construction_fd=descriptors[application.name],
                    runtime_root=application.root, runtime_version=application.version,
                    runtime_path=Path(runtime.__file__),
                    allow_breakaway=not staging,
                )
                if result.stdout:
                    print(result.stdout, end="")
                if result.stderr:
                    print(result.stderr, end="", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    import update_driver as driver

    parser = argparse.ArgumentParser(prog="worktree-manager-install", allow_abbrev=False)
    parser.add_argument("footprint", type=Path)
    parser.add_argument("--apps-root", type=Path)
    parser.add_argument("--app", action="append", default=[])
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--timeout", type=float, default=1800.0)
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(arguments)
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 7200:
        parser.error("--timeout must be finite seconds in (0, 7200]")
    if args.apps_root is not None and not args.apps_root.expanduser().is_absolute():
        parser.error("--apps-root must be absolute")
    footprint = args.footprint.expanduser().absolute()
    apps_root = (args.apps_root or Path.home() / ".copilot-extensions" / "apps").expanduser().absolute()
    normalized = [
        str(footprint), "--apps-root", str(apps_root), "--timeout", str(args.timeout),
        *(flag for name in args.app for flag in ("--app", name)),
        *(["--stage"] if args.stage else []), *(["--apply"] if args.apply else []),
    ]
    try:
        applications = plan(footprint, apps_root, selected=tuple(args.app))
        selection = _selection_key(footprint, applications)
        if not args.apply:
            print(json.dumps({
                "state": "planned", "stage_only": args.stage,
                "applications": [app.name for app in applications],
                "slots": [str(app.slot) for app in applications],
            }))
            return 0
        prepared = _preflight(applications)
        custody = driver.borrowed_custody()
        if custody is not None:
            with custody:
                request = cast("driver.Request", driver.status(custody.request_id, root=custody.root))
                if (
                    request.state != "running" or request.spec.backend != _BACKEND
                    or request.spec.argv != tuple(normalized)
                    or request.spec.source != selection
                ):
                    raise driver.StateError("Manager does not own this application installation request")
                custody.assert_owned()
                failure = None
                with custody.execution_scope():
                    try:
                        install(
                            applications, stage=args.stage,
                            deadline=time.monotonic() + args.timeout, prepared=prepared,
                        )
                    except subprocess.CalledProcessError as error:
                        # The canonical child owner confirmed exit and descendant settlement.
                        failure = error
                if failure is not None:
                    raise failure
                print(json.dumps({"state": "staged" if args.stage else "installed"}))
                return 0
        command, environment = driver.runtime_command(_MODULE, normalized)
        result = driver.run_explicit(
            driver.RequestSpec(
                _BACKEND, tuple(normalized), project=str(apps_root),
                cwd=str(footprint), source=selection,
            ),
            runner=driver.TrustedRunner(
                command, str(footprint), timeout=args.timeout, env=tuple(environment.items()),
            ),
        )
        observed = driver.wait(result.id, output=sys.stdout)
        return observed.exit_code if observed.state == "completed" and observed.exit_code is not None else 1
    except subprocess.CalledProcessError as error:
        if error.stdout:
            print(error.stdout[-65536:], end="")
        if error.stderr:
            print(error.stderr[-65536:], end="", file=sys.stderr)
        return error.returncode
    except (driver.DriverError, driver.ParticipantError, ImportError, OSError, ValueError, RuntimeError,
            subprocess.SubprocessError) as error:
        print(f"Application installation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
