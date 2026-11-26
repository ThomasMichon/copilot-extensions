"""Static admission for Containers' synchronized, package-owned runtime resources."""
from __future__ import annotations

import ast
from pathlib import Path

from artifact_source_build import PromotionArtifactError, validate_static_build

TRUSTED_ROOT = Path(__file__).resolve().parent.parent
CONSUMER = Path("plugins/agent-containers")
APPLICATIONS = {
    "agent-worktrees": "agent_worktrees",
    "agent-remote-driver": "agent_remote_driver_app",
}


def application_resources(source: Path, package: str) -> dict[str, Path]:
    """Read the trusted build owner's declarations without executing candidate code."""
    helper = TRUSTED_ROOT / "libs" / "versioned-runtime" / "standalone_build.py"
    declarations = {}
    for node in ast.parse(helper.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                    "APPLICATION_RESOURCES", "APPLICATION_METADATA_RESOURCES", "APPLICATION_SOURCE_FALLBACKS",
                ):
                    if target.id in declarations:
                        raise PromotionArtifactError("duplicate trusted application resource declaration")
                    declarations[target.id] = ast.literal_eval(node.value)
    if set(declarations) != {"APPLICATION_RESOURCES", "APPLICATION_METADATA_RESOURCES", "APPLICATION_SOURCE_FALLBACKS"}:
        raise PromotionArtifactError("invalid trusted application resource declaration")
    patterns = declarations["APPLICATION_RESOURCES"][package]
    resources = {
        package + "/_runtime_assets/" + path.relative_to(source).as_posix(): path
        for pattern in patterns for path in source.glob(pattern)
        if path.is_file() and path.suffix in {
            ".py", ".ps1", ".sh", ".cmd", ".conf", ".pyz", ".json", ".yaml", ".awk", ".cs", ".mjs",
        }
    }
    resources.update({
        package + "/_runtime_assets/" + path.relative_to(source).as_posix(): path
        for pattern in declarations["APPLICATION_METADATA_RESOURCES"].get(package, ())
        for path in source.glob(pattern) if path.is_file()
    })
    for destination, relative, names in declarations["APPLICATION_SOURCE_FALLBACKS"].get(package, ()):
        for name in names:
            resources.setdefault(f"{package}/_runtime_assets/{destination}/{name}", source / relative / name)
    return resources


def build_inputs(source: Path, *, root: Path | None) -> tuple[dict[str, object], dict[str, Path]]:
    """Admit only the registered hook; no candidate build code runs here."""
    application = next((
        name for name in APPLICATIONS
        if root is not None and source.absolute() == (root / "plugins" / name).absolute()
    ), None)
    if application is not None:
        assert root is not None
        package = APPLICATIONS[application]
        import uv_editable_ref as uer

        if (
            uer._find_symlinked_ancestor(source, root.resolve()) is not None
            or uer._find_symlink(source) is not None
        ):
            raise PromotionArtifactError("runtime resource source must not be linked")
        helper = root / "libs" / "versioned-runtime" / "standalone_build.py"
        trusted = TRUSTED_ROOT / "libs" / "versioned-runtime" / "standalone_build.py"
        if helper.read_bytes() != trusted.read_bytes():
            raise PromotionArtifactError("runtime build helper differs from the trusted builder")
        declarations = [
            node.value for node in ast.parse(trusted.read_text(encoding="utf-8")).body
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "RUNTIME_RESOURCES"
                for target in node.targets
            )
        ]
        mapping = ast.literal_eval(declarations[0])
        resources = {
            package + "/" + destination: helper.parent / name
            for name, destination in mapping.items()
        } if application == "agent-worktrees" else {}
        resources.update(application_resources(source, package))
        for name, path in resources.items():
            if not path.is_file():
                raise PromotionArtifactError(f"missing runtime resource: {name}")
            included = source / "src" / name
            if included.exists() and included.read_bytes() != path.read_bytes():
                raise PromotionArtifactError("included runtime resource differs from its source")
        included_helper = source / "_standalone_runtime_build.py"
        if included_helper.exists() and included_helper.read_bytes() != trusted.read_bytes():
            raise PromotionArtifactError("included runtime build helper differs from its source")
        return validate_static_build(
            source, trusted_setup=(TRUSTED_ROOT / "plugins" / application / "setup.py").read_bytes(),
        ), resources
    if root is None or source.absolute() != (root / CONSUMER).absolute():
        return validate_static_build(source), {}
    import uv_editable_ref as uer

    if (
        uer._find_symlinked_ancestor(source, root.resolve()) is not None
        or uer._find_symlink(source) is not None
    ):
        raise PromotionArtifactError("runtime resource source must not be linked")
    helper = source / "scripts/standalone_build.py"
    trusted = TRUSTED_ROOT / "libs/versioned-runtime/standalone_build.py"
    if not helper.is_file() or helper.read_bytes() != trusted.read_bytes():
        raise PromotionArtifactError("runtime build helper differs from the trusted builder")
    declarations = [
        node.value for node in ast.parse(trusted.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "RUNTIME_RESOURCES"
            for target in node.targets
        )
    ]
    if len(declarations) != 1:
        raise PromotionArtifactError("invalid trusted runtime resource declaration")
    mapping = ast.literal_eval(declarations[0])
    resources = {
        "agent_containers/" + destination: source / "scripts" / name
        for name, destination in mapping.items()
    }
    resources["agent_containers/_runtime_profile.py"] = source / "scripts/runtime_profile.py"
    for name, path in resources.items():
        if not path.is_file():
            raise PromotionArtifactError(f"missing runtime resource: {name}")
        included = source / "src" / name
        if included.exists() and included.read_bytes() != path.read_bytes():
            raise PromotionArtifactError("included runtime resource differs from its source")
    included_helper = source / "_standalone_runtime_build.py"
    if included_helper.exists() and included_helper.read_bytes() != trusted.read_bytes():
        raise PromotionArtifactError("included runtime build helper differs from its source")
    data = validate_static_build(
        source, trusted_setup=(TRUSTED_ROOT / CONSUMER / "setup.py").read_bytes(),
    )
    return data, resources


def build_owner_roots(source: Path, *, root: Path) -> set[Path]:
    """Canonical owners needed before validating resource-bearing applications."""
    if any(source.absolute() == (root / "plugins" / name).absolute() for name in APPLICATIONS):
        return {root / "libs" / "versioned-runtime"}
    return set()


def build_resource_roots(source: Path, *, root: Path) -> set[Path]:
    """Retain the admitted build owner as well as the bytes it places in wheels."""
    _data, injected = build_inputs(source, root=root)
    roots = {path.parent for path in injected.values()}
    roots.update(build_owner_roots(source, root=root))
    return roots
