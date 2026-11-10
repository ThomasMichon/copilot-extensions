"""Run explicit, reproducible MyPy checks for the repository's admitted projects."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import hashlib
from itertools import product
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from typing import Callable, Literal, cast
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "libs" / "installer-engine"))
from tools._admission_protocol import acquire as acquire_environment  # noqa: E402
from tools.cache_directory_paths import ordinary_cache_root  # noqa: E402
from tools import plugin_test_resources  # noqa: E402
from tools.typecheck_source_views import (  # noqa: E402
    GUARD_ENTRY, GUARD_MODULE, INSTALLATION_ENTRY, SourceView, SourceViewError,
    _write_view, guard_alias_view, installation_view,
    HARNESS_PREFIX, HarnessProgramView, harness_emitted_inputs, harness_emitted_views,
)
from tools.typecheck_catalog_views import (  # noqa: E402
    MEMBERS as CATALOG_MEMBERS, catalog_inputs, catalog_views,
)
from installer_dependency_policy import DependencyPolicyError, run_uv_install  # noqa: E402
from single_instance_lease import AlreadyRunningError  # noqa: E402

Level = Literal["pending", "basic", "strict"]


class TypecheckError(ValueError):
    """The checker cannot faithfully qualify its selected input."""


@dataclass(frozen=True)
class Project:
    name: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    level: Level
    python_version: str
    platforms: tuple[str, ...]
    requirements: str
    additional_python_versions: tuple[str, ...] = ()
    import_roots: tuple[str, ...] = ()

    def owns(self, path: str) -> bool:
        """Match exact files or complete directory prefixes, not filename globs."""
        def matches(prefix: str) -> bool:
            return path == prefix or path.startswith(prefix + "/")
        return any(map(matches, self.include)) and not any(map(matches, self.exclude))


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypecheckError("Expected a JSON object with string keys")
    return cast(dict[str, object], value)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TypecheckError(f"Duplicate manifest field: {key}")
        result[key] = value
    return result


def _strings(value: object, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypecheckError(f"{field} must be a list of strings")
    result = tuple(item for item in value if isinstance(item, str))
    if (not result and not allow_empty) or any(not item for item in result):
        raise TypecheckError(f"{field} cannot be empty")
    if len(set(result)) != len(result):
        raise TypecheckError(f"{field} contains duplicate values")
    return result


def _relative(value: str) -> str:
    if (
        not value or "\\" in value or ":" in value or value.startswith("/")
        or any(ord(char) < 32 or 127 <= ord(char) < 160 or char in "\u2028\u2029" for char in value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise TypecheckError(f"Not a repository-relative path: {value!r}")
    return PurePosixPath(value).as_posix()


def load_projects(root: Path = ROOT) -> tuple[Project, ...]:
    """Read a versioned manifest without accepting arbitrary MyPy arguments."""
    value: object = json.loads(
        (root / "tools" / "typecheck-projects.json").read_text(encoding="utf-8"),
        object_pairs_hook=_pairs,
    )
    manifest = _object(value)
    fields = {"version", "projects", "python_version", "platforms", "requirements"}
    if set(manifest) != fields or type(manifest["version"]) is not int:
        raise TypecheckError("Invalid typecheck manifest fields")
    if manifest["version"] != 1 or not isinstance(manifest["projects"], list):
        raise TypecheckError("Unsupported typecheck manifest")
    projects: list[Project] = []
    rows: list[object] = manifest["projects"]
    required = {"name", "include", "exclude", "level"}
    defaults = {key: manifest[key] for key in ("python_version", "platforms", "requirements")}
    for value in rows:
        row = _object(value)
        if not required <= set(row) <= required | set(defaults) | {"additional_python_versions", "import_roots"}:
            raise TypecheckError("Invalid project fields")
        row = {**defaults, **row}
        name, level, python_version, requirements = (
            row["name"], row["level"], row["python_version"], row["requirements"]
        )
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            raise TypecheckError("Invalid project name")
        if level not in ("pending", "basic", "strict"):
            raise TypecheckError(f"{name}: invalid admission level")
        if not isinstance(python_version, str) or not re.fullmatch(r"3\.[0-9]+", python_version):
            raise TypecheckError(f"{name}: invalid Python version")
        additional = _strings(
            row.get("additional_python_versions", []), "additional_python_versions", allow_empty=True,
        )
        for version in additional:
            if not re.fullmatch(r"3\.(?:0|[1-9][0-9]*)", version):
                raise TypecheckError(f"{name}: invalid additional Python version")
            if tuple(map(int, version.split("."))) <= tuple(map(int, python_version.split("."))):
                raise TypecheckError(f"{name}: additional Python versions must exceed the minimum")
        if not isinstance(requirements, str):
            raise TypecheckError(f"{name}: invalid requirements path")
        include = tuple(_relative(path) for path in _strings(row["include"], "include"))
        exclude = tuple(_relative(path) for path in _strings(row["exclude"], "exclude", allow_empty=True))
        import_roots = tuple(
            _relative(path) for path in _strings(row.get("import_roots", []), "import_roots", allow_empty=True)
        )
        for path in import_roots:
            _import_directory(root, path)
        platforms = _strings(row["platforms"], "platforms")
        if any(platform not in ("linux", "win32", "darwin") for platform in platforms):
            raise TypecheckError(f"{name}: unsupported platform")
        projects.append(Project(
            name, include, exclude, cast(Level, level), python_version, platforms,
            _relative(requirements), additional,
            import_roots=import_roots,
        ))
    if len({project.name for project in projects}) != len(projects) or not projects:
        raise TypecheckError("Project names must be unique and the portfolio nonempty")
    return tuple(projects)


def _import_directory(root: Path, relative: str) -> Path:
    _import_identity(root, directory=True)
    directory = root
    for part in _relative(relative).split("/"):
        directory /= part
        _import_identity(directory, directory=True)
    if os.pathsep in str(directory.absolute()):
        raise TypecheckError(
            f"Import root contains the native path-list separator: {directory}"
        )
    return directory


def _import_identity(path: Path, *, directory: bool = False) -> tuple[int, int]:
    try:
        info = path.lstat()
    except OSError as error:
        raise TypecheckError(f"Import input is missing: {path}") from error
    ordinary = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not ordinary or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise TypecheckError(f"Import input must be an ordinary repository {'directory' if directory else 'file'}: {path}")
    return info.st_dev, info.st_ino


@dataclass(frozen=True)
class _ImportInputs:
    roots: tuple[str, ...]
    inventory: tuple[str, ...]
    directories: tuple[tuple[str, tuple[int, int]], ...]
    files: tuple[tuple[str, tuple[int, int], bytes], ...]

    def verify(self, root: Path) -> None:
        if self != _import_inputs(root, self.roots, self.inventory):
            raise TypecheckError("Declared import inputs changed during type checking")


def _import_suffix(path: Path) -> str:
    suffix = path.suffix
    lower = suffix.lower()
    if os.name == "nt" and suffix != lower and lower in {".py", ".pyi"}:
        try:
            if path.with_suffix(lower).samefile(path):
                return lower
        except OSError:
            pass
    return suffix


def _import_inputs(root: Path, roots: tuple[str, ...], paths: tuple[str, ...]) -> _ImportInputs:
    owned = set(paths)
    directories: dict[str, tuple[int, int]] = {}
    files: dict[str, tuple[tuple[int, int], bytes]] = {}
    expected: set[str] = set()
    for relative in roots:
        directory = _import_directory(root, relative)
        ancestor = root
        directories[""] = _import_identity(ancestor, directory=True)
        for part in relative.split("/"):
            ancestor /= part
            directories[ancestor.relative_to(root).as_posix()] = _import_identity(ancestor, directory=True)
        prefix = relative + "/"
        members = {
            path for path in paths if path.startswith(prefix)
            and path.endswith(".py")
            and all(part.isidentifier() for part in path[len(prefix):-3].split("/"))
        }
        if not members:
            raise TypecheckError(f"Import root has no inventoried Python source: {relative}")
        expected.update(members)
        root_identity = _import_identity(directory, directory=True)
        directories[relative] = root_identity
        pending: list[tuple[Path, tuple[tuple[str, tuple[int, int]], ...]]] = [
            (directory, ((relative, root_identity),)),
        ]
        while pending:
            parent, ancestors = pending.pop()
            for child in sorted(parent.iterdir()):
                suffix = _import_suffix(child)
                module = suffix in {".py", ".pyi"} and child.stem.isidentifier()
                if not module and not child.name.isidentifier():
                    continue
                info = child.lstat()
                name = _relative(child.relative_to(root).as_posix())
                if stat.S_ISDIR(info.st_mode) or (not module and child.is_dir()):
                    if child.name.isidentifier():
                        identity = _import_identity(child, directory=True)
                        pending.append((child, (*ancestors, (name, identity))))
                    continue
                if not module:
                    continue
                identity = _import_identity(child)
                if suffix == ".pyi":
                    raise TypecheckError(f"First-party import stubs cannot substitute for source: {name}")
                owned_name = child.with_suffix(suffix).relative_to(root).as_posix()
                if owned_name not in owned:
                    raise TypecheckError(f"Import source is unowned or ignored: {name}")
                content = child.read_bytes()
                if _import_identity(child) != identity:
                    raise TypecheckError(f"Import source identity changed during capture: {name}")
                directories.update(ancestors)
                files[owned_name] = identity, content
    if set(files) != expected:
        raise TypecheckError("Declared import source membership changed or is missing")
    return _ImportInputs(
        roots, paths, tuple(sorted(directories.items())),
        tuple((name, identity, content) for name, (identity, content) in sorted(files.items())),
    )


@dataclass
class _SourceViews:
    root: Path
    stage: Path
    views: list[SourceView] = field(default_factory=list)
    directories: dict[Path, tuple[int, int]] = field(default_factory=dict)
    sources: dict[Path, tuple[tuple[int, int], bytes]] = field(default_factory=dict)
    outputs: dict[Path, tuple[tuple[int, int], bytes]] = field(default_factory=dict)
    guard_search: tuple[str, ...] = ()
    procutil_search: tuple[str, ...] = ()

    def _parents(self, directory: Path, *, create: bool = False) -> None:
        current = Path(directory.anchor)
        self._directory(current, create=False)
        for part in directory.parts[1:]:
            current /= part
            self._directory(current, create=create)

    def _directory(self, path: Path, *, create: bool) -> None:
        if create:
            try:
                path.lstat()
            except FileNotFoundError:
                try:
                    path.mkdir()
                except FileExistsError:
                    pass
        identity = _import_identity(path, directory=True)
        if path in self.directories and self.directories[path] != identity:
            raise TypecheckError(f"Checker view directory identity changed: {path}")
        self.directories[path] = identity

    def _membership(self) -> None:
        expected = {path for path in self.outputs if path.parent == self.stage}
        if self.stage.exists() and set(self.stage.iterdir()) != expected:
            raise TypecheckError("Checker view stage contains unexpected or missing members")

    def add(
        self, factory: Callable[[Path, tuple[str, ...], Path], SourceView],
        paths: tuple[str, ...], inputs: tuple[str, ...], filename: str,
    ) -> SourceView:
        self._parents(self.stage, create=True)
        self._membership()
        for relative in inputs:
            source = self.root / relative
            self._parents(source.parent)
            identity = _import_identity(source)
            content = source.read_bytes()
            if _import_identity(source) != identity:
                raise TypecheckError(f"Checker source identity changed during capture: {relative}")
            self.sources[source] = identity, content
        view = factory(self.root, paths, self.stage / filename)
        self.views.append(view)
        self.outputs[view.output] = view.identity, view.content
        self.verify()
        return view

    def write(self, path: Path, content: bytes) -> None:
        self._parents(path.parent)
        identity = _write_view(path, content)
        self.outputs[path] = identity, content

    def add_harness(self, paths: tuple[str, ...]) -> tuple[SourceView, ...]:
        self._parents(self.stage, create=True)
        self._membership()
        inputs, _programs = harness_emitted_inputs(self.root, paths)
        for relative, content in inputs:
            source = self.root / relative
            self._parents(source.parent)
            identity = _import_identity(source)
            if source.read_bytes() != content or _import_identity(source) != identity:
                raise SourceViewError("Harness input changed during capture")
            self.sources[source] = identity, content
        emitted = harness_emitted_views(self.root, paths, self.stage)
        self.views.extend(emitted)
        for view in emitted:
            self.outputs[view.output] = view.identity, view.content
        self.verify()
        return emitted

    def add_catalog(self, paths: tuple[str, ...]) -> tuple[SourceView, ...]:
        self._parents(self.stage, create=True)
        self._membership()
        inputs, _flight, _template, _members = catalog_inputs(self.root, paths)
        for relative, content in inputs:
            source = self.root / relative
            self._parents(source.parent)
            identity = _import_identity(source)
            if source.read_bytes() != content or _import_identity(source) != identity:
                raise SourceViewError("Catalog input changed during capture")
            self.sources[source] = identity, content
        emitted = catalog_views(self.root, paths, self.stage)
        self.views.extend(emitted)
        for view in emitted:
            self.outputs[view.output] = view.identity, view.content
        self.verify()
        return emitted

    def _guard_providers(self) -> None:
        allowed = {
            view.output for view in self.views
            if view.output.name == GUARD_MODULE + ".py" and view.output in self.outputs
        }
        for entry in self.guard_search:
            directory = Path(entry)
            if not directory.exists():
                continue
            self._parents(directory)
            for child in directory.iterdir():
                name = os.path.normcase(child.name)
                module = os.path.normcase(GUARD_MODULE)
                if name == module or (
                    _import_suffix(child) in {".py", ".pyi"}
                    and os.path.normcase(child.stem) == module
                ):
                    if child not in allowed:
                        raise TypecheckError(f"Competing {GUARD_MODULE} provider: {child}")
                    if _import_identity(child) != self.outputs[child][0]:
                        raise TypecheckError(f"Guard alias output identity changed: {child}")

    def verify_inputs(self) -> None:
        for directory, identity in self.directories.items():
            if _import_identity(directory, directory=True) != identity:
                raise TypecheckError(f"Checker view directory identity changed: {directory}")
        for source, (identity, content) in self.sources.items():
            if _import_identity(source) != identity:
                raise TypecheckError(f"Checker source identity changed: {source}")
            if source.read_bytes() != content:
                raise SourceViewError(f"{source.relative_to(self.root).as_posix()}: source changed during type checking")

    def verify(self) -> None:
        self.verify_inputs()
        if self.procutil_search:
            _harness_procutil_provider(self.root, list(self.procutil_search))
        for output, (identity, content) in self.outputs.items():
            if _import_identity(output) != identity:
                raise TypecheckError(f"Checker output identity changed: {output}")
            if output.read_bytes() != content:
                raise SourceViewError("Checker output changed during type checking")
        self._membership()
        self._guard_providers()
        harness_bindings: set[tuple[object, ...]] = set()
        for view in self.views:
            if isinstance(view, HarnessProgramView):
                binding = (view.members, view.assets, view.inputs)
                if binding in harness_bindings:
                    continue
                harness_bindings.add(binding)
            view.verify(self.root)

    def cleanup(self) -> None:
        errors: list[ValueError | OSError] = []
        for output, (identity, _content) in tuple(self.outputs.items()):
            try:
                self._parents(output.parent)
                if output.exists():
                    if _import_identity(output) != identity:
                        raise TypecheckError(f"Replaced checker output retained: {output}")
                    output.unlink()
                del self.outputs[output]
            except (OSError, ValueError) as error:
                errors.append(error)
        if errors:
            raise errors[0]
        self.verify_inputs()
        self._membership()
        self._guard_providers()

    def diagnostics(self, text: str) -> str:
        for view in self.views:
            text = view.diagnostics(text, self.root)
        return text


def _run(argv: list[str], root: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="strict", timeout=120, creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )


def git_environment() -> dict[str, str]:
    """Keep repository selectors from redirecting inventory or change comparison."""
    selectors = {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
    env = {
        key: value for key, value in os.environ.items()
        if key.upper() not in selectors and not key.upper().startswith("GIT_CONFIG")
    }
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
    return env


def changed_paths(root: Path, base: str, head: str | None = None) -> tuple[str, ...]:
    """Compare verified commits, or the base against staged and unstaged source."""
    revisions: list[str] = []
    env = git_environment()
    for ref in (base,) if head is None else (base, head):
        result = _run(["git", "rev-parse", "--verify", "--end-of-options", ref + "^{commit}"], root, env=env)
        if result.returncode or not re.fullmatch(r"[0-9a-f]{40,64}\n?", result.stdout):
            raise TypecheckError(f"Cannot resolve change-comparison commit: {ref!r}")
        revisions.append(result.stdout.strip())
    result = _run(
        ["git", "diff", "--name-only", "--no-renames", "-z", *revisions, "--"], root, env=env,
    )
    if result.returncode:
        raise TypecheckError(result.stderr.strip() or "Git change comparison failed")
    return tuple(_relative(path) for path in result.stdout.split("\0") if path)


def python_paths(root: Path = ROOT) -> tuple[str, ...]:
    """Include staged, unstaged and untracked non-ignored developer files."""
    env = git_environment()
    result = _run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], root, env=env)
    if result.returncode:
        raise TypecheckError(result.stderr.strip() or "Git inventory failed")
    deleted = _run(["git", "ls-files", "--deleted", "-z"], root, env=env)
    if deleted.returncode:
        raise TypecheckError(deleted.stderr.strip() or "Git deletion inventory failed")
    missing = set(deleted.stdout.split("\0"))
    paths = sorted({path for path in result.stdout.split("\0") if path.endswith(".py")})
    present: list[str] = []
    for path in paths:
        _relative(path)
        source = root.joinpath(*path.split("/"))
        if path in missing and not source.exists():
            continue
        if not source.is_file() or not source.resolve().is_relative_to(root.resolve()):
            raise TypecheckError(f"Python source is missing or outside the repository: {path}")
        present.append(path)
    return tuple(present)


def inventory(paths: tuple[str, ...], projects: tuple[Project, ...]) -> dict[str, Project]:
    """Account for each physical Python file exactly once, even while pending."""
    owners: dict[str, Project] = {}
    for path in paths:
        matches = [project for project in projects if project.owns(path)]
        if len(matches) != 1:
            names = ", ".join(project.name for project in matches) or "unclassified"
            raise TypecheckError(f"{path}: expected one owner, found {names}")
        owners[path] = matches[0]
    if not owners:
        raise TypecheckError("Empty Python inventory cannot qualify the portfolio")
    return owners


def requirements_bytes(root: Path, project: Project) -> bytes:
    """Capture one flat lock; includes need a separately qualified graph snapshot."""
    requirements = root.joinpath(*project.requirements.split("/"))
    if not requirements.is_file() or not requirements.resolve().is_relative_to(root.resolve()):
        raise TypecheckError(f"{project.name}: requirements must exist inside the repository")
    contents = requirements.read_bytes()
    for line in contents.splitlines():
        option = line.strip()
        if option.startswith(b"-") and not option.startswith(b"--hash=sha256:"):
            raise TypecheckError(f"{project.name}: checker requirements must be a flat hash-locked export")
    return contents


def environment_path(
    root: Path, project: Project, *, captured_lock: bytes | None = None,
) -> tuple[Path, str]:
    contents = requirements_bytes(root, project) if captured_lock is None else captured_lock
    digest = hashlib.sha256(
        contents
        + f"\n{sys.version_info[:2]!r}\n{sys.platform}".encode("ascii")
    ).hexdigest()
    path = plugin_test_resources.resource_path(root) / "typecheck" / sys.platform / digest[:20]
    return ordinary_cache_root(path), digest


def environment_python(path: Path) -> Path:
    """Select only the explicitly prepared environment's interpreter."""
    return path / "Scripts" / "python.exe" if os.name == "nt" else path / "bin" / "python"


