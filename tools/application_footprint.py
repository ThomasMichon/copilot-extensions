"""Bundle canonically prepared applications for finite Manager installation."""
from __future__ import annotations

import argparse
import configparser
from contextlib import ExitStack, redirect_stdout
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
from types import ModuleType
from typing import Any
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def prepare_frozen(
    repository: Path, commit: str, main_head: str, dev_head: str, output: Path, *,
    uv: str, deadline: float,
) -> dict[str, str]:
    """Prepare an explicitly frozen WIP commit, never mutable working files."""
    import local_install_source as preparation
    import prepare_runtime_source as source
    import source_preparation_process as processes

    repository = repository.resolve(strict=True)
    if Path(source._git(
        repository, "rev-parse", "--show-toplevel", deadline=deadline,
    )).resolve() != repository:
        raise ValueError("frozen application source must name the exact checkout root")
    for label, value in (("source", commit), ("Main", main_head), ("Dev", dev_head)):
        if (
            source._OBJECT_ID.fullmatch(value) is None
            or source._git(repository, "rev-parse", f"{value}^{{commit}}", deadline=deadline) != value
        ):
            raise ValueError(f"frozen application {label} requires an exact commit")
    fetched_dev = source._git(
        repository, "rev-parse", "refs/remotes/origin/dev^{commit}", deadline=deadline,
    )
    if source._git(repository, "merge-base", dev_head, fetched_dev, deadline=deadline) != dev_head:
        raise ValueError("frozen application Dev seed is not in the fetched Dev branch")
    if source._git(repository, "merge-base", dev_head, commit, deadline=deadline) != dev_head:
        raise ValueError("frozen application source is not based on the selected Dev seed")
    output = output.absolute()
    if (
        os.path.lexists(output) or output.resolve().is_relative_to(repository)
        or output.resolve() == repository
    ):
        raise ValueError("frozen application output must be new and outside its source checkout")
    permissions = processes.procutil_module("python_cache_permissions")
    permissions.check(output.parent, ancestor=True)
    permissions.check_ancestors(output.parent)
    output = output.resolve()
    processes.procutil_module("_daemon_instance_owner").mkdir_private(output)
    built = output / "inputs"
    projects = ("standalone:worktree-manager",)
    record = preparation.compose(
        repository, commit, main_head, built, projects=projects, deadline=deadline,
    )
    with preparation._built_projects(
        built, record, projects=projects, uv=uv, deadline=deadline, retain_toolchain=True,
    ) as (_record, toolchain):
        snapshots = preparation._prepare_profiles(
            built, record, output, ("worktree-manager",), {}, toolchain,
            uv=uv, deadline=deadline,
        )
    if snapshots is None:
        raise ValueError("frozen application preparation returned no snapshots")
    completion = preparation._application_completion(snapshots, deadline=deadline)
    source._remaining(deadline)
    (output / "applications.json").write_text(
        json.dumps(completion, sort_keys=True) + "\n", encoding="utf-8",
    )
    return snapshots


def _ordinary_tree(root: Path, *, deadline: float) -> None:
    def unreadable(error: OSError) -> None:
        raise error
    for directory, children, files in os.walk(root, followlinks=False, onerror=unreadable):
        if time.monotonic() >= deadline:
            raise TimeoutError("application footprint export deadline expired")
        for path in (Path(directory), *(Path(directory) / name for name in (*children, *files))):
            info = path.lstat()
            if (
                path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
            ):
                raise ValueError("application footprint contains an indirect or special input")


def _setup_frontdoors(application: Path) -> None:
    from runtime_application_binstubs import setup_binstubs

    directory = application / "bin"
    directory.mkdir(exist_ok=True)
    for name, content in setup_binstubs().items():
        path = directory / name
        with path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(content)
        if path.suffix == ".sh":
            path.chmod(0o700)


