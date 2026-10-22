"""Offline construction and local health of an admitted client, never activation."""

from contextlib import ExitStack
from email.parser import BytesParser
import hashlib
import importlib.util
from importlib import metadata
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import zipfile

from . import artifact_staging, client_admission
from .planning import MAX_BYTES, PlanError, canonical, decode, digest, integer, record
from .release import validate_release
from .release_selection import ReleaseSelection

NAMESPACE = "client-runtime"
OWNER = "client-owner.json"
COMPLETION = "client-constructed.json"
BOOTSTRAP = "runtime-bootstrap"
REMOVING = "client-runtime-removing.json"


def _not_removing(namespace):
    if os.path.lexists(namespace / REMOVING):
        raise PlanError("client runtime removal is unsettled; exact owned removal retry is required")


def _write(path, value):
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    client_admission._sync_directory(path.parent)


def _read(path):
    client_admission._ordinary_file(path)
    return decode(artifact_staging._bytes(path, MAX_BYTES))


def _namespace(root, identity):
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
            client_admission._ordinary_file(entry)
        for entry in entries:
            entry.unlink()
        temporary.rmdir()
    temporary.mkdir(mode=0o700)
    _write(temporary / OWNER, expected)
    os.rename(temporary, namespace)
    client_admission._sync_directory(root)
    return namespace


def _compatible(manifest, target, identity):
    target = decode(canonical(target))
    record(target, "fleet-manager.client-target",
           {"authority", "installation", "revision", "components"})
    integer(target["revision"], "client target revision", minimum=1)
    if (target["authority"], target["installation"]) != (
        identity["authority"], identity["installation"],
    ):
        raise PlanError("client target installation scope mismatch")
    components = target["components"]
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
    return target


def _wheel(staged, snapshots):
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


def _environment(slot):
    home = slot / "health-home"
    home.mkdir(mode=0o700, exist_ok=True)
    environment = {name: os.environ[name] for name in (
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL",
    ) if name in os.environ}
    environment.update({
        "PATH": str(Path(sys.executable).parent) + (
            os.pathsep + os.defpath if os.name != "nt" else ""
        ),
        "HOME": str(home), "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home), "XDG_DATA_HOME": str(home),
        "XDG_CACHE_HOME": str(home), "TEMP": str(home), "TMP": str(home),
        "TMPDIR": str(home), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1", "PIP_CONFIG_FILE": os.devnull,
    })
    return environment


def _process_resource(name, *, evidence=None):
    if evidence is None:
        evidence = artifact_staging.diagnose_client()
    path = Path(__file__).absolute().with_name(name)
    content = artifact_staging._bytes(path, MAX_BYTES)
    if hashlib.sha256(content).hexdigest() != evidence["runtime_resource_sha256s"][name]:
        raise PlanError("client construction process resource changed after RECORD admission")
    spec = importlib.util.spec_from_file_location("fleet_manager." + path.stem, path)
    if spec is None or spec.loader is None:
        raise PlanError("client construction process resource could not be loaded")
    module = importlib.util.module_from_spec(spec)
    exec(compile(content, str(path), "exec"), module.__dict__)
    return module


def _run(argv, slot, phase, *, descriptor, resource_evidence=None):
    evidence = (artifact_staging.diagnose_client()
                if resource_evidence is None else resource_evidence)
    owner = _process_resource("_installer_process.py", evidence=evidence)
    procutil = _process_resource("_construction_procutil.py", evidence=evidence)
    try:
        result = owner.run_owned(
            [str(value) for value in argv], cwd=slot, env=_environment(slot),
            timeout=45, procutil=procutil, construction_fd=descriptor,
            runtime_root=slot.parent.parent, runtime_version=slot.name,
            runtime_path=Path(__file__).absolute().with_name("_versioned_runtime.py"),
        )
    except subprocess.TimeoutExpired:
        raise PlanError(f"client {phase} timed out") from None
    except subprocess.CalledProcessError as error:
        raise PlanError(f"client {phase} failed with exit status {error.returncode}") from None
    if len(result.stdout) > MAX_BYTES or len(result.stderr) > MAX_BYTES:
        raise PlanError(f"client {phase} output exceeds its byte bound")
    return result.stdout.encode("utf-8")