def prepare(root: Path, project: Project) -> None:
    """Explicitly create a hash-locked checker environment under its normal lease."""
    uv = shutil.which("uv")
    if uv is None:
        raise TypecheckError("uv is required for --prepare")
    contents = requirements_bytes(root, project)
    path, digest = environment_path(root, project, captured_lock=contents)
    plugin_test_resources.resource_root(root)
    path.mkdir(parents=True, exist_ok=True)
    lease = acquire_environment(0, path)
    try:
        marker = path / ".typecheck-prepared"
        if marker.is_file() and marker.read_text(encoding="utf-8").strip() == digest:
            if environment_python(path).is_file():
                return
        marker.unlink(missing_ok=True)
        before = path.stat()
        result = _run([uv, "venv", "--allow-existing", "--python", sys.executable, str(path)], root)
        if result.returncode:
            raise TypecheckError(result.stderr.strip() or "Checker environment creation failed")
        after = path.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise TypecheckError("uv replaced the leased checker environment directory")
        snapshot = path / "typecheck-requirements.txt"
        snapshot.write_bytes(contents)
        result = run_uv_install(
            uv, ["--python", str(environment_python(path)), "--require-hashes",
                 "-r", str(snapshot)],
            cwd=root, trusted_source_roots=(root,), check=True, timeout=300,
        )
        if result.returncode:
            raise TypecheckError("Checker dependency preparation failed")
        marker.write_text(digest + "\n", encoding="utf-8")
    finally:
        lease.release()