def _manager_setup(application: Path, inputs: Any, *, deadline: float) -> None:
    """Use the verified Manager wheel's frozen resources, not tooling-checkout bytes."""
    import prepare_runtime_source as preparation

    distribution = inputs.receipt()["distributions"]["copilot-extensions-worktree-manager"]
    wheel = application / "wheels" / distribution["filename"]
    resources = "worktree_manager/_runtime_assets/scripts/"
    provider = resources + "runtime-input-provider/libs/"
    with zipfile.ZipFile(wheel) as archive:
        def read(name: str) -> bytes:
            preparation._remaining(deadline)
            info = archive.getinfo(name)
            if info.is_dir() or info.file_size > 16 << 20:
                raise ValueError("Manager setup resource exceeds its ordinary-file bound")
            return archive.read(info)
        helper = read(provider + "installer-engine/runtime_application_binstubs.py")
        runtime = {}
        for source_directory in ("installer-engine/", "versioned-runtime/"):
            prefix = provider + source_directory
            for name in archive.namelist():
                relative = name.removeprefix(prefix)
                if name.startswith(prefix) and "/" not in relative and relative.endswith(".py"):
                    runtime[relative] = read(name)
        if not {"installer_engine.py", "versioned_runtime.py"} <= runtime.keys():
            raise ValueError("Manager wheel is missing its frozen setup runtime")
        install = read(provider + "installer-engine/runtime_application_install.py")
        lifecycle = read(resources + "daemon_instance_runtime.pyz")
        entries = [name for name in archive.namelist() if name.endswith(".dist-info/entry_points.txt")]
        if len(entries) != 1:
            raise ValueError("Manager wheel has no unambiguous command declaration")
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(read(entries[0]).decode("utf-8"))
        commands = dict(parser["console_scripts"])
    declaration = json.loads((application / "application.json").read_text(encoding="utf-8"))
    if set(commands) != set(declaration["commands"]):
        raise ValueError("Manager wheel commands differ from the prepared application")
    (application / "install.py").write_bytes(install)
    (application / "runtime_application_binstubs.py").write_bytes(helper)
    directory = application / "runtime"
    shutil.rmtree(directory)
    directory.mkdir()
    for name, content in runtime.items():
        (directory / name).write_bytes(content)
    (directory / "daemon_instance_runtime.pyz").write_bytes(lifecycle)
    launchers = ModuleType("_frozen_manager_binstubs")
    launchers.__file__ = str(application / "runtime_application_binstubs.py")
    exec(compile(helper, launchers.__file__, "exec"), vars(launchers))
    python = (application / ".python-version").read_text(encoding="utf-8").strip()
    python_request = python if python.startswith("cpython-") and "-windows-" in python else None
    for command, entry in commands.items():
        generated = launchers.slot_binstubs(command, entry, python_request=python_request)
        for name, text in generated.items():
            if name in {"install", "install.cmd", "install.ps1", "install.sh"} and command != declaration["name"]:
                continue
            if Path(name).name != name or name in {".", ".."}:
                raise ValueError("Manager setup generated a nonlocal launcher")
            path = application / name
            with path.open("w", encoding="utf-8", newline="") as stream:
                stream.write(text)
            if path.suffix == ".sh" or not path.suffix:
                path.chmod(0o700)


