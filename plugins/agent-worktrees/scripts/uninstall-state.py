"""Worktrees policy adapter for the canonical finite runtime-removal owner."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

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


def project_python(root: Path) -> Path:
    """Resolve completed installed code, never the incoming payload's version."""
    external_python(root)
    owner = _finite_owner()
    payload = Path(__file__).absolute().parents[1]
    owner._root(payload, root)
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


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--probe-python":
        try:
            external_python(Path(sys.argv[2]))
        except (OSError, RuntimeError, ValueError):
            return 1
        print(json.dumps(sys.executable))
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "--project-python":
        try:
            selected = project_python(Path(sys.argv[2]))
        except (OSError, RuntimeError, ValueError) as error:
            print(f"Project-command cleanup refused: {error}", file=sys.stderr)
            return 1
        print(json.dumps(str(selected)))
        return 0
    try:
        root = Path(sys.argv[sys.argv.index("--root") + 1])
        check_under_custody(root=root, payload_root=Path(__file__).absolute().parents[1],
                           plugin_id="agent-worktrees")
    except (IndexError, OSError, RuntimeError, ValueError) as error:
        print(f"Runtime uninstall refused: {error}", file=sys.stderr)
        return 1
    owner = _finite_owner()
    if "--purge" in sys.argv:
        # An unknown-only root is absent to the finite planner, not to a purge.
        try:
            payload = Path(sys.argv[sys.argv.index("--payload-root") + 1])
            host = owner._host(sys.argv[sys.argv.index("--shell") + 1])
            profile = Path(sys.argv[sys.argv.index("--profile") + 1])
            owner._root(payload, root)
            selected = owner.profile_for(payload, "agent-worktrees", profile)
            if root.exists():
                owner.refuse_consumers(payload, "agent-worktrees", root, host, selected)
        except (IndexError, OSError, RuntimeError, ValueError) as error:
            print(f"Runtime uninstall refused: {error}", file=sys.stderr)
            return 1
        sys.argv.remove("--purge")
    return owner.main()


if __name__ == "__main__":
    raise SystemExit(main())
