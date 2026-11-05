"""Restore a proven unchanged Worktrees predecessor after a contained failure."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import versioned_runtime as runtime  # noqa: E402
import versioned_runtime_publication as publication  # noqa: E402
from installer_dependency_config import native_retained_path  # noqa: E402
from installer_wheel_inputs import _ordinary  # noqa: E402
from installer_wheel_snapshot import _digest, _open  # noqa: E402
from installer_wheel_snapshot import _unchanged as _held_unchanged  # noqa: E402

_MAX_FILES = 4096
_MAX_BYTES = 256 << 20
_MAX_FILE = 64 << 20


def _supports_job() -> bool:
    return os.name == "nt"


def _fresh(root: Path) -> bool:
    publication._plain_parents(root)
    if not os.path.lexists(root):
        return True
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        return False
    with os.scandir(root) as entries:
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            if (
                entry.name not in {"bin", "scripts"}
                or not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                return False
            with os.scandir(entry.path) as contents:
                if next(contents, None) is not None:
                    return False
    return True


def _state(path: Path, deadline: float, custody: ExitStack | None = None):
    if not os.path.lexists(path):
        return None
    before = _ordinary(path)
    if before.st_size > _MAX_FILE:
        raise ValueError("Predecessor helper exceeds its byte budget")
    with ExitStack() as temporary:
        stream = (custody or temporary).enter_context(_open(path))
        opened = _held_unchanged(path, before, stream)
        if not (
            publication._windows_owned(stream.fileno())
            if os.name == "nt"
            else opened.st_uid == os.getuid()
        ):
            raise ValueError("Predecessor helper is not owned by this principal")
        digest, size = _digest(stream, _MAX_FILE, deadline)
        _held_unchanged(path, before, stream, opened=opened)
    return size, stat.S_IMODE(before.st_mode), digest


def _tree(root: Path, deadline: float, custody: ExitStack | None = None):
    native = Path(native_retained_path(root))
    if not os.path.lexists(native):
        return None
    records, count, size = {}, 0, 0
    publication._plain_parents(native)
    pending = [native]
    while pending:
        if time.monotonic() >= deadline:
            raise TimeoutError("Predecessor inspection timed out")
        directory = pending.pop()
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Predecessor helper directory is redirected")
        records[directory.relative_to(native).as_posix()] = (
            "directory",
            stat.S_IMODE(info.st_mode),
        )
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > _MAX_FILES or time.monotonic() >= deadline:
                    raise ValueError("Predecessor helper inventory exceeds its budget")
                item = Path(entry.path)
                info = entry.stat(follow_symlinks=False)
                if getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("Predecessor helper is redirected")
                if stat.S_ISDIR(info.st_mode):
                    if entry.name != ".publication-leases":
                        pending.append(item)
                elif stat.S_ISREG(info.st_mode):
                    value = _state(item, deadline, custody)
                    if value is None or value[0] != info.st_size:
                        raise ValueError("Predecessor helper changed during inspection")
                    size += value[0]
                    if size > _MAX_BYTES:
                        raise ValueError("Predecessor helper inventory exceeds its byte budget")
                    records[item.relative_to(native).as_posix()] = value
                else:
                    raise ValueError("Predecessor helper is not an ordinary file")
    return records


def _surfaces(root: Path, home: Path, deadline: float, custody: ExitStack | None = None):
    return tuple(
        _tree(path, deadline, custody)
        for path in (root / "bin", root / "scripts", home / ".local" / "bin")
    )


def _selectors(root: Path, deadline: float, custody: ExitStack | None = None):
    if os.path.lexists(root / "current"):
        raise ValueError("Legacy selector link prevents predecessor recovery")
    return tuple(
        _state(root / name, deadline, custody) for name in ("current-version", "last-known-good")
    )


def _monitor_clear(root: Path) -> bool:
    if os.path.lexists(root / "status-monitor.lock"):
        return False
    routing = root / "status-monitor-routing"
    if not os.path.lexists(routing):
        return True
    info = routing.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        return False
    with os.scandir(routing) as entries:
        return all(
            entry.name == "cutover.lock"
            and entry.is_file(follow_symlinks=False)
            and not getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400
            for entry in entries
        )


def _health(python: Path, deadline: float) -> bool:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    script = (
        "from agent_worktrees.runtime_candidate import "
        "verify_installed_origins,verify_runtime_candidate;"
        "verify_installed_origins();verify_runtime_candidate()"
    )
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", script],
        executable=native_retained_path(python) if os.name == "nt" else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=environment,
        timeout=min(remaining, 30),
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return result.returncode == 0


def _runtime_ok(root: Path, manifest: dict, selected_runtime, deadline: float) -> bool:
    version = manifest.get("runtime_slot")
    digest = manifest.get("payload_hash")
    slot = root / "versions" / version if isinstance(version, str) else None
    if (
        not isinstance(version, str)
        or not version
        or manifest.get("runtime_version", version) != version
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or not isinstance(manifest.get("venv"), str)
        or Path(manifest["venv"]) != slot
        or selected_runtime.current_version(root) != version
        or not selected_runtime.is_complete(root, version, expect_hash=digest)
    ):
        return False
    python = selected_runtime.slot_python(root, version)
    return (
        python is not None
        and selected_runtime.resolve_python(root) == python
        and _health(python, deadline)
    )


def _capture(binding, primitive, arguments, lease, selected_runtime, deadline):
    """Capture only an already selected, quiet predecessor while its lock is held."""
    root = Path(arguments["legacy_root"])
    environment = arguments.get("environment") or os.environ
    home = Path(
        environment.get("USERPROFILE") or Path.home()
        if os.name == "nt"
        else environment.get("HOME") or Path.home()
    )
    try:
        lease.assert_owned()
        selected = binding.resolve_command_binding(primitive, **arguments)
        lifecycle = binding._load_lifecycle()
        manifest = lifecycle.incumbent_manifest(binding._rules(), primitive, arguments)
        if (
            selected.get("status") != "selected"
            or selected.get("installation", {}).get("mode") != "legacy"
            or not _monitor_clear(root)
            or not _runtime_ok(root, manifest, selected_runtime, deadline)
            or any(
                not path.is_file()
                for path in (
                    root / "bin" / "resolve-runtime.ps1",
                    home / ".local" / "bin" / "agent-worktrees.ps1",
                    home / ".local" / "bin" / "agent-worktrees.cmd",
                )
            )
        ):
            return None
        selectors = _selectors(root, deadline)
        if selectors[0] is None:
            return None
        surfaces = _surfaces(root, home, deadline)
        lease.assert_owned()
        if (
            lifecycle.incumbent_manifest(binding._rules(), primitive, arguments) != manifest
            or _selectors(root, deadline) != selectors
            or not _monitor_clear(root)
        ):
            return None
        return {
            "manifest": json.loads(json.dumps(manifest)),
            "home": home,
            "selectors": selectors,
            "surfaces": surfaces,
        }
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return None


def _unchanged(proof, root, selected_runtime, deadline, *, health: bool, custody=None):
    return (
        _monitor_clear(root)
        and _selectors(root, deadline, custody) == proof["selectors"]
        and _surfaces(root, proof["home"], deadline, custody) == proof["surfaces"]
        and (not health or _runtime_ok(root, proof["manifest"], selected_runtime, deadline))
    )


def _restore(proof, binding, primitive, arguments, lease, selected_runtime, *, deadline=None):
    """Rebind only after the caller has proved its child tree has exited."""
    root = Path(arguments["legacy_root"])
    lifecycle = binding._load_lifecycle()
    previous = proof["manifest"]
    pending = {
        **previous,
        "commandLifecycle": {"version": 1, "state": "pending", "owner": lease.owner},
    }
    deadline = (
        min(deadline, time.monotonic() + 30) if deadline is not None else time.monotonic() + 30
    )
    written = None
    try:
        lease.assert_owned()
        with ExitStack() as custody:
            if lifecycle.incumbent_manifest(
                binding._rules(), primitive, arguments
            ) != pending or not _unchanged(
                proof, root, selected_runtime, deadline, health=True, custody=custody
            ):
                return False
            unbound = {
                key: value
                for key, value in previous.items()
                if key not in {"commandBinding", "commandLifecycle"}
            }
            with binding.legacy_manifest_publication(primitive, unbound, **arguments) as (
                bound,
                result,
                lock,
            ):
                if (
                    result["status"] != "published"
                    or bound.get("commandBinding") != previous.get("commandBinding")
                    or lifecycle.incumbent_manifest(binding._rules(), primitive, arguments)
                    != pending
                    or not _unchanged(proof, root, selected_runtime, deadline, health=False)
                ):
                    return False
                lease.assert_owned()
                primitive._atomic_write_json(root / "deploy-manifest.json", bound, lock=lock)
                written = bound
                if (
                    binding.resolve_command_binding(primitive, **arguments).get("status")
                    != "selected"
                ):
                    raise RuntimeError("Restored command binding could not be selected")
        return True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        if written is not None:
            current = lifecycle.incumbent_manifest(binding._rules(), primitive, arguments)
            if current != written:
                raise RuntimeError(
                    "Restored command binding changed before recovery could be refused"
                ) from error
            primitive._atomic_write_json(root / "deploy-manifest.json", pending, lock=lease.lock)
        return False


def run_guarded(binding, primitive, command, *, timeout: float, **arguments) -> int:
    """Use the existing contained lifecycle; uncertain outcomes remain pending."""
    if (
        not _supports_job()
        or type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or timeout < 10
        or not command
        or not Path(command[0]).is_absolute()
    ):
        raise ValueError("Guarded recovery requires a bounded Windows lifecycle child")
    lifecycle = binding._load_lifecycle()
    deadline = time.monotonic() + timeout
    recovery_budget = min(45, max(1, timeout / 4))
    with lifecycle.legacy_lifecycle(
        binding._rules(),
        primitive,
        reserve=False,
        managed_deployment=True,
        lock_timeout=min(timeout / 4, 60),
        **arguments,
    ) as lease:
        proof = _capture(
            binding,
            primitive,
            arguments,
            lease,
            runtime,
            min(time.monotonic() + 30, deadline - recovery_budget),
        )
        if proof is None:
            if not _fresh(Path(arguments["legacy_root"])):
                raise ValueError(
                    "A quiet, healthy selected predecessor is required for guarded recovery"
                )
            available = deadline - time.monotonic()
            if available <= 0:
                raise TimeoutError("Guarded lifecycle has no remaining child budget")
            lease.reserve()
            result = lifecycle.run_lifecycle(
                binding._rules(),
                primitive,
                command,
                timeout=available,
                managed_deployment=True,
                **arguments,
            )
            if (
                result == 0
                and binding.resolve_command_binding(primitive, **arguments).get("status")
                != "selected"
            ):
                print(
                    "Runtime returned success without a selected command binding.", file=sys.stderr
                )
                return 1
            return result
        if deadline - time.monotonic() <= recovery_budget:
            raise TimeoutError("Guarded recovery has no remaining child budget")
        lease.reserve()
        version = proof["manifest"]["runtime_slot"]
        started = False
        try:
            available = deadline - time.monotonic() - recovery_budget
            with runtime.retention_lease(
                Path(arguments["legacy_root"]),
                version,
                payload_hash=proof["manifest"]["payload_hash"],
                timeout=min(30, max(0, available)),
            ):
                if not _unchanged(
                    proof,
                    Path(arguments["legacy_root"]),
                    runtime,
                    min(time.monotonic() + 30, deadline - recovery_budget),
                    health=True,
                ):
                    raise RuntimeError("The predecessor changed before its contained update")
                available = deadline - time.monotonic() - recovery_budget
                if available <= 0:
                    raise TimeoutError("Guarded recovery has no remaining child budget")
                started = True
                result = lifecycle.run_lifecycle(
                    binding._rules(),
                    primitive,
                    command,
                    timeout=available,
                    managed_deployment=True,
                    **arguments,
                )
                if result:
                    restored = _restore(
                        proof,
                        binding,
                        primitive,
                        arguments,
                        lease,
                        runtime,
                        deadline=deadline,
                    )
                    if restored:
                        message = "Previous command binding restored after contained failure."
                    else:
                        try:
                            state = binding.resolve_command_binding(primitive, **arguments)
                        except (OSError, ValueError, RuntimeError):
                            state = {}
                        if state.get("status") == "selected":
                            message = (
                                "A command binding is selected despite the failure; "
                                "reconcile the runtime before retrying. "
                                "Predecessor restoration was not proved."
                            )
                        elif state.get("reason") == "legacy-lifecycle-incomplete":
                            message = (
                                "Command lifecycle is incomplete; "
                                "predecessor restoration could not be proved."
                            )
                        else:
                            message = (
                                "Command binding could not be resolved; "
                                "predecessor restoration could not be proved."
                            )
                    print(message, file=sys.stderr, flush=True)
                elif (
                    binding.resolve_command_binding(primitive, **arguments).get("status")
                    != "selected"
                ):
                    print(
                        "Runtime returned success without a selected command binding.",
                        file=sys.stderr,
                    )
                    return 1
                return result
        except BaseException:
            if not started:
                _restore(proof, binding, primitive, arguments, lease, runtime, deadline=deadline)
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-root", type=Path, required=True)
    parser.add_argument("--legacy-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--source-descriptor", type=json.loads, required=True)
    parser.add_argument("--lifecycle-timeout", type=float, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    if arguments.payload_root.resolve() != SCRIPTS.parent:
        parser.error("The selected payload does not own this recovery lifecycle")
    command = arguments.command
    if command and command[0] == "--":
        command = command[1:]
    import command_binding as binding

    primitive = binding._load_primitive(arguments.payload_root)
    try:
        return run_guarded(
            binding,
            primitive,
            command,
            timeout=arguments.lifecycle_timeout,
            payload_root=arguments.payload_root,
            plugin_id="agent-worktrees",
            legacy_root=arguments.legacy_root,
            project_root=arguments.project_root,
            environment={**os.environ, "COPILOT_PLUGIN_ROOT": str(arguments.payload_root)},
            mode_arguments={"source_descriptor": arguments.source_descriptor},
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Guarded Worktrees lifecycle refused: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