def export(
    snapshots: dict[str, Path], destination: Path, *,
    dependencies: dict[str, tuple[str, ...]] | None = None, deadline: float,
) -> Path:
    """Copy the existing promotion/profile outputs; never build or install them."""
    import local_install_profile as profiles
    import prepare_runtime_source as preparation
    import install_source as source_inputs
    import installer_engine as engine
    from installer_wheel_snapshot import WheelInstallInputs

    dependencies = {} if dependencies is None else dependencies
    if (
        not snapshots or len(snapshots) > 64
        or any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in snapshots)
        or set(dependencies) - snapshots.keys()
        or any(
            not isinstance(edges, tuple) or len(set(edges)) != len(edges)
            or any(name not in snapshots for name in edges)
            for edges in dependencies.values()
        )
    ):
        raise ValueError("footprint requires selected applications and explicit IPC dependency edges")
    destination = destination.absolute()
    if os.path.lexists(destination):
        raise FileExistsError("application footprint output already exists")
    roots = [path.resolve(strict=True) for path in snapshots.values()]
    if any(destination.resolve().is_relative_to(root) for root in roots):
        raise ValueError("application footprint output must be outside its prepared snapshots")
    applications: dict[str, tuple[Path, dict[str, Any], WheelInstallInputs]] = {}
    def copy(origin: str, target: str) -> str:
        preparation._remaining(deadline)
        with open(origin, "rb") as source, open(target, "xb") as output:
            preparation._copy(source, output, deadline=deadline)
        shutil.copystat(origin, target)
        return target
    with ExitStack() as retained:
        for component, snapshot in snapshots.items():
            preparation._remaining(deadline)
            selected, inputs = retained.enter_context(source_inputs.project_inputs(
                snapshot, component, deadline=deadline,
            ))
            profiles._verify_application(snapshot, component, selected.record, selected, inputs)
            application = source_inputs._member(snapshot, selected.project["application"])
            declaration = source_inputs._read(application / "application.json")
            name = declaration["name"]
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise ValueError("prepared application has no ordinary command identity")
            if name in {row[1]["name"] for row in applications.values()}:
                raise ValueError("prepared application command identities conflict")
            if any(os.path.lexists(application / name) for name in (".venv", "state")):
                raise ValueError("prepared application contains installed environment or state")
            if not (application / ".python-version").is_file():
                raise ValueError("prepared application has no declared interpreter")
            _ordinary_tree(application, deadline=deadline)
            applications[component] = (application, declaration, inputs)
        preparation._private_output_directory(destination)
        destination = destination.resolve()
        app_root = destination / "apps"
        app_root.mkdir()
        rows = []
        for component, (source, declaration, inputs) in applications.items():
            preparation._remaining(deadline)
            application = app_root / declaration["name"]
            shutil.copytree(source, application, symlinks=True, copy_function=copy)
            _ordinary_tree(application, deadline=deadline)
            if source_inputs._read(application / "application.json") != declaration:
                raise ValueError("application declaration changed during footprint export")
            engine.verify_application_wheels(inputs, application)
            if declaration["name"] == "worktree-manager":
                _manager_setup(application, inputs, deadline=deadline)
            _setup_frontdoors(application)
            inputs.verify()
            rows.append({
                "name": declaration["name"],
                "version": declaration["construction"]["runtime_version"],
                "dependencies": [
                    applications[name][1]["name"] for name in dependencies.get(component, ())
                ],
            })
        preparation._remaining(deadline)
        (destination / "footprint.json").write_text(json.dumps({
            "schema": "copilot-extensions.application-footprint", "version": 1, "apps": rows,
        }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return destination


def main(argv: list[str] | None = None) -> int:
    """Prepare the reference through the existing promotion owner, then export."""
    import local_install_source as preparation

    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--source-checkout", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--main-head", required=True)
    parser.add_argument("--branch", choices=("main", "dev", "wip"), required=True)
    parser.add_argument("--dev-head", help="Fetched Dev ancestor of an explicitly frozen WIP commit")
    parser.add_argument("--source-descriptor", help="Existing installation source descriptor as JSON")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 7200:
        parser.error("--timeout must be finite seconds in (0, 7200]")
    if (args.branch == "wip") != (args.dev_head is not None):
        parser.error("--dev-head is required only with --branch wip")
    if args.branch == "wip" and args.source_descriptor is not None:
        parser.error("--source-descriptor applies to Main/Dev acquisition, not frozen WIP")
    uv = shutil.which("uv")
    if uv is None:
        parser.error("application preparation requires uv on PATH")
    deadline = time.monotonic() + args.timeout
    with redirect_stdout(sys.stderr):
        if args.branch == "wip":
            snapshots = prepare_frozen(
                args.source_checkout, args.source_commit, args.main_head, args.dev_head, args.output,
                uv=uv, deadline=deadline,
            )
        else:
            snapshots = preparation.prepare_applications(
                args.source_checkout, args.source_commit, args.main_head, args.output,
                components=("worktree-manager",), branch=args.branch, uv=uv, deadline=deadline,
                **({"source_descriptor": json.loads(args.source_descriptor)}
                   if args.source_descriptor is not None else {}),
            )
        destination = export(
            {name: Path(path) for name, path in snapshots.items()},
            args.output / "footprint", deadline=deadline,
        )
    print(json.dumps({"footprint": str(destination)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
