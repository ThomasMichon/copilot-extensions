"""Explicit first-party standalone identities and their non-project build inputs."""
from __future__ import annotations

import ast
from pathlib import Path

from standalone_consumers import standalone_build_resources

COMPONENTS = {
    "fleet-manager": "fleet-manager", "pr-service": "services/pr",
    "chat-service": "services/chat", "dispatch-service": "services/dispatch",
    "ssh-service": "services/ssh", "worktree-manager": "worktree-manager",
}
TRUSTED_ROOT = Path(__file__).resolve().parent.parent
_RESOURCE_PACKAGES = {
    "fleet-manager": "fleet_manager",
    "chat-service": "agent_chat_service",
}


def artifact_label(record: dict) -> str:
    """Keep standalone release names separate from unchanged plugin names."""
    if "composition" in record:
        if record["composition"] != "core" or "plugin" in record or "component" in record:
            raise ValueError("invalid core artifact identity")
        return "composition-core"
    if "component" in record:
        component = record["component"]
        if not isinstance(component, str) or component not in COMPONENTS or "plugin" in record:
            raise ValueError("invalid standalone artifact identity")
        return f"standalone-{component}"
    return record["plugin"]


def project_key(record: dict) -> str:
    label = artifact_label(record)
    if "composition" in record:
        return "composition:core"
    return f"standalone:{record['component']}" if "component" in record else label


def source_closure(component: str, root: Path, builder) -> tuple[Path, list, tuple[Path, ...]]:
    """Only registered standalone projects can select resource-bearing builds."""
    from build_toolchain_lock import ArtifactBuildError

    if not isinstance(component, str) or component not in COMPONENTS:
        raise ArtifactBuildError("unknown standalone artifact component")
    source = root / COMPONENTS[component]
    resources = standalone_build_resources(COMPONENTS[component], source_root=root)
    for path in (source, *resources):
        if (
            not path.is_dir() or not path.resolve().is_relative_to(root.resolve())
            or builder.uer._find_symlinked_ancestor(path, root) is not None
            or builder.uer._find_symlink(path, skip_test_runs=False) is not None
        ):
            raise ArtifactBuildError("standalone source/resource is missing, linked or outside candidate")
    if not (source / "pyproject.toml").is_file():
        raise ArtifactBuildError("standalone project metadata is missing")
    return source, builder.resolve_vendored_libs(source), resources


def injected_resources(component: str, root: Path) -> dict[str, Path]:
    """Describe the trusted standalone helper's outputs without executing candidate code."""
    if component not in _RESOURCE_PACKAGES:
        return {}
    from build_toolchain_lock import ArtifactBuildError

    helper = root / "libs" / "versioned-runtime" / "standalone_build.py"
    trusted = TRUSTED_ROOT / "libs" / "versioned-runtime" / "standalone_build.py"
    if helper.read_bytes() != trusted.read_bytes():
        raise ArtifactBuildError("standalone build helper differs from trusted builder")
    declarations = [
        node.value for node in ast.parse(trusted.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "RUNTIME_RESOURCES"
            for target in node.targets
        )
    ]
    if len(declarations) != 1:
        raise ArtifactBuildError("invalid trusted runtime resource declaration")
    mapping = ast.literal_eval(declarations[0])
    package = _RESOURCE_PACKAGES[component] + "/"
    result = {
        package + destination: helper.parent / name for name, destination in mapping.items()
    }
    if component == "fleet-manager":
        result.update({
            package + "_runtime_launcher.py": root / "libs" / "installer-engine" / "runtime_launcher.py",
            package + "_installer_process.py": root / "libs" / "installer-engine" / "installer_engine_process.py",
            package + "_construction_procutil.py": (
                root / "libs" / "agent-procutil" / "src" / "agent_procutil" / "__init__.py"
            ),
        })
    else:
        result.update({
            package + name: root / "libs" / "installer-engine" / name
            for name in (
                "installer_engine.py", "installer_engine_process.py",
                "installer_dependency_policy.py", "installer_dependency_config.py",
                "installer_wheel_inputs.py", "installer_wheel_snapshot.py", "runtime_launcher.py",
            )
        })
    source = root / COMPONENTS[component]
    included_helper = source / "_standalone_runtime_build.py"
    optional = [(included_helper, trusted), *(
        (source / "src" / name, path) for name, path in result.items()
    )]
    for included, canonical in optional:
        if included.exists() and included.read_bytes() != canonical.read_bytes():
            raise ArtifactBuildError("included standalone build resource differs from canonical source")
    return result


def validate_build(source: Path, *, component: str, root: Path) -> dict:
    from artifact_source_build import PromotionArtifactError, validate_static_build
    from build_toolchain_lock import ArtifactBuildError

    try:
        injected_resources(component, root)
        trusted_setup = (
            (TRUSTED_ROOT / COMPONENTS[component] / "setup.py").read_bytes()
            if component in _RESOURCE_PACKAGES else None
        )
        return validate_static_build(source, trusted_setup=trusted_setup)
    except (OSError, SyntaxError, ValueError, PromotionArtifactError) as exc:
        raise ArtifactBuildError(f"invalid standalone build inputs: {exc}") from exc


def build_standalone_artifacts(
    component: str, *, out_dir: Path, python: str | None = None, toolchain=None,
    builder=None,
) -> dict:
    if builder is None:
        import build_python_artifacts as builder
    source, dependencies, resources = source_closure(component, builder.REPO, builder)
    validate_build(source, component=component, root=builder.REPO)
    return builder.build_source_artifacts(
        identity={"component": component}, source_dir=source, dependencies=dependencies,
        resources=resources, out_dir=out_dir, python=python, toolchain=toolchain,
    )
