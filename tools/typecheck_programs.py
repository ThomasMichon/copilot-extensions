"""Explicit checker execution and physical input custody for independent programs."""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
import os
from pathlib import Path
import re
import subprocess
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from tools.typecheck import Project
    from tools.typecheck_source_views import InstallerProgramView


class ProgramError(ValueError):
    """A declared program cannot be checked faithfully."""


class IdentityReader(Protocol):
    def __call__(self, path: Path, *, directory: bool = False) -> tuple[int, int]: ...


class _ProgramChecker(Protocol):
    def __call__(
        self, root: Path, project: Project, files: tuple[str, ...], *,
        all_paths: tuple[str, ...], strict: bool = False, program_root: str | None = None,
        environment_root: Path | None = None, input_verifier: Callable[[], None] | None = None,
        payload: InstallerOverlay | None = None,
    ) -> int: ...


class _InstallerFactory(Protocol):
    def __call__(
        self, root: Path, program: str, paths: tuple[str, ...], shared: tuple[str, ...],
        stage_factory: Callable[[], Path], identity: IdentityReader,
    ) -> AbstractContextManager[InstallerProgramView | None]: ...


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


@dataclass(frozen=True)
class Program:
    root: str
    import_roots: tuple[str, ...] = ()


@dataclass(frozen=True)
class InstallerOverlay:
    root: Path
    consumer: str

    def remap(self, source_root: Path, path: Path) -> Path:
        if path.is_relative_to(source_root / self.consumer):
            return self.root / path.relative_to(source_root)
        return path

    def imports(self, source_root: Path, paths: list[str], adapter: Path) -> list[str]:
        owned_src = source_root / self.consumer / "src"
        return list(dict.fromkeys((
            *((str(adapter),) if str(adapter) in paths else ()),
            str(self.root / self.consumer / "scripts"),
            *((str(self.remap(source_root, owned_src)),) if str(owned_src) in paths else ()),
            *(str(self.remap(source_root, Path(path))) for path in paths),
        )))

    def targets(self, source_root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
        mapped: list[str] = []
        for path in paths:
            original = Path(path) if Path(path).is_absolute() else source_root / path
            target = self.remap(source_root, original)
            mapped.append(str(target) if target != original else path)
        return tuple(mapped)

    def diagnostics(self, text: str) -> str:
        prefixes = (str(self.root) + os.sep, self.root.as_posix() + "/")
        return re.sub(
            r"^(?:" + "|".join(re.escape(prefix) for prefix in prefixes) + r")([^\r\n:]+)",
            lambda match: match.group(1).replace("\\", "/"), text, flags=re.MULTILINE,
        )


def independent_programs(
    files: tuple[str, ...], all_paths: tuple[str, ...], roots: tuple[str, ...],
    owns: Callable[[str], bool],
) -> tuple[tuple[str, ...], ...]:
    """Assign every selected physical file to exactly one unchanged owner program."""
    for index, root in enumerate(roots):
        if any(root == other or root.startswith(other + "/") or other.startswith(root + "/")
               for other in roots[:index]):
            raise ProgramError("Independent program roots must not overlap")
        if not any(path.startswith(root + "/") and owns(path) for path in all_paths):
            raise ProgramError(f"Independent program root has no owned Python source: {root}")
    if len(files) != len(set(files)) or not set(files) <= set(all_paths) or any(
        not owns(path) for path in files
    ):
        raise ProgramError("Independent programs require unique inventoried owner files")
    assigned: list[list[str]] = [[] for _ in range(len(roots) + 1)]
    for path in files:
        group = next(
            (index for index, root in enumerate(roots, 1) if path.startswith(root + "/")), 0,
        )
        assigned[group].append(path)
    return tuple(tuple(group) for group in assigned if group)


def check_ordinary_program(
    root: Path, project: Project, files: tuple[str, ...], *,
    all_paths: tuple[str, ...], strict: bool, check: _ProgramChecker,
    identity: IdentityReader, candidate_factory: _InstallerFactory,
    environment: Callable[[Path, Project], tuple[Path, str]], nonce: Callable[[], str],
) -> int:
    """Keep ordinary imports while qualifying registered consumers as shipped."""
    from tools import installer_engine_ref

    declared = tuple(dict.fromkeys((
        *(f"plugins/{name}" for name in (*installer_engine_ref.ADOPTERS, *installer_engine_ref.PYTHON_ADOPTERS)),
        *installer_engine_ref.STANDALONE_PYTHON_ADOPTERS,
        *installer_engine_ref.STANDALONE_NATIVE_ADOPTERS,
    )))
    family = (*installer_engine_ref.FILES, *installer_engine_ref.PYTHON_FILES, *installer_engine_ref.SUPPORT_FILES)
    consumers = tuple(
        consumer for consumer in sorted(declared, key=len, reverse=True)
        if any(project.owns(consumer + "/scripts/" + name) for name in family)
        and any(path.startswith(consumer + "/") for path in files)
    )
    if not consumers:
        return check(root, project, files, all_paths=all_paths, strict=strict)
    captured = ProgramInputs.capture(root, files, identity)
    remaining = files
    failure = 0
    for consumer in consumers:
        selected = tuple(path for path in remaining if path.startswith(consumer + "/"))
        if not selected:
            continue
        member_paths = tuple(path for path in all_paths if path.startswith(consumer + "/"))
        captured.verify()
        try:
            with candidate_factory(
                root, consumer, member_paths, (),
                lambda: environment(root, project)[0].parent / (
                    "installer-program-" + project.name + "-" + nonce()
                ),
                identity,
            ) as candidate:
                if candidate is None:
                    raise ProgramError("Declared installer consumer lacks its materialized source view")
                print(f"Checking materialized ordinary installer consumer: {consumer}")
                result = check(
                    root, project, selected, all_paths=all_paths, strict=strict,
                    input_verifier=candidate.verify,
                    payload=InstallerOverlay(candidate.root, consumer),
                )
                failure = failure or result
        finally:
            captured.verify()
        remaining = tuple(path for path in remaining if path not in selected)
    if remaining:
        captured.verify()
        try:
            result = check(root, project, remaining, all_paths=all_paths, strict=strict)
            failure = failure or result
        finally:
            captured.verify()
    return failure


def check_independent_project(
    root: Path, project: Project, files: tuple[str, ...], *,
    all_paths: tuple[str, ...], strict: bool, check: _ProgramChecker,
    identity: IdentityReader, candidate_factory: _InstallerFactory,
    environment: Callable[[Path, Project], tuple[Path, str]], nonce: Callable[[], str],
) -> int:
    """Run isolated owned programs with their declared shared dependencies."""
    programs = independent_programs(
        files, all_paths, tuple(program.root for program in project.program_roots), project.owns,
    )
    if not programs:
        raise ProgramError("Independent programs have no selected Python files")
    shared_roots = tuple(
        directory for directory in project.import_roots
        if not any(
            project.owns(path) and path.startswith(directory + "/")
            for path in all_paths
        )
    )
    captured = ProgramInputs.capture(root, files, identity)
    failure = 0
    for index, program in enumerate(programs, 1):
        declaration = next(
            (item for item in project.program_roots if program[0].startswith(item.root + "/")), None,
        )
        member_project = replace(
            project, import_roots=tuple(dict.fromkeys((*declaration.import_roots, *shared_roots))),
        ) if declaration else project
        member_paths = tuple(
            path for path in all_paths
            if path.startswith(declaration.root + "/")
            or any(path.startswith(directory + "/") for directory in shared_roots)
        ) if declaration else all_paths
        captured.verify()
        print(f"Independent program {index}/{len(programs)}: {project.name}")
        try:
            if declaration:
                with candidate_factory(
                    root, declaration.root, member_paths, shared_roots,
                    lambda: environment(root, project)[0].parent / (
                        "installer-program-" + project.name + "-" + nonce()
                    ),
                    identity,
                ) as candidate:
                    if candidate is None:
                        result = check(
                            root, member_project, program, all_paths=member_paths, strict=strict,
                            program_root=declaration.root,
                        )
                    else:
                        print("Checking materialized installer program; developer mirrors are not packaged proof.")
                        result = check(
                            candidate.root, member_project, program,
                            all_paths=candidate.paths, strict=strict, program_root=declaration.root,
                            environment_root=root, input_verifier=candidate.verify,
                        )
            else:
                result = check_ordinary_program(
                    root, member_project, program, all_paths=member_paths, strict=strict,
                    check=check, identity=identity, candidate_factory=candidate_factory,
                    environment=environment, nonce=nonce,
                )
            failure = failure or result
        finally:
            captured.verify()
    return failure


@dataclass(frozen=True)
class ProgramInputs:
    root: Path
    paths: tuple[str, ...]
    identity: IdentityReader
    directories: tuple[tuple[Path, tuple[int, int]], ...]
    files: tuple[tuple[Path, tuple[int, int], bytes], ...]

    @classmethod
    def capture(
        cls, root: Path, paths: tuple[str, ...], identity: IdentityReader,
    ) -> ProgramInputs:
        directories: dict[Path, tuple[int, int]] = {}
        files: list[tuple[Path, tuple[int, int], bytes]] = []
        for relative in paths:
            path = root / relative
            parent = Path(path.anchor)
            directories[parent] = identity(parent, directory=True)
            for part in path.parts[1:-1]:
                parent /= part
                current = identity(parent, directory=True)
                if parent in directories and directories[parent] != current:
                    raise ProgramError("Independent program directory changed during capture")
                directories[parent] = current
            before = identity(path)
            content = path.read_bytes()
            if identity(path) != before:
                raise ProgramError("Independent program file changed during capture")
            files.append((path, before, content))
        captured = cls(root, paths, identity, tuple(directories.items()), tuple(files))
        captured.verify()
        return captured

    def verify(self) -> None:
        for path, expected in self.directories:
            if self.identity(path, directory=True) != expected:
                raise ProgramError("Independent program directory identity changed")
        for path, expected, content in self.files:
            if (self.identity(path) != expected or path.read_bytes() != content
                    or self.identity(path) != expected):
                raise ProgramError("Independent program source changed during type checking")
