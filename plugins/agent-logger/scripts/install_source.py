"""Source and closed-wheel inputs, independent of an installed runtime."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import sys
import time
import uuid

_SCRIPTS = Path(__file__).resolve().parent
if _SCRIPTS.name == "installer-engine" and _SCRIPTS.parent.name == "libs":
    sys.path.insert(0, str(_SCRIPTS.parent / "versioned-runtime"))
sys.path.insert(0, str(_SCRIPTS))
import installer_engine as engine
from installer_wheel_inputs import WheelLimits, inspect_archive
from installer_wheel_snapshot import WheelReuseInputs, retain_wheel_inputs
import versioned_runtime_source as sources

SCHEMA = "copilot-extensions.install-source"
MANIFEST = "install-source.json"
APPLICATION_INSTALL_CONTRACT = {
    "schema": "copilot-extensions.prepared-application-install",
    "version": 1,
}


class _ApplicationDeclarationConflict(engine.ContentRepairRequired):
    """Different valid contracts require explicit repair, not corruption recovery."""


@dataclass(frozen=True)
class ProjectSource:
    payload: Path
    identity: sources.SourceIdentity
    record: dict
    project_key: str

    @property
    def project(self) -> dict:
        return self.record["projects"][self.project_key]

    @property
    def source_descriptor(self) -> dict | None:
        descriptor = self.record.get("source_descriptor")
        if descriptor is not None and (
            not isinstance(descriptor, dict) or set(descriptor) != {"kind", "canonical", "ref"}
            or any(
                not isinstance(value, str) or any(char in value for char in "\r\n\0")
                for value in descriptor.values()
            )
        ):
            raise ValueError("installation source has invalid repository provenance")
        return descriptor

    @property
    def composition(self) -> dict | None:
        if "composition" not in self.record:
            return None
        composition = self.record["composition"]
        if (
            self.record.get("kind") != "source"
            or not isinstance(composition, dict) or set(composition) != {"main_seed", "tree"}
            or composition.get("main_seed") != self.record.get("main_head")
            or any(
                not isinstance(composition.get(key), str)
                or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", composition[key]) is None
                for key in ("main_seed", "tree")
            )
        ):
            raise ValueError("installation source has an invalid Git composition association")
        return dict(composition)


def _read(path: Path) -> dict:
    if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("installation input must not be linked")
    if not path.is_file() or path.stat().st_size > 4 << 20:
        raise ValueError("installation input is not a bounded ordinary document")
    with path.open("rb") as stream:
        content = stream.read((4 << 20) + 1)
    if len(content) > 4 << 20:
        raise ValueError("installation input exceeds its document bound")
    record = json.loads(content)
    if not isinstance(record, dict):
        raise ValueError("installation input must be an object")
    return record


def _member(root: Path, name: str) -> Path:
    if not isinstance(name, str):
        raise ValueError("installation input has no relative member")
    relative = PurePosixPath(name)
    if (
        relative.is_absolute() or not relative.parts or ".." in relative.parts
        or ":" in name or "\\" in name
    ):
        raise ValueError("installation input member is not a portable relative path")
    result = root
    for part in relative.parts:
        result /= part
        if result.is_symlink() or getattr(result.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("installation input member must not be linked")
    return result


def select_project(snapshot: Path, component: str) -> ProjectSource:
    """Consume an explicitly selected immutable source, never a payload fallback."""
    if not isinstance(component, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", component) is None:
        raise ValueError("installation source requires one portable project identifier")
    if not snapshot.is_absolute() or snapshot.is_symlink():
        raise ValueError("--install-source requires an absolute ordinary snapshot")
    snapshot = snapshot.resolve(strict=True)
    record = _read(snapshot / MANIFEST)
    if (
        record.get("schema") != SCHEMA or type(record.get("version")) is not int
        or record["version"] != 1 or record.get("kind") not in {"main", "source"}
        or record.get("kind") == "main" and record.get("main_head") is None
        or any(
            not isinstance(record.get(key), str)
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", record[key]) is None
            for key in (
                ("source_commit", "source_tree", "main_head")
                if record.get("main_head") is not None else ("source_commit", "source_tree")
            )
        )
        or not isinstance(record.get("projects"), dict)
    ):
        raise ValueError("unsupported or incomplete installation-source snapshot")
    keys = [key for key in (component, "standalone:" + component) if key in record["projects"]]
    if len(keys) != 1:
        raise ValueError(f"installation-source snapshot requires one {component} project")
    project_key = keys[0]
    project = record["projects"][project_key]
    if not isinstance(project, dict):
        raise ValueError("installation-source project must be an object")
    payload = _member(snapshot, project.get("payload"))
    version = sources.payload_package_version(payload, component)
    identity = sources.read_source_identity(payload, version, plugin=component)
    if identity is None or identity.document() != project.get("source") or (
        identity.source_commit != record["source_commit"]
        or identity.source_tree != record["source_tree"]
    ):
        raise ValueError(f"{component} source differs from its immutable source association")
    selected = ProjectSource(payload, identity, record, project_key)
    selected.composition
    return selected


def select(snapshot: Path) -> ProjectSource:
    return select_project(snapshot, "worktree-manager")


@contextmanager
def wheel_inputs(
    profile_path: Path, wheel_directory: Path, *, deadline: float | None = None,
    expected_profile_sha256: str | None = None,
):
    """Retain an independently admitted profile; this receiver grants no approval."""
    if not profile_path.is_absolute() or not wheel_directory.is_absolute():
        raise ValueError("wheel inputs require absolute profile and directory paths")
    if wheel_directory.is_symlink() or getattr(wheel_directory.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("wheel input directory must not be linked")
    deadline = deadline or time.monotonic() + 600
    profile = _read(profile_path)
    if expected_profile_sha256 is not None and hashlib.sha256(json.dumps(
        profile, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest() != expected_profile_sha256:
        raise ValueError("Native wheel profile changed after driver admission")
    rows = profile.get("wheels")
    if not isinstance(rows, list) or not rows or len(rows) > 256:
        raise ValueError("project requires a complete bounded wheel profile")
    archives, expected = {}, {}
    for row in rows:
        if not isinstance(row, dict) or row.get("filename") in archives:
            raise ValueError("project wheel profile has invalid or duplicate inputs")
        filename = row.get("filename")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError("project wheel profile has an invalid filename")
        archives[filename] = _member(wheel_directory, filename)
        expected[filename] = row.get("sha256")
    limits = WheelLimits(
        max_archive_bytes=128 << 20, max_expanded_bytes=1 << 30,
        max_member_bytes=512 << 20, max_metadata_bytes=16 << 20,
        max_members=100_000, max_tags=10_000, max_metadata_member_bytes=4 << 20,
    )
    with retain_wheel_inputs(
        archives, expected, max_wheels=256, max_archive_bytes=128 << 20,
        max_total_bytes=512 << 20, max_snapshot_bytes=1 << 30, deadline=deadline,
    ) as retained:
        distributions = []
        expanded = 0
        for row in rows:
            facts = inspect_archive(
                retained.archives[row["filename"]], limits=limits, deadline=deadline,
            )
            expanded += facts.unpacked_size
            if expanded > limits.max_expanded_bytes:
                raise ValueError("project wheel closure exceeds its expanded byte budget")
            if (
                re.sub(r"[-_.]+", "-", facts.name).lower() != row.get("name")
                or facts.version != row.get("version")
                or facts.requires_python != row.get("requires_python")
                or list(facts.requires_dist) != row.get("requires_dist")
                or list(facts.provides_extras) != row.get("provides_extras")
            ):
                raise ValueError("project wheel metadata differs from its prepared profile")
            distributions.append((
                row["name"], row["version"], row["filename"], row["sha256"],
            ))
        inputs = engine.WheelInstallInputs(
            retained, json.dumps(profile, sort_keys=True, separators=(",", ":"), allow_nan=False),
            tuple(distributions),
        )
        inputs.verify()
        yield inputs
        inputs.verify()
        if _read(profile_path) != profile:
            raise ValueError("wheel profile changed during installation")


@contextmanager
def project_inputs(snapshot: Path, component: str, *, deadline: float | None = None):
    """Hold the prepared complete wheel set through canonical installed health."""
    selected = select_project(snapshot, component)
    composition = selected.composition
    project = selected.project
    if "profile" not in project or "wheel_inputs" not in project:
        raise ValueError("installation source has first-party inputs only; prepare its native wheel profile")
    with wheel_inputs(
        _member(snapshot, project.get("profile")),
        _member(snapshot, project["wheel_inputs"]), deadline=deadline,
    ) as inputs:
        profile = inputs.receipt()["binding"]
        if profile.get("release") != {
            "identity": "source:" + selected.identity.source_commit,
            "sha256": "sha256:" + selected.identity.assembly_hash,
        } or profile.get("component") != component:
            raise ValueError("project wheel profile differs from its source association")
        yield selected, inputs
        inputs.verify()
        observed = select_project(snapshot, component)
        if observed.identity != selected.identity:
            raise ValueError("project source changed during installation")
        if observed.source_descriptor != selected.source_descriptor:
            raise ValueError("project repository provenance changed during installation")
        if observed.composition != composition:
            raise ValueError("project Git composition changed during installation")


@contextmanager
def manager_inputs(snapshot: Path, *, deadline: float | None = None):
    with project_inputs(snapshot, "worktree-manager", deadline=deadline) as inputs:
        yield inputs


def _wheel_selection(payload: Path, version: str, inputs: engine.WheelInstallInputs) -> dict:
    inputs.verify()
    receipt = inputs.receipt()
    packages = engine._package_roots(payload)
    distributions = receipt["distributions"]
    local = tuple(sorted(
        engine._package_name(package) for package in packages
        if engine._package_name(package) in distributions
    ))
    name = engine._package_name(payload)
    if name not in local:
        raise ValueError("native wheel profile omits its application")
    profile = receipt["binding"]
    extras = tuple(next(
        row["selected_extras"] for row in profile["wheels"] if row["name"] == name
    ))
    extras = engine._selected_extras(payload, extras)
    return {
        "runtime_version": version + ".profile." + receipt["lock_id"][7:23],
        "payload_hash": engine._wheel_construction_hash(
            engine._construction_hash(engine._source_files(packages), extras, local), inputs,
        ),
        "extras": list(extras), "local_packages": list(local),
    }


def wheel_selection(payload: Path, component: str, inputs: engine.WheelInstallInputs) -> dict:
    version = sources.payload_package_version(payload, component)
    if sources.read_source_identity(payload, version, plugin=component) is not None:
        raise ValueError("Source payloads require their installation-source association")
    environment = inputs.receipt()["binding"].get("target", {}).get("environment", {})
    actual = {
        "implementation_name": sys.implementation.name, "os_name": os.name,
        "platform_machine": platform.machine(), "platform_system": platform.system(),
        "platform_python_implementation": platform.python_implementation(),
        "python_version": ".".join(platform.python_version_tuple()[:2]),
        "sys_platform": sys.platform,
    }
    if any(environment.get(key) != value for key, value in actual.items()):
        raise ValueError("wheel profile target differs from the selected native interpreter")
    selection = {"identity": {}, **_wheel_selection(payload, version, inputs)}
    admitted = os.environ.get("COPILOT_EXTENSIONS_NATIVE_WHEEL_SELECTION")
    if admitted is not None and json.loads(admitted) != selection:
        raise ValueError("Native wheel selection changed after driver admission")
    return selection


def native_current(root: Path, payload: Path, component: str) -> bool:
    """Read generation-qualified readiness; installation owns content admission."""
    try:
        version = sources.payload_package_version(payload, component)
        if os.path.lexists(payload / sources.SOURCE_FILE):
            return False
        current = engine.runtime.current_version(root)
        if not current:
            return False
        slot = Path(engine._native_retained_path(engine.runtime.version_dir(root, current)))
        record = _read(slot / ".runtime-input-origins.json")
        if (
            record.get("schema") != "installer-engine.frozen-input-origins"
            or type(record.get("version")) is not int or record["version"] != 2
        ):
            return False
        inputs = WheelReuseInputs(
            current, json.dumps(record["wheel_inputs"], sort_keys=True, separators=(",", ":"), allow_nan=False),
            time.monotonic() + 60,
        )
        receipt = inputs.receipt()
        name = engine._package_name(payload)
        profile_wheels = receipt["binding"]["wheels"]
        if (
            name not in receipt["distributions"]
            or not isinstance(profile_wheels, list) or not 0 < len(profile_wheels) <= 256
            or receipt["distributions"] != {
                row["name"]: {key: row[key] for key in ("version", "filename", "sha256")}
                for row in profile_wheels
            }
            or len(profile_wheels) != len(receipt["distributions"])
            or name not in record.get("local_packages", [])
            or record.get("extras", []) != list(engine._selected_extras(
                payload, tuple(next(row["selected_extras"] for row in profile_wheels if row["name"] == name)),
            ))
        ):
            return False
        if re.fullmatch(
            re.escape(version + ".profile." + receipt["lock_id"][7:23])
            + r"(?:[.+]repair\.[0-9a-f]{32})?", current,
        ) is None:
            return False
        manifest = _read(root / "deploy-manifest.json")
        source = manifest.get("source", {})
        if not isinstance(source, dict):
            return False
        fingerprint = source.get("content_hash")
        if (
            source.get("version") != version or source.get("plugin") != component
            or not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
            or manifest.get("runtime_version") != current or manifest.get("runtime_source")
        ):
            return False
        # Selectors, payload identity, profile, manifest and completion are read
        # afresh. Do not turn this startup hint into a second integrity scanner.
        return engine.runtime.is_complete(
            root, current, expect_hash=fingerprint,
        ) and engine.runtime.slot_python(root, current) is not None
    except (OSError, KeyError, TypeError, ValueError):
        return False


def native_selection(selected: ProjectSource, inputs: engine.WheelInstallInputs) -> dict:
    """Use the constructor's existing source/profile identity before native setup."""
    selection = _wheel_selection(selected.payload, selected.identity.runtime_version, inputs)
    distributions = inputs.receipt()["distributions"]
    artifact = selected.project.get("artifact")
    artifacts = artifact.get("wheels") if isinstance(artifact, dict) else None
    if not isinstance(artifacts, list) or not artifacts or len(artifacts) > 256:
        raise ValueError("native installation requires associated first-party wheels")
    associated = {}
    for row in artifacts:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError("native first-party wheel association is invalid")
        package_name = re.sub(r"[-_.]+", "-", row["name"]).lower()
        if package_name in associated:
            raise ValueError("native first-party wheel associations are ambiguous")
        associated[package_name] = row
    for package_name in selection["local_packages"]:
        row = associated.get(package_name, {})
        if any(
            distributions[package_name][key] != row.get(key) for key in ("filename", "sha256")
        ):
            raise ValueError("native first-party wheel differs from its source association")
    return {
        "identity": sources.manifest_fields(selected.identity),
        "source_descriptor": selected.source_descriptor,
        **selection,
    }