def _python(slot):
    return slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _bootstrap(slot, evidence):
    directory = slot / BOOTSTRAP
    directory.mkdir(mode=0o700)
    for source, target in (
        ("_runtime_launcher.py", "runtime_launcher.py"),
        ("_versioned_runtime.py", "versioned_runtime.py"),
        *((name + ".py", name + ".py") for name in (
            "versioned_runtime_leases", "versioned_runtime_construction",
            "versioned_runtime_source", "versioned_runtime_publication",
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
    client_admission._sync_directory(directory)
    client_admission._sync_directory(slot)


def _pip(slot):
    return [
        sys.executable, "-I", "-B", "-m", "pip", "--python", _python(slot),
        "--isolated", "--disable-pip-version-check",
    ]


def _health(slot, checkpoint, descriptor, resource_evidence):
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


def _tree(primitive, slot):
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


def _unfinished(slot, intent, binding):
    artifact_staging._directory(slot)
    if _read(intent) != binding:
        raise PlanError("unfinished client candidate has a foreign installation or release")


def _construction_intent(namespace, version, slot, binding):
    directory = namespace / "client-inputs"
    intent = directory / (version + ".json")
    if os.path.lexists(directory):
        artifact_staging._directory(directory)
    else:
        directory.mkdir(mode=0o700)
        client_admission._sync_directory(namespace)
    if os.path.lexists(intent):
        if _read(intent) != binding:
            raise PlanError("client construction intent belongs to a different installation or release")
        return intent
    if os.path.lexists(slot):
        artifact_staging._directory(slot)
        if any(slot.iterdir()):
            raise PlanError("unfinished client candidate has no owned construction intent")
    temporary = intent.with_name(intent.name + ".tmp")
    if os.path.lexists(temporary):
        client_admission._ordinary_file(temporary)
        temporary.unlink()
    _write(intent, binding)
    return intent


def _completed_intent(namespace, version, binding):
    intent = namespace / "client-inputs" / (version + ".json")
    artifact_staging._directory(intent.parent)
    if _read(intent) != binding:
        raise PlanError("completed client candidate has no matching owned construction intent")
    return intent


def _invalidate_removed_outcome(namespace, version, identity):
    directory = namespace / "client-runtime-removed"
    if not os.path.lexists(directory):
        return
    artifact_staging._directory(directory)
    path = directory / (version + ".json")
    if os.path.lexists(path):
        from .client_runtime_removal import _outcome

        _outcome(_read(path), identity, version)
        path.unlink()
        client_admission._sync_directory(directory)


def _discard(primitive, namespace, version, intent, binding):
    primitive.discard_failed_candidate(
        namespace, version, validate=lambda candidate: _unfinished(candidate, intent, binding),
    )


def _request(identity, selection, staged, current_policy, current_target):
    identity = client_admission._identity(identity)
    selection = client_admission._selection(selection, identity)
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
        "selection_digest": ReleaseSelection.from_dict(selection).selection_digest,
    }
    return identity, selection, binding


def _checkpoint(connection, identity, selection, binding, staged, current_policy,
                current_target, clock, unchanged):
    unchanged()
    admitted = client_admission._read_state(connection, identity)
    if not admitted or admitted["selection_digest"] != binding["selection_digest"]:
        raise PlanError("client construction requires the exact durably admitted selection")
    policy = decode(canonical(current_policy()))
    evidence = staged.reverify(policy, clock=clock)
    if (
        evidence["release_digest"] != selection["release_digest"]
        or evidence["sequence"] != selection["sequence"]
        or sorted(staged.artifacts) != selection["components"]
    ):
        raise PlanError("client construction release differs from its admitted selection")
    manifest = validate_release(decode(staged.read_manifest(policy, clock=clock)))
    target = _compatible(manifest, current_target(), identity)
    unchanged()
    return policy, target, admitted


def _completion(primitive, namespace, version, binding):
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
    return completion


def construct_client_release(root, identity, selection, staged, *,
                             current_policy, current_target, clock=lambda: int(time.time())):
    """Build the supplied admitted client; current trusted inputs are supplied by its owner."""
    identity, selection, binding = _request(
        identity, selection, staged, current_policy, current_target,
    )
    try:
        metadata.distribution("pip")
    except metadata.PackageNotFoundError:
        raise PlanError("client construction requires the construction extra in its owning client") from None
    except (OSError, ValueError):
        raise PlanError("client construction toolchain metadata could not be read") from None
    resource_evidence = artifact_staging.diagnose_client()
    try:
        with (
            client_admission._owned(root) as (path, database_root, unchanged),
            client_admission._connection(
                path, database_root=database_root, readonly=False,
            ) as connection,
        ):
            connection.execute("BEGIN IMMEDIATE")

            def checkpoint():
                return _checkpoint(
                    connection, identity, selection, binding, staged,
                    current_policy, current_target, clock, unchanged,
                )

            policy, _, _ = checkpoint()
            filename, content = _wheel(staged, staged.read_artifacts(policy, clock=clock))
            namespace = _namespace(path, identity)
            _not_removing(namespace)
            version = f"client-{selection['sequence']}-{selection['release_digest']}"
            with artifact_staging._primitive() as primitive, ExitStack() as retained:
                previous = {primitive.current_version(namespace)}
                good = namespace / primitive.LAST_KNOWN_GOOD_FILE
                if os.path.lexists(good):
                    client_admission._ordinary_file(good)
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
