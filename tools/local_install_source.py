"""Freeze local installation inputs without changing declared package versions.

No publication, repository registration or runtime activation happens here.
The output is disposable build data, not a release or a source approval token.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import contextmanager, nullcontext, redirect_stdout
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
from installer_dependency_config import native_retained_path

import prepare_runtime_source as preparation
import materialize_main as materialization
import source_preparation_process as preparation_process

MANIFEST = "install-source.json"
SCHEMA = "copilot-extensions.install-source"


def _selected_project_paths(
    repository: Path, commit: str, projects: tuple[str, ...] | None, deadline: float,
) -> tuple[str, ...] | None:
    if projects is None:
        return None
    from promotion_python_artifacts import _PLUGIN
    from standalone_python_artifacts import COMPONENTS
    from application_sources import project_locations, tomllib

    if (
        not isinstance(projects, tuple) or not projects
        or any(not isinstance(key, str) for key in projects)
        or len(set(projects)) != len(projects)
    ):
        raise ValueError("source selection requires unique declared Python project keys")
    root_manifest = preparation._git(
        repository, "ls-tree", commit, "--", "pyproject.toml", deadline=deadline,
    )
    locations = {}
    if root_manifest:
        if root_manifest.split()[0] not in {"100644", "100755"}:
            raise ValueError("repository project declaration is not ordinary at the source commit")
        locations = project_locations(tomllib.loads(preparation._git(
            repository, "show", f"{commit}:pyproject.toml", deadline=deadline,
        )))
    paths = []
    for key in projects:
        if key.startswith("standalone:") and key[11:] in COMPONENTS:
            relative = COMPONENTS[key[11:]]
        elif _PLUGIN.fullmatch(key):
            relative = locations.get(key, "plugins/" + key)
        else:
            raise ValueError("unsupported selected source project")
        entry = preparation._git(
            repository, "ls-tree", commit, "--", relative + "/pyproject.toml", deadline=deadline,
        )
        if not entry or entry.split()[0] not in {"100644", "100755"}:
            raise ValueError("selected Python project is absent or not ordinary at the source commit")
        paths.append(relative)
    return tuple(paths)


@contextmanager
def _source_phase(
    observer: Callable[[int, int, float], None] | None, phase: int, started: float,
):
    if observer is not None:
        observer(phase, 0, time.monotonic() - started)
    yield
    if observer is not None:
        observer(phase, 1, time.monotonic() - started)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("local artifact builder is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def repository_source(repository: Path, descriptor: dict | None, *, deadline: float) -> dict | None:
    if descriptor is None:
        names = preparation._git(repository, "remote", deadline=deadline).splitlines()
        if "origin" not in names:
            return None
        remote = preparation._git(repository, "remote", "get-url", "origin", deadline=deadline)
        if remote.startswith("git@") and ":" in remote:
            host, path = remote[4:].split(":", 1)
            remote = "ssh://git@" + host + "/" + path
        parsed = urlsplit(remote)
        if (
            parsed.password or parsed.username and not (
                parsed.scheme == "ssh" and parsed.username == "git"
            ) or parsed.query or parsed.fragment
        ):
            raise ValueError("installation source repository must not contain credentials or query data")
        descriptor = {
            "source": "github" if parsed.hostname == "github.com" else "git",
            "url": remote,
        }
    if not isinstance(descriptor, dict):
        raise ValueError("installation source requires an existing source descriptor object")
    name = "_local_install_source_provenance"
    spec = importlib.util.spec_from_file_location(
        name,
        Path(__file__).resolve().parents[1] / "libs" / "installation-context" / "installation_context.py",
    )
    if spec is None or spec.loader is None:
        raise ValueError("installation source provenance normalizer is unavailable")
    primitive = importlib.util.module_from_spec(spec)
    sys.modules[name] = primitive
    spec.loader.exec_module(primitive)
    normalized = primitive.normalize_source(descriptor)
    return {"kind": normalized.kind, "canonical": normalized.canonical, "ref": normalized.ref}


def compose(
    repository: Path, commit: str, main_head: str | None, output: Path, *,
    finalized_main: bool = False, deadline: float | None = None,
    source_descriptor: dict | None = None,
    projects: tuple[str, ...] | None = None,
    observer: Callable[[int, int, float], None] | None = None,
) -> dict:
    """Materialize one Git object; only finalized Main selection needs ancestry."""
    started = time.monotonic()
    deadline = started + 600 if deadline is None else deadline
    preparation._remaining(deadline)
    if observer is not None and not callable(observer):
        raise ValueError("source phase observer must be callable")
    repository = repository.resolve(strict=True)
    descriptor = repository_source(repository, source_descriptor, deadline=deadline)
    output = Path(native_retained_path(output))
    selected = [("source", commit)]
    if main_head is not None:
        selected.append(("Main head", main_head))
    for name, value in selected:
        if preparation._OBJECT_ID.fullmatch(value) is None:
            raise ValueError(f"{name} requires a full Git commit")
        if preparation._git(repository, "rev-parse", f"{value}^{{commit}}", deadline=deadline) != value:
            raise ValueError(f"{name} does not name the requested Git commit")
    project_paths = _selected_project_paths(repository, commit, projects, deadline)
    if output.exists() or output.is_symlink():
        raise ValueError("install-source output already exists; retain previous attempts")
    if finalized_main:
        if main_head is None:
            raise ValueError("finalized Main selection requires --main-head")
        try:
            preparation._git(
                repository, "merge-base", "--is-ancestor", commit, main_head, deadline=deadline,
            )
        except preparation.SourcePreparationError as error:
            raise ValueError("last-good source ancestry against selected Main could not be verified") from error
    composition = None
    if not finalized_main and main_head is not None:
        from local_runtime_promotion import compose as compose_runtime

        composition = compose_runtime(
            repository, source_commit=commit, main_seed=main_head, deadline=deadline,
            **({"select_inputs": lambda source: _application_export_paths(source, projects)}
               if projects is not None else {}),
        )
        preparation._remaining(deadline)
    preparation._private_output_directory(output)
    archive = output / "source.tar"
    with _source_phase(observer, 1, started):
        preparation._archive(
            repository, composition.tree if composition is not None else commit,
            archive, deadline=deadline,
        )
    source = output / "source"
    source.mkdir()
    file_pointer_candidates: list[Path] = []
    with _source_phase(observer, 2, started):
        preparation._extract_archive(
            archive, source, deadline=deadline,
            file_pointer_candidates=file_pointer_candidates,
            **({"include_paths": composition.export_paths}
               if composition is not None and composition.export_paths is not None else {}),
        )
    archive.unlink()
    source_tree = preparation._git(repository, "rev-parse", f"{commit}^{{tree}}", deadline=deadline)
    if not finalized_main and composition is None:
        with _source_phase(observer, 3, started):
            if project_paths is None:
                log = materialization.materialize(
                    source, canonical_root=source,
                    file_pointer_candidates=file_pointer_candidates,
                )
                refused = [line for line in log if line.startswith("SKIP")]
                if refused:
                    raise ValueError("source materialization refused:\n" + "\n".join(refused))
            else:
                for relative in project_paths:
                    consumer = source / relative
                    preparation.materialize_consumer(
                        source, consumer, consumer, deadline=deadline,
                        file_pointer_candidates=[
                            path for path in file_pointer_candidates if path.is_relative_to(consumer)
                        ],
                    )
    preparation._remaining(deadline)
    return {
        "schema": SCHEMA, "version": 1,
        "kind": "main" if finalized_main else "source",
        "source_commit": commit, "source_tree": source_tree, "main_head": main_head,
        **({"composition": {"main_seed": composition.main_seed, "tree": composition.tree}}
           if composition is not None else {}),
        **({"source_descriptor": descriptor} if descriptor is not None else {}),
        "projects": {},
    }


def build_manager(
    output: Path, record: dict, *, source_policy: str = "governed",
    min_age_days: int | None = None, reuse_source: Path | None = None,
    uv: str | Path | None = None, deadline: float | None = None,
) -> dict:
    return build_projects(
        output, record, projects=("standalone:worktree-manager",),
        source_policy=source_policy, min_age_days=min_age_days, reuse_source=reuse_source,
        uv=uv, deadline=deadline,
    )


def _build_input_roots(source: Path, selected: tuple[str, ...], builder) -> tuple[Path, ...]:
    if "composition:core" in selected:
        return (source,)
    from standalone_python_artifacts import source_closure
    from application_sources import canonical_application

    roots = set()
    for key in selected:
        if key.startswith("standalone:"):
            project, dependencies, resources = source_closure(key.split(":", 1)[1], source, builder)
            roots.update((project, *resources, *(path for _name, path in dependencies)))
        else:
            project = canonical_application(source, key)
            roots.add(project)
            roots.update(path for _name, path in builder.resolve_vendored_libs(project))
            if (project / "setup.py").is_file():
                from plugin_runtime_resources import build_inputs

                _data, resources = build_inputs(project, root=source)
                roots.update(path.parent for path in resources.values())
    result = []
    for path in sorted(roots, key=lambda value: (len(value.parts), str(value))):
        if not path.is_relative_to(source) or builder.uer._find_symlinked_ancestor(path, source):
            raise ValueError("local build input is linked or outside the retained source")
        if not any(path.is_relative_to(parent) for parent in result):
            result.append(path)
    return tuple(result)


def _build_input_fingerprint(roots: tuple[Path, ...]) -> tuple:
    from versioned_runtime import fingerprint_source
    from versioned_runtime_source import SOURCE_FILE, assembly_hash

    sidecars = tuple(
        root / name for root in roots for name in (SOURCE_FILE, "snapshot-provenance.json")
        if (root / name).exists() or (root / name).is_symlink()
    )
    return tuple(assembly_hash(root) for root in roots), (
        fingerprint_source(sidecars) if sidecars else None
    )


def _application_export_paths(source: Path, projects: tuple[str, ...]) -> tuple[str, ...]:
    import build_python_artifacts as builder
    from standalone_python_artifacts import COMPONENTS

    if not projects or len(set(projects)) != len(projects):
        raise ValueError("local source requires unique declared Python projects")
    for key in projects:
        if key == "composition:core":
            continue
        if key.startswith("standalone:"):
            relative = COMPONENTS.get(key.split(":", 1)[1])
        else:
            relative = "plugins/" + key if builder.uer.is_safe_lib_name(key) else None
        if relative is None or not (source / relative / "pyproject.toml").is_file():
            raise ValueError("local source requires unique declared Python projects")
    builder = _load_module(Path(builder.__file__), "_local_install_export_builder")
    builder.REPO, builder.PLUGINS_DIR, builder.LIBS_DIR = source, source / "plugins", source / "libs"
    roots = set(_build_input_roots(source, projects, builder))
    roots.update((source / "plugins").glob("*/pyproject.toml"))
    roots.update(
        source / relative / "pyproject.toml"
        for relative in COMPONENTS.values() if (source / relative).exists()
    )
    roots.update(
        source / relative for relative in ("bundle/core.json", ".github/plugin/marketplace.json")
        if (source / relative).exists() or (source / relative).is_symlink()
    )
    selected = []
    for path in sorted(roots, key=lambda value: (len(value.parts), str(value))):
        if not path.is_relative_to(source) or builder.uer._find_symlinked_ancestor(path, source):
            raise ValueError("application export input is linked or outside the retained source")
        if not any(path.is_relative_to(parent) for parent in selected):
            selected.append(path)
    return tuple(path.relative_to(source).as_posix() for path in selected)


@contextmanager
def _build_workspace(
    source: Path, output: Path, selected: tuple[str, ...], builder, *,
    retain_toolchain: bool = False,
):
    """Keep Windows backend and interpreter paths short while binding their inputs."""
    declaration = source / "pyproject.toml"
    original_declaration = declaration.read_bytes() if declaration.exists() else None
    if os.name != "nt":
        yield source, output / "build-tools", lambda: None
        if (declaration.read_bytes() if declaration.exists() else None) != original_declaration:
            raise ValueError("repository project declaration changed during artifact construction")
        return

    with tempfile.TemporaryDirectory(
        prefix="ce-source-", dir=native_retained_path(tempfile.gettempdir()),
    ) as directory:
        workspace = Path(directory) / "w"
        staged = workspace / "source"
        roots = _build_input_roots(source, selected, builder)
        original_hash = _build_input_fingerprint(roots)
        for root in roots:
            destination = staged / root.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(root, destination, symlinks=True)
        if original_declaration is not None:
            (staged / "pyproject.toml").write_bytes(original_declaration)
        staged_roots = tuple(staged / root.relative_to(source) for root in roots)
        if _build_input_fingerprint(staged_roots) != original_hash:
            raise ValueError("compact build source differs from the retained installation source")
        def verify():
            if _build_input_fingerprint(staged_roots) != original_hash:
                raise ValueError("compact build source changed during artifact construction")
            if _build_input_fingerprint(roots) != original_hash:
                raise ValueError("retained installation source changed during artifact construction")
            staged_declaration = staged / "pyproject.toml"
            if (
                (staged_declaration.read_bytes() if staged_declaration.exists() else None) != original_declaration
                or (declaration.read_bytes() if declaration.exists() else None) != original_declaration
            ):
                raise ValueError("repository project declaration changed during artifact construction")

        yield staged, output / "build-tools" if retain_toolchain else workspace / "build-tools", verify
        verify()


def _reuse_projects(
    snapshot: Path, output: Path, record: dict, selected: tuple[str, ...],
    identities: dict, roster: dict, *, source_policy: str, min_age_days: int | None,
    copy_wheels: bool = True, deadline: float | None = None,
) -> dict:
    """Revalidate explicitly selected prior inputs without reconstructing them."""
    from local_install_profile import source_inputs
    from installer_wheel_snapshot import retain_wheel_inputs
    from packaging.tags import parse_tag, sys_tags
    from promotion_python_artifacts import validate_manifest, validate_source_binding, validate_wheel
    from standalone_python_artifacts import COMPONENTS, project_key

    if not snapshot.is_absolute() or snapshot.is_symlink():
        raise ValueError("reused installation source requires an absolute ordinary snapshot")
    previous = source_inputs._read(snapshot / MANIFEST)
    binding = (
        "schema", "version", "kind", "source_commit", "source_tree",
        "main_head", "composition", "source_descriptor",
    )
    if any(previous.get(key) != record.get(key) for key in binding):
        raise ValueError("reused installation source/composition differs from the selected source")
    manifests, shared_toolchain = {}, None
    compatible_tags = frozenset(sys_tags())
    deadline = time.monotonic() + 600 if deadline is None else deadline
    for key in selected:
        preparation._remaining(deadline)
        component = key.split(":", 1)[1] if key.startswith("standalone:") else key
        old_key = "worktree-manager" if key == "standalone:worktree-manager" else key
        if key != "composition:core":
            observed = source_inputs.select_project(snapshot, component)
            if observed.identity != identities[key]:
                raise ValueError("reused project source differs from the selected source")
            old_key = observed.project_key
            project = observed.project
        else:
            project = previous["projects"].get(old_key)
        if not isinstance(project, dict):
            raise ValueError("reused installation source omits a selected project")
        manifest = project.get("artifact")
        if not isinstance(manifest, dict):
            raise ValueError("reused installation source has no complete artifact manifest")
        validate_manifest(manifest)
        if project_key(manifest) != key or manifest["version"] != roster[key]:
            raise ValueError("reused artifact differs from the selected project/version")
        toolchain = manifest["build_toolchain"]
        provenance = toolchain["provenance"]
        if provenance["source_policy"] != source_policy:
            raise ValueError("reused artifact build source policy differs from the selected policy")
        if source_policy == "seasoned-public":
            from seasoned_public_feed import DEFAULT_MIN_AGE_DAYS

            if provenance["min_age_days"] != (
                DEFAULT_MIN_AGE_DAYS if min_age_days is None else min_age_days
            ):
                raise ValueError("reused artifact build seasoning differs from the selected policy")
        if shared_toolchain is not None and toolchain != shared_toolchain:
            raise ValueError("reused artifacts do not share one admitted build-toolchain closure")
        shared_toolchain = toolchain
        validate_source_binding(manifest, output / "source")
        rows = manifest["wheels"]
        archives = {
            row["filename"]: source_inputs._member(
                snapshot, project["wheels"] + "/" + row["filename"],
            ) for row in rows
        }
        expected = {row["filename"]: row["sha256"] for row in rows}
        wheel_dir = output / "wheels" / ("composition-core" if key == "composition:core" else component)
        with retain_wheel_inputs(
            archives, expected, max_wheels=256, max_archive_bytes=128 << 20,
            max_total_bytes=512 << 20, max_snapshot_bytes=1 << 30, deadline=deadline,
        ) as retained:
            for row in rows:
                if not compatible_tags.intersection(parse_tag("-".join(
                    row[field] for field in ("python_tag", "abi_tag", "platform_tag")
                ))):
                    raise ValueError("reused wheel is incompatible with the selected interpreter/platform")
                archive = retained.archives[row["filename"]]
                validate_wheel(
                    archive, row, source_dir=output / "source" / row["source"],
                    component=(
                        next((name for name, relative in COMPONENTS.items() if relative == row["source"]), None)
                        if key == "composition:core" else component if row["role"] == "standalone" else None
                    ),
                    candidate_root=output / "source",
                )
                if copy_wheels:
                    wheel_dir.mkdir(parents=True, exist_ok=True)
                    archive.seek(0)
                    with (wheel_dir / row["filename"]).open("xb") as destination:
                        shutil.copyfileobj(archive, destination, 1 << 20)
            retained.verify()
        current = source_inputs._read(snapshot / MANIFEST)
        if (
            any(current.get(field) != previous.get(field) for field in binding)
            or current["projects"].get(old_key) != project
        ):
            raise ValueError("reused installation source changed while wheel inputs were held")
        manifests[key] = manifest
    return manifests


def _source_projects(source: Path, record: dict, projects: tuple[str, ...] | None):
    from promotion_python_artifacts import candidate_projects
    from standalone_python_artifacts import COMPONENTS
    from application_sources import canonical_application
    from versioned_runtime_source import (
        SOURCE_FILE, SourceIdentity, assembly_hash, payload_package_version, runtime_version,
    )

    roster = candidate_projects(source)
    selected = tuple(roster) if projects is None else projects
    if not selected or len(set(selected)) != len(selected) or set(selected) - roster.keys():
        raise ValueError("local source requires unique declared Python projects")
    identities = {}
    payloads = {
        key: canonical_application(source, key)
        for key in selected if key != "composition:core"
    }
    for key in sorted(payloads, key=lambda key: len(payloads[key].parts), reverse=True):
        payload = payloads[key]
        component = key.split(":", 1)[1] if key.startswith("standalone:") else key
        version = payload_package_version(payload, component)
        if version != roster[key]:
            raise ValueError("local source package and project versions differ")
        digest = assembly_hash(payload)
        identity = SourceIdentity(
            component, version, record["source_commit"], record["source_tree"], digest,
            runtime_version(version, record["source_commit"], digest),
        )
        (payload / SOURCE_FILE).write_text(
            json.dumps(identity.document(), sort_keys=True) + "\n", encoding="utf-8",
        )
        identities[key] = identity
    return roster, selected, payloads, identities


@contextmanager
def _built_projects(
    output: Path, record: dict, *, projects: tuple[str, ...] | None = None,
    source_policy: str = "governed", min_age_days: int | None = None,
    reuse_source: Path | None = None,
    uv: str | Path | None = None, deadline: float | None = None,
    retain_toolchain: bool = False,
):
    """Keep one existing builder/toolchain alive through complete profile readback."""
    import build_python_artifacts as builder
    from build_toolchain_lock import resolve_toolchain_lock
    from promotion_python_artifacts import project_key
    from standalone_python_artifacts import COMPONENTS, build_standalone_artifacts
    from versioned_runtime_source import assembly_hash

    preparation._remaining(deadline)
    output = Path(native_retained_path(output))
    source = output / "source"
    roster, selected, payloads, identities = _source_projects(source, record, projects)
    reused = _reuse_projects(
        reuse_source, output, record, selected, identities, roster,
        source_policy=source_policy, min_age_days=min_age_days, deadline=deadline,
    ) if reuse_source is not None else None
    builder = _load_module(Path(builder.__file__), "_local_install_artifact_builder") if reused is None else None
    execution = {}
    if uv is not None:
        execution["uv"] = uv
    if deadline is not None:
        execution["deadline"] = deadline
    if execution and builder is not None:
        build_wheel = builder.build_wheel

        def bounded_build_wheel(*args, **kwargs):
            if uv is not None:
                kwargs["uv"] = str(uv)
            if deadline is not None:
                preparation._remaining(deadline)
                kwargs["deadline"] = min(deadline, time.monotonic() + 180)
            return build_wheel(*args, **kwargs)

        builder.build_wheel = bounded_build_wheel
    workspace = nullcontext((source, output / "build-tools", lambda: None))
    if builder is not None:
        builder.REPO, builder.PLUGINS_DIR, builder.LIBS_DIR = source, source / "plugins", source / "libs"
        workspace = _build_workspace(
            source, output, selected, builder, retain_toolchain=retain_toolchain,
        )
    with workspace as (build_source, toolchain_dir, verify_workspace):
        toolchain = None
        if reused is None:
            builder.REPO = build_source
            builder.PLUGINS_DIR, builder.LIBS_DIR = build_source / "plugins", build_source / "libs"
            toolchain = resolve_toolchain_lock(
                toolchain_dir, python=getattr(sys, "_base_executable", sys.executable),
                source_policy=source_policy, min_age_days=min_age_days,
                **execution,
            )
        prepared = {}
        for key in selected:
            preparation._remaining(deadline)
            if key == "composition:core":
                from core_python_artifacts import build_core_artifacts

                manifest = reused[key] if reused is not None else build_core_artifacts(
                    out_dir=output / "wheels" / "composition-core", toolchain=toolchain, builder=builder,
                    **execution,
                )
                project = {"payload": "source", "wheels": "wheels/composition-core", "artifact": manifest}
            else:
                standalone = key.startswith("standalone:")
                component = key.split(":", 1)[1] if standalone else key
                relative = payloads[key].relative_to(source).as_posix()
                identity = identities[key]
                wheel_dir = output / "wheels" / component
                manifest = reused[key] if reused is not None else (
                    build_standalone_artifacts(
                        component, out_dir=wheel_dir, toolchain=toolchain, builder=builder,
                        **execution,
                    ) if standalone else builder.build_plugin_artifacts(
                        component, out_dir=wheel_dir, toolchain=toolchain,
                        **execution,
                    )
                )
                project = {
                    "payload": "source/" + relative, "wheels": "wheels/" + component,
                    "source": identity.document(), "artifact": manifest,
                }
            if (
                project_key(manifest) != key or manifest["version"] != roster[key]
                or toolchain is not None and manifest["build_toolchain"] != toolchain.record
            ):
                raise ValueError("local artifact builder returned another project, version or toolchain")
            prepared["worktree-manager" if key == "standalone:worktree-manager" else key] = project
        if deadline is not None:
            preparation._remaining(deadline)
        if any(assembly_hash(payloads[key]) != identity.assembly_hash for key, identity in identities.items()):
            raise ValueError("local artifact construction changed its associated source payload")
        verify_workspace()
        preparation._remaining(deadline)
        record["projects"].update(prepared)
        (output / MANIFEST).write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        yield record, toolchain
        if any(assembly_hash(payloads[key]) != identity.assembly_hash for key, identity in identities.items()):
            raise ValueError("local profile preparation changed its associated source payload")
    preparation._remaining(deadline)


def build_projects(
    output: Path, record: dict, *, projects: tuple[str, ...] | None = None,
    source_policy: str = "governed", min_age_days: int | None = None,
    reuse_source: Path | None = None,
    uv: str | Path | None = None, deadline: float | None = None,
) -> dict:
    with _built_projects(
        output, record, projects=projects, source_policy=source_policy,
        min_age_days=min_age_days, reuse_source=reuse_source, uv=uv, deadline=deadline,
    ) as (prepared, _toolchain):
        return prepared


def _prepare_profiles(
    built: Path, record: dict, output: Path, components: tuple[str, ...], extras: dict,
    toolchain, *, uv: str, deadline: float,
    reuse_source: Path | None = None,
) -> dict[str, str] | None:
    from build_toolchain_lock import _query_marker_environment, sanitize_subprocess_env

    environment = _query_marker_environment(Path(sys.executable), deadline=deadline)
    preparation._remaining(deadline)
    result = preparation_process.run(
        [
            sys.executable, str(Path(__file__).with_name("local_install_profile.py")),
            "--install-source", str(built), "--output", str(output / "profiles"),
            "--uv", uv, "--deadline", str(deadline),
            "--application-components", json.dumps(components),
            "--application-extras", json.dumps(extras),
            "--target-environment", json.dumps(environment, sort_keys=True),
            "--source-record-sha256", hashlib.sha256(
                json.dumps(record, sort_keys=True).encode("utf-8"),
            ).hexdigest(),
            *(["--reuse-applications", str(reuse_source),
               "--toolchain-record", json.dumps(toolchain.record, sort_keys=True)]
              if reuse_source is not None else []),
        ],
        capture_output=True, text=True, env=sanitize_subprocess_env(),
        deadline=deadline,
    )
    preparation._remaining(deadline)
    if result.returncode == 3 and reuse_source is not None:
        return None
    if result.returncode:
        raise ValueError(f"application profile preparation failed: {result.stderr.strip()}")
    snapshots = json.loads(result.stdout)
    expected_root = output if reuse_source is None else reuse_source
    expected = {name: str(expected_root / "profiles" / name / "snapshot") for name in components}
    if snapshots != expected:
        raise ValueError("application profile worker returned another selected snapshot")
    return snapshots


def _application_completion(snapshots: dict[str, str], *, deadline: float) -> dict:
    from versioned_runtime import fingerprint_source

    return {
        "schema": "copilot-extensions.prepared-applications", "version": 1,
        "snapshots": snapshots,
        "content": {
            name: fingerprint_source(
                [Path(snapshot)], on_progress=lambda: preparation._remaining(deadline),
            ) for name, snapshot in snapshots.items()
        },
    }


def _reused_applications(
    built: Path, record: dict, output: Path, components: tuple[str, ...], extras: dict,
    *, uv: str, deadline: float,
) -> dict[str, str] | None:
    from build_toolchain_lock import ArtifactBuildError, resolve_toolchain_lock
    from install_source import _member, _read

    try:
        permissions = preparation_process.procutil_module("python_cache_permissions")
        permissions.check(output.parent, private=True)
        permissions.check_ancestors(output.parent)
    except (ImportError, OSError):
        return None
    candidates = []
    with os.scandir(output.parent) as entries:
        for index, entry in enumerate(entries):
            preparation._remaining(deadline)
            if index >= 4096:
                break
            if not entry.is_dir(follow_symlinks=False):
                continue
            previous = Path(entry.path)
            try:
                permissions.check(previous, private=True)
                if previous.is_symlink() or getattr(previous.lstat(), "st_file_attributes", 0) & 0x400:
                    continue
                completion = _read(_member(previous, "applications.json"))
                if (
                    completion.get("schema") != "copilot-extensions.prepared-applications"
                    or type(completion.get("version")) is not int or completion["version"] != 1
                    or not isinstance(completion.get("snapshots"), dict)
                    or set(completion["snapshots"]) != set(components)
                ):
                    continue
                old = _read(_member(previous, "inputs/" + MANIFEST))
                if any(old.get(key) != record.get(key) for key in (
                    "schema", "version", "kind", "source_commit", "source_tree",
                    "main_head", "composition", "source_descriptor",
                )):
                    continue
                candidates.append((entry.stat(follow_symlinks=False).st_mtime_ns, previous, old))
            except (OSError, ValueError):
                continue
    for _modified, previous, old in sorted(candidates, reverse=True)[:32]:
        preparation._remaining(deadline)
        try:
            permissions.check(previous, private=True)
            permissions.check_bytecode(
                previous, max_entries=100_000,
                seconds=preparation._remaining(deadline),
            )
            toolchain = resolve_toolchain_lock(
                _member(previous, "inputs/build-tools"),
                python=getattr(sys, "_base_executable", sys.executable),
                uv=uv, deadline=deadline, reuse_only=True,
            )
            projects = old.get("projects")
            if not isinstance(projects, dict) or not projects or any(
                not isinstance(project, dict) or not isinstance(project.get("artifact"), dict)
                or project["artifact"].get("build_toolchain") != toolchain.record
                for project in projects.values()
            ):
                continue
        except (OSError, ValueError, ArtifactBuildError):
            continue
        preparation._remaining(deadline)
        (built / MANIFEST).write_text(
            json.dumps(record, sort_keys=True) + "\n", encoding="utf-8",
        )
        snapshots = _prepare_profiles(
            built, record, output, components, extras, toolchain,
            uv=uv, deadline=deadline, reuse_source=previous,
        )
        if snapshots is not None:
            return snapshots
    return None


def prepare_applications(
    repository: Path, commit: str, main_head: str, output: Path, *,
    components: tuple[str, ...], branch: str, uv: str, deadline: float,
    extras: dict[str, tuple[str, ...]] | None = None,
    source_descriptor: dict | None = None,
) -> dict[str, str]:
    """Prepare complete applications through the existing composition/profile owners."""
    from standalone_python_artifacts import COMPONENTS

    preparation._remaining(deadline)
    if (
        not components or len(components) > 64
        or any(not isinstance(name, str) or not preparation._PLUGIN.fullmatch(name)
               for name in components)
        or len(set(components)) != len(components)
        or not isinstance(uv, str) or not uv or "\0" in uv
    ):
        raise ValueError("application preparation requires bounded unique components and a UV executable")
    extras = {} if extras is None else extras
    if (
        not isinstance(extras, dict) or set(extras) - set(components)
        or any(
            not isinstance(values, tuple) or len(values) > 64
            or any(not isinstance(value, str) or not preparation._PLUGIN.fullmatch(value)
                   for value in values)
            or len(set(values)) != len(values)
            for values in extras.values()
        )
    ):
        raise ValueError("application extras require bounded unique names for selected components")
    repository = repository.resolve(strict=True)
    if output.is_symlink():
        raise ValueError("application output must be new and outside the source checkout")
    output = output.absolute()
    permissions = preparation_process.procutil_module("python_cache_permissions")
    permissions.check(output.parent, ancestor=True)
    permissions.check_ancestors(output.parent)
    output = output.resolve()
    if output.exists() or output == repository or output.is_relative_to(repository):
        raise ValueError("application output must be new and outside the source checkout")
    output = Path(native_retained_path(output))
    projects = tuple("standalone:" + name if name in COMPONENTS else name for name in components)
    admitted = preparation._admit(repository, commit, branch, deadline=deadline)
    owner = preparation_process.procutil_module("_daemon_instance_owner")
    owner.mkdir_private(output)
    permissions.check(output, private=True)
    built = output / "inputs"
    record = compose(
        repository, commit, main_head, built, finalized_main=branch == "main",
        deadline=deadline, source_descriptor=source_descriptor, projects=projects,
    )
    if record["source_commit"] != commit or record["source_tree"] != admitted[1]:
        raise ValueError("application composition differs from the admitted source")
    reused = _reused_applications(
        built, record, output, components, extras, uv=uv, deadline=deadline,
    )
    if reused is not None:
        if preparation._admit(repository, commit, branch, deadline=deadline) != admitted:
            raise ValueError("source checkout changed during application reuse")
        preparation._remaining(deadline)
        return reused
    with _built_projects(
        built, record, projects=projects, uv=uv, deadline=deadline, retain_toolchain=True,
    ) as (_record, toolchain):
        snapshots = _prepare_profiles(
            built, record, output, components, extras, toolchain, uv=uv, deadline=deadline,
        )
    if preparation._admit(repository, commit, branch, deadline=deadline) != admitted:
        raise ValueError("source checkout changed during application preparation")
    preparation._remaining(deadline)
    completion = _application_completion(snapshots, deadline=deadline)
    preparation._remaining(deadline)
    (output / "applications.json").write_text(
        json.dumps(completion, sort_keys=True) + "\n", encoding="utf-8",
    )
    return snapshots


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--source-checkout", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-descriptor", type=json.loads, help="Existing installation source descriptor")
    parser.add_argument("--main-head", help="Fetched Main head; required only for --finalized-main")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--reuse-install-source", type=Path,
        help="Reuse matching first-party wheels from this explicitly selected prior snapshot",
    )
    parser.add_argument("--finalized-main", action="store_true")
    parser.add_argument("--prepare-applications", action="store_true")
    parser.add_argument("--application-extras", type=json.loads)
    parser.add_argument("--branch", choices=("dev", "main"))
    parser.add_argument("--uv")
    budget = parser.add_mutually_exclusive_group()
    budget.add_argument("--timeout", type=float)
    budget.add_argument("--deadline", type=float)
    parser.add_argument("--toolchain-source", choices=("governed", "seasoned-public"), default="governed")
    parser.add_argument(
        "--toolchain-min-age-days", type=int,
        help="Minimum release age for an explicitly selected seasoned-public toolchain",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--suite", action="store_true", help="Build the repository-declared Python portfolio")
    selection.add_argument(
        "--project", action="append", dest="projects",
        help="Build only this declared project; repeat for a bounded installation set",
    )
    args = parser.parse_args(argv)
    if args.application_extras is not None and not args.prepare_applications:
        parser.error("--application-extras requires --prepare-applications")
    if args.toolchain_source == "governed" and args.toolchain_min_age_days is not None:
        parser.error("--toolchain-min-age-days requires --toolchain-source seasoned-public")
    deadline = args.deadline
    if args.timeout is not None:
        deadline = time.monotonic() + args.timeout
    if args.prepare_applications:
        if (
            args.branch is None or args.main_head is None or not args.projects or args.suite
            or args.reuse_install_source is not None or args.finalized_main
            or args.toolchain_source != "governed" or args.uv is None
        ):
            parser.error("application preparation requires branch, Main head and selected projects only")
        components = tuple(name.removeprefix("standalone:") for name in args.projects)
        extras = args.application_extras
        if extras is not None:
            if not isinstance(extras, dict) or any(
                not isinstance(values, list) for values in extras.values()
            ):
                parser.error("--application-extras requires an object of component extra lists")
            extras = {name: tuple(values) for name, values in extras.items()}
        with redirect_stdout(sys.stderr):
            snapshots = prepare_applications(
                args.source_checkout, args.source_commit, args.main_head, args.output,
                components=components, branch=args.branch, uv=args.uv,
                deadline=time.monotonic() + 600 if deadline is None else deadline,
                extras=extras, source_descriptor=args.source_descriptor,
            )
        print(json.dumps(snapshots, sort_keys=True))
        return 0
    projects = None if args.suite else tuple(args.projects or ("standalone:worktree-manager",))
    record = compose(
        args.source_checkout, args.source_commit, args.main_head, args.output,
        finalized_main=args.finalized_main,
        source_descriptor=args.source_descriptor,
        projects=projects,
        deadline=deadline,
    )
    build_projects(
        args.output.absolute(), record, source_policy=args.toolchain_source,
        min_age_days=args.toolchain_min_age_days,
        projects=projects,
        reuse_source=args.reuse_install_source, uv=args.uv, deadline=deadline,
    )
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
