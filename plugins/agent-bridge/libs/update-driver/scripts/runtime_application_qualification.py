"""Canonical application declaration, Source/profile and qualification rules."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any, Callable, Iterator, cast

if TYPE_CHECKING:
    from runtime_application_install import InstalledApplicationSource


def _materialize_interpreter(_installer: ModuleType, slot: Path) -> None:
    if sys.platform == "win32":
        return
    environment = slot / ".venv"
    directory = environment / "bin"
    if environment.is_symlink() or directory.is_symlink():
        raise RuntimeError("application interpreter directory must not be indirect")
    aliases = (
        "python",
        f"python{_installer.sys.version_info.major}",
        f"python{_installer.sys.version_info.major}.{_installer.sys.version_info.minor}",
    )
    links = [directory / name for name in aliases if (directory / name).is_symlink()]
    if not links:
        return
    import fcntl

    descriptor = _installer.os.open(
        directory,
        _installer.os.O_RDONLY | _installer.os.O_DIRECTORY | _installer.os.O_NOFOLLOW,
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "application interpreter preparation is already in progress"
            ) from error
        links = [
            directory / name for name in aliases if (directory / name).is_symlink()
        ]
        if not links:
            return
        if (slot / ".install-complete.json").exists():
            raise RuntimeError("completed application contains a linked interpreter")
        source = Path(_installer.sys.executable).resolve(strict=True)
        if any((not path.resolve(strict=True).samefile(source) for path in links)):
            raise RuntimeError(
                "application interpreter link differs from its running interpreter"
            )
        content = _installer._read(source, limit=128 << 20)
        if content is None:
            raise RuntimeError("application running interpreter is unavailable")
        for path in links:
            _installer._write(path, content, executable=True)
    finally:
        _installer.os.close(descriptor)


def _application(_installer: ModuleType, slot: Path) -> dict[str, Any]:
    raw = _installer._read(slot / "application.json", limit=4 << 20)
    if raw is None:
        raise RuntimeError("the copied application has no install declaration")
    return cast(
        dict[str, Any], _installer._validate_application(_installer.json.loads(raw))
    )


def callback_timeouts(
    _installer: ModuleType, record: dict[str, Any]
) -> dict[str, float]:
    """Read finite per-phase process budgets without changing legacy defaults."""
    declared = record.get("callback_timeout_seconds", {})
    if (
        not isinstance(declared, dict)
        or set(declared) - {"setup", "start"}
        or any(
            (
                type(value) not in (int, float)
                or not 0 < value <= 3600
                or (not _installer.math.isfinite(value))
                for value in declared.values()
            )
        )
    ):
        raise ValueError(
            "application callback timeouts must be finite seconds in (0, 3600]"
        )
    return {"setup": 180, "start": 180, **declared}


def application_install_timeout(
    _installer: ModuleType, record: dict[str, Any]
) -> float:
    """Leave publication/qualification room outside explicitly budgeted callbacks."""
    budgets = _installer.callback_timeouts(record)
    if "callback_timeout_seconds" not in record:
        return 180
    return cast(
        float,
        _installer._QUALIFICATION_TIMEOUT
        + _installer._PUBLICATION_ALLOWANCE
        + sum((budgets[phase] for phase in budgets if record.get(phase))),
    )


def _validate_application(
    _installer: ModuleType, record: dict[str, Any]
) -> dict[str, Any]:
    fields = {
        "name",
        "module",
        "commands",
        "root_environment",
        "default_directory",
        "setup",
        "start",
        "construction",
    }
    if (
        not isinstance(record, dict)
        or set(record) - {"callback_timeout_seconds", "remove"} != fields
    ):
        raise ValueError("invalid application install declaration")
    _installer.callback_timeouts(record)
    if "remove" in record:
        _installer.removal_callback(record["remove"])
    if (
        not isinstance(record["module"], str)
        or not _installer.re.fullmatch(
            "[A-Za-z_]\\w*(?:\\.[A-Za-z_]\\w*)*(?::[A-Za-z_]\\w*(?:\\.[A-Za-z_]\\w*)*)?",
            record["module"],
        )
        or any(
            (
                not isinstance(record[key], list)
                or any(
                    (
                        not isinstance(argument, str) or "\x00" in argument
                        for argument in record[key]
                    )
                )
                for key in ("setup", "start")
            )
        )
        or (not isinstance(record["commands"], list))
        or (not record["commands"])
        or any((not isinstance(name, str) for name in record["commands"]))
        or (len(set(record["commands"])) != len(record["commands"]))
        or (record["name"] not in record["commands"])
    ):
        raise ValueError("invalid application command declaration")
    return record


def removal_callback(_installer: ModuleType, value: Any) -> str:
    """Require one explicit application-owned removal entry point."""
    if (
        not isinstance(value, str)
        or _installer.re.fullmatch(
            "[A-Za-z_]\\w*(?:\\.[A-Za-z_]\\w*)*:[A-Za-z_]\\w*(?:\\.[A-Za-z_]\\w*)*",
            value,
        )
        is None
    ):
        raise ValueError(
            "application removal callback must be an explicit Python entry point"
        )
    return value


def _construction_metadata(
    _installer: ModuleType,
    record: dict[str, Any],
    runtime: ModuleType,
    engine: ModuleType,
    deadline: float,
) -> SimpleNamespace:
    """Validate claimed Source/profile facts without reading or qualifying a slot."""
    inputs = record["construction"]
    fields = {
        "identity",
        "runtime_version",
        "payload_hash",
        "source_hash",
        "wheel_inputs",
        "extras",
        "local_packages",
        "excluded_packages",
    }
    if not isinstance(inputs, dict) or set(inputs) != fields:
        raise ValueError(
            "regular install requires the actual Source/profile construction inputs"
        )
    if not isinstance(inputs["identity"], dict):
        raise ValueError("application construction identity must be an object")
    source = inputs["identity"].get("runtime_source")
    source_owner = runtime._runtime_component("versioned_runtime_source")
    identity = source_owner.parse_source_identity(source)
    source = cast(dict[str, Any], source)
    if (
        inputs["identity"].get("runtime_version") != source["runtime_version"]
        or any(
            (
                not isinstance(inputs[key], str)
                or _installer.re.fullmatch("[0-9a-f]{64}", inputs[key]) is None
                for key in ("source_hash", "payload_hash")
            )
        )
        or any(
            (
                not isinstance(inputs[key], list)
                or any(
                    (
                        not isinstance(name, str)
                        or _installer.re.fullmatch("[a-z0-9]+(?:-[a-z0-9]+)*", name)
                        is None
                        for name in inputs[key]
                    )
                )
                or len(set(inputs[key])) != len(inputs[key])
                for key in ("extras", "local_packages", "excluded_packages")
            )
        )
    ):
        raise ValueError(
            "application construction inputs differ from their Source identity"
        )
    wheels = engine.WheelReuseInputs(
        inputs["runtime_version"],
        _installer.json.dumps(
            inputs["wheel_inputs"], sort_keys=True, separators=(",", ":")
        ),
        deadline,
    )
    wheels.verify()
    receipt = wheels.receipt()
    generation = source["runtime_version"] + ".profile." + receipt["lock_id"][7:23]
    if (
        inputs["runtime_version"] != generation
        or engine._wheel_construction_hash(inputs["source_hash"], wheels)
        != inputs["payload_hash"]
        or receipt["binding"].get("release")
        != {
            "identity": "source:" + source["source_commit"],
            "sha256": "sha256:" + source["assembly_hash"],
        }
        or (receipt["binding"].get("component") != source["plugin"])
        or (not set(inputs["local_packages"]) <= receipt["distributions"].keys())
        or set(inputs["excluded_packages"]) & receipt["distributions"].keys()
    ):
        raise ValueError(
            "application Source/profile construction metadata is inconsistent"
        )
    return SimpleNamespace(
        inputs=inputs, identity=identity, source=source, wheels=wheels, receipt=receipt
    )


def _construction_inputs(
    _installer: ModuleType,
    slot: Path,
    record: dict[str, Any],
    runtime: ModuleType,
    engine: ModuleType,
    deadline: float,
) -> SimpleNamespace:
    association = _installer._construction_metadata(record, runtime, engine, deadline)
    inputs, source = (association.inputs, association.source)
    wheels = _installer.replace(association.wheels, generation=slot.name)
    receipt = association.receipt
    source_bytes = _installer._read(slot / "runtime-source.json")
    declaration = _installer._read(slot / "application.json", limit=4 << 20)
    if (
        source_bytes is None
        or _installer.json.loads(source_bytes) != source
        or declaration is None
        or (_installer.json.loads(declaration) != record)
        or (
            _installer.re.fullmatch(
                _installer.re.escape(inputs["runtime_version"])
                + "(?:[.+]repair\\.[0-9a-f]{32})?",
                slot.name,
            )
            is None
        )
    ):
        raise ValueError(
            "application construction inputs differ from their Source identity"
        )
    origins = {
        name: engine._file_uri(slot / "wheels" / filename)
        for name, _version, filename, _digest in wheels.distributions
    }
    proof = {
        "schema": "installer-engine.frozen-input-origins",
        "version": 2,
        "packages": origins,
        "wheel_inputs": receipt,
        "local_packages": inputs["local_packages"],
        **({"extras": inputs["extras"]} if inputs["extras"] else {}),
    }
    return SimpleNamespace(
        inputs=inputs,
        identity=association.identity,
        source=source,
        source_bytes=source_bytes,
        declaration=declaration,
        wheels=wheels,
        receipt=receipt,
        origins=origins,
        proof=proof,
    )


def _unchanged_application(
    _installer: ModuleType, slot: Path, association: SimpleNamespace, engine: ModuleType
) -> None:
    proof = _installer._read(slot / ".runtime-input-origins.json", limit=4 << 20)
    if (
        _installer._read(slot / "runtime-source.json") != association.source_bytes
        or _installer._read(slot / "application.json", limit=4 << 20)
        != association.declaration
        or proof is None
        or (_installer.json.loads(proof) != association.proof)
    ):
        raise RuntimeError("application construction inputs changed before publication")
    engine.verify_application_wheels(association.wheels, slot)


def _inspect_application_wheels(
    _installer: ModuleType,
    slot: Path,
    association: SimpleNamespace,
    engine: ModuleType,
    deadline: float,
) -> None:
    from installer_wheel_inputs import WheelLimits, inspect_archive

    wheels, receipt = (association.wheels, association.receipt)
    engine.verify_application_wheels(wheels, slot)
    rows = receipt["binding"].get("wheels")
    if (
        not isinstance(rows, list)
        or len(rows) != len(wheels.distributions)
        or {row["name"] for row in rows} != receipt["distributions"].keys()
    ):
        raise ValueError("application profile does not cover its complete wheel set")
    limits = WheelLimits(
        max_archive_bytes=128 << 20,
        max_expanded_bytes=1 << 30,
        max_member_bytes=512 << 20,
        max_metadata_bytes=16 << 20,
        max_members=100000,
        max_tags=10000,
        max_metadata_member_bytes=4 << 20,
    )
    expanded = 0
    for row in rows:
        with engine._open(slot / "wheels" / row["filename"]) as archive:
            facts = inspect_archive(archive, limits=limits, deadline=deadline)
        expanded += facts.unpacked_size
        if (
            expanded > limits.max_expanded_bytes
            or _installer.re.sub("[-_.]+", "-", facts.name).lower() != row["name"]
            or any(
                (
                    row[key] != receipt["distributions"][row["name"]][key]
                    for key in ("version", "filename", "sha256")
                )
            )
            or (facts.version != row["version"])
            or (facts.requires_python != row["requires_python"])
            or (list(facts.requires_dist) != row["requires_dist"])
            or (list(facts.provides_extras) != row["provides_extras"])
        ):
            raise ValueError(
                "application wheel metadata differs from its Source/profile"
            )


def qualify_installed_application(
    _installer: ModuleType,
    slot: Path,
    *,
    component: str,
    command: str,
    entry_point: str,
    deadline: float | None = None,
) -> InstalledApplicationSource:
    """Read selected completed Source/profile evidence without lifecycle effects.

    Use the existing isolated, no-bytecode startup context. This neither imports
    application code nor replaces installed health, root or provider admission.
    """
    if not _installer.sys.flags.isolated or not _installer.sys.dont_write_bytecode:
        raise ValueError(
            "application Source reading requires isolated no-bytecode Python"
        )
    if not slot.is_absolute():
        raise ValueError(
            "application Source reading requires an absolute physical slot"
        )
    slot = slot.resolve(strict=True)
    root = slot.parent.parent
    if slot.parent.name != "versions" or slot.name == "dev":
        raise ValueError("application Source reading requires a numbered version slot")
    for directory in (root, slot.parent, slot, slot / ".venv"):
        state = directory.lstat()
        if (
            not _installer.stat.S_ISDIR(state.st_mode)
            or directory.is_symlink()
            or getattr(state, "st_file_attributes", 0) & 1024
        ):
            raise ValueError("application Source slot must be an ordinary directory")
    record = _installer._application(slot)
    if record["name"] != command or record["module"] != entry_point:
        raise ValueError("application command differs from the expected entry point")
    runtime, engine = _installer._resources(slot)
    python = runtime.slot_python(root, slot.name)

    def normalized(path: Path | str) -> str:
        return cast(
            str,
            _installer.os.path.normcase(
                runtime._normalize_windows_extended_path(
                    _installer.os.path.abspath(path)
                ),
            ),
        )

    if (
        python is None
        or normalized(_installer.sys.executable) != normalized(python)
        or (not Path(_installer.sys.prefix).samefile(slot / ".venv"))
    ):
        raise ValueError(
            "application Source interpreter differs from the physical slot"
        )
    deadline = deadline if deadline is not None else _installer.time.monotonic() + 600
    association = _installer._construction_inputs(
        slot, record, runtime, engine, deadline
    )
    if association.identity.plugin != component:
        raise ValueError(
            "application Source component differs from the expected component"
        )
    selected = (slot.name + "\n").encode("utf-8")
    if _installer._read(
        root / "current-version"
    ) != selected or not runtime.is_complete(
        root, slot.name, expect_hash=association.inputs["payload_hash"]
    ):
        raise ValueError("application Source slot is not selected and complete")
    _installer._inspect_application_wheels(slot, association, engine, deadline)
    runtime.require_immutable_environment(slot)
    _installer._unchanged_application(slot, association, engine)
    if _installer._read(
        root / "current-version"
    ) != selected or not runtime.is_complete(
        root, slot.name, expect_hash=association.inputs["payload_hash"]
    ):
        raise RuntimeError("application Source selection changed while reading")
    return cast(
        "InstalledApplicationSource",
        _installer.InstalledApplicationSource(
            slot,
            root,
            python,
            association.identity,
            association.inputs["payload_hash"],
            association.receipt["lock_id"],
        ),
    )


@contextmanager
def _qualified(
    _installer: ModuleType,
    slot: Path,
    record: dict[str, Any],
    *,
    verify_on_exit: bool = True,
) -> Iterator[tuple[ModuleType, Callable[[], None]]]:
    runtime_path = slot / "runtime"
    runtime, engine = _installer._resources(slot)
    started = _installer.time.monotonic()
    deadline = started + _installer._QUALIFICATION_TIMEOUT
    association = _installer._construction_inputs(
        slot, record, runtime, engine, deadline
    )
    inputs, source = (association.inputs, association.source)
    wheels, origins, proof = (
        association.wheels,
        association.origins,
        association.proof,
    )

    def unchanged() -> None:
        _installer._unchanged_application(slot, association, engine)

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        command = list(argv)
        executable = command[0]
        cwd: Path | str = slot
        if _installer.os.name == "nt":
            # CreateProcess rejects long cwd values, including extended paths.
            # Enter the physical slot inside the same isolated interpreter.
            code = command.index("-c") + 1
            command[code] = (
                f"import os; os.chdir({engine._native_retained_path(slot)!r})\n"
                + command[code]
            )
            executable, cwd = (engine._native_retained_path(executable), slot.anchor)
        result = _installer.subprocess.run(
            command,
            executable=executable,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(0.1, min(180, deadline - _installer.time.monotonic())),
            creationflags=getattr(_installer.subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            print(result.stderr, file=_installer.sys.stderr, end="")
            raise _installer.subprocess.CalledProcessError(
                result.returncode, argv, output=result.stdout, stderr=result.stderr
            )
        return cast(subprocess.CompletedProcess[str], result)

    root = slot.parent.parent
    with _installer.ExitStack() as retention:
        with runtime.construction_lease(root, slot.name, timeout=180):
            engine._refuse(
                runtime.check_admission(
                    root, slot.name, payload_hash=inputs["payload_hash"]
                ),
                slot.name,
            )
            complete = runtime.is_complete(
                root, slot.name, expect_hash=inputs["payload_hash"]
            )
            _installer._inspect_application_wheels(slot, association, engine, deadline)
            proof_path = slot / ".runtime-input-origins.json"
            previous = _installer._read(proof_path, limit=4 << 20)
            if previous is None and (not complete):
                _installer._write(
                    proof_path,
                    _installer.json.dumps(proof, indent=2).encode("utf-8") + b"\n",
                )
            elif previous is None or _installer.json.loads(previous) != proof:
                raise RuntimeError(
                    "application installed origins differ from its construction inputs"
                )
            bootstrap = slot / ".runtime-bootstrap"
            bootstrap.mkdir(exist_ok=True)
            for name in (
                "runtime_launcher.py",
                "versioned_runtime.py",
                "versioned_runtime_leases.py",
                "versioned_runtime_source.py",
                "versioned_runtime_construction.py",
                "versioned_runtime_identity.py",
                "versioned_runtime_fingerprint.py",
                "versioned_runtime_publication.py",
                "versioned_runtime_cache.py",
                "daemon_instance_runtime.pyz",
            ):
                content = _installer._read(runtime_path / name, limit=1 << 20)
                previous = _installer._read(bootstrap / name, limit=1 << 20)
                if content is None or (previous is not None and previous != content):
                    raise RuntimeError(
                        "application retention bootstrap differs from its runtime"
                    )
                if previous is None:
                    if complete:
                        raise RuntimeError(
                            "completed application is missing its retention bootstrap"
                        )
                    _installer._write(bootstrap / name, content)
            module = record["module"].partition(":")[0]
            health_callable = None
            if source["plugin"] in {"worktree-manager", "agent-worktrees"}:
                package = (
                    "worktree_manager"
                    if source["plugin"] == "worktree-manager"
                    else "agent_worktrees"
                )
                module, health_callable = (
                    package + ".runtime_candidate",
                    "verify_runtime_candidate",
                )
                if not complete:
                    run(
                        [
                            _installer.sys.executable,
                            "-I",
                            "-X",
                            "utf8",
                            "-B",
                            "-c",
                            f"from {module} import prepare_runtime_candidate; "
                            + (
                                "prepare_runtime_candidate({})"
                                if package == "worktree_manager"
                                else "prepare_runtime_candidate()"
                            ),
                        ]
                    )
            engine.verify_installed_inputs(
                slot,
                Path(_installer.sys.executable),
                module,
                origins=origins,
                extras=tuple(inputs["extras"]),
                local_packages=tuple(inputs["local_packages"]),
                excluded_packages=inputs["excluded_packages"],
                wheel_inputs=wheels,
                health_callable=health_callable,
                run=run,
            )
            unchanged()
            if not complete:
                runtime._runtime_component(
                    "versioned_runtime_publication"
                ).prepare_retained_generation(slot)
                artifacts = _installer._artifacts(slot)
                artifacts.seal_inventory(
                    slot,
                    {
                        "component": source["plugin"],
                        "payload_hash": inputs["payload_hash"],
                        "profile_lock_id": association.receipt["lock_id"],
                    },
                    deadline=deadline,
                )
                runtime.mark_complete(
                    root, slot.name, payload_hash=inputs["payload_hash"]
                )
            retention.enter_context(
                runtime.retention_lease(
                    root, slot.name, payload_hash=inputs["payload_hash"], timeout=180
                )
            )
        if "callback_timeout_seconds" in record:
            association.wheels = _installer.replace(
                association.wheels,
                deadline=started + _installer.application_install_timeout(record),
            )
        yield (runtime, unchanged)
        if verify_on_exit:
            unchanged()


def publication_directory(
    _installer: ModuleType, directory: Path, *, root: Path | None = None
) -> Path:
    """Validate an explicit publication selection without creating its ancestry."""
    if (
        not directory.is_absolute()
        or ".." in directory.parts
        or any((char in str(directory) for char in "\r\n\x00"))
    ):
        raise ValueError(
            "application command directory must be an absolute ordinary path"
        )
    for parent in reversed((directory, *directory.parents)):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if (
            not _installer.stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 1024
        ):
            raise ValueError(
                "application command directory must have ordinary directory ancestry"
            )
    if root is not None and directory.resolve().is_relative_to(
        (root / "versions").resolve()
    ):
        raise ValueError(
            "application command directory cannot overlap runtime version slots"
        )
    return directory


def require_publication_selection(_installer: ModuleType, helper: Path) -> None:
    """Read support from the selected helper, without executing a preflight."""
    try:
        content = _installer._read(helper, limit=1 << 20)
        body = _installer.ast.parse(content if content is not None else b"").body
        declarations = [
            node.value
            for node in body
            if isinstance(node, _installer.ast.Assign)
            and any(
                (
                    isinstance(target, _installer.ast.Name)
                    and target.id == "APPLICATION_INSTALL_CAPABILITIES"
                    for target in node.targets
                )
            )
        ]
        capabilities = (
            _installer.ast.literal_eval(declarations[0])
            if len(declarations) == 1
            else ()
        )
        supported = (
            isinstance(capabilities, (tuple, list))
            and all((isinstance(value, str) for value in capabilities))
            and ("global-bin/v1" in capabilities)
        )
    except (SyntaxError, ValueError, TypeError) as error:
        raise ValueError(
            "selected application helper lacks global-bin/v1 support"
        ) from error
    if not supported:
        raise ValueError("selected application helper lacks global-bin/v1 support")


def _run(
    _installer: ModuleType,
    record: dict[str, Any],
    command: list[str],
    root: Path,
    slot: Path,
    *,
    timeout: float = 180,
) -> None:
    if not command:
        return
    environment = {**_installer.os.environ, record["root_environment"]: str(root)}
    target = (
        [str(slot / (record["name"] + ".py"))]
        if ":" in record["module"]
        else ["-m", record["module"]]
    )
    python = _installer.sys.executable
    cwd: str | None = None
    if _installer.os.name == "nt":
        _, engine = _installer._resources(slot)
        python, cwd = (engine._native_retained_path(python), slot.anchor)
        command_directory = root if record["name"] == "worktree-manager" else slot
        target = [
            "-c",
            f"import os,runpy,sys\nos.chdir({engine._native_retained_path(command_directory)!r})\ntarget=sys.argv.pop(1)\nrunpy.run_module(sys.argv.pop(1), run_name='__main__', alter_sys=True) if target == '-m' else runpy.run_path(target, run_name='__main__')",
            *target,
        ]
    result = _installer.subprocess.run(
        [python, "-I", "-X", "utf8", "-B", *target, *command],
        executable=python,
        cwd=cwd,
        env=environment,
        timeout=timeout,
        stdout=_installer.sys.stdout,
        stderr=_installer.sys.stderr,
        creationflags=getattr(_installer.subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError(
            f"application command failed with exit code {result.returncode}"
        )


def _global_replacements(
    _installer: ModuleType, slot: Path, record: dict[str, Any], global_bin: Path | None
) -> dict[Path, bytes]:
    root = slot.parent.parent
    launchers = _installer._load(
        slot / "runtime_application_binstubs.py", "_application_binstubs"
    )
    globals_text: dict[str, str] = {}
    for name in record["commands"]:
        globals_text.update(
            launchers.global_binstubs(
                name, record["root_environment"], record["default_directory"], root=root
            )
        )
    directory = global_bin or Path.home() / ".local" / "bin"
    return {
        directory / name: text.encode("utf-8") for name, text in globals_text.items()
    }


def _publication_current(
    _installer: ModuleType,
    slot: Path,
    record: dict[str, Any],
    *,
    global_bin: Path | None = None,
) -> bool:
    """Verify publication only; the caller retains and health-gates the candidate."""
    root = slot.parent.parent
    runtime, _engine = _installer._resources(slot)
    replacements = _installer._global_replacements(slot, record, global_bin)
    publication = runtime._runtime_component("versioned_runtime_publication")
    selectors = tuple(
        ((slot.name + ending).encode("utf-8") for ending in ("\n", "\r\n"))
    )
    success = (
        _installer.json.dumps(
            {"state": "installed", "version": slot.name}, sort_keys=True
        ).encode("utf-8")
        + b"\n"
    )
    with (
        runtime._publication_lease(root),
        publication.target_leases(root, list(replacements)),
    ):
        return (
            all(
                (
                    _installer._read(root / name) in selectors
                    for name in ("current-version", "last-known-good")
                )
            )
            and _installer._read(slot / "install-success.json") == success
            and runtime.is_complete(
                root, slot.name, expect_hash=record["construction"]["payload_hash"]
            )
            and all(
                (
                    _installer._read(path) == content
                    for path, content in replacements.items()
                )
            )
        )
