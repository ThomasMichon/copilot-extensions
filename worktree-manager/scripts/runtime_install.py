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
from runtime_install_selection import (  # noqa: E402
    _source_profile_reuse,
    base_python as base_python,
    prepared_manager_inputs as prepared_manager_inputs,
    selection_workspace as _selection_workspace,
    selector_text as _selector_text,
    source_digest as _source_digest,
    selected_version as _selected_version,
    source_install_selection as source_install_selection,
)


@contextmanager
def candidate(
    payload: Path, root: Path, *, assemble, uv: Path | None = None,
    python: Path | None = None,
    wheel_inputs: engine.WheelInstallInputs | engine.WheelReuseInputs | None = None,
    native_wheel_inputs: engine.WheelInstallInputs | None = None,
    local_packages: tuple[str, ...] | None = None, validate_candidate=None,
    application: Path | None = None,
):
    """Retain a healthy target; never publish selectors during construction."""
    if os.name == "nt":
        uv = Path(engine._select_uv_executable(str(Path.cwd() / uv) if uv is not None else "uv"))
    else:
        uv = uv or Path(shutil.which("uv") or "")
    if not uv.is_file():
        raise RuntimeError("uv is required for Manager runtime construction")
    root.mkdir(parents=True, exist_ok=True)
    inputs = Path(engine._native_retained_path(root / ".installer-inputs"))
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
        if native_wheel_inputs is not None:
            if identity is not None or wheel_inputs is not None:
                raise ValueError("Manager native wheels cannot replace a Source association")
            from install_source import wheel_selection

            selected = wheel_selection(payload, "worktree-manager", native_wheel_inputs)
            wheel_inputs = native_wheel_inputs
            local_packages = tuple(selected["local_packages"])
        if wheel_inputs is None and identity is not None:
            current = _selected_version(root)
            reused = _source_profile_reuse(
                payload, identity, runtime.version_dir(root, current) if current else None,
            )
            if reused is not None:
                wheel_inputs, local_packages = reused
        if wheel_inputs is not None:
            if identity is None and native_wheel_inputs is None:
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
            process_retention=True, reuse_generations=identity is None and wheel_inputs is None,
            expected_source_identity=runtime_source.manifest_fields(identity),
        )
        if identity is not None:
            arguments["construction_data"] = {"source_payload": str(prepared)}
        if wheel_inputs is not None:
            arguments.update(wheel_inputs=wheel_inputs, local_packages=local_packages)
        if validate_candidate is not None:
            arguments["validate_candidate"] = validate_candidate
        if application is not None:
            arguments["application"] = application
        current = _selected_version(root)
        if (
            not reuse_only and (identity is not None or native_wheel_inputs is not None) and current
            and re.fullmatch(re.escape(generation) + r"(?:[.+]repair\.[0-9a-f]{32})?", current)
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
            except (engine.HealthRepairRequired, engine.ContentRepairRequired):
                if reuse_only:
                    raise
                separator = "." if "+" in requested_generation else "+"
                repaired = f"{requested_generation}{separator}repair.{uuid.uuid4().hex}"
                arguments["reuse_generations"] = False
                ready = custody.enter_context(engine.numbered_candidate(root, repaired, **arguments))
            yield ready


def stage_application(root: Path, slot: Path, version: str, unchanged):
    """Prove readiness while the caller retains qualified application inputs."""
    from worktree_manager.runtime_stage import probe
    from worktree_manager.self_install import SelfInstallResult

    probe(root)
    unchanged()
    return SelfInstallResult(
        version=version, action="staged", root=str(root), slot=str(slot), marker=slot.name,
    )


def verify_stage_readiness(
    root: Path, ready: engine.ReadyCandidate, *, wheel_inputs=None, timeout: float = 60,
) -> None:
    """Probe the installed app under the candidate's existing retained custody."""
    root = root.resolve()
    if ready.slot.parent != root / "versions":
        raise RuntimeError("Manager staging candidate belongs to another installation")
    python = runtime.slot_python(root, ready.slot.name)
    if python is None:
        raise RuntimeError("Manager staging candidate has no interpreter")
    inputs = engine.FrozenInputs(
        ready.frozen_plugin, engine._package_roots(ready.frozen_plugin), ready.payload_hash,
        wheel_inputs=wheel_inputs,
    )
    result = engine._child(
        [
            str(python), "-I", "-S", "-B", "-X", "utf8",
            str(ready.slot / ".runtime-bootstrap" / "runtime_launcher.py"),
            "-m", "worktree_manager.runtime_stage", "--root", str(root),
        ],
        inputs=inputs, descriptor=ready.descriptor, timeout=timeout,
        cwd=root, candidate=ready.slot, environment=_installation_child_options().get("env"),
    )
    record = json.loads(result.stdout)
    if (
        not isinstance(record, dict) or type(record.get("version")) is not int
        or record.get("ready") is not True or record != {
            "schema": "copilot-extensions.manager-stage-readiness", "version": 1,
            "generation": ready.slot.name, "ready": True,
        }
    ):
        raise RuntimeError("Manager staging candidate returned no exact readiness result")


def activate(root: Path, version: str, *, expected: str | None, after_publish=None,
             before_publish=None, forward_only=None) -> None:
    """Publish under custody; acknowledged incoming startup is forward-only."""
    runtime._validate_dev_previous_version(version)
    root = root.resolve()
    publication = runtime._runtime_component("versioned_runtime_publication")
    targets = [root / "current-version", root / "last-known-good"]
    with runtime._publication_lease(root):
        baseline = {path: publication.state_path(path) for path in targets}
        contents = {
            path: publication.CopyPublication._bytes(path, before)
            for path, before in baseline.items() if before is not None
        }
        selected = _selector_text(contents.get(targets[0], b""))
        if selected not in (expected, version):
            raise RuntimeError("Manager selection changed during construction")
    # Cleanup takes retention before publication. Acquire in that same order,
    # then compare both snapshots under publication to close the unlocked gap.
    with ExitStack() as custody:
        generations = {version}
        for generation in filter(None, {_selector_text(data) for data in contents.values()}):
            try:
                runtime._validate_dev_previous_version(generation)
            except ValueError:
                continue
            generations.add(generation)
        for generation in sorted(generations):
            if runtime.version_dir(root, generation).is_dir():
                custody.enter_context(runtime.retention_lease(root, generation))
        with runtime._publication_lease(root):
            if any(publication.state_path(path) != before for path, before in baseline.items()):
                raise RuntimeError("Manager selectors changed before publication")
            inputs = root / ".installer-inputs"
            inputs.mkdir(exist_ok=True)
            cancelled = None
            with _selection_workspace(inputs) as workspace:
                bindings = []
                for index, path in enumerate(targets):
                    before = baseline[path]
                    backup = workspace / f"before.{index}"
                    if before is not None:
                        with os.fdopen(publication._created_file(backup), "wb") as stream:
                            stream.write(contents[path])
                            stream.flush()
                            os.fsync(stream.fileno())
                        backup.chmod(stat.S_IMODE(before.mode))
                    bindings.append([str(path), str(backup) if before else "",
                                     before.digest if before else ""])
                context = workspace / "context.json"
                publication.create_context(context, root, bindings)
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

                while True:
                    forward = workspace / f"forward.{len(receipts)}.jsonl"
                    try:
                        if before_publish is not None:
                            before_publish()
                        runtime._activate(root, version, link_free=True, ownership_receipt=forward)
                        if after_publish is not None:
                            after_publish()
                        break
                    except BaseException as error:
                        if forward.exists():
                            receipts.append(forward)
                        committed = forward_only is not None and forward_only()
                        if committed and isinstance(error, (KeyboardInterrupt, SystemExit)):
                            cancelled = cancelled or error
                            continue
                        if not committed:
                            _settle(recover)
                        raise
            if cancelled is not None:
                raise cancelled


class ActivationStarted(BaseException):
    """V2 acknowledged startup; finish publication before propagating the error."""

    def __init__(self, interruption: BaseException, outcome: dict) -> None:
        self.interruption, self.outcome = interruption, outcome


def _activate_after_bootstrap_update(
    root: Path, slot: Path, *, start_if_missing: bool = False,
) -> dict:
    from worktree_manager.bootstrap_activation import started_outcome

    python = runtime.slot_python(root, slot.name)
    if python is None:
        raise RuntimeError("Constructed Manager has no activation interpreter")
    command = [
        str(python), "-I", "-S", "-B", "-X", "utf8",
        str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
        "-m", "worktree_manager.bootstrap_activation", "--root", str(root),
    ]
    if start_if_missing:
        command.append("--start-if-missing")
    process = subprocess.Popen(
        command, executable=command[0], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        **_installation_child_options(),
    )
    interrupted = None
    try:
        stdout, stderr = process.communicate()
    except BaseException as error:
        interrupted, output = error, []
        try:
            _settle(lambda: output.append(process.communicate()))
        except BaseException:
            if not output:
                raise
        stdout, stderr = output[-1]
    if stdout:
        print(stdout, end="", flush=True)
    if stderr:
        print(stderr, end="", file=sys.stderr, flush=True)
    outcome = started_outcome(stdout, slot.name)
    if outcome is not None:
        if interrupted is not None or process.returncode:
            raise ActivationStarted(
                interrupted or RuntimeError(f"Manager activation helper exited after V2 startup ({process.returncode})"),
                outcome,
            )
        return outcome
    if interrupted is not None:
        print("Manager V2 startup is unconfirmed; selection was not published", file=sys.stderr, flush=True)
        raise interrupted
    raise RuntimeError(f"Constructed Manager did not acknowledge V2 startup ({process.returncode})")


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


def _dispatch_manager(
    command: list[str], *, handoff: dict[str, str] | None = None, wait_for_exit: bool = False,
    cwd: str | None = None, fallback_cwd: str | None = None,
) -> int:
    if os.name == "nt" or handoff or wait_for_exit or cwd is not None:
        console = os.name == "nt" and any(
            _windows_console_stream(stream) for stream in (sys.stdin, sys.stdout, sys.stderr)
        )
        options = dict(
            stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
            **({"creationflags": 0 if console else subprocess.CREATE_NO_WINDOW}
               if os.name == "nt" else {}),
            **({"env": {**os.environ, **handoff}} if handoff else {}),
            **({"cwd": cwd} if cwd is not None else {}),
        )
        try:
            process = subprocess.Popen(command, **options)
        except (FileNotFoundError, NotADirectoryError):
            if cwd is None or fallback_cwd is None or Path(cwd).is_dir():
                raise
            process = subprocess.Popen(command, **{**options, "cwd": fallback_cwd})
        try:
            return process.wait()
        except BaseException:
            _settle(process.wait)
            raise
    os.execv(command[0], command)
    raise AssertionError("Manager bootstrap exec returned")


def _installation_child_options() -> dict[str, object]:
    from update_driver import participant_environment

    return {"env": {**os.environ, **participant_environment()}}


def _execution_driver(payload: Path):
    # Source controls need application locals as well as the required execution libraries.
    required = {"agent-update-driver", "agent-single-instance-lease", "agent-procutil", "agent-zdd"}
    roots = engine._package_roots(payload)
    packages = {
        engine._package_name(package): package for package in roots
        if engine._package_name(package) in required
    }
    if packages.keys() != required:
        raise RuntimeError("Manager bootstrap requires its complete execution-driver source closure")
    sys.path[:0] = [str(package / "src") for package in roots]
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


def _validate_update_request(args) -> None:
    expected = getattr(args, "admitted_source", None)
    if expected is not None:
        from worktree_manager.update_driver_cli import _source_key, _update_context_directory

        if _source_key() != expected:
            raise RuntimeError("Manager source changed after submission; submit a new request")
        _update_context_directory(os.environ)


def _bootstrap_hold_path() -> str:
    override = os.environ.get("BOOTSTRAP_KILLSWITCH_STATE_FILE", "")
    return os.path.abspath(os.path.expanduser(override)) if override else ""


def _automatic_bootstrap_refusal(args) -> str | None:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if command[:1] != ["update"]:
        return None
    from worktree_manager.update_driver_cli import parse_options, request_context

    options, _forwarded = parse_options(command[1:])
    if not options.auto:
        return None
    driver = _execution_driver(SCRIPTS.parent)
    request = driver.RequestSpec(
        "manager-bootstrap", ("update", "--auto"),
        context=tuple({**request_context(), "WORKTREE_MANAGER_ROOT": str(args.root.resolve())}.items()),
        killswitch_path=_bootstrap_hold_path(),
    )
    reader = getattr(driver, "automatic_update_refusal", None)
    if callable(reader):
        return reader(request)
    from worktree_manager.manager_config import ManagerConfigError, load_config

    try:
        if not load_config(args.root).maintenance.automatic:
            return "Manager maintenance.automatic is false; automatic update not run"
    except ManagerConfigError as error:
        return f"Manager automatic-maintenance policy unavailable: {error}; nothing ran"
    return (
        "automatic-update KillSwitch active; nothing ran"
        if driver._automatic_update_inhibited(request) else None
    )


def _automatic_bootstrap_inhibited(args) -> bool:
    return _automatic_bootstrap_refusal(args) is not None


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
    identity = ["update" if automatic else "install"]
    if getattr(args, "install_source", None) is not None:
        source_path = str(args.install_source)
        arguments.extend(["--install-source", source_path])
        identity.extend(["install-source", source_path])
    if getattr(args, "wheel_profile", None) is not None:
        for name in ("wheel_profile", "wheel_directory", "wheel_profile_sha256", "wheel_python", "wheel_uv"):
            arguments.extend(["--" + name.replace("_", "-"), str(getattr(args, name))])
        identity.extend(["wheel-profile", args.wheel_profile_sha256])
    if args.skip_instance_reconcile:
        arguments.append("--skip-instance-reconcile")
        identity.append("skip-instance-reconcile")
    if args.repair_update:
        arguments.append("--repair-update")
        identity.append("repair-update")
    if getattr(args, "stage_only", False):
        arguments.append("--stage")
        identity.append("stage")
    if args.recover_adopted is not None:
        evidence = str(args.recover_adopted.resolve())
        arguments.extend(["--recover-adopted", evidence])
        identity.extend(["recover-adopted", evidence])
    if automatic:
        identity.append("--auto")
    caller = str(Path.cwd().resolve())
    request = driver.enqueue(
        driver.RequestSpec(
            "manager-bootstrap", tuple(identity), project=str(args.root.resolve()),
            cwd=caller, source=digest,
            context=tuple({**request_context(), "WORKTREE_MANAGER_ROOT": str(args.root.resolve())}.items()),
            repair=repair,
            killswitch_path=_bootstrap_hold_path(),
        ),
        runner=driver.TrustedRunner(
            tuple(arguments), caller, context_keys=_CONTEXT_KEYS,
        ),
    )
    driver.drain()
    result = driver.wait(request.id, output=sys.stderr)
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
        "schema": (
            "copilot-extensions.manager-stage" if getattr(args, "stage_only", False)
            else "copilot-extensions.manager-bootstrap"
        ), "version": 1,
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
    from runtime_uninstall import report_absent_target, uninstall, validate_frontdoor

    if validate_frontdoor(args.root, as_json=args.json):
        return 1
    if os.name != "nt" or not args.apply:
        return uninstall(args.root, apply=args.apply, as_json=args.json)
    absent = report_absent_target(args.root, as_json=args.json)
    if absent is not None:
        return absent
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
    if getattr(args, "wheel_profile", None) is not None:
        from install_source import wheel_inputs, wheel_selection

        with wheel_inputs(
            args.wheel_profile, args.wheel_directory, expected_profile_sha256=args.wheel_profile_sha256,
        ) as wheels:
            wheel_selection(args.payload, "worktree-manager", wheels)
            return _install_payload(args, native_wheel_inputs=wheels)
    if getattr(args, "install_source", None) is None:
        return _install_payload(args)
    with prepared_manager_inputs(args.install_source) as (selected, options):
        if selected.payload.resolve() != args.payload.resolve():
            raise ValueError("Manager installation source selection changed")
        return _install_payload(args, **options)


def _install_payload(
    args, *, wheel_inputs=None, native_wheel_inputs=None, local_packages=None, application=None,
) -> Path | int:
    _validate_update_request(args)
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
        _validate_update_request(args)

    with ExitStack() as source_custody:
        install_payload = args.payload
        options = {
            "wheel_inputs": wheel_inputs, "local_packages": local_packages,
            "application": application, "native_wheel_inputs": native_wheel_inputs,
        }
        if (
            args.recover_adopted is None and getattr(args, "install_source", None) is None
            and native_wheel_inputs is None
        ):
            from source_acquisition import manager_payload

            try:
                install_payload, prepared_options = source_custody.enter_context(
                    manager_payload(args.payload, args.root),
                )
            except (OSError, ValueError, RuntimeError, ImportError) as error:
                print(f"Manager source acquisition refused: {error}", file=sys.stderr)
                return 1
            options.update(prepared_options)
        return _install_selected_payload(
            args, install_payload, before_publish=before_publish, reconcile=reconcile, **options,
        )


def _install_selected_payload(
    args, install_payload: Path, *, before_publish, reconcile: bool,
    wheel_inputs, local_packages, application, native_wheel_inputs,
) -> Path | int:
    # Application execution stays under the constructed interpreter and retention bootstrap.
    from worktree_manager.self_install import self_install

    if args.recover_adopted is not None:
        evidence = json.loads(args.recover_adopted.read_text(encoding="utf-8"))
        if evidence.get("root") != str(args.root.resolve()):
            raise RuntimeError("Recovery adoption names another Manager root")
        from worktree_manager.self_install import _copy_payload

        candidate_options = {"application": application} if application is not None else {}
        with candidate(args.payload, args.root, assemble=_copy_payload, **candidate_options) as ready:
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
        source_options = (
            {"_wheel_inputs": wheel_inputs, "_local_packages": local_packages}
            if wheel_inputs is not None else {}
        )
        if application is not None:
            source_options["_application"] = application
        if native_wheel_inputs is not None:
            source_options.update(
                _native_wheel_inputs=native_wheel_inputs,
                _base_python=args.wheel_python, _uv=args.wheel_uv,
            )
        staged = getattr(args, "stage_only", False)
        result = self_install(
            install_payload, root=args.root, dry_run=False,
            _before_publish=before_publish,
            _after_activate=None if staged else lambda slot: _activate_after_bootstrap_update(args.root, slot),
            _transactional_activation=not staged,
            _reconcile_instances=reconcile,
            **source_options,
            **({"stage_only": True} if staged else {}),
        )
        if result.action == "error" or (staged and result.action != "staged"):
            print(
                result.reason if result.action == "error" else "Manager staging returned a non-staged result",
                file=sys.stderr,
            )
            return 1
        slot = Path(result.slot)
    return slot


def _finish(args, slot: Path | int, command: list[str]) -> int:
    if isinstance(slot, int):
        return slot
    if args.install_only or getattr(args, "stage_only", False):
        print(json.dumps({
            "schema": (
                "copilot-extensions.manager-stage" if getattr(args, "stage_only", False)
                else "copilot-extensions.manager-bootstrap"
            ), "version": 1,
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
    from worktree_manager.update_driver_cli import handoff_environment, request_source_anchor
    from worktree_manager.update_python_path import finish_update
    from update_driver import participant_environment

    handoff = handoff_environment(command)
    owned_without_handoff = not handoff and bool(participant_environment())
    selected = {
        "cwd": request_source_anchor(), "fallback_cwd": str(args.root.resolve()),
    } if args.update_participant else {}
    with runtime.retention_lease(args.root, slot.name):
        if handoff:
            result = _dispatch_manager(invocation, handoff=handoff, **selected)
        elif owned_without_handoff:
            result = _dispatch_manager(invocation, wait_for_exit=True, **selected)
        else:
            result = _dispatch_manager(invocation, **selected)
    return result if args.update_participant else finish_update(command, result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--payload", type=Path, default=SCRIPTS.parent)
    parser.add_argument("--bootstrap-attempt", help=argparse.SUPPRESS)
    parser.add_argument("--describe-install", action="store_true")
    parser.add_argument("--stage", dest="stage_only", action="store_true")
    parser.add_argument("--recover-adopted", type=Path)
    parser.add_argument("--describe-recovery", action="store_true")
    parser.add_argument("--repair-update", action="store_true")
    parser.add_argument("--recover-for-repair", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--install-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--update-participant", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source-digest", help=argparse.SUPPRESS)
    parser.add_argument("--skip-instance-reconcile", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--install-source", type=Path)
    for name in ("wheel-profile", "wheel-directory", "wheel-python", "wheel-uv"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--wheel-profile-sha256", help=argparse.SUPPRESS)
    parser.add_argument("--describe-source-inputs", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    from runtime_install_inputs import admit_native_wheels

    admit_native_wheels(args, parser)
    if args.describe_source_inputs:
        if any((
            args.root, args.install_source, args.command, args.uninstall, args.apply,
            args.recover_adopted, args.describe_recovery, args.repair_update,
            args.recover_for_repair, args.json, args.install_only, args.update_participant,
            args.source_digest,
            args.skip_instance_reconcile, args.describe_install, args.stage_only,
        )):
            parser.error("Source-input discovery cannot be combined with an operation")
        print(json.dumps({
            "schema": "copilot-extensions.manager-source-inputs", "version": 1,
            "capabilities": ["install-source"],
        }))
        return 0
    if args.describe_install:
        if (
            args.root is not None or args.payload.resolve() != SCRIPTS.parent
            or args.stage_only or args.recover_adopted or args.describe_recovery
            or args.repair_update or args.recover_for_repair or args.uninstall or args.apply
            or args.json or args.install_only or args.source_digest
            or args.skip_instance_reconcile or args.command or args.install_source
            or args.describe_source_inputs
        ):
            parser.error("--describe-install accepts no installation or application arguments")
        print(json.dumps({
            "schema": "copilot-extensions.manager-install-entry", "version": 1,
            "provider": "worktree-manager", "capabilities": {"stage": 1, "activate": 1},
        }))
        return 0
    if args.stage_only and (
        args.recover_adopted or args.describe_recovery or args.repair_update
        or args.recover_for_repair or args.uninstall or args.apply or args.json or args.command
    ):
        parser.error("--stage cannot be combined with recovery, removal or application commands")
    if args.install_source is not None:
        if args.uninstall or args.recover_for_repair or args.recover_adopted or args.describe_recovery:
            parser.error("--install-source cannot change recovery or uninstall source")
        from install_source import select

        selected = select(args.install_source)
        args.payload = selected.payload
    if args.uninstall:
        if (
            args.root is None or args.recover_adopted or args.describe_recovery
            or args.repair_update or args.recover_for_repair or args.command
            or args.install_only or args.update_participant or args.source_digest
            or args.skip_instance_reconcile
            or args.payload.resolve() != SCRIPTS.parent
        ):
            parser.error("--uninstall requires --root and only accepts --apply and --json")
        return _uninstall(args)
    if args.apply:
        parser.error("--apply requires --uninstall")
    if args.recover_for_repair:
        if (
            args.recover_adopted or args.describe_recovery or args.repair_update or args.command
            or args.install_only or args.update_participant or args.source_digest
            or args.skip_instance_reconcile
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
    if args.update_participant and (
        args.install_only or command[:1] != ["update"] or args.source_digest is None
        or args.recover_adopted is not None
    ):
        parser.error("Update participant requires a source digest and one update command")
    if args.skip_instance_reconcile and not args.install_only:
        parser.error("Instance reconciliation policy requires an installation participant")
    if args.source_digest is not None and (
        not (args.install_only or args.update_participant)
        or re.fullmatch("[0-9a-f]{64}", args.source_digest) is None
    ):
        parser.error("Source digest requires an installation participant and a SHA-256")
    if args.repair_update and not args.install_only and command[:1] != ["update"]:
        parser.error("Bootstrap repair is only valid for update")
    os.environ.pop("__PYVENV_LAUNCHER__", None)
    os.environ["WORKTREE_MANAGER_ROOT"] = str(args.root.resolve())
    sys.path.insert(0, str(SCRIPTS.parent / "src"))
    if not args.install_only:
        args.skip_instance_reconcile = bool(command and command[:1] not in (["update"], ["self-install"]))
    driver = _execution_driver(SCRIPTS.parent)
    if command[:1] == ["update"]:
        from bootstrap_update import _request_control
        from worktree_manager.update_driver_cli import cmd_update_request, parse_options

        options, forwarded = parse_options(command[1:])
        if _request_control(options, forwarded):
            if args.update_participant:
                parser.error("Request inspection/recovery cannot be an installation participant")
            return cmd_update_request(command[1:])
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
    refusal = None if args.update_participant else _automatic_bootstrap_refusal(args)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 3
    if os.name == "nt" or command[:1] == ["update"]:
        with _installation_custody() as custody:
            if custody is None:
                if args.install_only or args.update_participant:
                    raise RuntimeError("Manager installation participant requires live driver custody")
                if command[:1] == ["update"]:
                    from bootstrap_update import execute

                    return execute(args, driver, source_digest=_source_digest(args.payload))
                slot = _foreground_install(args, driver)
            else:
                if args.update_participant:
                    from worktree_manager.update_driver_cli import _request_environment

                    request = driver.status(custody.request_id, root=custody.root)
                    with _request_environment(
                        request.spec.context, killswitch_path=request.spec.killswitch_path,
                        cwd=request.spec.cwd,
                    ):
                        args.admitted_source = request.spec.source
                        _validate_update_request(args)
                        refusal = _automatic_bootstrap_refusal(args)
                        if refusal is not None:
                            print(refusal, file=sys.stderr)
                            return 3
                        return _finish(args, _install(args), command)
                slot = _install(args)
            return _finish(args, slot, command)
    else:
        if args.install_only or args.update_participant:
            raise RuntimeError("Windows installation participant is unsupported on this platform")
        slot = _install(args)
    return _finish(args, slot, command)


if __name__ == "__main__":
    raise SystemExit(main())
