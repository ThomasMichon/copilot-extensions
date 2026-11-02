"""Worktrees policy adapter for the canonical finite runtime-removal owner."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import subprocess

sys.dont_write_bytecode = True


def external_python(root: Path) -> None:
    if sys.version_info < (3, 10):
        raise RuntimeError("Uninstall requires an existing external Python 3.10+")
    roots = (Path(os.path.abspath(root)), root.resolve())
    for candidate in (
        sys.executable, getattr(sys, "_base_executable", sys.executable),
        sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix,
    ):
        path = Path(os.path.abspath(candidate))
        if any(value == base or value.is_relative_to(base)
               for value in (path, path.resolve()) for base in roots):
            raise RuntimeError("Uninstall interpreter/base closure depends on the runtime root")


def check_under_custody(*, root: Path, payload_root: Path, plugin_id: str) -> None:
    external_python(root)
    if plugin_id != "agent-worktrees":
        raise RuntimeError("Worktrees uninstall policy cannot authorize another product")
    legacy = root / "lib"
    if os.path.lexists(legacy):
        value = legacy.lstat()
        if (legacy.is_symlink() or getattr(value, "st_file_attributes", 0) & 0x400
                or not legacy.is_dir() or any(legacy.iterdir())):
            raise RuntimeError(
                "Occupied legacy lib has no finite ownership mapping; explicit migration is required"
            )


def project_cleanup_candidates(payload: Path, project: str) -> bool:
    """Inspect canonical names and receipts without importing the installed CLI."""
    if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", project) is None:
        raise RuntimeError("Project-command cleanup requires a single project name")
    owner = _finite_owner()
    paths = owner._load(payload / "src/agent_worktrees/registry_paths.py")
    home = Path(os.path.abspath(paths._legacy_root())).parent
    directory = home / ".local/bin"
    templates = owner._load(payload / "src/agent_worktrees/project_command_templates.py")
    names = templates.render(project, payload, windows=os.name == "nt", user_bin=directory)
    receipt = home / ".agent-worktrees/binstub-receipts" / (
        (project.casefold() if os.name == "nt" else project) + ".json"
    )
    return any(os.path.lexists(directory / name) for name in names) or os.path.lexists(receipt)


def project_python(root: Path, project: str | None = None) -> Path | None:
    """Resolve completed installed code, never the incoming payload's version."""
    external_python(root)
    owner = _finite_owner()
    payload = Path(__file__).absolute().parents[1]
    owner._root(payload, root)
    if project is not None and not project_cleanup_candidates(payload, project):
        return None
    for marker in ("current-version", "last-known-good"):
        owner._plain(root / marker, file=True)
    runtime = owner._load(payload / "scripts/versioned_runtime.py")
    selected = runtime.resolve_python(root)
    if selected is None:
        raise RuntimeError(
            "No unambiguous completed installed runtime for project-command cleanup; "
            "refusing to guess or provision an interpreter"
        )
    relative = selected.relative_to(root / "versions")
    if len(relative.parts) != 3 or not owner._generation(relative.parts[0]):
        raise RuntimeError("Project-command interpreter is outside an ordinary installed generation")
    owner._plain(selected.parent)
    if not os.access(selected, os.X_OK):
        raise RuntimeError("Selected project-command interpreter is not executable")
    return selected


def _finite_owner():
    helper = Path(__file__).absolute().with_name("installer_engine_uninstall.py")
    spec = importlib.util.spec_from_file_location("_worktrees_finite_uninstall", helper)
    if spec is None or spec.loader is None:
        raise RuntimeError("Canonical finite uninstall owner is unavailable")
    owner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = owner
    spec.loader.exec_module(owner)
    return owner


def cleanup(*, payload_root: Path, plugin_id: str, root: Path, shell: str,
            profile: Path | None = None, check_consumers: bool = False,
            remove_runtime: bool = False, purge: bool = False,
            project_name: str | None = None, controller_custody=None) -> dict:
    check_under_custody(root=root, payload_root=payload_root, plugin_id=plugin_id)
    if project_name:
        project_python(root, project_name)
    owner = _finite_owner()
    controls = {} if controller_custody is None else {"controller_custody": controller_custody}
    if purge:
        # Unknown-only roots are absent to finite removal, not to a purge.
        owner._root(payload_root, root)
        selected = owner.profile_for(payload_root, plugin_id, profile)
        if root.exists():
            if controller_custody is None:
                owner.refuse_consumers(payload_root, plugin_id, root, owner._host(shell), selected)
            else:
                runtime = owner._load(payload_root / "scripts" / "versioned_runtime.py")
                binding = owner._load(payload_root / "scripts" / "command_binding.py")
                ownership = (binding, binding._load_primitive(payload_root))
                targets = owner._targets(payload_root, plugin_id, root, selected, runtime)
                with owner._custody(payload_root, plugin_id, root, ownership, targets, runtime) as lease:
                    owner.refuse_consumers(
                        payload_root, plugin_id, root, owner._host(shell), selected,
                        **controls, lifecycle_lease=lease,
                    )
    result = owner.cleanup(
        payload_root, plugin_id, root, shell, profile=profile,
        remove_runtime=remove_runtime, check_consumers=check_consumers, **controls,
    )
    if result["status"] == "absent":
        selected = owner.profile_for(payload_root, plugin_id, profile)
        _, commands = owner._local_bindings(payload_root, root, selected)
        if commands:
            raise RuntimeError(
                "Owned global/update command footprint remains without runtime ownership "
                "evidence; command files preserved, nothing removed"
            )
    return result


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--probe-python":
        try:
            external_python(Path(sys.argv[2]))
        except (OSError, RuntimeError, ValueError):
            return 1
        print(json.dumps(sys.executable))
        return 0
    if len(sys.argv) in (3, 4) and sys.argv[1] == "--project-python":
        try:
            selected = project_python(Path(sys.argv[2]), sys.argv[3] if len(sys.argv) == 4 else None)
        except (OSError, RuntimeError, ValueError) as error:
            print(f"Project-command cleanup refused: {error}", file=sys.stderr)
            return 1
        print(json.dumps(str(selected) if selected is not None else None))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-root", required=True, type=Path)
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--shell", required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--check-consumers", action="store_true")
    parser.add_argument("--purge", action="store_true")
    parser.add_argument("--project-name")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--remove-runtime", action="store_true")
    arguments = parser.parse_args()
    try:
        result = cleanup(
            payload_root=arguments.payload_root, plugin_id=arguments.plugin_id,
            root=arguments.root, shell=arguments.shell,
            profile=arguments.profile, remove_runtime=arguments.remove_runtime,
            check_consumers=arguments.check_consumers, purge=arguments.purge,
            project_name=arguments.project_name,
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Runtime uninstall refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
