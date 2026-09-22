#!/usr/bin/env python3
"""Vendor the canonical installation-context bootstrap into its Phase 3 adopters.

The exemplar files remain non-operative until each plugin explicitly changes
its installer and payload-invocation contract. Agent Worktrees consumes the
same validator for read-only reconciliation and update guards. This tool keeps
every standalone payload byte-identical.

Dispatch, CodeSpaces, Containers, and Logger peer launchers consume the Python primitive packaged
inside their wheels. They must bootstrap validation from their own installed bytes,
not import a validator from an as-yet-unvalidated receipt's payload pointer.

Worktree Manager -- a standalone, non-plugin payload outside ``plugins/`` --
vendors the same primitive for its own read-only ``agent_plugin_runtime.py``
resolver (worktree-manager-control-plane effort): it never provisions or
activates anything, only reads the shared installation-mode policy so its
legacy-vs-namespaced decision can never disagree with what an agent-* plugin
itself would compute for the same file.
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CANONICAL_DIR = REPO / "libs" / "installation-context"
FILES = (
    "_installation_context_base.py",
    "_installation_context_files.py",
    "_installation_context_source.py",
    "_installation_context_receipts.py",
    "_installation_context_snapshot.py",
    "_installation_context_runtime_slot_ownership.py",
    "_installation_context_runtime_slot_completion.py",
    "_installation_context_resolution.py",
    "_installation_context_activation.py",
    "_installation_context_legacy_attribution.py",
    "_installation_context_legacy_transition.py",
    "_installation_context_legacy_retirement.py",
    "_installation_context_maintenance.py",
    "_installation_context_mode_cli.py",
    "installation_context.py",
    "installation-context.sh",
    "installation-context.ps1",
    "json-query.awk",
)
LEGACY_ENTRYPOINT_FILES = (
    "legacy-entrypoint-probe.sh",
    "legacy-entrypoint-probe.ps1",
)
ADOPTERS = (
    "agent-bridge",
    "agent-codespaces",
    "agent-containers",
    "agent-dispatch",
    "agent-machines",
    "agent-index",
    "agent-logger",
    "agent-mcp",
    "agent-ssh",
    "agent-vault",
    "agent-worktrees",
)
LEGACY_ENTRYPOINT_ADOPTERS = ("agent-machines", "agent-index")

#: Standalone, non-plugin payloads (outside ``plugins/``) that vendor the
#: Python primitive the same way agent-dispatch/codespaces/containers do:
#: read-only discovery of an installed agent-* runtime, never provisioning.
#: Worktree Manager's ``agent_plugin_runtime.py`` is the consumer (Phase 3b/4
#: follow-on, worktree-manager-control-plane effort). Stored as a REPO-relative
#: path (not a resolved ``Path``) so a test's ``module.REPO`` reassignment is
#: honored by ``vendor_pairs()`` at call time instead of a stale absolute path
#: baked in at import time.
STANDALONE_PYTHON_ADOPTERS: tuple[tuple[str, str], ...] = (
    ("worktree-manager/src/worktree_manager", "worktree_manager"),
)


def vendor_pairs() -> list[tuple[Path, Path]]:
    return [
        (
            CANONICAL_DIR / name,
            REPO / "plugins" / plugin / "scripts" / "installation-context" / name,
        )
        for plugin in ADOPTERS
        if plugin != "agent-dispatch"
        for name in FILES
    ] + [
        (
            CANONICAL_DIR / name,
            REPO / "plugins" / plugin / "src" / plugin.replace("-", "_")
            / ("_installation_context.py" if name == "installation_context.py" else name),
        )
        for plugin in ADOPTERS
        if plugin in {
            "agent-bridge", "agent-dispatch", "agent-codespaces", "agent-containers",
            "agent-logger", "agent-index", "agent-machines",
        }
        for name in FILES
        if name.endswith(".py")
    ] + [
        (
            CANONICAL_DIR / name,
            REPO / "plugins" / plugin / "scripts" / "installation-context" / name,
        )
        for plugin in LEGACY_ENTRYPOINT_ADOPTERS
        for name in LEGACY_ENTRYPOINT_FILES
    ] + [
        (
            CANONICAL_DIR / name,
            REPO / relative_dir / ("_installation_context.py" if name == "installation_context.py" else name),
        )
        for relative_dir, _label in STANDALONE_PYTHON_ADOPTERS
        for name in FILES
        if name.endswith(".py")
    ]


def unregistered_adopters() -> list[str]:
    """Plugins that vendor the foundation but are absent from ``ADOPTERS``.

    A plugin that ships ``scripts/installation-context/`` executes it, so a copy
    outside the adopter list never receives foundation updates while this tool
    still reports everything in sync. That drift is invisible until the stale
    copy fails, so name it as a problem instead.
    """
    plugins_root = REPO / "plugins"
    if not plugins_root.is_dir():
        return []
    return sorted(
        candidate.name
        for candidate in plugins_root.iterdir()
        if candidate.name not in ADOPTERS
        and (candidate / "scripts" / "installation-context").is_dir()
    )


def verify() -> list[str]:
    problems: list[str] = []
    for plugin in unregistered_adopters():
        problems.append(
            f"plugins/{plugin} vendors scripts/installation-context/ but is not "
            "listed in ADOPTERS, so its copy never receives updates"
        )
    for source, destination in vendor_pairs():
        relative = destination.relative_to(REPO).as_posix()
        if not source.is_file():
            problems.append(f"canonical source missing: {source.relative_to(REPO)}")
        elif not destination.is_file():
            problems.append(f"{relative} is missing")
        elif destination.read_bytes() != source.read_bytes():
            problems.append(f"{relative} differs from {source.relative_to(REPO)}")
        elif os.name != "nt" and stat.S_IMODE(destination.stat().st_mode) != stat.S_IMODE(
            source.stat().st_mode
        ):
            problems.append(f"{relative} mode differs from {source.relative_to(REPO)}")
    return problems


def sync() -> list[str]:
    written: list[str] = []
    for source, destination in vendor_pairs():
        if not source.is_file():
            raise FileNotFoundError(f"canonical source missing: {source}")
        content_matches = (
            destination.is_file() and destination.read_bytes() == source.read_bytes()
        )
        mode_matches = (
            destination.is_file()
            and (
                os.name == "nt"
                or stat.S_IMODE(destination.stat().st_mode)
                == stat.S_IMODE(source.stat().st_mode)
            )
        )
        if content_matches and mode_matches:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not content_matches:
            shutil.copyfile(source, destination)
        if os.name != "nt":
            shutil.copymode(source, destination)
        written.append(destination.relative_to(REPO).as_posix())
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify vendored copies without changing files",
    )
    arguments = parser.parse_args()
    if arguments.check:
        problems = verify()
        if problems:
            print("installation-context vendoring is out of sync:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            print("\nRun: python tools/sync-installation-context.py", file=sys.stderr)
            return 1
        print(
            "installation-context files in sync across "
            f"{len(ADOPTERS)} plugin adopters and "
            f"{len(STANDALONE_PYTHON_ADOPTERS)} standalone-payload adopter(s)."
        )
        return 0

    written = sync()
    if written:
        print(f"Synced installation-context files ({len(written)} file(s)):")
        for path in written:
            print(f"  + {path}")
    else:
        print("Installation-context vendoring already in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