def _harness_procutil_provider(root: Path, imports: list[str]) -> Path:
    expected = root / "libs" / "agent-procutil" / "src" / "agent_procutil"
    for entry in imports:
        directory = Path(entry)
        candidates = [
            directory / name for name in
            ("agent_procutil", "agent_procutil.py", "agent_procutil.pyi", "agent_procutil-stubs")
            if (directory / name).exists() or (directory / name).is_symlink()
        ]
        if not candidates:
            continue
        _import_identity(directory, directory=True)
        if any(
            candidate.suffix == ".pyi" or candidate.name.endswith("-stubs")
            or (candidate.is_dir() and (candidate / "__init__.pyi").exists())
            for candidate in candidates
        ):
            raise TypecheckError("Harness emitted imports cannot use a winning agent_procutil stub")
        if candidates != [expected]:
            raise TypecheckError("Harness emitted imports require the winning candidate-local agent_procutil provider")
        _import_identity(expected, directory=True)
        _import_identity(expected / "__init__.py")
        return expected
    raise TypecheckError("Harness emitted imports lack their canonical agent_procutil provider")


def check_project(
    root: Path, project: Project, files: tuple[str, ...], *,
    all_paths: tuple[str, ...], strict: bool = False,
) -> int:
    """Check one declared scope; never execute product or test code."""
    if not files:
        raise TypecheckError(f"{project.name}: selected scope has no Python files")
    physical_count = len(files)
    path, digest = environment_path(root, project)
    python = environment_python(path)
    marker = path / ".typecheck-prepared"
    if not python.is_file() or not marker.is_file() or marker.read_text(encoding="utf-8").strip() != digest:
        raise TypecheckError(f"{project.name}: run python tools/typecheck.py {project.name} --prepare")
    lease = acquire_environment(0, path)
    views = _SourceViews(
        root, path.parent / ("source-views-" + path.name) / (project.name + uuid4().hex),
    )
    import_inputs: _ImportInputs | None = None
    try:
        if not python.is_file() or not marker.is_file() or marker.read_text(encoding="utf-8").strip() != digest:
            raise TypecheckError(f"{project.name}: run python tools/typecheck.py {project.name} --prepare")
        imports = {str(root), str(root / "libs" / "installer-engine")}
        imports.update(
            str(root.joinpath(*file.split("/")[:file.split("/").index("src") + 1]))
            for file in all_paths if "src" in file.split("/")
        )
        ordered_imports = sorted(imports)
        if project.import_roots:
            import_inputs = _import_inputs(root, project.import_roots, all_paths)
        for relative in project.import_roots:
            directory = _import_directory(root, relative)
            if str(directory) not in ordered_imports:
                ordered_imports.append(str(directory))
        guard = project.name == "copilot-extensions-harness"
        if guard:
            if (
                files.count(GUARD_ENTRY) != 1 or all_paths.count(GUARD_ENTRY) != 1
                or not project.owns(GUARD_ENTRY) or len(set(files)) != len(files)
                or set(files) != {file for file in all_paths if project.owns(file)}
            ):
                raise TypecheckError(
                    f"{project.name}: guard alias requires its complete owned physical binding"
                )
            views.guard_search = tuple(ordered_imports)
        supplemental = 0
        if project.name == "lib-payload-invocation":
            if set(files) != set(CATALOG_MEMBERS) or len(files) != len(CATALOG_MEMBERS):
                raise TypecheckError("Catalog witnesses require the complete canonical project")
            emitted_catalogs = views.add_catalog(all_paths)
            supplemental += len(emitted_catalogs)
            files = (*files, *(str(view.output) for view in emitted_catalogs))
        if guard and HARNESS_PREFIX.rstrip("/") in project.include:
            if "libs/agent-procutil/src" not in project.import_roots:
                raise TypecheckError("Harness emitted imports require the declared ordinary procutil root")
            _harness_procutil_provider(root, ordered_imports)
            views.procutil_search = tuple(ordered_imports)
            emitted = views.add_harness(all_paths)
            supplemental += len(emitted)
            files = (*files, *(str(view.output) for view in emitted))
            views.verify()
        env = {
            key: value for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME", "MYPYPATH"}
        }
        if INSTALLATION_ENTRY in files or INSTALLATION_ENTRY in all_paths:
            installation_inputs = tuple(
                file for file in all_paths if file == INSTALLATION_ENTRY or (
                    file.startswith(INSTALLATION_ENTRY.rsplit("/", 1)[0] + "/_installation_context_")
                    and "/" not in file[len(INSTALLATION_ENTRY.rsplit("/", 1)[0]) + 1:]
                    and not file.endswith("_installation_context_worker.py")
                )
            )
            view = views.add(
                installation_view, all_paths, installation_inputs, "installation_context.py",
            )
            if INSTALLATION_ENTRY in files:
                members = {name for name, _content in view.inputs}
                if not members <= set(files):
                    raise TypecheckError(f"{project.name}: composed fragments must share one project owner")
                files = (*tuple(file for file in files if file not in members), str(view.output))
            ordered_imports.insert(0, str(view.output.parent))
        if guard:
            view = views.add(guard_alias_view, files, (GUARD_ENTRY,), GUARD_MODULE + ".py")
            files = tuple(str(view.output) if file == GUARD_ENTRY else file for file in files)
            if str(view.output.parent) not in ordered_imports:
                ordered_imports.insert(0, str(view.output.parent))
            views.guard_search = tuple(ordered_imports)
            if supplemental:
                views.procutil_search = tuple(ordered_imports)
        if (project.import_roots or guard) and any(os.pathsep in entry for entry in ordered_imports):
            raise TypecheckError("Import search paths contain the native path-list separator")
        env["MYPYPATH"] = os.pathsep.join(ordered_imports)
        views._parents(views.stage, create=True)
        views._membership()
        targets = views.stage / "typecheck-targets.txt"
        views.write(targets, ("\n".join(files) + "\n").encode("utf-8"))
        config = views.stage / "typecheck-mypy.ini"
        views.write(config, b"[mypy]\n")
        failure = 0
        versions = (project.python_version, *project.additional_python_versions)
        for version, platform in product(versions, project.platforms):
            argv = [
                str(python), "-I", "-X", "utf8", "-m", "mypy", "--config-file", str(config),
                "--python-version", version, "--platform", platform,
                "--explicit-package-bases", "--follow-imports", "silent",
                "--check-untyped-defs", "--show-error-codes", "--no-pretty",
                "--cache-dir", (
                    os.devnull if project.import_roots or guard
                    else str(path / "mypy-cache" / platform / version)
                ),
            ]
            if strict or project.level == "strict":
                argv.append("--strict")
            argv.append("@" + str(targets))
            if import_inputs is not None:
                import_inputs.verify(root)
            views.verify()
            try:
                result = _run(argv, root, env=env)
            finally:
                try:
                    views.verify()
                finally:
                    if import_inputs is not None:
                        import_inputs.verify(root)
            print(f"[{project.level}] {project.name} (Python {version}, {platform}, {physical_count} physical files, {len(files)} targets)")
            if supplemental:
                print(f"Supplemental portable emitted programs: {supplemental}; no native execution qualification")
            print(views.diagnostics(result.stdout), end="")
            print(views.diagnostics(result.stderr), end="", file=sys.stderr)
            if result.returncode:
                if not project.additional_python_versions:
                    return result.returncode
                failure = failure or result.returncode
        return failure
    finally:
        try:
            try:
                views.verify()
            finally:
                views.cleanup()
                if views.stage in views.directories:
                    views.stage.rmdir()
        finally:
            try:
                if import_inputs is not None:
                    import_inputs.verify(root)
            finally:
                lease.release()


