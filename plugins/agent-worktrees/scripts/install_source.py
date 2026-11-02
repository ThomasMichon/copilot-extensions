"""Source-owned project inputs, independent of an installed runtime."""
from __future__ import annotations

from contextlib import contextmanager
import argparse
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer_engine as engine
from installer_wheel_inputs import WheelLimits, inspect_archive
from installer_wheel_snapshot import retain_wheel_inputs
import versioned_runtime_source as sources

SCHEMA = "copilot-extensions.install-source"
MANIFEST = "install-source.json"


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
    return ProjectSource(payload, identity, record, project_key)


def select(snapshot: Path) -> ProjectSource:
    return select_project(snapshot, "worktree-manager")


@contextmanager
def project_inputs(snapshot: Path, component: str, *, deadline: float | None = None):
    """Hold the prepared complete wheel set through canonical installed health."""
    deadline = deadline or time.monotonic() + 600
    selected = select_project(snapshot, component)
    project = selected.project
    if "profile" not in project or "wheel_inputs" not in project:
        raise ValueError("installation source has first-party inputs only; prepare its native wheel profile")
    profile = _read(_member(snapshot, project.get("profile")))
    if profile.get("release") != {
        "identity": "source:" + selected.identity.source_commit,
        "sha256": "sha256:" + selected.identity.assembly_hash,
    } or profile.get("component") != component:
        raise ValueError("project wheel profile differs from its source association")
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
        archives[filename] = _member(snapshot, project["wheel_inputs"] + "/" + filename)
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
                re.sub(r"[-_.]+", "-", facts.name).lower() != row["name"]
                or facts.version != row["version"]
                or facts.requires_python != row["requires_python"]
                or list(facts.requires_dist) != row["requires_dist"]
                or list(facts.provides_extras) != row["provides_extras"]
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
        yield selected, inputs
        inputs.verify()
        if select_project(snapshot, component).identity != selected.identity:
            raise ValueError("project source changed during installation")
        if select_project(snapshot, component).source_descriptor != selected.source_descriptor:
            raise ValueError("project repository provenance changed during installation")


@contextmanager
def manager_inputs(snapshot: Path, *, deadline: float | None = None):
    with project_inputs(snapshot, "worktree-manager", deadline=deadline) as inputs:
        yield inputs


def native_selection(selected: ProjectSource, inputs: engine.WheelInstallInputs) -> dict:
    """Use the constructor's existing source/profile identity before native setup."""
    inputs.verify()
    receipt = inputs.receipt()
    packages = engine._package_roots(selected.payload)
    distributions = receipt["distributions"]
    local = tuple(sorted(
        engine._package_name(package) for package in packages
        if engine._package_name(package) in distributions
    ))
    name = engine._package_name(selected.payload)
    if name not in local:
        raise ValueError("native wheel profile omits its application")
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
    for package_name in local:
        row = associated.get(package_name, {})
        if any(
            distributions[package_name][key] != row.get(key) for key in ("filename", "sha256")
        ):
            raise ValueError("native first-party wheel differs from its source association")
    profile = receipt["binding"]
    extras = tuple(next(
        row["selected_extras"] for row in profile["wheels"] if row["name"] == name
    ))
    extras = engine._selected_extras(selected.payload, extras)
    return {
        "identity": sources.manifest_fields(selected.identity),
        "source_descriptor": selected.source_descriptor,
        "runtime_version": selected.identity.runtime_version + ".profile." + receipt["lock_id"][7:23],
        "payload_hash": engine._wheel_construction_hash(
            engine._construction_hash(engine._source_files(packages), extras, local), inputs,
        ),
        "extras": list(extras), "local_packages": list(local),
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


def native_session(
    snapshot: Path, component: str, payload: Path, root: Path, *,
    uv: Path, base_python: Path, health_module: str,
    expected_version: str, expected_hash: str, repair: bool = False,
) -> int:
    """Retain a completed wheel candidate while its native owner settles setup."""
    if base_python.resolve(strict=True) != Path(sys.executable).resolve(strict=True):
        raise ValueError("source construction must use its selected base interpreter")
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", health_module):
        raise ValueError("source construction requires an ordinary application module")
    with project_inputs(snapshot, component) as (selected, inputs):
        if sources.read_source_identity(
            payload, selected.identity.package_version, plugin=component,
        ) != selected.identity:
            raise ValueError("invocation payload differs from selected installation source")
        selection = native_selection(selected, inputs)
        if selection["payload_hash"] != expected_hash or selection["runtime_version"] != expected_version:
            raise ValueError("native source/profile selection changed before construction")
        version = expected_version + (".repair." + uuid.uuid4().hex if repair else "")
        with engine.numbered_candidate(
            root, version, plugin=selected.payload, uv=uv, base_python=base_python,
            health_module=health_module, extras=tuple(selection["extras"]),
            local_packages=tuple(selection["local_packages"]), wheel_inputs=inputs,
            application=_member(snapshot, selected.project["application"])
            if "application" in selected.project else None,
            process_retention=True, reuse_generations=False,
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
    parser.add_argument("--install-source", required=True, type=Path)
    parser.add_argument("--component", required=True)
    parser.add_argument("--payload", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--native-inputs", action="store_true")
    mode.add_argument("--native-session", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--uv", type=Path)
    parser.add_argument("--base-python", type=Path)
    parser.add_argument("--health-module")
    parser.add_argument("--expected-runtime-version")
    parser.add_argument("--expected-payload-hash")
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args(argv)
    selected = select_project(args.install_source, args.component)
    if args.payload is not None and sources.read_source_identity(
        args.payload, selected.identity.package_version, plugin=args.component,
    ) != selected.identity:
        parser.error("invocation payload differs from selected installation source")
    construction = (
        args.root, args.uv, args.base_python, args.health_module,
        args.expected_runtime_version, args.expected_payload_hash,
    )
    if args.native_session:
        if args.payload is None or any(value is None for value in construction):
            parser.error("native construction requires payload, root, uv, base Python, module and selection")
        return native_session(
            args.install_source, args.component, args.payload, args.root,
            uv=args.uv, base_python=args.base_python, health_module=args.health_module,
            expected_version=args.expected_runtime_version, expected_hash=args.expected_payload_hash,
            repair=args.repair,
        )
    if any(value is not None for value in construction) or args.repair:
        parser.error("construction arguments require --native-session")
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
