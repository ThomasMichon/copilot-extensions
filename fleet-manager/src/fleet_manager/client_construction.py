"""Offline construction and local health of an admitted client, never activation."""

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from email.parser import BytesParser
import hashlib
import importlib.util
from importlib import metadata
import io
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
import subprocess
import sys
import time
from types import ModuleType
from typing import Any, cast
import uuid
import zipfile

from . import artifact_staging, client_admission
from . import planning as _planning
from .planning import MAX_BYTES, PlanError
from .private_state import create_private_file, private_directory
from .release import validate_release
from .release_selection import ReleaseSelection

NAMESPACE = "client-runtime"
OWNER = "client-owner.json"
COMPLETION = "client-constructed.json"
BOOTSTRAP = "runtime-bootstrap"
REMOVING = "client-runtime-removing.json"
canonical = cast(Callable[[object], bytes], _planning.canonical)
decode = cast(Callable[[bytes], Any], _planning.decode)
digest = cast(Callable[[object], str], _planning.digest)
integer = cast(Callable[..., None], _planning.integer)
record = cast(Callable[..., None], _planning.record)
_Checkpoint = Callable[[], tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]
_Custody = Callable[[], AbstractContextManager[tuple[Path, Callable[[], None], _Checkpoint]]]


def _not_removing(namespace: Path) -> None:
    if os.path.lexists(namespace / REMOVING):
        raise PlanError("client runtime removal is unsettled; exact owned removal retry is required")


