"""Freeze local installation inputs without changing declared package versions.

No publication, repository registration or runtime activation happens here.
The output is disposable build data, not a release or a source approval token.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

import prepare_runtime_source as preparation
import materialize_main as materialization

MANIFEST = "install-source.json"
SCHEMA = "copilot-extensions.install-source"


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
) -> dict:
    """Materialize one Git object; only finalized Main selection needs ancestry."""
    deadline = deadline or time.monotonic() + 600
    repository = repository.resolve(strict=True)
    descriptor = repository_source(repository, source_descriptor, deadline=deadline)
    output = output.absolute()
    selected = [("source", commit)]
    if main_head is not None:
        selected.append(("Main head", main_head))
    for name, value in selected:
        if preparation._OBJECT_ID.fullmatch(value) is None:
            raise ValueError(f"{name} requires a full Git commit")
        if preparation._git(repository, "rev-parse", f"{value}^{{commit}}", deadline=deadline) != value:
            raise ValueError(f"{name} does not name the requested Git commit")
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
    preparation._git(
        repository, "-c", "tar.umask=0022", "archive", "--format=tar",
        "--output", str(archive), commit, deadline=deadline,
    )
    source = output / "source"
    source.mkdir()
    preparation._extract_archive(archive, source, deadline=deadline)
    archive.unlink()
    source_tree = preparation._git(repository, "rev-parse", f"{commit}^{{tree}}", deadline=deadline)
    if not finalized_main:
        log = materialization.materialize(source, canonical_root=source)
        refused = [line for line in log if line.startswith("SKIP")]
        if refused:
            raise ValueError("source materialization refused:\n" + "\n".join(refused))
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


def build_projects(
    output: Path, record: dict, *, projects: tuple[str, ...] | None = None,
    source_policy: str = "governed", min_age_days: int | None = None,
) -> dict:
    """Use the existing project roster and one seasoned build-tool closure."""
    import build_python_artifacts as builder
    from build_toolchain_lock import resolve_toolchain_lock
    from promotion_python_artifacts import candidate_projects, project_key
    from standalone_python_artifacts import COMPONENTS, build_standalone_artifacts
    from versioned_runtime_source import (
        SOURCE_FILE, SourceIdentity, assembly_hash, payload_package_version, runtime_version,
    )

    source = output / "source"
    roster = candidate_projects(source)
    selected = tuple(roster) if projects is None else projects
    if not selected or len(set(selected)) != len(selected) or set(selected) - roster.keys():
        raise ValueError("local source requires unique declared Python projects")
    from promote_release import _load_module

    builder = _load_module(Path(builder.__file__), "_local_install_artifact_builder")
    builder.REPO, builder.PLUGINS_DIR, builder.LIBS_DIR = source, source / "plugins", source / "libs"
    toolchain = resolve_toolchain_lock(
        output / "build-tools", python=sys.executable,
        source_policy=source_policy, min_age_days=min_age_days,
    )
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
    prepared = {}
    for key in selected:
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
            payload = source / relative
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
    if any(assembly_hash(payloads[key]) != identity.assembly_hash for key, identity in identities.items()):
        raise ValueError("local artifact construction changed its associated source payload")
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
    record = compose(
        args.source_checkout, args.source_commit, args.main_head, args.output,
        finalized_main=args.finalized_main,
        source_descriptor=args.source_descriptor,
    )
    build_projects(
        args.output.absolute(), record, source_policy=args.toolchain_source,
        min_age_days=args.toolchain_min_age_days,
        projects=None if args.suite else tuple(args.projects or ("standalone:worktree-manager",)),
    )
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