def main(argv: list[str] | None = None) -> int:
    """Provide the same explicit scope and policy selection locally and in CI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="*")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true", help="probe all projects, including pending")
    selection.add_argument("--enforced", action="store_true", help="check admitted projects (default)")
    selection.add_argument("--changed", action="store_true", help="conservatively check all admitted consumers")
    selection.add_argument("--inventory", action="store_true", help="classify Python without checking")
    parser.add_argument("--base", help="--changed comparison base (default: origin/dev)")
    parser.add_argument("--head", help="--changed pinned head (default: current staged/unstaged files)")
    parser.add_argument("--prepare", action="store_true", help="explicitly prepare selected environments")
    parser.add_argument("--strict", action="store_true", help="probe strictness without changing admission")
    parser.add_argument("--require-complete", action="store_true", help="require every project to be strict")
    parser.add_argument("--json", action="store_true", help="machine-readable --inventory output")
    args = parser.parse_args(argv)
    if args.projects and (args.all or args.enforced or args.changed or args.inventory):
        parser.error("choose project names or a portfolio selector, not both")
    if (args.base or args.head) and not args.changed:
        parser.error("--base and --head require --changed")
    if args.inventory and (args.prepare or args.strict):
        parser.error("--inventory does not prepare or run a checker")
    if args.json and not args.inventory:
        parser.error("--json requires --inventory")
    try:
        projects = load_projects()
        owners = inventory(python_paths(), projects)
        if args.changed:
            changes = changed_paths(ROOT, args.base or "origin/dev", args.head)
            print(
                f"Compared {len(changes)} changed paths; checking every admitted project "
                "(conservative dependency impact; untracked Python included in inventory)."
            )
        counts = Counter(project.level for project in projects)
        if not args.json:
            print(
                f"Physical Python inventory: {len(owners)} files; "
                f"{counts['pending']} pending / {counts['basic']} basic / {counts['strict']} strict projects"
            )
        if args.require_complete and any(project.level != "strict" for project in projects):
            raise TypecheckError("Portfolio is not complete: pending/basic projects remain")
        if args.inventory:
            if args.json:
                print(json.dumps({
                    "version": 1, "kind": "inventory-only",
                    "project_levels": dict(counts),
                    "files": [
                        {"path": path, "project": owner.name, "level": owner.level}
                        for path, owner in owners.items()
                    ],
                }))
                return 0
            for project in projects:
                count = sum(owner.name == project.name for owner in owners.values())
                print(f"{project.name}: {project.level}, {count} files")
            return 0
        names: list[str] = args.projects
        unknown = set(names) - {project.name for project in projects}
        if unknown:
            raise TypecheckError("Unknown projects: " + ", ".join(sorted(unknown)))
        selected = [
            project for project in projects
            if project.name in names or (not names and (args.all or project.level != "pending"))
        ]
        if not selected:
            raise TypecheckError("No projects selected")
        failed = False
        for project in selected:
            files = tuple(path for path, owner in owners.items() if owner.name == project.name)
            if args.all and project.level == "pending" and not files:
                print(f"{project.name}: pending, no physical Python files in this checkout; not checked.")
                continue
            if args.prepare:
                prepare(ROOT, project)
                path, _digest = environment_path(ROOT, project)
                print(f"Prepared {project.name}: {environment_python(path)}")
            else:
                failed = check_project(
                    ROOT, project, files, all_paths=tuple(owners), strict=args.strict,
                ) != 0 or failed
        if not args.prepare and any(project.level == "pending" for project in selected):
            print("Pending project probes do not certify admission.", file=sys.stderr)
            failed = True
        return int(failed)
    except AlreadyRunningError as exc:
        print(f"typecheck: checker environment is busy: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError, DependencyPolicyError, subprocess.SubprocessError) as exc:
        print(f"typecheck: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
