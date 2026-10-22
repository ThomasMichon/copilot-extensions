"""Produce existing release declarations from explicit local artifact bytes."""

from .artifacts import MAX_TOTAL_BYTES, _artifact
from .planning import PlanError, canonical, decode, record
from .release import _fields, validate_release


def assemble_release_manifest(components, sources, *, release_id, roles) -> bytes:
    """Return canonical schema-v1 release bytes, without signing or publishing."""
    components = decode(canonical(components))
    sources = decode(canonical(sources))
    roles = decode(canonical(roles))
    if not isinstance(components, dict):
        raise PlanError("component contracts must be an object")
    declarations = {}
    for name, contract in components.items():
        _fields(contract, ("kind", "api", "data_schemas"), "component contract")
        # Validate all metadata before reads; replace every digest before returning.
        declarations[name] = {**contract, "artifact_sha256": "0" * 64}
    manifest = {
        "schema": "fleet-manager.release", "version": 1, "id": release_id,
        "components": declarations, "roles": roles,
    }
    validate_release(manifest)
    record(sources, "fleet-manager.artifact-sources", {"components"})
    files = sources["components"]
    if not isinstance(files, dict) or files.keys() != declarations.keys():
        raise PlanError("assembly requires exactly the declared component sources")
    for path in files.values():
        if not isinstance(path, str) or not path or "\0" in path:
            raise PlanError("artifact source must be a bounded nonempty path")
        try:
            length = len(path.encode("utf-8"))
        except UnicodeError:
            raise PlanError("artifact source must be a bounded nonempty path") from None
        if length > 4096:
            raise PlanError("artifact source must be a bounded nonempty path")
    total = 0
    for name in sorted(declarations):
        actual, size = _artifact(files[name], MAX_TOTAL_BYTES - total)
        declarations[name]["artifact_sha256"] = actual
        total += size
    return canonical(manifest)
