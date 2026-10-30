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
def candidate(
    payload: Path, root: Path, *, assemble, uv: Path | None = None,
    python: Path | None = None,
    wheel_inputs: engine.WheelInstallInputs | engine.WheelReuseInputs | None = None,
    local_packages: tuple[str, ...] | None = None, validate_candidate=None,
):
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
        if wheel_inputs is not None:
            if identity is None:
                raise ValueError("Manager source wheels require an attributed source identity")
            if not isinstance(wheel_inputs, (engine.WheelInstallInputs, engine.WheelReuseInputs)):
                raise ValueError("Manager source wheels require inspected retained inputs")
            wheel_inputs.verify()
        elif local_packages is not None:
            raise ValueError("Manager local-package selection requires closed source wheels")
        prepared = Path(temporary) / "payload"
        if identity is None:
            assemble(payload, prepared)
        else:
            assemble(payload, prepared, mark_complete=False)
            if runtime_source.read_source_identity(prepared, version, plugin="worktree-manager") != identity:
                raise RuntimeError("Manager source identity changed during assembly")
        generation = identity.runtime_version if identity is not None else version
        if wheel_inputs is not None:
            generation += ".profile." + wheel_inputs.receipt()["lock_id"][7:23]
        reuse_only = isinstance(wheel_inputs, engine.WheelReuseInputs)
        if reuse_only:
            if re.fullmatch(re.escape(generation) + r"(?:[.+]repair\.[0-9a-f]{32})?", wheel_inputs.generation) is None:
                raise ValueError("Manager reuse evidence differs from its source/profile generation")
            generation = wheel_inputs.generation
        requested_generation = generation
        if generation == "dev":
            raise RuntimeError("Manager mutable dev installation requires an explicit owning lifecycle")
        arguments = dict(
            plugin=prepared, uv=uv, base_python=python or base_python(),
            health_module="worktree_manager.runtime_candidate",
            health_callable="verify_runtime_candidate",
            construction_callable="worktree_manager.runtime_candidate:prepare_runtime_candidate",
            process_retention=True, reuse_generations=identity is None,
            expected_source_identity=runtime_source.manifest_fields(identity),
        )
        if wheel_inputs is not None:
            arguments.update(wheel_inputs=wheel_inputs, local_packages=local_packages)
        if validate_candidate is not None:
            arguments["validate_candidate"] = validate_candidate
        current = runtime.current_version(root)
        if (
            not reuse_only and identity is not None and current and current.startswith(generation + ".repair.")
            and runtime.is_complete(root, current)
        ):
            generation = current
        if not reuse_only and runtime.version_dir(root, generation).exists() and not runtime.is_complete(root, generation):
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
                if reuse_only:
                    raise
                separator = "." if "+" in requested_generation else "+"
                repaired = f"{requested_generation}{separator}repair.{uuid.uuid4().hex}"
                arguments["reuse_generations"] = False
                ready = custody.enter_context(engine.numbered_candidate(root, repaired, **arguments))
            yield ready


def activate(root: Path, version: str, *, expected: str | None, after_publish=None,
             before_publish=None) -> None:
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
                    if before_publish is not None:
                        before_publish()
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
        **_installation_child_options(),
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


def _dispatch_manager(command: list[str], *, handoff: dict[str, str] | None = None) -> int:
    if os.name == "nt":
        console = any(_windows_console_stream(stream)
                      for stream in (sys.stdin, sys.stdout, sys.stderr))
        process = subprocess.Popen(
            command, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
            creationflags=0 if console else subprocess.CREATE_NO_WINDOW,
            **({"env": {**os.environ, **handoff}} if handoff else {}),
        )
        try:
            return process.wait()
        except BaseException:
            _settle(process.wait)
            raise
    os.execv(command[0], command)
    raise AssertionError("Manager bootstrap exec returned")


def _installation_child_options() -> dict[str, object]:
    if os.name != "nt":
        return {}
    from update_driver import participant_environment

    return {"env": {**os.environ, **participant_environment()}}


def _execution_driver(payload: Path):
    # Resolve the declared closure in both source and materialized payloads.
    required = {"agent-update-driver", "agent-single-instance-lease", "agent-procutil", "agent-zdd"}
    packages = {
        engine._package_name(package): package for package in engine._package_roots(payload)
        if engine._package_name(package) in required
    }
    if packages.keys() != required:
        raise RuntimeError("Manager bootstrap requires its complete execution-driver source closure")
    sys.path[:0] = [str(packages[name] / "src") for name in sorted(required)]
    from update_driver import core

    return core