def application_inputs(selected: ProjectSource, inputs: engine.WheelInstallInputs) -> dict:
    """Carry the actual Source constructor's facts into a copyable application."""
    selection = native_selection(selected, inputs)
    packages = engine._package_roots(selected.payload)
    extras = tuple(selection["extras"])
    local = tuple(selection["local_packages"])
    return {
        **{key: selection[key] for key in (
            "identity", "runtime_version", "payload_hash", "extras", "local_packages",
        )},
        "source_hash": engine._construction_hash(engine._source_files(packages), extras, local),
        "wheel_inputs": inputs.receipt(),
        "excluded_packages": sorted(
            {engine._package_name(package) for package in packages} - set(local),
        ),
    }


def install_application(
    snapshot: Path, component: str, payload: Path, root: Path, *,
    uv: Path, base_python: Path, health_module: str,
    expected_version: str, expected_hash: str, arguments: tuple[str, ...] = (),
    repair: bool = False,
) -> dict:
    """Compose existing construction and application owners for a prepared source.

    The caller retains independent source approval through this call. Snapshot
    consistency and this adapter's contract declaration do not grant that trust.
    Application callbacks retain service-specific setup, readiness and recovery.
    """
    if base_python.resolve(strict=True) != Path(sys.executable).resolve(strict=True):
        raise ValueError("source construction must use its selected base interpreter")
    if not root.is_absolute() or not uv.is_absolute():
        raise ValueError("application installation requires absolute runtime and UV paths")
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", health_module):
        raise ValueError("source construction requires an ordinary application module")
    if (
        not isinstance(arguments, tuple) or len(arguments) > 64
        or any(not isinstance(value, str) or "\0" in value for value in arguments)
        or sum(len(value.encode("utf-8")) for value in arguments) > 65536
    ):
        raise ValueError("application installation arguments exceed their contract")
    with project_inputs(snapshot, component) as (selected, inputs):
        if sources.read_source_identity(
            payload, selected.identity.package_version, plugin=component,
        ) != selected.identity:
            raise ValueError("invocation payload differs from selected installation source")
        selection = native_selection(selected, inputs)
        if selection["payload_hash"] != expected_hash or selection["runtime_version"] != expected_version:
            raise ValueError("application source/profile selection changed before construction")
        if "application" not in selected.project:
            raise ValueError("application installation requires a prepared application project")
        application = _member(snapshot, selected.project["application"])
        declaration = _read(application / "application.json")
        if declaration.get("construction") != application_inputs(selected, inputs):
            raise ValueError("application declaration differs from its retained Source/profile")
        if declaration.get("name") == "worktree-manager":
            raise ValueError("Manager installation requires its existing native lifecycle")
        version = expected_version + (".repair." + uuid.uuid4().hex if repair else "")
        if not repair:
            try:
                current = engine.runtime.current_version(root)
            except ValueError:
                current = None
            if current and re.fullmatch(re.escape(expected_version) + r"\.repair\.[0-9a-f]{32}", current):
                version = current

        def validate(slot):
            try:
                installed = _read(slot / "application.json")
            except (FileNotFoundError, ValueError, UnicodeError) as error:
                raise engine.ContentRepairRequired(
                    "application declaration is not reusable; select a distinct repair generation"
                ) from error
            if installed != declaration:
                from runtime_application_install import _validate_application

                try:
                    _validate_application(installed)
                except ValueError as error:
                    raise engine.ContentRepairRequired(
                        "application declaration is not reusable; select a distinct repair generation"
                    ) from error
                raise _ApplicationDeclarationConflict(
                    "application declaration differs from its completed slot; "
                    "select a distinct repair generation"
                )

        candidate_options = {"health_module": health_module}
        if component == "agent-worktrees":
            identity = selection["identity"]["runtime_source"]
            candidate_options.update(
                health_module="agent_worktrees.runtime_candidate",
                health_callable="verify_runtime_candidate",
                construction_callable="agent_worktrees.runtime_candidate:prepare_runtime_candidate",
                construction_data={
                    "version": identity["package_version"], "commit": identity["source_commit"],
                    "branch": "source", "source": "source:" + identity["source_commit"],
                    "deployment_source": json.dumps(identity),
                },
            )
        with ExitStack() as retained:
            for attempt in range(2):
                existing = os.path.lexists(root / "versions" / version)
                try:
                    candidate = retained.enter_context(engine.numbered_candidate(
                        root, version, plugin=selected.payload, uv=uv, base_python=base_python,
                        **candidate_options, extras=tuple(selection["extras"]),
                        local_packages=tuple(selection["local_packages"]), wheel_inputs=inputs,
                        application=application, process_retention=True, reuse_generations=False,
                        expected_source_identity=selection["identity"], validate_candidate=validate,
                    ))
                except (engine.ContentRepairRequired, engine.HealthRepairRequired) as error:
                    if (
                        isinstance(error, _ApplicationDeclarationConflict)
                        or repair or attempt or not existing
                    ):
                        raise
                    inputs.verify()
                    if _read(application / "application.json") != declaration:
                        raise ValueError("prepared application changed before repair construction")
                    version = expected_version + ".repair." + uuid.uuid4().hex
                else:
                    break
            if candidate.payload_hash != expected_hash:
                raise ValueError("application candidate differs from its admitted construction inputs")
            inputs.verify()
            if _read(application / "application.json") != declaration:
                raise ValueError("prepared application declaration changed during construction")
            validate(candidate.slot)
            if (
                candidate.reused and not repair and not arguments
                and not declaration.get("setup") and not declaration.get("start")
            ):
                from runtime_application_install import _publication_current

                if _publication_current(candidate.slot, declaration):
                    inputs.verify()
                    validate(candidate.slot)
                    return {"state": "installed", "slot": str(candidate.slot)}
            frozen = engine.FrozenInputs(
                candidate.frozen_plugin, engine._package_roots(candidate.frozen_plugin),
                candidate.payload_hash, wheel_inputs=inputs,
            )
            python = candidate.slot / ".venv" / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            command = [str(candidate.slot / "install.py"), "--", *arguments]
            cwd = candidate.slot
            if os.name == "nt":
                # CreateProcess rejects long cwd values, including native spelling.
                # Enter the physical slot inside the same isolated interpreter.
                command = [
                    "-c", "import os,runpy,sys\n"
                    f"os.chdir({engine._native_retained_path(candidate.slot)!r})\n"
                    "runpy.run_path(sys.argv.pop(1), run_name='__main__')",
                    *command,
                ]
                cwd = Path(candidate.slot.anchor)
            engine._child(
                [
                    str(engine._native_retained_path(python)), "-I", "-X", "utf8", "-B",
                    *command,
                ],
                inputs=frozen, descriptor=candidate.descriptor,
                timeout=180, cwd=cwd, candidate=candidate.slot,
                allow_breakaway=True,
            )
            inputs.verify()
            receipt = _read(candidate.slot / "install-success.json")
            if receipt != {"state": "installed", "version": candidate.slot.name}:
                raise RuntimeError("application owner did not publish its installation result")
            return {"state": "installed", "slot": str(candidate.slot)}


