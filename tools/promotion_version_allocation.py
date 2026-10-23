"""Keep unlanded immutable version reservations out of later source releases."""
from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path, PurePosixPath

Entry = tuple[str, str, str]


class VersionReservationError(RuntimeError):
    pass


def payload_entries(
    entries: Mapping[str, Entry], roots: Sequence[str],
) -> dict[str, Entry]:
    """Select the complete source roots and their inherited checkout attributes."""
    attributes = {
        str(parent / ".gitattributes")
        for root in roots
        for parent in PurePosixPath(root).parents
    }
    return {
        path: entry for path, entry in entries.items()
        if path in attributes or any(path.startswith(root + "/") for root in roots)
    }


def next_reserved_version(version: str, *, accumulator) -> str:
    """Advance a reserved prerelease without going below a final release."""
    kind = "patch" if accumulator.parse_version(version)[3] is None else "dev"
    return accumulator.bump_version(version, kind)


def select_version(
    version: str, occupied: Collection[str], *, matches: Callable[[str], bool],
    advance: Callable[[str], str],
) -> str:
    """Retain an exact reservation, otherwise advance to a compatible or free one."""
    while version in occupied:
        if matches(version):
            return version
        version = advance(version)
    return version


def allocate_versions(scratch: Path, *, repo: Path, accumulator) -> dict[str, tuple[str, str]]:
    """Reuse an occupied version only for an unchanged complete source closure.

    Tags are read once, never created or moved. Git blob identity is a
    conservative pre-build comparison, not publication authorization: the
    publisher still authenticates prior aliases and validates exact manifests.
    """
    import promote_release as promotion
    from build_toolchain_lock import ArtifactBuildError
    from promotion_control import ControlPreservationError, _git, _tree

    try:
        reservations = {}
        for line in _git(
            repo, ["for-each-ref", "--format=%(refname:strip=2) %(objectname)", "refs/tags/"],
        ).decode("utf-8").splitlines():
            tag, object_id = line.split()
            reservations[tag] = object_id
        plugins = [
            project.parent for project in sorted((scratch / "plugins").glob("*/pyproject.toml"))
            if any(tag.startswith(project.parent.name + "-v") for tag in reservations)
        ]
        if not plugins:
            return {}
        builder = promotion._load_module(
            Path(__file__).parent / "build_python_artifacts.py", "allocation_artifact_sources",
        )
        builder.REPO, builder.PLUGINS_DIR, builder.LIBS_DIR = (
            scratch, scratch / "plugins", scratch / "libs",
        )
        prior_trees: dict[str, dict[str, Entry]] = {}
        candidate: dict[str, Entry] | None = None
        result = {}
        for plugin in plugins:
            name = plugin.name
            version = original = accumulator.read_consumer_version(name)
            if version is None:
                raise VersionReservationError(f"{name}: cannot read candidate version")
            roots = [
                directory.resolve().relative_to(scratch.resolve()).as_posix()
                for directory in [
                    plugin, *(directory for _name, directory in builder.resolve_vendored_libs(plugin)),
                ]
            ]
            def matches(version: str) -> bool:
                nonlocal candidate
                tag = f"{name}-v{version}"
                object_id = reservations[tag]
                if object_id not in prior_trees:
                    prior_trees[object_id] = _tree(repo, object_id + "^{commit}")
                if candidate is None:
                    candidate = _tree(repo, promotion.build_promotion_tree(scratch))
                current_payload = payload_entries(candidate, roots)
                return current_payload == payload_entries(prior_trees[object_id], roots)

            def advance(version: str) -> str:
                nonlocal candidate
                following = next_reserved_version(version, accumulator=accumulator)
                if accumulator.apply({name: (version, following)}) != [name]:
                    raise VersionReservationError(f"{name}: cannot write unreserved version {following}")
                candidate = None
                return following

            version = select_version(
                version, {
                    tag.removeprefix(name + "-v")
                    for tag in reservations if tag.startswith(name + "-v")
                },
                matches=matches, advance=advance,
            )
            if version != original:
                result[name] = original, version
        return result
    except (ControlPreservationError, ArtifactBuildError, UnicodeError) as exc:
        raise VersionReservationError(f"cannot allocate immutable versions: {exc}") from exc


def apply_version_reservations(
    scratch: Path, repo: Path, accumulator,
    computed: dict[str, tuple[str, str]], applied: list[str],
    promotion_error: type[RuntimeError],
) -> None:
    """Apply reservations while keeping the original Main baseline in the summary."""
    try:
        reserved = allocate_versions(scratch, repo=repo, accumulator=accumulator)
    except VersionReservationError as exc:
        raise promotion_error(str(exc)) from exc
    for consumer, (old, new) in reserved.items():
        computed[consumer] = computed.get(consumer, (old, old))[0], new
        if consumer not in applied:
            applied.append(consumer)