@contextmanager
def _installation_custody():
    from update_driver import core, borrowed_custody, participant, participant_environment
    from update_driver.participant import ENVIRONMENT_KEY

    custody = borrowed_custody(root=core.default_root())
    if custody is None:
        inherited = participant_environment()
        if inherited:
            custody = participant(root=core.default_root(), witness=inherited[ENVIRONMENT_KEY])
    if custody is None:
        yield None
    else:
        with custody, custody.execution_scope():
            yield custody


def adopt_broker(root: Path, slot: Path) -> None:
    """Adopt while the candidate is retained, before any publication callback."""
    if os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        return
    from update_driver import core, participant_environment

    with _installation_custody() as custody:
        if custody is None:
            raise RuntimeError("Manager broker adoption requires live installation custody")
        custody.assert_owned()
        python = runtime.slot_python(root, slot.name)
        if python is None:
            raise RuntimeError("Constructed Manager has no broker adoption interpreter")
        command = [
            str(python), "-I", "-m", "update_driver.windows_broker",
            "--runtime-root", str(root), "--slot", str(slot),
            "--execution-root", str(core.default_root()),
        ]
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
            env={**os.environ, **participant_environment()},
        )
        try:
            stdout, stderr = process.communicate()
        except BaseException:
            _settle(process.communicate)
            raise
        if process.returncode:
            if stderr:
                print(stderr, end="", file=sys.stderr, flush=True)
            raise RuntimeError(f"Constructed Manager broker adoption failed ({process.returncode})")
        receipt = json.loads(stdout)
        if not isinstance(receipt, dict) or set(receipt) != {"adopted"} or receipt["adopted"] is not True:
            raise RuntimeError("Constructed Manager returned no broker adoption receipt")
        custody.assert_owned()


def _source_digest(payload: Path) -> str:
    return runtime.fingerprint_source(engine._source_files(engine._package_roots(payload)))


def _bootstrap_hold_path() -> str:
    override = os.environ.get("BOOTSTRAP_KILLSWITCH_STATE_FILE", "")
    return os.path.abspath(os.path.expanduser(override)) if override else ""


def _automatic_bootstrap_inhibited(args) -> bool:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if command[:1] != ["update"]:
        return False
    from worktree_manager.update_driver_cli import parse_options, request_context

    options, _forwarded = parse_options(command[1:])
    if not options.auto:
        return False
    driver = _execution_driver(args.payload)
    return driver._automatic_update_inhibited(driver.RequestSpec(
        "manager-bootstrap", context=tuple(request_context().items()),
        killswitch_path=_bootstrap_hold_path(),
    ))


def _foreground_install(args, driver) -> Path | int:
    """Explicit bootstrap owns the existing finite driver, not a spawn fallback."""
    from worktree_manager.update_driver_cli import _CONTEXT_KEYS, parse_options, request_context

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    repair = False
    automatic = False
    if command[:1] == ["update"]:
        options, _forwarded = parse_options(command[1:])
        automatic = options.auto
        repair = not automatic
    digest = _source_digest(args.payload)
    arguments = [
        sys.executable, "-I", "-S", "-B", "-X", "utf8", str(SCRIPTS / "runtime_install.py"),
        "--root", str(args.root.resolve()), "--payload", str(args.payload.resolve()),
        "--install-only", "--source-digest", digest,
    ]
    identity = ["install"]
    if args.skip_instance_reconcile:
        arguments.append("--skip-instance-reconcile")
        identity.append("skip-instance-reconcile")
    if args.repair_update:
        arguments.append("--repair-update")
        identity.append("repair-update")
    if args.recover_adopted is not None:
        evidence = str(args.recover_adopted.resolve())
        arguments.extend(["--recover-adopted", evidence])
        identity.extend(["recover-adopted", evidence])
    if automatic:
        identity.append("--auto")
    request = driver.enqueue(
        driver.RequestSpec(
            "manager-bootstrap", tuple(identity), project=str(args.root.resolve()),
            cwd=str(args.payload.resolve()), source=digest,
            context=tuple({**request_context(), "WORKTREE_MANAGER_ROOT": str(args.root.resolve())}.items()),
            repair=repair,
            killswitch_path=_bootstrap_hold_path(),
        ),
        runner=driver.TrustedRunner(
            tuple(arguments), str(args.payload.resolve()), context_keys=_CONTEXT_KEYS,
        ),
    )
    driver.drain()
    result = driver.wait(request.id, output=sys.stdout)
    if result.state != "completed" or result.exit_code is None:
        raise RuntimeError(f"Manager bootstrap did not complete: {result.state}")
    if result.exit_code:
        return result.exit_code
    with result.log_path.open("rb") as log:
        offset = max(0, result.log_path.stat().st_size - 65538)
        log.seek(offset)
        tail = log.read()
    if offset:
        _partial, _separator, tail = tail.partition(b"\n")
    lines = tail.splitlines()
    if not lines or len(lines[-1]) > 65536:
        raise RuntimeError("Manager bootstrap returned no bounded installation receipt")
    receipt = json.loads(lines[-1])
    if not isinstance(receipt, dict) or type(receipt.get("version")) is not int or receipt != {
        "schema": "copilot-extensions.manager-bootstrap", "version": 1,
        "root": str(args.root.resolve()), "source": digest, "slot": receipt.get("slot"),
    }:
        raise RuntimeError("Manager bootstrap installation receipt does not match admission")
    if not isinstance(receipt["slot"], str):
        raise RuntimeError("Manager bootstrap returned no installed slot")
    slot = Path(receipt["slot"])
    runtime._validate_dev_previous_version(slot.name)
    if (
        slot.parent != args.root.resolve() / "versions" or slot.resolve() != slot
        or not runtime.is_complete(args.root, slot.name)
    ):
        raise RuntimeError("Manager bootstrap returned no complete owned runtime")
    return slot


