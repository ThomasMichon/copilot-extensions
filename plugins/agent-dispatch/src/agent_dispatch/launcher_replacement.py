"""Installer-owned, central-only retirement for a conflicting stable launcher."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import ntpath
from pathlib import Path
import time

from agent_procutil.daemon_instances import inspect_instances
from agent_procutil.process_retirement import PinnedProcess, RetirementRefused

from .supervisor_processes import WindowsProcess, _agent_dispatch_argv, _tokens, iter_windows_processes
from .windows_launcher_holders import launcher_holders

_ROLES = {
    "serve-service.ps1": ("agent-dispatch-coordinator", "serve"),
    "supervise-service.ps1": ("agent-dispatch-supervisor", "supervise"),
}


def _name(value: str | Path) -> str:
    return ntpath.normcase(ntpath.abspath(str(value)))


def _within(value: str, root: Path) -> bool:
    if not value or not ntpath.isabs(value):
        return False
    try:
        return ntpath.commonpath([_name(value), _name(root)]) == _name(root)
    except ValueError:
        return False


def _is_wrapper(process: WindowsProcess, launcher: Path) -> bool:
    tokens = _tokens(process.command_line)
    executable = ntpath.basename(process.executable).lower()
    declared = tokens[0] if tokens else ""
    matching = (
        _name(declared) == _name(process.executable) if ntpath.isabs(declared)
        else declared.lower() == executable
    )
    if (
        not tokens or not matching
        or executable not in {"powershell.exe", "pwsh.exe"}
    ):
        return False
    index = 1
    while index < len(tokens):
        option = tokens[index].lower()
        if option in {"-noprofile", "-noninteractive", "-nologo"}:
            index += 1
        elif option in {"-executionpolicy", "-windowstyle"} and index + 1 < len(tokens):
            index += 2
        elif option == "-file" and index + 1 < len(tokens):
            return _name(tokens[index + 1]) == _name(launcher)
        else:
            return False
    return False


def _central(
    process: WindowsProcess, instances: list[dict], role: str, command: str, slot: Path,
) -> bool:
    argv = _agent_dispatch_argv(process.command_line)
    if not argv or argv[0] != command:
        return False
    if command == "supervise" and len(argv) > 1 and argv[1] != "serve" and not argv[1].startswith("-"):
        return False
    return any(
        row.get("state") == "running" and row.get("service") == role
        and row.get("pid") == str(process.pid)
        and row.get("birth") == "windows:" + process.start_time
        and _within(row.get("source", ""), slot)
        and _within(row.get("runtime_root", ""), slot)
        for row in instances
    )


def _pair(
    holder: WindowsProcess, processes: list[WindowsProcess], instances: list[dict],
    launcher: Path, slot: Path,
) -> tuple[WindowsProcess, ...]:
    role, command = _ROLES[launcher.name]
    if not _is_wrapper(holder, launcher):
        return ()
    by_pid = {process.pid: process for process in processes}

    def owned_ancestry(process: WindowsProcess) -> bool:
        parent = by_pid.get(process.parent_pid)
        if parent == holder:
            return True
        # Windows venv's console redirector may wait between the PowerShell
        # wrapper and the admitted base Python body. It is not a new kill target.
        return bool(
            parent and parent.parent_pid == holder.pid
            and _name(parent.executable) == _name(slot / "Scripts" / "python.exe")
            and _agent_dispatch_argv(parent.command_line) == _agent_dispatch_argv(process.command_line)
            and parent.start_time.isdigit() and process.start_time.isdigit()
            and int(holder.start_time) <= int(parent.start_time) <= int(process.start_time)
        )

    children = [
        process for process in processes
        if process.start_time.isdigit() and holder.start_time.isdigit() and owned_ancestry(process)
        and int(process.start_time) >= int(holder.start_time)
        and _central(process, instances, role, command, slot)
    ]
    # A wrapper with ambiguous children is not a retirement authority.
    return (children[0], holder) if len(children) == 1 else ()


def _selection(root: Path, incoming: str, outgoing: str) -> None:
    for value in (incoming, outgoing):
        if not value or value in {".", ".."} or any(c in value for c in "/\\:"):
            raise ValueError("launcher retirement requires exact version-slot names")
    if incoming == outgoing or (root / "current-version").read_text(encoding="utf-8").strip() != incoming:
        raise RetirementRefused("launcher retirement selection changed")


def retire_launcher_conflict(
    root: Path, launcher: Path, incoming: str, outgoing: str, *, max_seconds: float = 30,
) -> dict:
    """Called under the installer's existing cross-version activation mutex.

    No subtree, schedule, registry artifact or application state is removed.
    Each effect rechecks the exact replaced generation and current selection.
    """
    root, launcher = root.absolute(), launcher.absolute()
    if launcher.parent != root or launcher.name not in _ROLES:
        raise ValueError("not a generated Dispatch service launcher")
    with launcher.open(encoding="utf-8-sig") as stream:
        header = stream.read(1024)
    marker = (
        "# agent-dispatch coordinator launcher (generated by install.ps1)"
        if launcher.name == "serve-service.ps1" else
        "# agent-dispatch embody supervisor launcher (generated by install.ps1;"
    )
    if not header.splitlines() or not header.splitlines()[0].startswith(marker):
        raise RetirementRefused("custom launcher is not a retirement target")
    _selection(root, incoming, outgoing)
    slot = root / "versions" / outgoing
    if slot.resolve() != slot:
        raise RetirementRefused("outgoing slot is redirected")
    deadline = time.monotonic() + max_seconds

    def remaining() -> float:
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise RetirementRefused("launcher retirement deadline elapsed")
        return budget

    def inventory() -> list[WindowsProcess]:
        return iter_windows_processes(timeout=min(15, remaining()))

    holders = launcher_holders(launcher)
    if len(holders) > 16:
        raise RetirementRefused("too many conflicting launcher holders")
    processes = inventory()
    instances = inspect_instances()
    selected = {process.pid: process for process in processes}
    retired, skipped = [], []
    for pid, birth in holders.items():
        holder = selected.get(pid)
        pair = _pair(holder, processes, instances, launcher, slot) if holder else ()
        if not pair or birth != "windows:" + holder.start_time:
            skipped.append(pid)
            continue
        try:
            with ExitStack() as stack:
                pins = [
                    stack.enter_context(PinnedProcess(process.pid, "windows:" + process.start_time))
                    for process in pair
                ]
                fresh = inventory()
                fresh_instances = inspect_instances()
                fresh_holder = next((p for p in fresh if p.pid == pid), None)
                if (
                    fresh_holder != holder
                    or _pair(fresh_holder, fresh, fresh_instances, launcher, slot) != pair
                    or launcher_holders(launcher).get(pid) != birth
                ):
                    raise RetirementRefused("launcher holder ownership changed")
                # The admitted central proves the wrapper's generation; it is
                # not the file holder and is not an additional kill target.
                # Leave it, its owned Jobs and every protected child alone.
                _selection(root, incoming, outgoing)
                pins[-1].terminate(timeout=min(5, remaining()))
                retired.append(holder.pid)
        except (OSError, ValueError, RetirementRefused):
            skipped.append(pid)
    return {"retired": retired, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--incoming", required=True)
    parser.add_argument("--outgoing", required=True)
    args = parser.parse_args()
    try:
        result = retire_launcher_conflict(
            args.install_dir, args.launcher, args.incoming, args.outgoing,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"retired": [], "error": str(error)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
