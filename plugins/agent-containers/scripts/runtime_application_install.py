"""Slot-local install entry: stage is environment-only; regular install activates."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import NamedTuple


class ApplicationStartupUncertain(RuntimeError):
    """Startup was invoked; only its application owner can reconcile its effects."""

    def __init__(self, slot: Path):
        self.slot = slot
        super().__init__(
            "application startup outcome is unknown; selection and launchers retained "
            "for application-owned reconciliation"
        )


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"application runtime resource is unavailable: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _read(path: Path, *, limit: int = 65536) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > limit:
        raise RuntimeError(f"application publication target is not an ordinary file: {path}")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise RuntimeError(f"application publication target exceeds its byte bound: {path}")
    return content


def _write(path: Path, content: bytes, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if executable or path.suffix == ".sh" or not path.suffix:
            temporary.chmod(0o700)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _materialize_interpreter(slot: Path) -> None:
    if os.name == "nt":
        return
    environment = slot / ".venv"
    directory = environment / "bin"
    if environment.is_symlink() or directory.is_symlink():
        raise RuntimeError("application interpreter directory must not be indirect")
    aliases = ("python", f"python{sys.version_info.major}",
               f"python{sys.version_info.major}.{sys.version_info.minor}")
    links = [directory / name for name in aliases if (directory / name).is_symlink()]
    if not links:
        return
    import fcntl

    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("application interpreter preparation is already in progress") from error
        links = [directory / name for name in aliases if (directory / name).is_symlink()]
        if not links:
            return
        if (slot / ".install-complete.json").exists():
            raise RuntimeError("completed application contains a linked interpreter")
        source = Path(sys.executable).resolve(strict=True)
        if any(not path.resolve(strict=True).samefile(source) for path in links):
            raise RuntimeError("application interpreter link differs from its running interpreter")
        content = _read(source, limit=128 << 20)
        if content is None:
            raise RuntimeError("application running interpreter is unavailable")
        for path in links:
            _write(path, content, executable=True)
    finally:
        os.close(descriptor)


def _application(slot: Path) -> dict:
    raw = _read(slot / "application.json", limit=4 << 20)
    if raw is None:
        raise RuntimeError("the copied application has no install declaration")
    return _validate_application(json.loads(raw))


def _validate_application(record: dict) -> dict:
    fields = {
        "name", "module", "commands", "root_environment", "default_directory",
        "setup", "start", "construction",
    }
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError("invalid application install declaration")
    if (
        not isinstance(record["module"], str)
        or not re.fullmatch(
            r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?::[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)?",
            record["module"],
        )
        or any(
            not isinstance(record[key], list)
            or any(not isinstance(argument, str) or "\0" in argument for argument in record[key])
            for key in ("setup", "start")
        )
        or not isinstance(record["commands"], list) or not record["commands"]
        or any(not isinstance(name, str) for name in record["commands"])
        or len(set(record["commands"])) != len(record["commands"])
        or record["name"] not in record["commands"]
    ):
        raise ValueError("invalid application command declaration")
    return record


class InstalledApplicationSource(NamedTuple):
    """Source/profile association, not application health or root admission."""

    slot: Path
    root: Path
    python: Path
    identity: object
    payload_hash: str
    profile_lock_id: str


def _resources(slot: Path):
    runtime_path = slot / "runtime"
    runtime = _load(runtime_path / "versioned_runtime.py", "versioned_runtime")
    previous = sys.path[:]
    try:
        sys.path.insert(0, str(runtime_path))
        engine = _load(runtime_path / "installer_engine.py", "_application_engine")
    finally:
        sys.path[:] = previous
    return runtime, engine


def _construction_inputs(slot: Path, record: dict, runtime, engine, deadline: float):
    inputs = record["construction"]
    fields = {
        "identity", "runtime_version", "payload_hash", "source_hash", "wheel_inputs",
        "extras", "local_packages", "excluded_packages",
    }
    if not isinstance(inputs, dict) or set(inputs) != fields:
        raise ValueError("regular install requires the actual Source/profile construction inputs")
    source_bytes = _read(slot / "runtime-source.json")
    declaration = _read(slot / "application.json", limit=4 << 20)
    if not isinstance(inputs["identity"], dict):
        raise ValueError("application construction identity must be an object")
    source = inputs["identity"].get("runtime_source")
    source_owner = runtime._runtime_component("versioned_runtime_source")
    identity = source_owner.parse_source_identity(source)
    if (
        source_bytes is None or json.loads(source_bytes) != source
        or declaration is None or json.loads(declaration) != record
        or inputs["identity"].get("runtime_version") != source["runtime_version"]
        or any(
            not isinstance(inputs[key], str)
            or re.fullmatch(r"[0-9a-f]{64}", inputs[key]) is None
            for key in ("source_hash", "payload_hash")
        )
        or any(
            not isinstance(inputs[key], list)
            or len(set(inputs[key])) != len(inputs[key])
            or any(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None for name in inputs[key])
            for key in ("extras", "local_packages", "excluded_packages")
        )
    ):
        raise ValueError("application construction inputs differ from their Source identity")
    wheels = engine.WheelReuseInputs(
        slot.name, json.dumps(inputs["wheel_inputs"], sort_keys=True, separators=(",", ":")),
        deadline,
    )
    wheels.verify()
    receipt = wheels.receipt()
    generation = source["runtime_version"] + ".profile." + receipt["lock_id"][7:23]
    if (
        inputs["runtime_version"] != generation
        or re.fullmatch(re.escape(generation) + r"(?:[.+]repair\.[0-9a-f]{32})?", slot.name) is None
        or engine._wheel_construction_hash(inputs["source_hash"], wheels) != inputs["payload_hash"]
        or receipt["binding"].get("release") != {
            "identity": "source:" + source["source_commit"],
            "sha256": "sha256:" + source["assembly_hash"],
        }
        or receipt["binding"].get("component") != source["plugin"]
        or not set(inputs["local_packages"]) <= receipt["distributions"].keys()
        or set(inputs["excluded_packages"]) & receipt["distributions"].keys()
    ):
        raise ValueError("application source/profile selection differs from its version slot")
    origins = {
        name: (slot / "wheels" / filename).as_uri()
        for name, _version, filename, _digest in wheels.distributions
    }
    proof = {
        "schema": "installer-engine.frozen-input-origins", "version": 2,
        "packages": origins, "wheel_inputs": receipt,
        "local_packages": inputs["local_packages"],
        **({"extras": inputs["extras"]} if inputs["extras"] else {}),
    }
    return SimpleNamespace(
        inputs=inputs, identity=identity, source=source, source_bytes=source_bytes,
        declaration=declaration, wheels=wheels, receipt=receipt, origins=origins, proof=proof,
    )


def _unchanged_application(slot: Path, association, engine):
    if (
        _read(slot / "runtime-source.json") != association.source_bytes
        or _read(slot / "application.json", limit=4 << 20) != association.declaration
        or json.loads(_read(slot / ".runtime-input-origins.json", limit=4 << 20)) != association.proof
    ):
        raise RuntimeError("application construction inputs changed before publication")
    engine.verify_application_wheels(association.wheels, slot)


def _inspect_application_wheels(slot: Path, association, engine, deadline: float):
    from installer_wheel_inputs import WheelLimits, inspect_archive

    wheels, receipt = association.wheels, association.receipt
    engine.verify_application_wheels(wheels, slot)
    rows = receipt["binding"].get("wheels")
    if (
        not isinstance(rows, list) or len(rows) != len(wheels.distributions)
        or {row["name"] for row in rows} != receipt["distributions"].keys()
    ):
        raise ValueError("application profile does not cover its complete wheel set")
    limits = WheelLimits(
        max_archive_bytes=128 << 20, max_expanded_bytes=1 << 30,
        max_member_bytes=512 << 20, max_metadata_bytes=16 << 20,
        max_members=100_000, max_tags=10_000, max_metadata_member_bytes=4 << 20,
    )
    expanded = 0
    for row in rows:
        with engine._open(slot / "wheels" / row["filename"]) as archive:
            facts = inspect_archive(archive, limits=limits, deadline=deadline)
        expanded += facts.unpacked_size
        if (
            expanded > limits.max_expanded_bytes
            or re.sub(r"[-_.]+", "-", facts.name).lower() != row["name"]
            or any(row[key] != receipt["distributions"][row["name"]][key]
                   for key in ("version", "filename", "sha256"))
            or facts.version != row["version"]
            or facts.requires_python != row["requires_python"]
            or list(facts.requires_dist) != row["requires_dist"]
            or list(facts.provides_extras) != row["provides_extras"]
        ):
            raise ValueError("application wheel metadata differs from its Source/profile")


def qualify_installed_application(
    slot: Path, *, component: str, command: str, entry_point: str,
    deadline: float | None = None,
) -> InstalledApplicationSource:
    """Read selected completed Source/profile evidence without lifecycle effects.

    Use the existing isolated, no-bytecode startup context. This neither imports
    application code nor replaces installed health, root or provider admission.
    """
    if not sys.flags.isolated or not sys.dont_write_bytecode:
        raise ValueError("application Source reading requires isolated no-bytecode Python")
    if not slot.is_absolute():
        raise ValueError("application Source reading requires an absolute physical slot")
    slot = slot.resolve(strict=True)
    root = slot.parent.parent
    if slot.parent.name != "versions" or slot.name == "dev":
        raise ValueError("application Source reading requires a numbered version slot")
    for directory in (root, slot.parent, slot, slot / ".venv"):
        state = directory.lstat()
        if (
            not stat.S_ISDIR(state.st_mode) or directory.is_symlink()
            or getattr(state, "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("application Source slot must be an ordinary directory")
    record = _application(slot)
    if record["name"] != command or record["module"] != entry_point:
        raise ValueError("application command differs from the expected entry point")
    runtime, engine = _resources(slot)
    python = runtime.slot_python(root, slot.name)

    def normalized(path):
        return os.path.normcase(runtime._normalize_windows_extended_path(os.path.abspath(path)))

    if (
        python is None or normalized(sys.executable) != normalized(python)
        or not Path(sys.prefix).samefile(slot / ".venv")
    ):
        raise ValueError("application Source interpreter differs from the physical slot")
    deadline = deadline if deadline is not None else time.monotonic() + 600
    association = _construction_inputs(slot, record, runtime, engine, deadline)
    if association.identity.plugin != component:
        raise ValueError("application Source component differs from the expected component")
    selected = (slot.name + "\n").encode("utf-8")
    if (
        _read(root / "current-version") != selected
        or not runtime.is_complete(root, slot.name, expect_hash=association.inputs["payload_hash"])
    ):
        raise ValueError("application Source slot is not selected and complete")
    _inspect_application_wheels(slot, association, engine, deadline)
    runtime.require_immutable_environment(slot)
    _unchanged_application(slot, association, engine)
    if (
        _read(root / "current-version") != selected
        or not runtime.is_complete(root, slot.name, expect_hash=association.inputs["payload_hash"])
    ):
        raise RuntimeError("application Source selection changed while reading")
    return InstalledApplicationSource(
        slot, root, python, association.identity,
        association.inputs["payload_hash"], association.receipt["lock_id"],
    )


@contextmanager
def _qualified(slot: Path, record: dict, *, verify_on_exit: bool = True):
    runtime_path = slot / "runtime"
    runtime, engine = _resources(slot)
    deadline = time.monotonic() + 600
    association = _construction_inputs(slot, record, runtime, engine, deadline)
    inputs, source = association.inputs, association.source
    wheels, origins, proof = association.wheels, association.origins, association.proof

    def unchanged():
        _unchanged_application(slot, association, engine)

    def run(argv):
        command = list(argv)
        executable, cwd = command[0], slot
        if os.name == "nt":
            # CreateProcess rejects long cwd values, including extended paths.
            # Enter the physical slot inside the same isolated probe interpreter.
            code = command.index("-c") + 1
            command[code] = (
                f"import os; os.chdir({engine._native_retained_path(slot)!r})\n"
                + command[code]
            )
            executable, cwd = engine._native_retained_path(executable), slot.anchor
        result = subprocess.run(
            command, executable=executable, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=max(0.1, min(180, deadline - time.monotonic())),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode:
            print(result.stderr, file=sys.stderr, end="")
            raise subprocess.CalledProcessError(
                result.returncode, argv, output=result.stdout, stderr=result.stderr,
            )
        return result

    root = slot.parent.parent
    with ExitStack() as retention:
        with runtime.construction_lease(root, slot.name, timeout=180):
            engine._refuse(
                runtime.check_admission(root, slot.name, payload_hash=inputs["payload_hash"]), slot.name,
            )
            complete = runtime.is_complete(root, slot.name, expect_hash=inputs["payload_hash"])
            _inspect_application_wheels(slot, association, engine, deadline)
            proof_path = slot / ".runtime-input-origins.json"
            previous = _read(proof_path, limit=4 << 20)
            if previous is None and not complete:
                _write(proof_path, json.dumps(proof, indent=2).encode("utf-8") + b"\n")
            elif previous is None or json.loads(previous) != proof:
                raise RuntimeError("application installed origins differ from its construction inputs")
            bootstrap = slot / ".runtime-bootstrap"
            bootstrap.mkdir(exist_ok=True)
            for name in (
                "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
                "versioned_runtime_source.py", "versioned_runtime_construction.py",
                "versioned_runtime_publication.py",
                "versioned_runtime_cache.py",
                "daemon_instance_runtime.pyz",
            ):
                content = _read(runtime_path / name, limit=1 << 20)
                previous = _read(bootstrap / name, limit=1 << 20)
                if content is None or previous is not None and previous != content:
                    raise RuntimeError("application retention bootstrap differs from its runtime")
                if previous is None:
                    if complete:
                        raise RuntimeError("completed application is missing its retention bootstrap")
                    _write(bootstrap / name, content)
            module = record["module"].partition(":")[0]
            health_callable = None
            if source["plugin"] in {"worktree-manager", "agent-worktrees"}:
                package = "worktree_manager" if source["plugin"] == "worktree-manager" else "agent_worktrees"
                module, health_callable = package + ".runtime_candidate", "verify_runtime_candidate"
                if not complete:
                    run([
                        sys.executable, "-I", "-X", "utf8", "-B", "-c",
                        f"from {module} import prepare_runtime_candidate; "
                        + ("prepare_runtime_candidate({})" if package == "worktree_manager"
                           else "prepare_runtime_candidate()"),
                    ])
            engine.verify_installed_inputs(
                slot, Path(sys.executable), module, origins=origins,
                extras=tuple(inputs["extras"]), local_packages=tuple(inputs["local_packages"]),
                excluded_packages=inputs["excluded_packages"], wheel_inputs=wheels,
                health_callable=health_callable, run=run,
            )
            unchanged()
            if not complete:
                runtime.mark_complete(root, slot.name, payload_hash=inputs["payload_hash"])
            retention.enter_context(runtime.retention_lease(
                root, slot.name, payload_hash=inputs["payload_hash"], timeout=180,
            ))
        yield runtime, unchanged
        if verify_on_exit:
            unchanged()


def _run(record: dict, command: list[str], root: Path, slot: Path) -> None:
    if not command:
        return
    environment = {**os.environ, record["root_environment"]: str(root)}
    target = (
        [str(slot / (record["name"] + ".py"))] if ":" in record["module"]
        else ["-m", record["module"]]
    )
    python, cwd = sys.executable, None
    if os.name == "nt":
        _, engine = _resources(slot)
        python, cwd = engine._native_retained_path(python), slot.anchor
        target = [
            "-c", "import os,runpy,sys\n"
            f"os.chdir({engine._native_retained_path(slot)!r})\n"
            "target=sys.argv.pop(1)\n"
            "runpy.run_module(sys.argv.pop(1), run_name='__main__', alter_sys=True) "
            "if target == '-m' else runpy.run_path(target, run_name='__main__')",
            *target,
        ]
    result = subprocess.run(
        [python, "-I", "-X", "utf8", "-B", *target, *command],
        executable=python, cwd=cwd, env=environment, timeout=180,
        stdout=sys.stdout, stderr=sys.stderr,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise RuntimeError(f"application command failed with exit code {result.returncode}")


def _global_replacements(slot: Path, record: dict, global_bin: Path | None) -> dict[Path, bytes]:
    root = slot.parent.parent
    launchers = _load(slot / "runtime_application_binstubs.py", "_application_binstubs")
    globals_text = {}
    for name in record["commands"]:
        globals_text.update(launchers.global_binstubs(
            name, record["root_environment"], record["default_directory"], root=root,
        ))
    directory = global_bin or Path.home() / ".local" / "bin"
    return {directory / name: text.encode("utf-8") for name, text in globals_text.items()}


def _publication_current(slot: Path, record: dict, *, global_bin: Path | None = None) -> bool:
    """Verify publication only; the caller retains and health-gates the candidate."""
    root = slot.parent.parent
    runtime, _engine = _resources(slot)
    replacements = _global_replacements(slot, record, global_bin)
    publication = runtime._runtime_component("versioned_runtime_publication")
    selectors = tuple((slot.name + ending).encode("utf-8") for ending in ("\n", "\r\n"))
    success = json.dumps({
        "state": "installed", "version": slot.name,
    }, sort_keys=True).encode("utf-8") + b"\n"
    with runtime._publication_lease(root), publication.target_leases(root, list(replacements)):
        return (
            all(_read(root / name) in selectors for name in ("current-version", "last-known-good"))
            and _read(slot / "install-success.json") == success
            and runtime.is_complete(root, slot.name, expect_hash=record["construction"]["payload_hash"])
            and all(_read(path) == content for path, content in replacements.items())
        )


def install(
    slot: Path, *, stage: bool, global_bin: Path | None = None,
    arguments: tuple[str, ...] = (),
) -> dict:
    """UV has synchronized the environment before this entry runs."""
    slot = slot.resolve()
    if (
        not (slot / ".venv" / "pyvenv.cfg").is_file()
        or not Path(sys.prefix).samefile(slot / ".venv")
    ):
            raise RuntimeError(
                "application install must run through its slot-local uv binstub "
                f"(prefix={Path(sys.prefix).resolve()}, expected={(slot / '.venv').resolve()}, "
                f"config={(slot / '.venv' / 'pyvenv.cfg').is_file()})"
            )
    _materialize_interpreter(slot)
    if stage:
        return {"state": "staged", "slot": str(slot)}
    if slot.parent.name != "versions" or not re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", slot.name):
        raise ValueError("regular install requires an owned version-slot layout")
    root = slot.parent.parent
    record = _application(slot)
    if record["name"] == "worktree-manager":
        if arguments:
            raise ValueError("Manager installation does not accept application configuration arguments")
        if global_bin is not None and global_bin != Path.home() / ".local" / "bin":
            raise ValueError("Manager installation requires its owned global command directory")
        _run(record, ["self-install", "--apply"], root, slot)
        return {"state": "installed", "slot": str(slot)}
    global_bin = global_bin or Path.home() / ".local" / "bin"
    replacements = _global_replacements(slot, record, global_bin)
    previous = {path: _read(path) for path in replacements}
    selectors = (root / "current-version", root / "last-known-good")
    if arguments and not (record["setup"] or record["start"]):
        raise ValueError("application installation has no configurable setup or startup action")
    setup = [*record["setup"], *arguments] if record["setup"] else []
    start = [*record["start"], *arguments] if record["start"] else []
    with _qualified(
        slot, record, verify_on_exit=False,
    ) as (runtime, unchanged):
        if (
            not arguments and not setup and not start
            and _publication_current(slot, record, global_bin=global_bin)
        ):
            unchanged()
            return {"state": "installed", "slot": str(slot)}
        _run(record, setup, root, slot)
        unchanged()
        if not runtime.is_complete(root, slot.name, expect_hash=record["construction"]["payload_hash"]):
            raise RuntimeError("application publication requires canonical completed construction")
        selector_content = (slot.name + "\n").encode("utf-8")
        selector_forms = (selector_content, (slot.name + "\r\n").encode("utf-8"))
        success_content = json.dumps({
            "state": "installed", "version": slot.name,
        }, sort_keys=True).encode("utf-8") + b"\n"
        publication = runtime._runtime_component("versioned_runtime_publication")
        with runtime._publication_lease(root), publication.target_leases(root, list(replacements)):
            selector_before = {path: _read(path) for path in selectors}
            written = {}
            publishing = False
            try:
                for path, content in replacements.items():
                    if _read(path) != previous[path]:
                        raise RuntimeError(f"application launcher changed before publication: {path}")
                    if previous[path] != content:
                        _write(path, content)
                        written[path] = content
                publishing = True
                publication.publish_selectors(
                    root, slot.name, None, lambda home, version: None,
                )
                if not start:
                    _write(root / "last-known-good", selector_content)
                    _write(slot / "install-success.json", success_content)
            except BaseException as error:
                if publishing and any(
                    _read(path) not in (selector_before[path], selector_content)
                    for path in selectors
                ):
                    raise RuntimeError("application selector changed during recovery") from error
                for path, content in written.items():
                    if _read(path) != content:
                        raise RuntimeError(f"application publication changed during recovery: {path}")
                    if previous[path] is None:
                        path.unlink()
                    else:
                        _write(path, previous[path])
                if publishing:
                    for path in selectors:
                        content = _read(path)
                        if content == selector_before[path]:
                            continue
                        if content != selector_content:
                            raise RuntimeError(f"application selector changed during recovery: {path}")
                        if selector_before[path] is None:
                            path.unlink(missing_ok=True)
                        else:
                            _write(path, selector_before[path])
                raise
        if not start:
            return {"state": "installed", "slot": str(slot)}
        try:
            _run(record, start, root, slot)
            unchanged()
            with runtime._publication_lease(root), publication.target_leases(root, list(replacements)):
                if (
                    _read(selectors[0]) not in selector_forms
                    or _read(selectors[1]) not in (selector_before[selectors[1]], *selector_forms)
                ):
                    raise RuntimeError("application selector changed before startup completion")
                if any(_read(path) != content for path, content in replacements.items()):
                    raise RuntimeError("application launcher changed before startup completion")
                _write(root / "last-known-good", selector_content)
                _write(slot / "install-success.json", success_content)
        except BaseException as error:
            uncertain = ApplicationStartupUncertain(slot)
            if isinstance(error, Exception):
                raise uncertain from error
            print(str(uncertain), file=sys.stderr)
            raise
    return {"state": "installed", "slot": str(slot)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--stage", action="store_true")
    parser.add_argument(
        "arguments", nargs=argparse.REMAINDER,
        help="application-owned setup/start arguments after -- (regular installation only)",
    )
    arguments = parser.parse_args(argv)
    forwarded = arguments.arguments
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    print(json.dumps(install(
        Path(__file__).parent, stage=arguments.stage, arguments=tuple(forwarded),
    )))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