def _uninstall(args) -> int:
    from runtime_uninstall import uninstall, validate_frontdoor

    if validate_frontdoor(args.root, as_json=args.json):
        return 1
    if os.name != "nt" or not args.apply:
        return uninstall(args.root, apply=args.apply, as_json=args.json)
    sys.path.insert(0, str(args.payload / "src"))
    driver = _execution_driver(args.payload)
    with _installation_custody() as custody:
        if custody is not None:
            custody.assert_owned()
            return uninstall(args.root, apply=True, as_json=args.json)
        from worktree_manager.update_driver_cli import _CONTEXT_KEYS, request_context

        arguments = [
            sys.executable, "-I", "-S", "-B", "-X", "utf8", str(SCRIPTS / "runtime_install.py"),
            "--root", str(args.root.resolve()), "--uninstall", "--apply",
        ]
        if args.json:
            arguments.append("--json")
        request = driver.enqueue(
            driver.RequestSpec(
                "manager-uninstall", ("--apply", *(("--json",) if args.json else ())),
                project=str(args.root.resolve()), cwd=str(args.payload.resolve()),
                source=_source_digest(args.payload),
                context=tuple({
                    **request_context(), "WORKTREE_MANAGER_ROOT": str(args.root.resolve()),
                }.items()),
            ),
            runner=driver.TrustedRunner(
                tuple(arguments), str(args.payload.resolve()), context_keys=_CONTEXT_KEYS,
            ),
        )
        driver.drain()
        result = driver.wait(request.id, output=sys.stdout)
        if result.state != "completed" or result.exit_code is None:
            raise RuntimeError(f"Manager removal did not complete: {result.state}")
        return result.exit_code


def _install(args) -> Path | int:
    reconcile = not args.skip_instance_reconcile
    if args.repair_update:
        from update_entry import validate_manager_ownership

        prior_commands = validate_manager_ownership(args.root, args.payload)
    if args.source_digest is not None and _source_digest(args.payload) != args.source_digest:
        raise RuntimeError("Manager installation source changed after driver admission")

    def before_publish():
        if args.source_digest is not None and _source_digest(args.payload) != args.source_digest:
            raise RuntimeError("Manager installation source changed during construction")
        if args.repair_update:
            validate_manager_ownership(args.root, args.payload, expected=prior_commands)

    # Only bootstrap installer modules are read from source. Application execution
    # happens below under the constructed interpreter and retention bootstrap.
    from worktree_manager.self_install import self_install

    install_payload = args.payload
    if args.recover_adopted is None:
        from source_acquisition import manager_payload

        try:
            install_payload = manager_payload(args.payload, args.root)
        except (OSError, ValueError, RuntimeError, ImportError) as error:
            print(f"Manager source acquisition refused: {error}", file=sys.stderr)
            return 1
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
                **_installation_child_options(),
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
                install_payload, root=args.root, dry_run=False,
                _before_publish=before_publish,
                _after_activate=lambda slot: _activate_after_bootstrap_update(args.root, slot),
                _transactional_activation=True,
                _reconcile_instances=reconcile,
            )
        else:
            result = self_install(
                install_payload, root=args.root, dry_run=False, _reconcile_instances=reconcile,
                _before_publish=before_publish,
            )
        if result.action == "error":
            print(result.reason, file=sys.stderr)
            return 1
        slot = Path(result.slot)
    return slot


