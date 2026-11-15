"""Deliver canonical runtime resources in independently buildable distributions."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
import importlib
from pathlib import Path
import os
import re
import shutil
import stat
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from setuptools._distutils.cmd import Command


DirectoryIdentities = dict[Path, tuple[int, int, int, int]]
ProjectMetadata = dict[str, Any]


HELPER_NAME = "_standalone_runtime_build.py"
RUNTIME_RESOURCES = {
    "versioned_runtime.py": "_versioned_runtime.py",
    "versioned_runtime_leases.py": "versioned_runtime_leases.py",
    "versioned_runtime_construction.py": "versioned_runtime_construction.py",
    "versioned_runtime_publication.py": "versioned_runtime_publication.py",
    "versioned_runtime_source.py": "versioned_runtime_source.py",
    "versioned_runtime_cache.py": "versioned_runtime_cache.py",
}
APPLICATION_RESOURCES = {
    "worktree_manager": (
        "scripts/installer*.py", "scripts/runtime*.py", "scripts/versioned_runtime*.py",
        "scripts/versioned_runtime_native.ps1", "scripts/source_acquisition.py", "scripts/install_source.py",
        "scripts/repair_recovery.py", "scripts/update_entry.py",
        "scripts/bootstrap-killswitch-guard.*", "scripts/killswitch_mutation.py",
        "scripts/daemon_instances.*", "scripts/daemon_instance_runtime.pyz",
        "scripts/installer-instance-reconcile.*",
        "scripts/runtime-source-tools/**/*",
        "scripts/runtime-input-provider/**/*",
        "scripts/libs/payload-invocation/*.py",
        "scripts/libs/versioned-runtime/standalone_build.py",
        "bin/**/*",
        "references/control-plane-provider.json", "references/uninstall-contract.json",
    ),
    "agent_worktrees": ("scripts/**/*",),
}
APPLICATION_METADATA_RESOURCES = {
    "worktree_manager": (
        "scripts/runtime-source-tools/**/*.toml",
        "scripts/runtime-source-tools/**/*.txt",
        "scripts/runtime-source-tools/**/*.lock",
    ),
}


def application_resources(
    project: str | os.PathLike[str], package: str,
) -> dict[str, Path | None]:
    """Keep operational assets in the wheel, without retaining a source payload."""
    project = Path(project)
    included = project / "src" / package / "_runtime_assets"
    patterns = APPLICATION_RESOURCES[package]
    sources = {
        path.relative_to(project).as_posix(): path
        for pattern in patterns for path in project.glob(pattern)
        if path.is_file()
        and path.suffix in {".py", ".ps1", ".sh", ".cmd", ".conf", ".pyz", ".json", ".yaml", ".awk", ".cs"}
    }
    sources.update({
        path.relative_to(project).as_posix(): path
        for pattern in APPLICATION_METADATA_RESOURCES.get(package, ())
        for path in project.glob(pattern) if path.is_file()
    })
    if sources:
        return {"_runtime_assets/" + name: path for name, path in sources.items()}
    if not included.is_dir():
        raise RuntimeError("application runtime resources are missing")
    return {
        "_runtime_assets/" + path.relative_to(included).as_posix(): None
        for path in included.rglob("*") if path.is_file()
    }


def ordinary_file(
    path: Path, checked: set[Path] | None = None, *,
    directories: DirectoryIdentities | None = None,
) -> bool:
    """Reject linked build inputs, including linked parent directories."""
    cache = directories if directories is not None else checked
    for entry in (*reversed(path.parents), path):
        if cache is not None and entry != path and entry in cache:
            continue
        try:
            info = entry.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise RuntimeError("runtime resource build input must not be linked")
        if entry != path and not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("runtime resource build input ancestor must be a directory")
        if entry != path and directories is not None:
            directories[entry] = (
                info.st_dev, info.st_ino, info.st_mode, getattr(info, "st_file_attributes", 0),
            )
        elif entry != path and checked is not None:
            checked.add(entry)
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("runtime resource build input must be a regular file")
    return True


def select_bytes(
    paths: Iterable[Path], label: str, *, optional_paths: Iterable[Path] = (),
    checked: set[Path] | None = None, directories: DirectoryIdentities | None = None,
) -> bytes:
    candidates = []
    for path in paths:
        if not ordinary_file(path, checked, directories=directories):
            raise RuntimeError(
                f"{label} is missing; build from the full release checkout or its complete sdist"
            )
        candidates.append(path.read_bytes())
    if not candidates:
        raise RuntimeError(
            f"{label} is missing; build from the full release checkout or its complete sdist"
        )
    candidates.extend(
        path.read_bytes() for path in optional_paths
        if ordinary_file(path, checked, directories=directories)
    )
    if any(data != candidates[0] for data in candidates[1:]):
        raise RuntimeError(f"included {label} differs from the canonical source")
    return candidates[0]


def embedded_projects(
    project: str | os.PathLike[str],
) -> dict[str, tuple[Path, ProjectMetadata]]:
    """Select the explicitly declared first-party build inputs, never runtime packages."""
    tomllib = importlib.import_module("tomllib")
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    project = Path(project).absolute()
    data = tomllib.loads(select_bytes([project / "pyproject.toml"], "project metadata").decode())
    groups = data.get("dependency-groups", {})
    if not isinstance(groups, dict):
        raise RuntimeError("dependency groups must be a table")
    declarations = groups.get("first-party-core", [])
    if not isinstance(declarations, list) or any(not isinstance(value, str) for value in declarations):
        raise RuntimeError("first-party-core must be a flat requirement list")
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    if not isinstance(sources, dict):
        raise RuntimeError("first-party source mappings must be a table")
    selected: dict[str, tuple[Path, ProjectMetadata]] = {}
    for text in declarations:
        requirement = Requirement(text)
        name = canonicalize_name(requirement.name)
        source = sources.get(name)
        if requirement.url or not isinstance(source, dict) or set(source) - {"path", "editable"}:
            raise RuntimeError("first-party core needs an ordinary declared source")
        if name in selected or not isinstance(source.get("path"), str):
            raise RuntimeError("ambiguous first-party core source")
        parts = Path(source["path"]).parts
        if (
            Path(source["path"]).is_absolute()
            or not (len(parts) == 3 and parts[:2] == ("..", "libs")
                    or len(parts) == 2 and parts[0] == "libs")
            or ("editable" in source and not isinstance(source["editable"], bool))
        ):
            raise RuntimeError("first-party core must use the declared library source layout")
        canonical = project / source["path"]
        included = project / "_first_party" / name
        directory = included if os.path.lexists(included) else canonical
        metadata = tomllib.loads(select_bytes(
            [directory / "pyproject.toml"], "first-party metadata",
        ).decode())
        if canonicalize_name(metadata["project"]["name"]) != name:
            raise RuntimeError("first-party source distribution identity differs")
        if not requirement.specifier.contains(metadata["project"]["version"], prereleases=True):
            raise RuntimeError("first-party source version differs")
        if metadata.get("tool", {}).get("setuptools", {}).get("packages", {}) != {
            "find": {"where": ["src"]},
        }:
            raise RuntimeError("first-party core requires the declared src package finder")
        selected[name] = (directory, metadata)
    return selected


def embedded_members(project: str | os.PathLike[str]) -> dict[str, Path]:
    """Bind embedded Python, declared package data and notices to their original bytes."""
    members: dict[str, Path] = {}
    checked: set[Path] = set()
    for directory, data in embedded_projects(project).values():
        base = directory / "src"
        roots = [path for path in base.iterdir() if (path / "__init__.py").is_file()]
        if len(roots) != 1:
            raise RuntimeError("first-party core requires one ordinary import root")
        files = {path.relative_to(base).as_posix(): path for path in base.rglob("*.py")}
        patterns = data.get("tool", {}).get("setuptools", {}).get("package-data", {})
        if not isinstance(patterns, dict):
            raise RuntimeError("first-party package data must be a table")
        for package, values in patterns.items():
            if not isinstance(package, str) or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", package,
            ) or not isinstance(values, list) or any(
                not isinstance(value, str) or "\\" in value or ".." in value.split("/")
                or value.startswith("/") for value in values
            ):
                raise RuntimeError("invalid first-party package data")
            package_root = base.joinpath(*package.split("."))
            if package_root != roots[0] and not package_root.is_relative_to(roots[0]):
                raise RuntimeError("foreign first-party package data")
            files.update({
                path.relative_to(base).as_posix(): path
                for pattern in values for path in package_root.glob(pattern) if path.is_file()
            })
        files[roots[0].name + "/LICENSE"] = directory / "LICENSE"
        for name, path in files.items():
            if name in members or not ordinary_file(path, checked):
                raise RuntimeError("missing or conflicting first-party member")
            members[name] = path
    return members


def _native_build_path(project: str | os.PathLike[str], path: str) -> str:
    if os.name != "nt":
        return path
    absolute = os.path.normpath(os.path.abspath(os.path.join(project, path)))
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def commands(
    project: str | os.PathLike[str], package: str,
    canonical_directory: str | os.PathLike[str] | None = None, *,
    companion_resources: Mapping[str, str | os.PathLike[str] | None] | None = None,
    runtime_resources: Mapping[str, str] | None = None,
) -> dict[str, type[Command]]:
    """Bind one package's hooks; generated sdist resources are not source mirrors."""
    from setuptools.command.build_py import build_py
    from setuptools.command.install_lib import install_lib
    from setuptools.command.sdist import sdist

    try:
        from setuptools.command.bdist_wheel import bdist_wheel
    except ModuleNotFoundError as error:
        if error.name != "setuptools.command.bdist_wheel":
            raise
        wheel_base = None
    else:
        wheel_base = bdist_wheel
    project = Path(project).absolute()
    project_root = project
    embedded = embedded_projects(project) if package == "worktree_manager" else {}
    members = embedded_members(project) if embedded else {}
    if embedded:
        from setuptools import find_packages
    resources = {
        name: Path("src") / package / destination
        for name, destination in (
            RUNTIME_RESOURCES if runtime_resources is None else runtime_resources
        ).items()
    }
    canonical_directory = Path(canonical_directory) if canonical_directory else None

    def inputs() -> tuple[dict[str, bytes], bytes]:
        directories: DirectoryIdentities = {}
        helper_paths = [project / HELPER_NAME]
        optional_helpers = []
        if canonical_directory is not None:
            optional_helpers = helper_paths
            helper_paths = [canonical_directory / "standalone_build.py"]
        primitives: dict[str, bytes] = {}
        for name, resource in resources.items():
            paths = [project / resource]
            optional = []
            if canonical_directory is not None:
                optional = paths
                paths = [canonical_directory / name]
            label = "runtime primitive" if name == "versioned_runtime.py" else name
            primitives[resource.name] = select_bytes(
                paths, label, optional_paths=optional, directories=directories,
            )
        for name, source in (companion_resources or {}).items():
            included = project / "src" / package / name
            paths = [Path(source)] if source is not None else [included]
            primitives[name] = select_bytes(
                paths, name, optional_paths=[included] if source is not None else [],
                directories=directories,
            )
        selected = (
            primitives,
            select_bytes(
                helper_paths, "runtime build helper", optional_paths=optional_helpers,
                directories=directories,
            ),
        )
        for directory, expected in directories.items():
            observed = directory.lstat()
            if (
                observed.st_dev, observed.st_ino, observed.st_mode,
                getattr(observed, "st_file_attributes", 0),
            ) != expected:
                raise RuntimeError("runtime resource build input ancestor changed during collection")
        return selected

    class BuildPy(build_py):
        build_lib: str

        def finalize_options(self) -> None:
            if embedded:
                distribution = self.distribution
                packages = set(distribution.packages or [])
                mapping = dict(distribution.package_dir or {"": "src"})
                package_data = dict(distribution.package_data or {})
                for name, (directory, data) in embedded.items():
                    source = directory / "src"
                    discovered = find_packages(where=str(source))
                    for selected in discovered:
                        destination = source.joinpath(*selected.split("."))
                        previous = mapping.get(selected)
                        if selected in packages and (
                            previous is None or (project / previous).resolve() != destination.resolve()
                        ):
                            raise RuntimeError("embedded import package collides with application")
                        mapping[selected] = os.path.relpath(
                            destination, project,
                        )
                    packages.update(discovered)
                    package_data.update(data.get("tool", {}).get("setuptools", {}).get("package-data", {}))
                distribution.packages = sorted(packages)
                distribution.package_dir = mapping
                distribution.package_data = package_data
            super().finalize_options()
            self.build_lib = _native_build_path(project, self.build_lib)

        def build_packages(self) -> None:
            if not embedded:
                return super().build_packages()
            from concurrent.futures import ThreadPoolExecutor

            modules: list[tuple[str, str, str]] = []
            for selected in self.packages:
                directory = self.get_package_dir(selected)
                find_modules = cast(
                    Callable[[str, str], list[tuple[str, str, str]]], self.find_package_modules,
                )
                modules.extend(find_modules(selected, directory))
                self.mkpath(os.path.join(self.build_lib, *selected.split(".")))
            if not modules:
                return

            def copy_module(item: tuple[str, str, str]) -> tuple[str, bool] | None:
                selected, module, source = item
                build_module = cast(
                    Callable[[str, str, str], tuple[str, bool] | None], self.build_module,
                )
                return build_module(module, source, selected)

            with ThreadPoolExecutor(max_workers=min(4, len(modules))) as pool:
                for _result in pool.map(copy_module, modules):
                    pass

        def run(self) -> None:
            primitives, _ = inputs()
            super().run()

            for name, data in primitives.items():
                destination = Path(self.build_lib) / package / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            for name, source in members.items():
                if name.endswith("/LICENSE"):
                    destination = Path(self.build_lib) / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(select_bytes([source], "embedded first-party notice"))

        def get_outputs(self, include_bytecode: bool | Literal[0, 1] = 1) -> list[str]:
            outputs = super().get_outputs(cast(bool, include_bytecode))
            for filename in (*[resource.name for resource in resources.values()],
                             *(companion_resources or {})):
                name = str(Path(self.build_lib) / package / filename)
                if name not in outputs:
                    outputs.append(name)
            outputs.extend(
                str(Path(self.build_lib) / name) for name in members
                if str(Path(self.build_lib) / name) not in outputs
            )
            return outputs

    class BuildSdist(sdist):
        def make_release_tree(self, base_dir: str, files: list[str]) -> None:
            primitives, helper = inputs()
            local = [
                name for name in files
                if Path(name).parts[0] != "_first_party"
                and (project_root / name).resolve().is_relative_to(project_root.resolve())
            ]
            super().make_release_tree(base_dir, local)
            for name, data in primitives.items():
                destination = Path(base_dir) / "src" / package / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            (Path(base_dir) / HELPER_NAME).write_bytes(helper)
            checked: set[Path] = set()

            def copy_input(source: str, destination: str) -> str:
                select_bytes([Path(source)], "first-party sdist input", checked=checked)
                return shutil.copy2(source, destination, follow_symlinks=False)

            ignore = shutil.ignore_patterns(
                "__pycache__", "*.egg-info", "build", "dist", ".venv", ".pytest_cache",
            )
            for name, (directory, _metadata) in embedded.items():
                destination = Path(base_dir) / "_first_party" / name
                for parent, directories, _files in os.walk(directory, followlinks=False):
                    excluded = ignore(parent, directories)
                    directories[:] = [child for child in directories if child not in excluded]
                    for child in directories:
                        info = (Path(parent) / child).lstat()
                        if stat.S_ISLNK(info.st_mode) or (
                            getattr(info, "st_file_attributes", 0)
                            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                        ):
                            raise RuntimeError("first-party sdist input must not be linked")
                shutil.copytree(
                    directory, destination,
                    dirs_exist_ok=True,
                    copy_function=copy_input,
                    ignore=ignore,
                )

    classes: dict[str, type[Command]] = {"build_py": BuildPy, "sdist": BuildSdist}
    if os.name == "nt":
        class WindowsInstallLib(install_lib):
            build_dir: str
            install_dir: str

            def finalize_options(self) -> None:
                super().finalize_options()
                self.build_dir = _native_build_path(project, self.build_dir)
                self.install_dir = _native_build_path(project, self.install_dir)

        classes["install_lib"] = WindowsInstallLib
        from setuptools import Distribution
        from distutils.errors import DistutilsModuleError

        try:
            wheel_base = Distribution().get_command_class("bdist_wheel")
        except (ImportError, DistutilsModuleError):
            wheel_base = None
        if wheel_base is not None:
            # Entry-point-selected commands retain their upstream command contracts.
            if TYPE_CHECKING:
                from setuptools.command.bdist_wheel import bdist_wheel as WindowsWheelBase
            else:
                WindowsWheelBase = wheel_base

            class BdistWheel(WindowsWheelBase):
                bdist_dir: str

                def finalize_options(self) -> None:
                    super().finalize_options()
                    self.bdist_dir = _native_build_path(project, self.bdist_dir)

            classes["bdist_wheel"] = BdistWheel
    if wheel_base is not None:
        if TYPE_CHECKING:
            from setuptools.command.bdist_wheel import bdist_wheel as BuildWheelBase
            from setuptools.command.install_lib import install_lib as InstallWheelBase
        else:
            BuildWheelBase = classes.get("bdist_wheel", wheel_base)
            InstallWheelBase = classes.get("install_lib", install_lib)

        class BuildWheel(BuildWheelBase):
            _runtime_staging_root: Path | None

            def run(self) -> None:
                self._runtime_staging_root = Path(cast(str, self.bdist_dir)).resolve()
                try:
                    super().run()
                finally:
                    self._runtime_staging_root = None

        class InstallWheelLib(InstallWheelBase):
            def copy_tree(
                self, infile: str | os.PathLike[str], outfile: str, preserve_mode: int = 1,
                preserve_times: int = 1, preserve_symlinks: int = 0, level: object = 1,
            ) -> list[str]:
                wheel = self.distribution.get_command_obj("bdist_wheel", create=False)
                staging = getattr(wheel, "_runtime_staging_root", None)
                if (
                    staging is None or self.dry_run
                    or cast(Callable[[], Collection[str]], self.get_exclusions)()
                    or not preserve_mode or not preserve_times or preserve_symlinks
                    or Path(infile).resolve() != Path(self.build_dir).resolve()
                    or not Path(outfile).resolve().is_relative_to(staging)
                ):
                    return cast(list[str], super().copy_tree(
                        infile, outfile, cast(bool, preserve_mode), cast(bool, preserve_times),
                        cast(bool, preserve_symlinks), level,
                    ))
                outputs: list[str] = []

                def link_file(source: str, destination: str) -> str:
                    output, _copied = self.copy_file(source, destination, link="hard", level=level)
                    outputs.append(output)
                    return output

                shutil.copytree(infile, outfile, copy_function=link_file, dirs_exist_ok=True)
                return outputs

        classes.update({"bdist_wheel": BuildWheel, "install_lib": InstallWheelLib})
    if not embedded:
        return classes

    from setuptools.command.egg_info import egg_info

    class EggInfo(egg_info):
        def run(self) -> None:
            if embedded:
                self.get_finalized_command("build_py")
            super().run()

    if TYPE_CHECKING:
        from setuptools.command.install_lib import install_lib as InstallLibBase
    else:
        InstallLibBase = classes.get("install_lib", install_lib)

    class InstallLib(InstallLibBase):
        def install(self) -> list[str] | None:
            if (
                not embedded or self.dry_run
                or cast(Callable[[], Collection[str]], self.get_exclusions)()
                or not os.path.isdir(self.build_dir)
            ):
                return cast(list[str] | None, super().install())
            from concurrent.futures import ThreadPoolExecutor

            files: list[tuple[str, str]] = []
            for parent, directories, names in os.walk(self.build_dir):
                directories[:] = [name for name in directories if not name.startswith(".nfs")]
                destination = Path(self.install_dir) / Path(parent).relative_to(self.build_dir)
                self.mkpath(str(destination))
                files.extend(
                    (str(Path(parent) / name), str(destination / name))
                    for name in names if not name.startswith(".nfs")
                )
            if not files:
                return []

            def copy_member(item: tuple[str, str]) -> str:
                return self.copy_file(*item)[0]

            with ThreadPoolExecutor(max_workers=min(4, len(files))) as pool:
                return list(pool.map(copy_member, files))

    classes.update({"egg_info": EggInfo, "install_lib": InstallLib})
    return classes
