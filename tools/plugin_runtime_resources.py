"""Static admission for Containers' synchronized, package-owned runtime resources."""
from __future__ import annotations

import ast
from pathlib import Path

from artifact_source_build import PromotionArtifactError, validate_static_build

TRUSTED_ROOT = Path(__file__).resolve().parent.parent
CONSUMER = Path("plugins/agent-containers")


def build_inputs(source: Path, *, root: Path | None) -> tuple[dict, dict[str, Path]]:
    """Admit only the registered hook; no candidate build code runs here."""
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
