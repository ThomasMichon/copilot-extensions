"""Freeze local installation inputs without changing declared package versions.

No publication, repository registration or runtime activation happens here.
The output is disposable build data, not a release or a source approval token.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
from installer_dependency_config import native_retained_path

import prepare_runtime_source as preparation
import materialize_main as materialization

MANIFEST = "install-source.json"
SCHEMA = "copilot-extensions.install-source"


def _selected_project_paths(
    repository: Path, commit: str, projects: tuple[str, ...] | None, deadline: float,
) -> tuple[str, ...] | None:
    if projects is None:
        return None
    from promotion_python_artifacts import _PLUGIN
    from standalone_python_artifacts import COMPONENTS

    if (
        not isinstance(projects, tuple) or not projects
        or any(not isinstance(key, str) for key in projects)
        or len(set(projects)) != len(projects)
    ):
        raise ValueError("source selection requires unique declared Python project keys")
    paths = []
    for key in projects:
        if key.startswith("standalone:") and key[11:] in COMPONENTS:
            relative = COMPONENTS[key[11:]]
        elif _PLUGIN.fullmatch(key):
            relative = "plugins/" + key
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
    deadline = deadline or started + 600
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
    output.mkdir(parents=True)
    archive = output / "source.tar"
    with _source_phase(observer, 1, started):
        preparation._archive(repository, commit, archive, deadline=deadline)
    source = output / "source"
    source.mkdir()
    file_pointer_candidates: list[Path] = []
    with _source_phase(observer, 2, started):
        preparation._extract_archive(
            archive, source, deadline=deadline,
            file_pointer_candidates=file_pointer_candidates,
        )
    archive.unlink()
    source_tree = preparation._git(repository, "rev-parse", f"{commit}^{{tree}}", deadline=deadline)
    if not finalized_main:
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
        **({"source_descriptor": descriptor} if descriptor is not None else {}),
        "projects": {},
    }


def build_manager(
    output: Path, record: dict, *, source_policy: str = "governed",
    min_age_days: int | None = None,
) -> dict:
    return build_projects(
        output, record, projects=("standalone:worktree-manager",),
        source_policy=source_policy, min_age_days=min_age_days,
    )


def _build_input_roots(source: Path, selected: tuple[str, ...], builder) -> tuple[Path, ...]:
    if "composition:core" in selected:
        return (source,)
    from standalone_python_artifacts import source_closure

    roots = set()
    for key in selected:
        if key.startswith("standalone:"):
            project, dependencies, resources = source_closure(key.split(":", 1)[1], source, builder)
            roots.update((project, *resources, *(path for _name, path in dependencies)))
        else:
            project = source / "plugins" / key
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


@contextmanager
def _build_workspace(source: Path, output: Path, selected: tuple[str, ...], builder):
    """Keep Windows backend and interpreter paths short while binding their inputs."""
    if os.name != "nt":
        yield source, output / "build-tools"
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
        staged_roots = tuple(staged / root.relative_to(source) for root in roots)
        if _build_input_fingerprint(staged_roots) != original_hash:
            raise ValueError("compact build source differs from the retained installation source")
        yield staged, workspace / "build-tools"
        if _build_input_fingerprint(staged_roots) != original_hash:
            raise ValueError("compact build source changed during artifact construction")
        if _build_input_fingerprint(roots) != original_hash:
            raise ValueError("retained installation source changed during artifact construction")


def build_projects(
    output: Path, record: dict, *, projects: tuple[str, ...] | None = None,
    source_policy: str = "governed", min_age_days: int | None = None,
    uv: str | Path | None = None, deadline: float | None = None,
) -> dict:
    """Use the existing project roster and one seasoned build-tool closure.

    Explicit UV/deadline selections also bound wheel work in the isolated
    builder; omitted selections retain the legacy construction path.
    """
    import build_python_artifacts as builder
    from build_toolchain_lock import resolve_toolchain_lock
    from promotion_python_artifacts import candidate_projects, project_key
    from standalone_python_artifacts import COMPONENTS, build_standalone_artifacts
    from versioned_runtime_source import (
        SOURCE_FILE, SourceIdentity, assembly_hash, payload_package_version, runtime_version,
    )

    if deadline is not None:
        preparation._remaining(deadline)
    output = Path(native_retained_path(output))
    source = output / "source"
    roster = candidate_projects(source)
    selected = tuple(roster) if projects is None else projects
    if not selected or len(set(selected)) != len(selected) or set(selected) - roster.keys():
        raise ValueError("local source requires unique declared Python projects")
    identities = {}
    payloads = {
        key: source / (COMPONENTS[key.split(":", 1)[1]]
                       if key.startswith("standalone:") else "plugins/" + key)
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
    builder = _load_module(Path(builder.__file__), "_local_install_artifact_builder")
    toolchain_options = {}
    if uv is not None:
        toolchain_options["uv"] = uv
    if deadline is not None:
        toolchain_options["deadline"] = deadline
    if toolchain_options:
        build_wheel = builder.build_wheel

        def bounded_build_wheel(*args, **kwargs):
            if uv is not None:
                kwargs["uv"] = str(uv)
            if deadline is not None:
                preparation._remaining(deadline)
                kwargs["deadline"] = min(deadline, time.monotonic() + 180)
            return build_wheel(*args, **kwargs)

        builder.build_wheel = bounded_build_wheel
    builder.REPO, builder.PLUGINS_DIR, builder.LIBS_DIR = source, source / "plugins", source / "libs"
    with _build_workspace(source, output, selected, builder) as (build_source, toolchain_dir):
        builder.REPO = build_source
        builder.PLUGINS_DIR, builder.LIBS_DIR = build_source / "plugins", build_source / "libs"
        toolchain = resolve_toolchain_lock(
            toolchain_dir, python=sys.executable,
            source_policy=source_policy, min_age_days=min_age_days,
            **toolchain_options,
        )
        prepared = {}
        for key in selected:
            if deadline is not None:
                preparation._remaining(deadline)
            if key == "composition:core":
                from core_python_artifacts import build_core_artifacts

                manifest = build_core_artifacts(
                    out_dir=output / "wheels" / "composition-core", toolchain=toolchain, builder=builder,
                )
                project = {"payload": "source", "wheels": "wheels/composition-core", "artifact": manifest}
            else:
                standalone = key.startswith("standalone:")
                component = key.split(":", 1)[1] if standalone else key
                relative = COMPONENTS[component] if standalone else "plugins/" + component
                identity = identities[key]
                wheel_dir = output / "wheels" / component
                manifest = (
                    build_standalone_artifacts(
                        component, out_dir=wheel_dir, toolchain=toolchain, builder=builder,
                    ) if standalone else builder.build_plugin_artifacts(
                        component, out_dir=wheel_dir, toolchain=toolchain,
                    )
                )
                project = {
                    "payload": "source/" + relative, "wheels": "wheels/" + component,
                    "source": identity.document(), "artifact": manifest,
                }
            if (
                project_key(manifest) != key or manifest["version"] != roster[key]
                or manifest["build_toolchain"] != toolchain.record
            ):
                raise ValueError("local artifact builder returned another project, version or toolchain")
            prepared["worktree-manager" if key == "standalone:worktree-manager" else key] = project
        if deadline is not None:
            preparation._remaining(deadline)
        if any(assembly_hash(payloads[key]) != identity.assembly_hash for key, identity in identities.items()):
            raise ValueError("local artifact construction changed its associated source payload")
    if deadline is not None:
        preparation._remaining(deadline)
    record["projects"].update(prepared)
    (output / MANIFEST).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--source-checkout", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-descriptor", type=json.loads, help="Existing installation source descriptor")
    parser.add_argument("--main-head", help="Fetched Main head; required only for --finalized-main")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--finalized-main", action="store_true")
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
    if args.toolchain_source == "governed" and args.toolchain_min_age_days is not None:
        parser.error("--toolchain-min-age-days requires --toolchain-source seasoned-public")
    projects = None if args.suite else tuple(args.projects or ("standalone:worktree-manager",))
    record = compose(
        args.source_checkout, args.source_commit, args.main_head, args.output,
        finalized_main=args.finalized_main,
        source_descriptor=args.source_descriptor,
        projects=projects,
    )
    build_projects(
        args.output.absolute(), record, source_policy=args.toolchain_source,
        min_age_days=args.toolchain_min_age_days,
        projects=projects,
    )
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