def _finish(args, slot: Path | int, command: list[str]) -> int:
    if isinstance(slot, int):
        return slot
    if args.install_only:
        print(json.dumps({
            "schema": "copilot-extensions.manager-bootstrap", "version": 1,
            "root": str(args.root.resolve()), "source": args.source_digest, "slot": str(slot),
        }), flush=True)
        return 0
    if args.repair_update:
        command = ["update", "--repair-engine", str(args.payload.parent), *command[1:]]
    python = runtime.slot_python(args.root, slot.name)
    if python is None:
        raise RuntimeError("Constructed Manager has no interpreter")
    invocation = [
        str(python), "-I", "-S", "-B", "-X", "utf8",
        str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
        "-m", "worktree_manager", *command,
    ]
    if os.name != "nt":
        return _dispatch_manager(invocation)
    from worktree_manager.update_driver_cli import handoff_environment

    handoff = handoff_environment(command)
    with runtime.retention_lease(args.root, slot.name):
        return _dispatch_manager(invocation, handoff=handoff) if handoff else _dispatch_manager(invocation)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--payload", type=Path, default=SCRIPTS.parent)
    parser.add_argument("--recover-adopted", type=Path)
    parser.add_argument("--describe-recovery", action="store_true")
    parser.add_argument("--repair-update", action="store_true")
    parser.add_argument("--recover-for-repair", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--install-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source-digest", help=argparse.SUPPRESS)
    parser.add_argument("--skip-instance-reconcile", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.uninstall:
        if (
            args.root is None or args.recover_adopted or args.describe_recovery
            or args.repair_update or args.recover_for_repair or args.command
            or args.install_only or args.source_digest or args.skip_instance_reconcile
            or args.payload.resolve() != SCRIPTS.parent
        ):
            parser.error("--uninstall requires --root and only accepts --apply and --json")
        return _uninstall(args)
    if args.apply:
        parser.error("--apply requires --uninstall")
    if args.recover_for_repair:
        if (
            args.recover_adopted or args.describe_recovery or args.repair_update or args.command
            or args.install_only or args.source_digest or args.skip_instance_reconcile
        ):
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
    if args.install_only and (command or args.source_digest is None):
        parser.error("Installation participant requires a source digest and no application command")
    if args.skip_instance_reconcile and not args.install_only:
        parser.error("Instance reconciliation policy requires an installation participant")
    if args.source_digest is not None and (
        not args.install_only or re.fullmatch("[0-9a-f]{64}", args.source_digest) is None
    ):
        parser.error("Source digest requires an installation participant and a SHA-256")
    if args.repair_update and not args.install_only and command[:1] != ["update"]:
        parser.error("Bootstrap repair is only valid for update")
    os.environ.pop("__PYVENV_LAUNCHER__", None)
    os.environ["WORKTREE_MANAGER_ROOT"] = str(args.root.resolve())
    sys.path.insert(0, str(args.payload / "src"))
    if not args.install_only:
        args.skip_instance_reconcile = bool(command and command[:1] not in (["update"], ["self-install"]))
    if command[:1] == ["update"]:
        from worktree_manager.runtime_source_config import configured_source, require_marketplace

        try:
            targeted = any(arg == "--target" or arg.startswith("--target=") for arg in command)
            if args.repair_update and not targeted:
                configured_source(args.root)
            else:
                require_marketplace(args.root)
        except (OSError, ValueError) as error:
            print(f"Runtime source: {error}", file=sys.stderr)
            return 1
    if os.name == "nt":
        driver = _execution_driver(args.payload)
        with _installation_custody() as custody:
            if custody is None:
                if args.install_only:
                    raise RuntimeError("Manager installation participant requires live driver custody")
                slot = _foreground_install(args, driver)
            else:
                slot = _install(args)
            return _finish(args, slot, command)
    else:
        if args.install_only:
            raise RuntimeError("Windows installation participant is unsupported on this platform")
        if _automatic_bootstrap_inhibited(args):
            print("automatic-update KillSwitch active; nothing ran", file=sys.stderr)
            return 3
        slot = _install(args)
    return _finish(args, slot, command)


if __name__ == "__main__":
    raise SystemExit(main())
