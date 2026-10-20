"""Bounded callback copy inventories and actual-outcome helper settlement."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from collections.abc import Callable
from typing import Any

import versioned_runtime as runtime

HOOKS = (
    "session-conduct.ps1", "session-conduct.sh", "session-machine.ps1", "session-machine.sh",
    "bootstrap-check.ps1", "bootstrap-check.sh", "bootstrap-killswitch-guard.ps1",
    "bootstrap-killswitch-guard.sh", "project-hooks.ps1", "project-hooks.sh",
    "register-nudge.ps1", "register-nudge.sh", "register-session.ps1", "register-session.sh",
    "deregister-session.ps1", "deregister-session.sh", "anchor-hygiene-check.ps1",
    "anchor-hygiene-check.sh", "provision-check.ps1", "provision-check.sh", "statelessness_guard.py",
    "cross_repo_guard.py", "anchor_shell_parser.py", "anchor_write_guard.py", "pr_supersede_guard.py",
    "registry_root.py", "nudge_status.py", "bind_nudge.py", "hook_runtime.py",
    "hook_lifecycle_runtime.py", "native_source_context.py", "hook_client.py",
    "post-tool-hook.ps1", "terminal_probe.py", "bind-nudge.sh", "bind-nudge.ps1",
)
SETUP = (
    "default-setup.ps1", "default-setup.sh", "launch-command.ps1", "launch-command.sh",
    "reconcile-machine-settings.ps1", "reconcile-machine-settings.sh",
)
TOOLS = ("agent-worktrees", "agent-worktrees.cmd", "agent-worktrees.ps1")


def declared_copies(content: Path, root: Path, scope: str, shell: str, contextual: bool) -> list[dict]:
    """The owner's allowlist, not authority inferred from callback output."""
    publication = runtime._runtime_component("versioned_runtime_publication")
    result = []

    def add(
        source: Path, target: Path, executable: bool, noop: bool = False, *, required: bool = False,
    ) -> None:
        state = publication.state_path(source, owned=False)
        if state is None:
            if required:
                raise ValueError(f"Required callback source is missing: {source}")
            return
        result.append({"source": str(source), "target": str(target), "digest": state.digest,
                       "executable": executable, "normalized_noop": noop})

    for name in ("resolve-runtime.ps1", "resolve-runtime.sh"):
        add(content / "scripts" / name, root / "bin" / name, shell == "sh", required=True)
    if scope == "wrappers":
        for name in HOOKS:
            source = (content / "src/agent_worktrees" if name == "native_source_context.py"
                      else content / "scripts") / name
            add(source, root / "bin" / name, shell == "sh")
        for source in sorted((content / "scripts/conduct").glob("*.md")):
            add(source, root / "bin/conduct" / source.name, False)
        for name in SETUP:
            add(content / "scripts" / name, root / "scripts" / name, shell == "sh")
    if not contextual:
        home = Path(os.environ.get("USERPROFILE") or Path.home()) if os.name == "nt" else Path.home()
        for name in TOOLS:
            add(content / "bin" / name, home / ".local/bin" / name, shell == "sh",
                shell == "ps1", required=True)
    return result


def parse_copies(plan: dict) -> list:
    publication = runtime._runtime_component("versioned_runtime_publication")
    raw = Path(plan["copies"]).read_bytes()
    fields = raw.decode("utf-8").split("\0")
    if not raw.endswith(b"\0") or (len(fields) - 1) % 4:
        raise ValueError("Callback copy inventory is incomplete")
    observed = {}
    for index in range(0, len(fields) - 1, 4):
        source, target, executable, noop = fields[index:index + 4]
        if executable not in ("0", "1") or noop not in ("0", "1"):
            raise ValueError("Callback copy policy is invalid")
        source_path, target_path = Path(source), Path(target)
        if not source_path.is_absolute() or not target_path.is_absolute():
            raise ValueError("Callback copy paths are not absolute")
        if source_path != publication.absolute(source_path) or target_path != publication.absolute(target_path):
            raise ValueError("Callback copy paths are aliased")
        key = os.path.normcase(str(target_path))
        if key in observed:
            raise ValueError("Callback copy target is duplicated")
        observed[key] = (str(source_path), executable == "1", noop == "1")
    expected = {
        os.path.normcase(item["target"]):
        (item["source"], item["executable"], item["normalized_noop"]) for item in plan["bindings"]
    }
    if observed != expected:
        raise ValueError("Callback copy inventory differs from its declared scope")
    return [
        publication.CopyBinding(Path(item["source"]), Path(item["target"]), item["digest"],
                                item["executable"], item["normalized_noop"])
        for item in plan["bindings"]
    ]


class HelperPublication:
    def __init__(self, transaction: Any, directory: Path, metadata: dict[Path, tuple[int, int]]) -> None:
        self.transaction, self.directory, self.metadata = transaction, directory, metadata

    def verify(self) -> None:
        self.transaction.verify()

    def rollback(self) -> None:
        try:
            self.transaction.rollback()
        finally:
            print(f"Helper recovery evidence retained: {self.directory}", file=sys.stderr)

    def keep(self) -> None:
        """Never turn live settlement into a mutable-source cleanup failure."""
        publication = runtime._runtime_component("versioned_runtime_publication")
        try:
            for path, identity in self.metadata.items():
                state = publication.state_path(path, private=True)
                if state is None or (state.device, state.inode) != identity:
                    raise ValueError(f"Helper metadata changed before cleanup: {path}")
            self.transaction.verify()
            for path, before in self.transaction.backups.items():
                if publication.state_path(path) != before:
                    raise ValueError(f"Helper backup changed before cleanup: {path}")
            self.transaction.keep()
            if self.transaction.context.exists() or any(path.exists() for path in self.transaction.receipts):
                return
            for path, identity in self.metadata.items():
                state = publication.state_path(path, private=True)
                if state is None or (state.device, state.inode) != identity:
                    raise ValueError(f"Helper metadata changed during cleanup: {path}")
                path.unlink()
            self.directory.rmdir()
        except (OSError, ValueError) as error:
            self.transaction._release()
            print(f"Helper evidence retained after live settlement: {self.directory}: {error}", file=sys.stderr)


def publish(
    plan: dict, directory: Path, metadata: dict[Path, tuple[int, int]],
    validate_inputs: Callable[[], None],
) -> HelperPublication:
    publication = runtime._runtime_component("versioned_runtime_publication")
    copies = Path(plan["copies"])
    before = publication.state_path(copies, private=True)
    bindings = parse_copies(plan)

    def recheck() -> None:
        validate_inputs()
        if publication.state_path(copies, private=True) != before:
            raise ValueError("Callback copy inventory changed before settlement")

    transaction = publication.CopyPublication(
        Path(plan["root"]), directory, bindings, validate_inputs=recheck,
    ).publish()
    return HelperPublication(transaction, directory, metadata)
