"""Update-only recovery of an already-adopted standalone Worktree Manager."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from contextlib import ExitStack
from collections.abc import Callable
from pathlib import Path

from agent_procutil import SyncProcessLaunchError, no_window_kwargs

from . import control_plane_providers as providers, manager_launch_cli, output, update_events
from .manager_launcher_ownership import _OWNED_STUBS as _OWNED_STUBS


class RecoveryRefused(RuntimeError):
    pass


class RecoveryTimeout(RecoveryRefused):
    """An owned child timed out after verified process-tree cleanup."""


def _ordinary(path: Path, *, directory: bool = False) -> None:
    if path.is_symlink() or path.resolve() != path.absolute():
        raise RecoveryRefused(f"Manager ownership path is indirect: {path}")
    if not (path.is_dir() if directory else path.is_file()):
        raise RecoveryRefused(f"Manager ownership path is missing: {path}")


def _read(path: Path) -> bytes | None:
    if path.is_symlink() or path.resolve() != path.absolute():
        raise RecoveryRefused(f"Manager ownership path is indirect: {path}")
    try:
        value = path.read_bytes()
    except FileNotFoundError:
        return None
    _ordinary(path)
    return value


def _digest(path: Path) -> str | None:
    value = _read(path)
    return hashlib.sha256(value).hexdigest() if value is not None else None


def _slot(root: Path, selection: bytes | None) -> Path | None:
    if selection is None:
        return None
    version = selection.decode("utf-8").strip()
    if not version:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version) or version.endswith("."):
        raise RecoveryRefused("Manager selection is not an ordinary slot name")
    slot = root / "versions" / version
    if not slot.is_dir():
        return None
    _ordinary(slot, directory=True)
    return slot


@dataclass(frozen=True)
class Adoption:
    root: Path
    command: tuple[str, ...]
    evidence: tuple[tuple[Path, str | None], ...]
    registry: Path
    registrations: tuple[str, ...]
    minimum_version: tuple[int, int, int, int]
    repo: str = ""
    ref: str = ""

    def verify(self) -> None:
        if tuple(sorted(path.name for path in self.registry.glob("*.json"))) != self.registrations:
            raise RecoveryRefused("Manager provider registrations changed during recovery")
        for path, digest in self.evidence:
            if _digest(path) != digest:
                raise RecoveryRefused(f"Manager adoption/source changed during recovery: {path}")

    def with_source(self) -> Adoption:
        self.verify()
        repo, ref, evidence = _source(self.root, _slot(self.root, _read(self.root / "current-version")))
        snapshots = dict(self.evidence)
        for path, digest in evidence:
            if path in snapshots and snapshots[path] != digest:
                raise RecoveryRefused(f"Manager source changed during recovery: {path}")
            snapshots[path] = digest
        selected = Adoption(
            self.root, self.command, tuple(snapshots.items()),
            self.registry, self.registrations, self.minimum_version, repo, ref,
        )
        selected.verify()
        return selected


def _source(root: Path, slot: Path | None) -> tuple[str, str, list[tuple[Path, str | None]]]:
    config = root / "config.toml"
    data = _read(config)
    document = tomllib.loads(data.decode("utf-8")) if data is not None else {}
    explicit = document.get("source", {})
    if not isinstance(explicit, dict):
        raise RecoveryRefused("Manager source configuration is not a table")
    defaults: dict[str, str] = {}
    evidence = [(config, hashlib.sha256(data).hexdigest() if data is not None else None)]
    if slot is not None:
        payload = slot / "payload" if (slot / "payload").is_dir() else slot
        project_path = payload / "pyproject.toml"
        project_bytes = _read(project_path)
        if project_bytes is None:
            raise RecoveryRefused("Registered Manager slot has no package metadata")
        project = tomllib.loads(project_bytes.decode("utf-8"))
        name = project.get("project", {}).get("name", "")
        if not isinstance(name, str) or re.sub(r"[-_.]+", "-", name).lower() != "copilot-extensions-worktree-manager":
            raise RecoveryRefused("Registered Manager slot belongs to another package")
        source = payload / "src" / "worktree_manager" / "source_config.py"
        source_bytes = _read(source)
        if source_bytes is None:
            raise RecoveryRefused("Registered Manager has no attributable source defaults")
        evidence += [
            (project_path, hashlib.sha256(project_bytes).hexdigest()),
            (source, hashlib.sha256(source_bytes).hexdigest()),
        ]
        # Read the installed Manager's own literal defaults; never execute its
        # unconstructed application or substitute the updater's marketplace.
        for node in ast.parse(source_bytes.decode("utf-8")).body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in ("DEFAULT_REPO", "DEFAULT_REF"):
                        if isinstance(node.value.value, str):
                            defaults[target.id] = node.value.value
    values = []
    for field, key in (("repo", "DEFAULT_REPO"), ("ref", "DEFAULT_REF")):
        value = explicit[field] if field in explicit else defaults.get(key)
        if not isinstance(value, str) or not value.strip() or value.startswith("-") or "\n" in value:
            raise RecoveryRefused(f"Selected Manager source has no attributable {field}")
        values.append(value)
    repo, ref = values
    if not re.match(r"^[^/\\:]+:", repo) and not Path(repo).is_absolute():
        raise RecoveryRefused("Relative Manager source requires an explicit absolute source selection")
    return repo, ref, evidence


def _registration(directory: Path):
    if directory.is_symlink() or directory.resolve() != directory.absolute():
        raise RecoveryRefused("Manager provider registry is indirect")
    if not directory.exists():
        selected = os.environ.get(manager_launch_cli._CONTROL_PLANE_PROVIDER_ENV, "").strip()
        if selected:
            raise RecoveryRefused("Selected control-plane provider is not registered")
        return None, ()
    _ordinary(directory, directory=True)
    manifests = {}
    entries = tuple(sorted(directory.glob("*.json")))
    for path in entries:
        data = _read(path)
        try:
            manifest = providers._parse_control_plane_provider_manifest(
                json.loads(data), source_path=str(path),
            )
        except (TypeError, ValueError) as error:
            raise RecoveryRefused(f"Invalid control-plane registration: {path}") from error
        if manifest.provider in manifests:
            raise RecoveryRefused(f"Duplicate control-plane registration: {manifest.provider}")
        manifests[manifest.provider] = manifest
    selected = os.environ.get(manager_launch_cli._CONTROL_PLANE_PROVIDER_ENV, "").strip()
    if selected:
        if selected not in manifests:
            raise RecoveryRefused("Selected control-plane provider is not registered")
        return manifests[selected], entries
    if len(manifests) > 1:
        raise RecoveryRefused("Multiple control-plane providers are registered; select one explicitly")
    return next(iter(manifests.values()), None), entries


def adopted_manager() -> Adoption | None:
    directory = providers._machine_control_plane_providers_dir()
    selected, entries = _registration(directory)
    if selected is None:
        return None
    if selected.provider != "worktree-manager":
        return None
    root = Path(selected.provider_root)
    if not root.is_absolute() or root == Path(root.anchor):
        raise RecoveryRefused("Manager provider has no attributable installation root")
    _ordinary(root, directory=True)
    command = selected.command
    if len(command) != 1:
        raise RecoveryRefused("Manager registration does not name its owned binstub")
    stub = Path(command[0])
    local_bin = Path(os.environ.get("USERPROFILE") or Path.home()) / ".local" / "bin"
    primary = "worktree-manager.cmd" if os.name == "nt" else "worktree-manager"
    if stub.parent.resolve() != local_bin.resolve() or stub.name != primary:
        raise RecoveryRefused("Manager command is outside its registered local-bin ownership")
    _ordinary(stub)
    paths = [*entries, stub, root / "current-version", root / "last-known-good", root / "config.toml"]
    snapshots = {path: _read(path) for path in paths}
    slot = _slot(root, snapshots[root / "current-version"])
    package_evidence = []
    if slot is not None:
        payload = slot / "payload" if (slot / "payload").is_dir() else slot
        project_path = payload / "pyproject.toml"
        project = _read(project_path)
        name = tomllib.loads(project.decode("utf-8")).get("project", {}).get("name", "") if project else ""
        if not isinstance(name, str) or re.sub(r"[-_.]+", "-", name).lower() != "copilot-extensions-worktree-manager":
            raise RecoveryRefused("Registered Manager slot belongs to another package")
        package_evidence.append((project_path, hashlib.sha256(project).hexdigest()))
    adoption = Adoption(
        root, command, (*(
            (path, hashlib.sha256(data).hexdigest() if data is not None else None)
            for path, data in snapshots.items()
        ), *package_evidence), directory,
        tuple(path.name for path in entries), selected.minimum_version,
    )
    adoption.verify()
    return adoption


def _runtime_api():
    prefix = Path(sys.prefix).resolve()
    if not Path(__file__).resolve().is_relative_to(prefix):
        raise RecoveryRefused("Manager recovery must execute from the installed updater runtime")
    path = prefix / ".runtime-bootstrap" / "versioned_runtime.py"
    _ordinary(path)
    spec = importlib.util.spec_from_file_location("_manager_recovery_runtime", path)
    if spec is None or spec.loader is None:
        raise RecoveryRefused("Installed updater has no retained runtime API")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _source_environment(
    adoption: Adoption, cwd: Path, env: dict[str, str],
) -> tuple[dict[str, str], Callable[[], None]]:
    from . import update_auth as auth, update_auth_context as context, update_events

    environment = auth.non_repository_environment(env, cwd)
    if context.mode() == "off":
        return environment, lambda: None
    source = auth.source("manager-recovery", adoption.repo, purpose="manager", cwd=cwd)
    if source is None:
        return environment, lambda: None  # Local/SSH sources retain configured Git authentication.
    report = auth.report([source], env=environment)
    overlay = auth.fallback(report, apply_fixes=context.mode() != "report-only")
    context.describe(report)
    for item in report.results:
        update_events.auth_result(
            item.summary(), applied=False,
            available=overlay is not None and item.source.id in overlay.applies_to,
        )
    if overlay is not None and overlay.denies:
        raise RecoveryRefused("Selected Manager source authentication is blocked")

    def used() -> None:
        for item in report.results:
            if overlay is not None and item.source.id in overlay.applies_to:
                update_events.auth_used(item.summary())

    return (overlay.environment(environment) if overlay is not None else environment), used


def _run(
    arguments: list[str], *, cwd: Path, env: dict[str, str], timeout: float = 120,
    durable_effects: bool = False,
):
    from .installer_run import run_installer

    if os.name == "nt":
        lines: list[str] = []
        result = run_installer(
            arguments, cwd=cwd, env=env, deadline_s=timeout,
            log=lines.append, require_tree_exit=not durable_effects,
        )
        text = "\n".join(lines)
        if result.rc is None:
            if not durable_effects and result.status.startswith("timed out ("):
                raise RecoveryTimeout(f"Manager recovery child failed: {result.status}")
            raise RecoveryRefused(f"Manager recovery child failed: {result.status}")
        return subprocess.CompletedProcess(arguments, result.rc, text, "")
    return subprocess.run(
        arguments, cwd=cwd, env=env, capture_output=True, text=True,
        timeout=timeout, **no_window_kwargs(),
    )


def _health_probe(arguments: list[str], root: Path, env: dict[str, str]):
    try:
        return _run(arguments, cwd=root, env=env, timeout=30)
    except (OSError, subprocess.TimeoutExpired, RecoveryTimeout) as error:
        if os.name == "nt" and not isinstance(error, (SyncProcessLaunchError, RecoveryTimeout)):
            raise
        output.warn(f"Adopted Manager health probe could not run: {error}")
        return None


def _healthy(adoption: Adoption, env: dict[str, str]) -> bool:
    slot = _slot(adoption.root, _read(adoption.root / "current-version"))
    if slot is None:
        return False
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    if python.is_file() and bootstrap.is_file() and (slot / ".install-complete.json").is_file():
        arguments = [
            str(python), "-I", "-S", "-B", "-X", "utf8", str(bootstrap),
            "-m", "worktree_manager", "--version",
        ]
    else:
        python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file():
            return False
        probe = (
            "import importlib.metadata as m,pathlib as p,sys,worktree_manager as w;"
            "s=p.Path(sys.argv[1]).resolve();"
            "assert p.Path(sys.prefix).resolve()==s/'.venv';"
            "assert p.Path(w.__file__).resolve().is_relative_to(s);"
            "assert p.Path(m.distribution('copilot-extensions-worktree-manager').locate_file(''))"
            ".resolve().is_relative_to(s/'.venv');"
            "print('worktree-manager '+w.__version__)"
        )
        arguments = [str(python), "-I", "-B", "-c", probe, str(slot)]
    health_env = {**env, "UV_OFFLINE": "1", "UV_NO_SYNC": "1"}
    result = _health_probe(arguments, adoption.root, health_env)
    version = providers._parse_comparable_version(result.stdout) if result is not None and result.returncode == 0 else None
    if version is None or version < adoption.minimum_version:
        return False
    registered = _health_probe(
        [*adoption.command, "--version"], adoption.root, health_env,
    )
    return registered is not None and registered.returncode == 0 and providers._parse_comparable_version(registered.stdout) == version


def recover() -> dict[str, str]:
    adoption = adopted_manager()
    if adoption is None:
        return {"status": "not-adopted"}
    env = dict(os.environ)
    for name in (
        "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__",
        "UV_PROJECT_ENVIRONMENT", "UV_INTERNAL__PYTHONHOME", "UV_PYTHON",
    ):
        env.pop(name, None)
    env["WORKTREE_MANAGER_ROOT"] = str(adoption.root)
    if _healthy(adoption, env):
        adoption.verify()
        return {"status": "current"}
    adoption = adoption.with_source()
    runtime = _runtime_api()
    with ExitStack() as custody:
        for name in ("current-version", "last-known-good"):
            path = adoption.root / name
            version = path.read_text(encoding="utf-8").strip() if path.exists() else ""
            if version:
                runtime._validate_dev_previous_version(version)
                if runtime.version_dir(adoption.root, version).is_dir():
                    custody.enter_context(runtime.retention_lease(adoption.root, version))
        adoption.verify()
        return _repair(adoption, env)


def _repair(adoption: Adoption, env: dict[str, str]) -> dict[str, str]:
    git = shutil.which("git")
    uv = shutil.which("uv")
    if not git or not uv:
        raise RecoveryRefused("Adopted Manager recovery requires the existing Git/uv tools")
    inputs = adoption.root / ".installer-inputs"
    inputs.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="manager-update-", dir=inputs) as temporary:
        snapshot = Path(temporary)
        env, auth_used = _source_environment(adoption, snapshot, env)
        from . import update_auth

        git_policy = [
            ("core.hooksPath", str(snapshot / "disabled-hooks")),
            ("core.fsmonitor", "false"),
        ]
        if os.name == "nt":
            git_policy.append(("core.longpaths", "true"))
        env.update(update_auth.command_scope(env, git_policy))
        for operation, arguments in (
            ("init", [git, "init", "--quiet", "--template=", str(snapshot)]),
            ("fetch", [git, "-C", str(snapshot), "fetch", "--quiet", "--depth=1", "--", adoption.repo, adoption.ref]),
            ("checkout", [git, "-C", str(snapshot), "checkout", "--quiet", "--detach", "FETCH_HEAD"]),
        ):
            from .installer_run import InstallerCleanupError

            try:
                acquired = _run(arguments, cwd=snapshot, env=env)
            except (subprocess.TimeoutExpired, RecoveryRefused, InstallerCleanupError):
                if operation == "fetch":
                    auth_used()
                raise
            if operation == "fetch":
                auth_used()
            if acquired.returncode:
                detail = update_events.bounded((acquired.stdout or "") + (acquired.stderr or ""), 1200)
                raise RecoveryRefused(
                    f"Selected Manager source {operation} failed (exit {acquired.returncode}): {detail}",
                )
        commit = _run([git, "-C", str(snapshot), "rev-parse", "--verify", "HEAD"], cwd=snapshot, env=env)
        if commit.returncode or not re.fullmatch(r"[0-9a-f]{40,64}", commit.stdout.strip()):
            raise RecoveryRefused("Selected Manager source did not resolve to one immutable commit")
        payload = snapshot / "worktree-manager"
        entry = payload / "scripts" / "runtime_install.py"
        if not entry.is_file():
            raise RecoveryRefused("Selected Manager source/pin does not provide canonical adopted recovery")
        _ordinary(entry)
        metadata = _read(payload / "pyproject.toml")
        project = tomllib.loads(metadata.decode("utf-8")).get("project", {}) if metadata else {}
        name, version = project.get("name"), project.get("version")
        comparable = providers._parse_comparable_version(version) if isinstance(version, str) else None
        if (
            not isinstance(name, str)
            or re.sub(r"[-_.]+", "-", name).lower() != "copilot-extensions-worktree-manager"
            or comparable is None or comparable < adoption.minimum_version
        ):
            raise RecoveryRefused("Selected Manager source does not satisfy its registered package/version contract")
        python = getattr(sys, "_base_executable", sys.executable)
        protocol = _run([
            str(python), "-I", "-S", "-B", "-X", "utf8", str(entry), "--describe-recovery",
        ], cwd=snapshot, env=env, timeout=30)
        try:
            descriptor = json.loads(protocol.stdout)
        except ValueError as error:
            raise RecoveryRefused("Selected Manager source has no canonical recovery protocol") from error
        if protocol.returncode or not isinstance(descriptor, dict) or type(descriptor.get("version")) is not int or descriptor != {
            "schema": "copilot-extensions.manager-recovery-entry", "version": 1,
            "provider": "worktree-manager",
        }:
            raise RecoveryRefused("Selected Manager source has an incompatible recovery protocol")
        adoption.verify()
        evidence = snapshot / "adoption.json"
        evidence.write_text(json.dumps({
            "schema": "copilot-extensions.manager-adoption", "version": 1,
            "root": str(adoption.root), "evidence": [[str(path), digest] for path, digest in adoption.evidence],
            "registry": str(adoption.registry), "registrations": list(adoption.registrations),
        }), encoding="utf-8")
        result = _run([
            str(python), "-I", "-S", "-B", "-X", "utf8", str(entry),
            "--root", str(adoption.root), "--payload", str(payload),
            "--recover-adopted", str(evidence), "--", "--version",
        ], cwd=adoption.root, env=env, timeout=180, durable_effects=True)
        if result.returncode:
            detail = update_events.bounded((result.stdout or "") + (result.stderr or ""), 1600, tail=True)
            raise RecoveryRefused(f"Canonical Manager recovery failed (exit {result.returncode}): {detail}")
        if not _healthy(adoption, env):
            raise RecoveryRefused("Recovered Manager failed installed-runtime verification")
        output.ok("Adopted Worktree Manager recovered through its selected canonical source")
        return {"status": "recovered", "source_commit": commit.stdout.strip()}