def native_session(
    snapshot: Path, component: str, payload: Path, root: Path, *,
    uv: Path, base_python: Path, health_module: str,
    expected_version: str, expected_hash: str, repair: bool = False,
) -> int:
    """Retain a completed wheel candidate while its native owner settles setup."""
    with project_inputs(snapshot, component) as (selected, inputs):
        if sources.read_source_identity(
            payload, selected.identity.package_version, plugin=component,
        ) != selected.identity:
            raise ValueError("invocation payload differs from selected installation source")
        return _native_candidate_session(
            selected.payload, root, inputs, native_selection(selected, inputs),
            uv=uv, base_python=base_python, health_module=health_module,
            expected_version=expected_version, expected_hash=expected_hash, repair=repair,
            application=_member(snapshot, selected.project["application"])
            if "application" in selected.project else None,
        )


def _native_candidate_session(
    payload: Path, root: Path, inputs: engine.WheelInstallInputs, selection: dict, *,
    uv: Path, base_python: Path, health_module: str, expected_version: str, expected_hash: str,
    repair: bool = False, application: Path | None = None,
    nested_environment: bool = False,
) -> int:
    if base_python.resolve(strict=True) != Path(sys.executable).resolve(strict=True):
        raise ValueError("native construction must use its selected base interpreter")
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", health_module):
        raise ValueError("native construction requires an ordinary application module")
    if selection["payload_hash"] != expected_hash or selection["runtime_version"] != expected_version:
        raise ValueError("native source/profile selection changed before construction")
    version = expected_version + (".repair." + uuid.uuid4().hex if repair else "")
    with engine.numbered_candidate(
        root, version, plugin=payload, uv=uv, base_python=base_python,
        health_module=health_module, extras=tuple(selection["extras"]),
        local_packages=tuple(selection["local_packages"]), wheel_inputs=inputs,
        application=application, process_retention=True, reuse_generations=False,
        nested_environment=nested_environment,
        expected_source_identity=selection["identity"],
    ) as candidate:
        if candidate.payload_hash != expected_hash:
            raise ValueError("native candidate differs from its admitted construction inputs")
        print(json.dumps({
            "admission": "reuse", "version": candidate.slot.name,
            "payload_hash": candidate.payload_hash, "reused": candidate.reused,
        }), flush=True)
        for line in sys.stdin:
            if len(line) > 4096:
                raise ValueError("native construction command exceeds its protocol bound")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("native construction command must be an object")
            command = request.get("command")
            if command == "complete":
                if request.get("payload_hash") != candidate.payload_hash or request.get(
                    "retain_helpers", False,
                ) is not True:
                    raise ValueError("native completion differs from its retained candidate")
                inputs.verify()
                print(json.dumps({"complete": True}), flush=True)
            elif command == "settled":
                print(json.dumps({"settled": True}), flush=True)
                return 0
            elif command == "abort":
                print(json.dumps({"aborted": True, "candidate_retained": True}), flush=True)
                return 1
            else:
                raise ValueError("unsupported native construction command")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--install-source", type=Path)
    source.add_argument("--wheel-profile", type=Path)
    parser.add_argument("--wheel-directory", type=Path)
    parser.add_argument("--component", required=True)
    parser.add_argument("--payload", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--native-inputs", action="store_true")
    mode.add_argument("--native-session", action="store_true")
    mode.add_argument("--install-application", action="store_true")
    mode.add_argument("--native-current", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--uv", type=Path)
    parser.add_argument("--base-python", type=Path)
    parser.add_argument("--health-module")
    parser.add_argument("--expected-runtime-version")
    parser.add_argument("--expected-payload-hash")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--nested-environment", action="store_true")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.arguments and (
        not args.install_application or args.arguments[0] != "--"
    ):
        parser.error("application arguments require --install-application and a -- separator")
    if args.nested_environment and not (args.wheel_profile is not None and args.native_session):
        parser.error("--nested-environment requires a closed-wheel native construction session")
    if args.native_current:
        if (
            args.root is None or args.payload is None
            or any(value is not None for value in (
                args.install_source, args.wheel_profile, args.wheel_directory,
                args.uv, args.base_python, args.health_module,
                args.expected_runtime_version, args.expected_payload_hash,
            )) or args.repair
        ):
            parser.error("native readiness requires only root, payload and component")
        return 0 if native_current(args.root, args.payload, args.component) else 1
    if args.install_source is None and args.wheel_profile is None:
        parser.error("select an installation source or closed-wheel profile")
    if args.wheel_profile is not None:
        if args.wheel_directory is None or args.payload is None:
            parser.error("closed wheels require a profile, wheel directory and payload")
        construction = (
            args.root, args.uv, args.base_python, args.health_module,
            args.expected_runtime_version, args.expected_payload_hash,
        )
        if args.native_session:
            if any(value is None for value in construction):
                parser.error("native construction requires root, uv, base Python, module and selection")
        elif not args.native_inputs or any(value is not None for value in construction) or args.repair:
            parser.error("closed wheels require --native-inputs or --native-session")
        with wheel_inputs(args.wheel_profile, args.wheel_directory) as inputs:
            selection = wheel_selection(args.payload, args.component, inputs)
            if args.native_inputs:
                print(json.dumps(selection, sort_keys=True, separators=(",", ":")))
                return 0
            return _native_candidate_session(
                args.payload, args.root, inputs, selection,
                uv=args.uv, base_python=args.base_python, health_module=args.health_module,
                expected_version=args.expected_runtime_version, expected_hash=args.expected_payload_hash,
                repair=args.repair,
                nested_environment=args.nested_environment,
            )
    if args.wheel_directory is not None:
        parser.error("--wheel-directory requires --wheel-profile")
    construction = (
        args.root, args.uv, args.base_python, args.health_module,
        args.expected_runtime_version, args.expected_payload_hash,
    )
    if args.native_session or args.install_application:
        if args.payload is None or any(value is None for value in construction):
            parser.error("construction requires payload, root, uv, base Python, module and selection")
    elif any(value is not None for value in construction) or args.repair:
        parser.error("construction arguments require --native-session or --install-application")
    selected = select_project(args.install_source, args.component)
    if args.payload is not None and sources.read_source_identity(
        args.payload, selected.identity.package_version, plugin=args.component,
    ) != selected.identity:
        parser.error("invocation payload differs from selected installation source")
    if args.install_application:
        result = install_application(
            args.install_source, args.component, args.payload, args.root,
            uv=args.uv, base_python=args.base_python, health_module=args.health_module,
            expected_version=args.expected_runtime_version, expected_hash=args.expected_payload_hash,
            arguments=tuple(args.arguments[1:]), repair=args.repair,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.native_session:
        return native_session(
            args.install_source, args.component, args.payload, args.root,
            uv=args.uv, base_python=args.base_python, health_module=args.health_module,
            expected_version=args.expected_runtime_version, expected_hash=args.expected_payload_hash,
            repair=args.repair,
        )
    if args.native_inputs:
        with project_inputs(args.install_source, args.component) as (selected, inputs):
            print(json.dumps(native_selection(selected, inputs), sort_keys=True, separators=(",", ":")))
        return 0
    print(json.dumps({
        "identity": sources.manifest_fields(selected.identity),
        "source_descriptor": selected.source_descriptor,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