def _write_exclusive(path: Path, value: object) -> None:
    descriptor = create_private_file(path)
    try:
        content = canonical(value)
        while content:
            written = os.write(descriptor, content)
            if written <= 0:
                raise OSError("client journal write made no progress")
            content = content[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write(path: Path, value: object) -> None:
    # Abandoned attempts remain evidence, never a shared retry prerequisite.
    temporary = path.with_name("." + path.name + ".write-" + uuid.uuid4().hex)
    _write_exclusive(temporary, value)
    os.replace(temporary, path)
    cast(Callable[[Path], None], client_admission._sync_directory)(path.parent)


def _read(path: Path) -> Any:
    cast(Callable[[Path], os.stat_result], client_admission._ordinary_file)(path)
    return decode(artifact_staging._bytes(path, MAX_BYTES))


def _namespace(root: Path, identity: dict[str, Any]) -> Path:
    expected = {"schema": "fleet-manager.client-runtime-owner", "version": 1, "identity": identity}
    namespace = root / NAMESPACE
    if os.path.lexists(namespace):
        artifact_staging._directory(namespace)
        if _read(namespace / OWNER) != expected:
            raise PlanError("client runtime namespace has a foreign installation owner")
        if os.path.lexists(namespace / "versions"):
            artifact_staging._directory(namespace / "versions")
        return namespace
    temporary = root / ("." + NAMESPACE + "-initializing")
    if os.path.lexists(temporary):
        artifact_staging._directory(temporary)
        entries = list(temporary.iterdir())
        if any(entry.name not in {OWNER, OWNER + ".tmp"} for entry in entries):
            raise PlanError("client runtime initialization contains foreign state")
        for entry in entries:
            cast(Callable[[Path], os.stat_result], client_admission._ordinary_file)(entry)
        for entry in entries:
            entry.unlink()
        temporary.rmdir()
    private_directory(temporary)
    _write_exclusive(temporary / OWNER, expected)
    cast(Callable[[Path], None], client_admission._sync_directory)(temporary)
    os.rename(temporary, namespace)
    cast(Callable[[Path], None], client_admission._sync_directory)(root)
    return namespace


def _compatible(
    manifest: dict[str, Any], target: object, identity: dict[str, Any],
) -> dict[str, Any]:
    selected_target = decode(canonical(target))
    record(selected_target, "fleet-manager.client-target",
           {"authority", "installation", "revision", "components"})
    integer(selected_target["revision"], "client target revision", minimum=1)
    if (selected_target["authority"], selected_target["installation"]) != (
        identity["authority"], identity["installation"],
    ):
        raise PlanError("client target installation scope mismatch")
    components = selected_target["components"]
    if not isinstance(components, dict) or set(components) != set(manifest["roles"]["client"]):
        raise PlanError("client target must describe the exact selected component closure")
    for name, current in components.items():
        if not isinstance(current, dict) or set(current) != {"api", "data_schemas"}:
            raise PlanError("client target requires explicit API and retained/config data schemas")
        integer(current["api"], "target API", minimum=1, maximum=65535)
        schemas = current["data_schemas"]
        if not isinstance(schemas, list) or not 1 <= len(schemas) <= 32:
            raise PlanError("client target data schemas must be a bounded nonempty list")
        for value in schemas:
            integer(value, "target data schema", minimum=1, maximum=65535)
        declaration = manifest["components"][name]
        if (
            len(set(schemas)) != len(schemas)
            or not declaration["api"]["min"] <= current["api"] <= declaration["api"]["max"]
            or not set(schemas) <= set(declaration["data_schemas"])
        ):
            raise PlanError("selected client is incompatible with the target API or retained/config data")
    return cast(dict[str, Any], selected_target)


def _wheel(staged: artifact_staging.StagedArtifacts, snapshots: dict[str, bytes]) -> tuple[str, bytes]:
    # This first constructor supports the existing dependency-free portable client,
    # not source builds, hosting extras or a new dependency resolver.
    if len(snapshots) != 1:
        raise PlanError("client construction currently requires one portable fleet-manager wheel")
    component, content = next(iter(snapshots.items()))
    filename = staged.artifact_filenames[component]
    if not filename.startswith("fleet_manager-") or not filename.endswith("-py3-none-any.whl"):
        raise PlanError("client construction requires a portable fleet-manager wheel")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
            wheels = [name for name in names if name.endswith(".dist-info/WHEEL")]
            if len(metadata) != 1 or len(wheels) != 1:
                raise PlanError("client wheel requires unique distribution metadata")
            if any(archive.getinfo(name).file_size > MAX_BYTES for name in (*metadata, *wheels)):
                raise PlanError("client wheel metadata exceeds its byte bound")
            package = BytesParser().parsebytes(archive.read(metadata[0]))
            wheel = BytesParser().parsebytes(archive.read(wheels[0]))
            if (
                package.get_all("Name") != ["fleet-manager"]
                or wheel.get_all("Root-Is-Purelib") != ["true"]
                or wheel.get_all("Tag") != ["py3-none-any"]
            ):
                raise PlanError("client wheel is not the supported portable distribution")
    except (zipfile.BadZipFile, KeyError, RuntimeError):
        raise PlanError("client wheel metadata could not be validated") from None
    return filename, content


def _environment(slot: Path) -> dict[str, str]:
    home = slot / "health-home"
    home.mkdir(mode=0o700, exist_ok=True)
    temporary = home
    if os.name == "nt":
        # Native launcher/console startup can hang on a slot-length TEMP path.
        temporary = slot.parent.parent / "process-tmp"
        temporary.mkdir(mode=0o700, exist_ok=True)
        artifact_staging._directory(temporary)
    environment = {name: os.environ[name] for name in (
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL",
    ) if name in os.environ}
    environment.update({
        "PATH": str(Path(sys.executable).parent) + (
            os.pathsep + os.defpath if os.name != "nt" else ""
        ),
        "HOME": str(home), "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home), "XDG_DATA_HOME": str(home),
        "XDG_CACHE_HOME": str(home), "TEMP": str(temporary), "TMP": str(temporary),
        "TMPDIR": str(temporary), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1", "PIP_CONFIG_FILE": os.devnull,
    })
    return environment


def _process_resource_data(
    name: str, *, evidence: dict[str, Any] | None = None,
) -> tuple[Path, bytes]:
    if (
        not isinstance(name, str) or not name or any(value in name for value in ("\\", ":", "\0"))
        or PureWindowsPath(name).anchor or PurePosixPath(name).anchor
        or ".." in PurePosixPath(name).parts or PurePosixPath(name).as_posix() != name
    ):
        raise PlanError("client construction process resource name must be canonical and relative")
    if evidence is None:
        evidence = artifact_staging.diagnose_client()
    if name not in evidence["runtime_resource_sha256s"]:
        raise PlanError("client construction process resource is not RECORD-admitted")
    path = Path(__file__).absolute().parent / name
    content = artifact_staging._bytes(path, MAX_BYTES)
    if hashlib.sha256(content).hexdigest() != evidence["runtime_resource_sha256s"][name]:
        raise PlanError("client construction process resource changed after RECORD admission")
    return path, content


def _process_resource(name: str, *, evidence: dict[str, Any] | None = None) -> ModuleType:
    path, content = _process_resource_data(name, evidence=evidence)
    spec = importlib.util.spec_from_file_location("fleet_manager." + path.stem, path)
    if spec is None or spec.loader is None:
        raise PlanError("client construction process resource could not be loaded")
    module = importlib.util.module_from_spec(spec)
    exec(compile(content, str(path), "exec"), module.__dict__)
    setattr(module, "_retained_source_bytes", content)
    return module


@contextmanager
def _procutil_resource(evidence: dict[str, Any]) -> Iterator[ModuleType]:
    members = {
        name: _process_resource_data("_construction_procutil/" + name + ".py", evidence=evidence)
        for name in ("__init__", "_job_scopes", "_windows_job_types", "_output_relay", "streaming")
    }
    name = f"fleet_manager._retained_construction_procutil_{uuid.uuid4().hex}"
    module = ModuleType(name)
    module.__package__, module.__path__ = name, []
    setattr(module, "_retained_source_members", {
        "_construction_procutil/" + member + ".py": content
        for member, (_, content) in members.items()
    })
    registered = []
    try:
        for member, (path, content) in members.items():
            child = module if member == "__init__" else ModuleType(name + "." + member)
            child.__file__, child.__package__ = str(path), name
            setattr(child, "_retained_source_bytes", content)
            sys.modules[child.__name__] = child
            registered.append(child)
            exec(compile(content, str(path), "exec"), child.__dict__)
        yield module
    finally:
        for child in reversed(registered):
            if sys.modules.get(child.__name__) is child:
                del sys.modules[child.__name__]


def _run(
    argv: Sequence[str | Path], slot: Path, phase: str, *, descriptor: int | None,
    resource_evidence: dict[str, Any] | None = None,
) -> bytes:
    deadline = time.monotonic() + 45
    evidence = (artifact_staging.diagnose_client()
                if resource_evidence is None else resource_evidence)
    owner = _process_resource("_installer_process.py", evidence=evidence)
    try:
        with _procutil_resource(evidence) as procutil:
            sources = {
                "_installer_process.py": owner._retained_source_bytes,
                **procutil._retained_source_members,
                **{name: _process_resource_data(name, evidence=evidence)[1] for name in (
                    "_versioned_runtime.py", "versioned_runtime_leases.py",
                    "versioned_runtime_construction.py", "versioned_runtime_source.py",
                    "versioned_runtime_identity.py", "versioned_runtime_fingerprint.py",
                    "versioned_runtime_publication.py",
                )},
            } if os.name == "nt" else None
            result = owner.run_owned(
                [str(value) for value in argv],
                # CreateProcess still limits cwd for long-path-aware executables.
                cwd=slot.parent.parent if os.name == "nt" else slot, env=_environment(slot),
                timeout=45, deadline=deadline, procutil=procutil, construction_fd=descriptor,
                runtime_root=slot.parent.parent, runtime_version=slot.name,
                runtime_path=Path(__file__).absolute().with_name("_versioned_runtime.py"),
                guardian_sources=sources,
            )
    except subprocess.TimeoutExpired:
        raise PlanError(f"client {phase} timed out") from None
    except subprocess.CalledProcessError as error:
        raise PlanError(f"client {phase} failed with exit status {error.returncode}") from None
    if len(result.stdout) > MAX_BYTES or len(result.stderr) > MAX_BYTES:
        raise PlanError(f"client {phase} output exceeds its byte bound")
    return cast(bytes, result.stdout.encode("utf-8"))


def _python(slot: Path) -> Path:
    return slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _import_policy(slot: Path, evidence: dict[str, Any], descriptor: int | None) -> None:
    purelib = decode(_run([
        _python(slot), "-I", "-B", "-c",
        "import json,sysconfig; print(json.dumps(sysconfig.get_path('purelib')))",
    ], slot, "candidate import policy", descriptor=descriptor, resource_evidence=evidence))
    if (
        not isinstance(purelib, str) or not Path(purelib).is_absolute()
        or not Path(purelib).is_relative_to(slot) or Path(purelib).resolve() != Path(purelib)
    ):
        raise PlanError("client candidate import directory leaves its owned runtime")
    purelib = Path(purelib)
    artifact_staging._directory(purelib)
    # Native console entry points cannot pass -B before their first package import.
    with (purelib / "immutable_runtime.pth").open("xb") as stream:
        os.chmod(stream.name, 0o600)
        stream.write(b"import sys; sys.dont_write_bytecode = True\n")
        stream.flush()
        os.fsync(stream.fileno())
    cast(Callable[[Path], None], client_admission._sync_directory)(purelib)


def _bootstrap(slot: Path, evidence: dict[str, Any]) -> None:
    directory = slot / BOOTSTRAP
    directory.mkdir(mode=0o700)
    for source, target in (
        ("_runtime_launcher.py", "runtime_launcher.py"),
        ("_versioned_runtime.py", "versioned_runtime.py"),
        *((name + ".py", name + ".py") for name in (
            "versioned_runtime_leases", "versioned_runtime_construction",
            "versioned_runtime_source", "versioned_runtime_publication",
            "versioned_runtime_identity", "versioned_runtime_fingerprint",
        )),
    ):
        data = artifact_staging._bytes(Path(__file__).absolute().with_name(source), MAX_BYTES)
        if hashlib.sha256(data).hexdigest() != evidence["runtime_resource_sha256s"][source]:
            raise PlanError("client launch resource changed after RECORD admission")
        with (directory / target).open("xb") as stream:
            os.chmod(directory / target, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    cast(Callable[[Path], None], client_admission._sync_directory)(directory)
    cast(Callable[[Path], None], client_admission._sync_directory)(slot)


def _pip(slot: Path) -> list[str | Path]:
    return [
        sys.executable, "-I", "-B", "-m", "pip", "--python", _python(slot),
        "--isolated", "--disable-pip-version-check",
    ]


def _health(
    slot: Path, checkpoint: _Checkpoint, descriptor: int | None,
    resource_evidence: dict[str, Any],
) -> dict[str, Any]:
    console = slot / ("Scripts/fleet-manager.exe" if os.name == "nt" else "bin/fleet-manager")
    artifact_staging._file_stat(console)
    checkpoint()
    _run([*_pip(slot), "check"], slot, "dependency check",
         descriptor=descriptor, resource_evidence=resource_evidence)
    checkpoint()
    version = _run([console, "--version"], slot, "console version check",
                   descriptor=descriptor, resource_evidence=resource_evidence)
    checkpoint()
    report = decode(_run([console, "doctor", "--role", "client"], slot, "local diagnostics",
                         descriptor=descriptor, resource_evidence=resource_evidence))
    if (
        not isinstance(report, dict)
        or report.get("schema") != "fleet-manager.client-diagnostics"
        or report.get("status") != "installed" or report.get("role") != "client"
        or report.get("mutations_performed") is not False
        or report.get("controller_readiness_verified") is not False
        or version.decode("utf-8").splitlines() != [f"fleet-manager {report.get('package_version')}"]
    ):
        raise PlanError("constructed client returned invalid local health evidence")
    return report


def _tree(primitive: ModuleType, slot: Path) -> str:
    members = sorted(
        (path for path in slot.iterdir()
         if path.name not in {COMPLETION, primitive.COMPLETE_MARKER}),
        key=lambda path: path.name,
    )
    paths, links = [], {}
    for path in members:
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(slot):
                raise PlanError("client runtime link leaves its owned candidate")
            links[path.name] = os.readlink(path)
        else:
            paths.append(path)
    for name in links:
        resolved = (slot / name).resolve(strict=True)
        if not any(resolved == path or resolved.is_relative_to(path) for path in paths):
            raise PlanError("client runtime link has no attributable target bytes")
    return digest({"files": primitive.fingerprint_source(paths), "links": links})


def _unfinished(slot: Path, intent: Path, binding: dict[str, Any]) -> None:
    artifact_staging._directory(slot)
    if _read(intent) != binding:
        raise PlanError("unfinished client candidate has a foreign installation or release")


def _construction_intent(namespace: Path, version: str, slot: Path, binding: dict[str, Any]) -> Path:
    directory = namespace / "client-inputs"
    intent = directory / (version + ".json")
    if os.path.lexists(directory):
        artifact_staging._directory(directory)
    else:
        private_directory(directory)
        cast(Callable[[Path], None], client_admission._sync_directory)(namespace)
    if os.path.lexists(intent):
        if _read(intent) != binding:
            raise PlanError("client construction intent belongs to a different installation or release")
        return intent
    if os.path.lexists(slot):
        artifact_staging._directory(slot)
        if any(slot.iterdir()):
            raise PlanError("unfinished client candidate has no owned construction intent")
    _write(intent, binding)
    return intent


def _completed_intent(namespace: Path, version: str, binding: dict[str, Any]) -> Path:
    intent = namespace / "client-inputs" / (version + ".json")
    artifact_staging._directory(intent.parent)
    if _read(intent) != binding:
        raise PlanError("completed client candidate has no matching owned construction intent")
    return intent


def _invalidate_removed_outcome(namespace: Path, version: str, identity: dict[str, Any]) -> None:
    directory = namespace / "client-runtime-removed"
    if not os.path.lexists(directory):
        return
    artifact_staging._directory(directory)
    path = directory / (version + ".json")
    if os.path.lexists(path):
        from .client_runtime_removal import _outcome

        cast(Callable[[object, dict[str, Any], str], object], _outcome)(_read(path), identity, version)
        path.unlink()
        cast(Callable[[Path], None], client_admission._sync_directory)(directory)


def _discard(
    primitive: ModuleType, namespace: Path, version: str, intent: Path, binding: dict[str, Any],
) -> None:
    primitive.discard_failed_candidate(
        namespace, version, validate=lambda candidate: _unfinished(candidate, intent, binding),
    )


def _request(
    identity: dict[str, Any], selection: dict[str, Any], staged: artifact_staging.StagedArtifacts,
    current_policy: Callable[[], object], current_target: Callable[[], object],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    identity = cast(Callable[[object], dict[str, Any]], client_admission._identity)(identity)
    selection = cast(Callable[[object, dict[str, Any]], dict[str, Any]], client_admission._selection)(selection, identity)
    if not isinstance(staged, artifact_staging.StagedArtifacts):
        raise PlanError("client construction requires a live retained artifact stage")
    if (staged.authority, staged.installation, staged.role) != (
        identity["authority"], identity["installation"], "client",
    ):
        raise PlanError("client construction stage installation scope mismatch")
    if len(staged.artifacts) != 1:
        raise PlanError("client construction currently requires one portable fleet-manager wheel")
    if not callable(current_policy) or not callable(current_target):
        raise PlanError("client construction requires current trusted policy and target suppliers")
    binding = {
        "schema": "fleet-manager.client-construction-input", "version": 1,
        "identity": identity, "selection": selection,
        "selection_digest": cast(Callable[[object], ReleaseSelection], ReleaseSelection.from_dict)(selection).selection_digest,
    }
    return identity, selection, binding


def _checkpoint(
    connection: sqlite3.Connection, identity: dict[str, Any], selection: dict[str, Any],
    binding: dict[str, Any], staged: artifact_staging.StagedArtifacts,
    current_policy: Callable[[], object], current_target: Callable[[], object],
    clock: Callable[[], int], unchanged: Callable[[], None],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    unchanged()
    admitted = cast(Callable[..., dict[str, Any]], client_admission._read_state)(connection, identity)
    if not admitted or admitted["selection_digest"] != binding["selection_digest"]:
        raise PlanError("client construction requires the exact durably admitted selection")
    policy, target = _verified(
        identity, selection, staged, current_policy, current_target, clock,
    )
    unchanged()
    return policy, target, admitted


def _verified(
    identity: dict[str, Any], selection: dict[str, Any], staged: artifact_staging.StagedArtifacts,
    current_policy: Callable[[], object], current_target: Callable[[], object], clock: Callable[[], int],
) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = decode(canonical(current_policy()))
    evidence = staged.reverify(policy, clock=clock)
    if (
        evidence["release_digest"] != selection["release_digest"]
        or evidence["sequence"] != selection["sequence"]
        or sorted(staged.artifacts) != selection["components"]
    ):
        raise PlanError("client construction release differs from its admitted selection")
    manifest = cast(Callable[[object], dict[str, Any]], validate_release)(decode(staged.read_manifest(policy, clock=clock)))
    target = _compatible(manifest, current_target(), identity)
    return policy, target


def _completion(
    primitive: ModuleType, namespace: Path, version: str, binding: dict[str, Any],
) -> dict[str, Any]:
    _completed_intent(namespace, version, binding)
    slot = primitive.version_dir(namespace, version)
    completion = _read(slot / COMPLETION)
    record(completion, "fleet-manager.client-constructed", {"input", "runtime_sha256"})
    if (
        set(completion) != {"schema", "version", "input", "runtime_sha256"}
        or completion["schema"] != "fleet-manager.client-constructed"
        or completion["version"] != 1 or completion["input"] != binding
        or primitive.check_admission(
            namespace, version, payload_hash=digest(completion),
        ) != primitive.ADMIT_REUSE
        or _tree(primitive, slot) != completion["runtime_sha256"]
    ):
        raise PlanError("completed client candidate differs; distinct-generation repair is required")
    return cast(dict[str, Any], completion)


def construct_client_release(
    root: Path, identity: dict[str, Any], selection: dict[str, Any],
    staged: artifact_staging.StagedArtifacts, *, current_policy: Callable[[], object],
    current_target: Callable[[], object], clock: Callable[[], int] = lambda: int(time.time()),
) -> dict[str, Any]:
    """Build the supplied admitted client; current trusted inputs are supplied by its owner."""
    identity, selection, binding = _request(
        identity, selection, staged, current_policy, current_target,
    )
    return _construct_client_release(
        identity, selection, staged, binding=binding, clock=clock,
        custody=lambda: _admitted_custody(
            root, identity, selection, binding, staged, current_policy, current_target, clock,
        ),
    )


@contextmanager
def _admitted_custody(
    root: Path, identity: dict[str, Any], selection: dict[str, Any], binding: dict[str, Any],
    staged: artifact_staging.StagedArtifacts, current_policy: Callable[[], object],
    current_target: Callable[[], object], clock: Callable[[], int],
) -> Iterator[tuple[Path, Callable[[], None], _Checkpoint]]:
    with (
        client_admission._owned(root) as (path, database_root, unchanged),
        client_admission._connection(
            path, database_root=database_root, readonly=False,
        ) as connection,
    ):
        cast(Callable[[sqlite3.Connection, Path], None], client_admission._begin_write)(connection, path)

        def checkpoint() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
            return _checkpoint(
                connection, identity, selection, binding, staged,
                current_policy, current_target, clock, unchanged,
            )

        yield path, unchanged, checkpoint


def _construct_client_release(
    identity: dict[str, Any], selection: dict[str, Any], staged: artifact_staging.StagedArtifacts,
    *, binding: dict[str, Any], custody: _Custody, clock: Callable[[], int],
) -> dict[str, Any]:
    try:
        metadata.distribution("pip")
    except metadata.PackageNotFoundError:
        raise PlanError("client construction requires the construction extra in its owning client") from None
    except (OSError, ValueError):
        raise PlanError("client construction toolchain metadata could not be read") from None
    resource_evidence = artifact_staging.diagnose_client()
    try:
        with custody() as (path, unchanged, checkpoint):
            policy, _, _ = checkpoint()
            filename, content = _wheel(staged, staged.read_artifacts(policy, clock=clock))
            namespace = _namespace(path, identity)
            _not_removing(namespace)
            version = f"client-{selection['sequence']}-{selection['release_digest']}"
            with artifact_staging._primitive() as primitive, ExitStack() as retained:
                previous = {primitive.current_version(namespace)}
                good = namespace / primitive.LAST_KNOWN_GOOD_FILE
                if os.path.lexists(good):
                    cast(Callable[[Path], os.stat_result], client_admission._ordinary_file)(good)
                    previous.add(good.read_text(encoding="utf-8").strip())
                for prior in previous:
                    if prior:
                        retained.enter_context(primitive.retention_lease(namespace, prior))
                with primitive.construction_lease(namespace, version) as descriptor:
                    checkpoint()
                    slot = primitive.version_dir(namespace, version)
                    reused = False
                    if os.path.lexists(primitive.marker_path(namespace, version)):
                        completion = _completion(primitive, namespace, version, binding)
                        retained.enter_context(primitive.retention_lease(
                            namespace, version, payload_hash=digest(completion),
                        ))
                        reused = True
                    else:
                        if primitive.check_admission(
                            namespace, version, payload_hash=digest(binding),
                        ) != primitive.ADMIT_CONSTRUCT:
                            raise PlanError("client candidate construction admission refused")
                        intent = _construction_intent(namespace, version, slot, binding)
                        _invalidate_removed_outcome(namespace, version, identity)
                        if os.path.lexists(slot):
                            _unfinished(slot, intent, binding)
                            _discard(primitive, namespace, version, intent, binding)
                        slot = primitive.slot(namespace, version)
                    completion_started = False
                    try:
                        if not reused:
                            wheel = slot / filename
                            with wheel.open("xb") as stream:
                                stream.write(content)
                                stream.flush()
                                os.fsync(stream.fileno())
                            checkpoint()
                            _run([sys.executable, "-I", "-B", "-m", "venv", "--copies",
                                  "--without-pip", slot], slot, "environment construction",
                                 descriptor=descriptor, resource_evidence=resource_evidence)
                            checkpoint()
                            _run([
                                _python(slot), "-I", "-B", "-c",
                                "import pathlib,sys; "
                                "assert pathlib.Path(sys.prefix).resolve()==pathlib.Path(sys.argv[1]).resolve(); "
                                "assert sys.prefix!=sys.base_prefix", slot,
                            ], slot, "candidate interpreter check", descriptor=descriptor,
                                 resource_evidence=resource_evidence)
                            checkpoint()
                            _run([*_pip(slot), "install",
                                  "--no-index", "--no-deps", "--no-compile", "--no-cache-dir",
                                  wheel], slot, "offline wheel installation",
                                 descriptor=descriptor, resource_evidence=resource_evidence)
                            _import_policy(slot, resource_evidence, descriptor)
                            _bootstrap(slot, resource_evidence)
                        checkpoint()
                        before_health = _tree(primitive, slot)
                        health = _health(slot, checkpoint, descriptor, resource_evidence)
                        _, target, admitted = checkpoint()
                        if _tree(primitive, slot) != before_health:
                            raise PlanError("client candidate bytes changed during health checks")
                        if not reused:
                            completion = {
                                "schema": "fleet-manager.client-constructed", "version": 1,
                                "input": binding, "runtime_sha256": before_health,
                            }
                            _write(slot / COMPLETION, completion)
                            completion_started = True
                            try:
                                primitive.mark_complete(namespace, version, payload_hash=digest(completion))
                            except OSError:
                                raise PlanError(
                                    "client completion outcome is unknown; retry under ownership"
                                ) from None
                            retained.enter_context(primitive.retention_lease(
                                namespace, version, payload_hash=digest(completion),
                            ))
                        unchanged()
                        return {
                            "schema": "fleet-manager.client-construction", "version": 1,
                            "status": "healthy", "identity": identity, "admitted": admitted,
                            "candidate": version, "runtime_sha256": completion["runtime_sha256"],
                            "target_digest": digest(target), "health": health, "reused": reused,
                            "constructed": True, "healthy": True, "active": False,
                            "activation_authorized": False, "live_readiness_verified": False,
                            "mutations_performed": not reused,
                        }
                    finally:
                        if not reused and not completion_started:
                            # Retain ownership outside the tree: a failed native
                            # removal can delete only part of an unpublished slot.
                            _unfinished(slot, intent, binding)
    except PlanError:
        raise
    except (OSError, sqlite3.Error, subprocess.SubprocessError, RuntimeError, ValueError):
        raise PlanError("client construction could not establish a healthy owned candidate") from None
