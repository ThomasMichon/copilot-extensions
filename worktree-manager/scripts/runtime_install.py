"""Standalone Manager adapter over the canonical Python installer engine."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TextIO

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import installer_engine as engine  # noqa: E402
from installer_engine_process import _settle  # noqa: E402
import versioned_runtime as runtime  # noqa: E402
import versioned_runtime_source as runtime_source  # noqa: E402


def base_python() -> Path:
    return Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)


def source_install_selection(
    payload: Path, version: str, selected: Path | None,
) -> tuple[str, bool]:
    """Resolve requested source identity without constructing or publishing."""
    identity = runtime_source.read_source_identity(payload, version, plugin="worktree-manager")
    if identity is None:
        return version, True
    if selected is None or not (
        selected.name == identity.runtime_version
        or selected.name.startswith(identity.runtime_version + ".repair.")
    ):
        return identity.runtime_version, False
    matches = runtime.is_complete(selected.parent.parent, selected.name)
    if matches:
        matches = runtime_source.read_source_identity(
            selected / "payload", version, plugin="worktree-manager",
        ) == identity
    return identity.runtime_version, matches


@contextmanager
def candidate(payload: Path, root: Path, *, assemble, uv: Path | None = None):
    """Retain a healthy target; never publish selectors during construction."""
    if os.name == "nt":
        uv = Path(engine._select_uv_executable(str(Path.cwd() / uv) if uv is not None else "uv"))
    else:
        uv = uv or Path(shutil.which("uv") or "")
    if not uv.is_file():
        raise RuntimeError("uv is required for Manager runtime construction")
    root.mkdir(parents=True, exist_ok=True)
    inputs = root / ".installer-inputs"
    inputs.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="manager-payload-", dir=inputs) as temporary:
        import tomllib

        project = tomllib.loads((payload / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})
        if not isinstance(project, dict):
            raise ValueError("Manager construction requires project metadata")
        version = project.get("version")
        name = project.get("name")
        if (
            not isinstance(name, str)
            or re.sub(r"[-_.]+", "-", name).lower() != "copilot-extensions-worktree-manager"
            or not isinstance(version, str) or not version
        ):
            raise ValueError("Manager construction requires its attributed project name and version")
        identity = runtime_source.read_source_identity(payload, version, plugin="worktree-manager")
        prepared = Path(temporary) / "payload"
        if identity is None:
            assemble(payload, prepared)
        else:
            assemble(payload, prepared, mark_complete=False)
            if runtime_source.read_source_identity(prepared, version, plugin="worktree-manager") != identity:
                raise RuntimeError("Manager source identity changed during assembly")
        generation = identity.runtime_version if identity is not None else version
        requested_generation = generation
        if generation == "dev":
            raise RuntimeError("Manager mutable dev installation requires an explicit owning lifecycle")
        arguments = dict(
            plugin=prepared, uv=uv, base_python=base_python(),
            health_module="worktree_manager.runtime_candidate",
            health_callable="verify_runtime_candidate",
            construction_callable="worktree_manager.runtime_candidate:prepare_runtime_candidate",
            process_retention=True, reuse_generations=identity is None,
            expected_source_identity=runtime_source.manifest_fields(identity),
        )
        current = runtime.current_version(root)
        if (
            identity is not None and current and current.startswith(generation + ".repair.")
            and runtime.is_complete(root, current)
        ):
            generation = current
        if runtime.version_dir(root, generation).exists() and not runtime.is_complete(root, generation):
            family = current and (
                current == generation or current.startswith(generation + "+repair.")
                or current.startswith(generation + ".repair.")
            )
            if family and runtime.is_complete(root, current):
                generation = current
            else:
                separator = "." if "+" in generation else "+"
                generation = f"{generation}{separator}repair.{uuid.uuid4().hex}"
            arguments["reuse_generations"] = False
        # A selected payload-only legacy slot is never rebuilt in place.
        # Published canonical health failures likewise need a distinct identity.
        with ExitStack() as custody:
            try:
                ready = custody.enter_context(engine.numbered_candidate(root, generation, **arguments))
            except engine.HealthRepairRequired:
                separator = "." if "+" in requested_generation else "+"
                repaired = f"{requested_generation}{separator}repair.{uuid.uuid4().hex}"
                arguments["reuse_generations"] = False
                ready = custody.enter_context(engine.numbered_candidate(root, repaired, **arguments))
            yield ready


def activate(root: Path, version: str, *, expected: str | None, after_publish=None) -> None:
    """Publish or recover both selectors without releasing runtime custody."""
    root = root.resolve()
    publication = runtime._runtime_component("versioned_runtime_publication")
    targets = [root / "current-version", root / "last-known-good"]
    with runtime._publication_lease(root):
        baseline = {path: publication.state_path(path) for path in targets}
        contents = {
            path: publication.CopyPublication._bytes(path, before)
            for path, before in baseline.items() if before is not None
        }
        selected = contents.get(targets[0], b"").decode("utf-8").strip() or None
        if selected not in (expected, version):
            raise RuntimeError("Manager selection changed during construction")
    # Cleanup takes retention before publication. Acquire in that same order,
    # then compare both snapshots under publication to close the unlocked gap.
    with ExitStack() as custody:
        previous = {data.decode("utf-8").strip() for data in contents.values()}
        generations = {version, *(previous - {""})}
        for generation in sorted(generations):
            # Canonical ordinary-slot validation also governs rollback references.
            runtime._validate_dev_previous_version(generation)
            if runtime.version_dir(root, generation).is_dir():
                custody.enter_context(runtime.retention_lease(root, generation))
        with runtime._publication_lease(root):
            if any(publication.state_path(path) != before for path, before in baseline.items()):
                raise RuntimeError("Manager selectors changed before publication")
            inputs = root / ".installer-inputs"
            inputs.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="manager-selection-", dir=inputs) as temporary:
                workspace = Path(temporary)
                bindings = []
                for index, path in enumerate(targets):
                    before = baseline[path]
                    backup = workspace / f"before.{index}"
                    if before is not None:
                        backup.write_bytes(contents[path])
                        backup.chmod(stat.S_IMODE(before.mode))
                    bindings.append([str(path), str(backup) if before else "",
                                     before.digest if before else ""])
                context = workspace / "context.json"
                publication.create_context(context, root, bindings)
                forward = workspace / "forward.jsonl"
                receipts = []
                digest = hashlib.sha256((version + "\n").encode()).hexdigest()
                declared = [[str(path), digest] for path in targets]

                def recover():
                    output = workspace / f"recovery.{len(receipts)}.jsonl"
                    try:
                        if not receipts:
                            if any(publication.state_path(path) != before for path, before in baseline.items()):
                                raise RuntimeError("Unreceipted selector change; retaining Manager custody")
                            return
                        _, entries = publication.read_context(context)
                        proofs = publication._proofs(receipts, root, entries, declared)
                        # The selector lease is already held. The public recovery
                        # wrapper would attempt to acquire that exclusive lease again.
                        foreign = publication._recover_uncontested_locked(
                            root, entries, proofs, output, baseline,
                        )
                        if foreign:
                            raise RuntimeError(f"Foreign Manager selectors preserved: {foreign}; {workspace}")
                    except publication.PublicationProofError as error:
                        raise RuntimeError(f"Manager recovery proof refused; retained {workspace}: {error}") from error
                    finally:
                        if output.exists():
                            receipts.append(output)

                try:
                    runtime._activate(root, version, link_free=True, ownership_receipt=forward)
                    if after_publish is not None:
                        after_publish()
                except BaseException:
                    if forward.exists():
                        receipts.append(forward)
                    _settle(recover)
                    raise


def _activate_after_bootstrap_update(root: Path, slot: Path) -> None:
    python = runtime.slot_python(root, slot.name)
    if python is None:
        raise RuntimeError("Constructed Manager has no activation interpreter")
    command = [
        str(python), "-I", "-S", "-B", "-X", "utf8",
        str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
        "-m", "worktree_manager.bootstrap_activation", "--root", str(root),
    ]
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        stdout, stderr = process.communicate()
    except BaseException:
        _settle(process.communicate)
        raise
    if stdout:
        print(stdout, end="", flush=True)
    if stderr:
        print(stderr, end="", file=sys.stderr, flush=True)
    if process.returncode:
        raise RuntimeError(f"Constructed Manager daemon activation failed ({process.returncode})")


def _windows_console_stream(stream: TextIO | None) -> bool:
    if stream is None:
        return False
    import ctypes
    from ctypes import wintypes
    import msvcrt

    mode = wintypes.DWORD()
    probe = ctypes.WinDLL("kernel32", use_last_error=True).GetConsoleMode
    probe.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    probe.restype = wintypes.BOOL
    return bool(probe(msvcrt.get_osfhandle(stream.fileno()), ctypes.byref(mode)))


def _dispatch_manager(command: list[str]) -> int:
    if os.name == "nt":
        console = any(_windows_console_stream(stream)
                      for stream in (sys.stdin, sys.stdout, sys.stderr))
        process = subprocess.Popen(
            command, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
            creationflags=0 if console else subprocess.CREATE_NO_WINDOW,
        )
        try:
            return process.wait()
        except BaseException:
            _settle(process.wait)
            raise
    os.execv(command[0], command)
    raise AssertionError("Manager bootstrap exec returned")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--payload", type=Path, default=SCRIPTS.parent)
    parser.add_argument("--recover-adopted", type=Path)
    parser.add_argument("--describe-recovery", action="store_true")
    parser.add_argument("--repair-update", action="store_true")
    parser.add_argument("--recover-for-repair", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.recover_for_repair:
        if args.recover_adopted or args.describe_recovery or args.repair_update or args.command:
            parser.error("Repair recovery cannot be combined with installation or application commands")
        if args.payload.resolve() != SCRIPTS.parent:
            parser.error("Repair recovery uses only this executing source's payload")
        from repair_recovery import recover

        return recover(SCRIPTS.parent, as_json=args.json)
    if args.json:
        parser.error("--json requires --recover-for-repair")
    if args.describe_recovery:
        print(json.dumps({
            "schema": "copilot-extensions.manager-recovery-entry", "version": 1,
            "provider": "worktree-manager",
        }))
        return 0
    if args.root is None:
        parser.error("--root is required for installation")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.repair_update and command[:1] != ["update"]:
        parser.error("Bootstrap repair is only valid for update")
    os.environ.pop("__PYVENV_LAUNCHER__", None)
    os.environ["WORKTREE_MANAGER_ROOT"] = str(args.root.resolve())
    # Bootstrap update must honor the same policy before installing Manager.
    sys.path.insert(0, str(args.payload / "src"))
    if command[:1] == ["update"]:
        from worktree_manager.runtime_source_config import require_marketplace

        try:
            require_marketplace(args.root)
        except (OSError, ValueError) as error:
            print(f"Runtime source: {error}", file=sys.stderr)
            return 1
    if args.repair_update:
        from update_entry import validate_manager_ownership

        prior_commands = validate_manager_ownership(args.root, args.payload)
    # Only bootstrap installer modules are read from source. Application execution
    # happens below under the constructed interpreter and retention bootstrap.
    from worktree_manager.self_install import self_install

    if args.recover_adopted is not None:
        evidence = json.loads(args.recover_adopted.read_text(encoding="utf-8"))
        if evidence.get("root") != str(args.root.resolve()):
            raise RuntimeError("Recovery adoption names another Manager root")
        from worktree_manager.self_install import _copy_payload

        with candidate(args.payload, args.root, assemble=_copy_payload) as ready:
            slot = ready.slot
            python = runtime.slot_python(args.root, slot.name)
            if python is None:
                raise RuntimeError("Constructed Manager has no interpreter")
            activation = [
                str(python), "-I", "-S", "-B", "-X", "utf8",
                str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
                "-m", "worktree_manager.runtime_recovery", "--root", str(args.root),
                "--evidence", str(args.recover_adopted),
            ]
            if ready.reused:
                activation.append("--reused")
            process = subprocess.Popen(
                activation, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                stdout, stderr = process.communicate()
            except BaseException:
                # Activation owns publication and cutover. Retain its candidate
                # until that owner settles, like the Worktrees runtime handoff.
                process.communicate()
                raise
            if stdout:
                print(stdout, end="", flush=True)
            if stderr:
                print(stderr, end="", file=sys.stderr, flush=True)
            if process.returncode:
                return process.returncode
    else:
        if args.repair_update:
            result = self_install(
                args.payload, root=args.root, dry_run=False,
                _before_publish=lambda: validate_manager_ownership(
                    args.root, args.payload, expected=prior_commands,
                ),
                _after_activate=lambda slot: _activate_after_bootstrap_update(args.root, slot),
                _transactional_activation=True,
            )
        else:
            result = self_install(args.payload, root=args.root, dry_run=False)
        if result.action == "error":
            print(result.reason, file=sys.stderr)
            return 1
        slot = Path(result.slot)
    if args.repair_update:
        command = ["update", "--repair-engine", str(args.payload.parent), *command[1:]]
    python = runtime.slot_python(args.root, slot.name)
    if python is None:
        raise RuntimeError("Constructed Manager has no interpreter")
    return _dispatch_manager([
        str(python), "-I", "-S", "-B", "-X", "utf8",
        str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
        "-m", "worktree_manager", *command,
    ])


if __name__ == "__main__":
    raise SystemExit(main())
