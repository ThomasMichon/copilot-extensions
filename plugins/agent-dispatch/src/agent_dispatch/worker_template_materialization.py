"""Provider fragments use the existing recipe and local-instance authorities."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

from .registrar import RegistrarError
from .registrar_recipes import _absolutize_emitter_cwd, resolve_extends


def inspect_template(path: Path, activation) -> tuple[dict, dict]:
    from .worker_instances import digest

    context = {}
    document = resolve_extends(
        {"extends": str(path)}, base_dir=path.parent, activation=activation,
        resolution_context=context,
    )
    if document.get("kind") == "composite":
        raise RegistrarError("template kind cannot be materialized by the local instance lifecycle")
    try:
        digest(context)
    except (TypeError, ValueError) as exc:
        raise RegistrarError("template recipe closure must contain JSON-compatible values") from exc
    return document, context


def selected_template(source_id: str, revision: str) -> dict:
    from plugin_activation import resolve_active_plugins
    from .worker_templates import discover_templates

    activation = resolve_active_plugins()
    definitions = {}
    discover_templates(activation, _definitions=definitions)
    selected = definitions.get(source_id)
    if selected is None or selected["row"]["revision"] != revision:
        raise RegistrarError("template is absent or changed; refresh the catalog")
    row = selected["row"]
    if row["instantiable"] != "true":
        raise RegistrarError(row["unsupported_reason"])
    return {**selected, "activation": activation}


def _absolute_or_named(value: object) -> bool:
    return isinstance(value, str) and (
        value.startswith("repo:")
        or any(cls(value).is_absolute() for cls in (Path, PurePosixPath, PureWindowsPath))
    )


def materialize_template(selected: dict, overrides: dict, name: str, repo_root: str) -> dict:
    from . import registrar_discovery as rd
    from .registrar_document import _identity_origin_root, qualify_supplier_context
    from .registrar_reconcile import declaration_to_registration
    from .repository_resources import RepositoryResourceSnapshot
    from .worker_instances import OWNER, validate_configuration

    if "extends" in overrides:
        raise RegistrarError("template overrides cannot replace the selected source's extends")
    if overrides.get("name") not in (None, name):
        raise RegistrarError("declaration name must match the immutable instance name")
    if overrides.get("owner") not in (None, OWNER):
        raise RegistrarError("instance owner is user-local and cannot be overridden")
    path = selected["path"]
    directory = Path(repo_root).expanduser() if repo_root else path.parent
    request = {"extends": selected["reference"], **overrides, "name": name, "owner": OWNER}
    origins = {}
    document = resolve_extends(
        request, base_dir=directory, activation=selected["activation"], path_origins=origins,
    )
    # This is the same reviewer cwd normalization used by source expansion.
    if document.get("kind") == "reviewer-loop" and "emitter.cwd" in origins:
        document["emitter"] = _absolutize_emitter_cwd(
            {"kind": "emitter", "spec": document["emitter"]},
            directory=origins["emitter.cwd"],
        )["spec"]
    original = rd.expand_declaration_document(
        request, directory=directory, repo_root=repo_root or None,
        activation=selected["activation"],
    )
    source = selected["row"]["source"]
    if source["provider"].startswith("repo:"):
        repository = source["provider"].removeprefix("repo:")
        original = tuple(
            qualify_supplier_context(declaration, repository, Path(source["root"]))
            for declaration in original
        )
        if document.get("kind") in {"emitter", "evaluator"}:
            document["spec"] = dict(original[0].spec)
    if document.get("kind") == "effort-driver-loop" and document.get("worker_identity"):
        resources = RepositoryResourceSnapshot(execution=False)
        origin = _identity_origin_root(origins.get("worker_identity", directory), directory)
        emitter = next(d for d in original if d.kind == "emitter")
        effective = str(emitter.spec.get("cwd") or directory)
        if Path(resources.resolve(origin)) != Path(resources.resolve(effective)):
            raise RegistrarError("effort worker identity source context cannot yet be materialized safely")
    for declaration in original:
        if declaration.kind == "supervised-lane" and declaration.evaluator and not _absolute_or_named(declaration.evaluator):
            raise RegistrarError("source-relative evaluator paths cannot yet be materialized safely")
        if declaration.kind == "evaluator":
            path = declaration.spec.get("evaluator")
            if path and (not _absolute_or_named(path) or path.startswith("repo:")):
                raise RegistrarError("file-backed evaluator materialization requires an absolute local path")
            spec = declaration.spec.get("evaluator_spec")
            if isinstance(spec, dict) and spec.get("scripts") and not _absolute_or_named(spec.get("cwd")):
                raise RegistrarError("source-relative evaluator script cwd cannot yet be materialized safely")
        if declaration.kind == "emitter":
            from .producers.emitter import validate_spec

            try:
                validate_spec(dict(declaration.spec), resolve_resources=True)
            except ValueError as exc:
                raise RegistrarError(f"template dependency validation failed: {exc}") from exc
            for key in ("repository_issue_loop", "effort_driver_loop"):
                spec = declaration.spec.get(key)
                if isinstance(spec, dict) and spec.get("worker_identity") and not (
                    _absolute_or_named(declaration.spec.get("worker_identity_cwd"))
                    or _absolute_or_named(declaration.spec.get("cwd"))
                ):
                    raise RegistrarError("worker identity source context cannot yet be materialized safely")
    data, relocated = validate_configuration(name, document, repo_root)
    def registrations(declarations):
        result = []
        for declaration in declarations:
            registration = declaration_to_registration(declaration, machine=None)
            spec = registration["spec"]
            if (
                "worker_identity_cwd" in spec
                and spec["worker_identity_cwd"] == spec.get("cwd")
            ):
                spec.pop("worker_identity_cwd")
            result.append(registration)
        return result

    before = registrations(original)
    after = registrations(relocated)
    if before != after:
        raise RegistrarError(
            "template source-relative dependencies cannot be materialized safely with this repo_root; "
            "retain the defining repository context or use an explicit source-owned reference"
        )
    return data
