"""User-local worker drafts and explicit publication to the existing registrar."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time

from . import registrar_discovery as rd
from .registrar import RegistrarError
from .registrar_recipes import GLOBAL_RECIPES, deep_merge
from .single_instance import SingleInstance

OWNER = "user-local:workers"
POINTER = "user-worker-instances"
ACTIVATION_CONSENT = "activate-local-worker"
POOL_TEMPLATE = {
    "kind": "supervised-lane", "labels": ["work"], "repos": "all",
    "concurrency": 1, "body": {"type": "headless"},
}


def instance_dir() -> Path:
    return rd.registrar_dir() / "worker-instances"


def active_dir() -> Path:
    return rd.registrar_dir() / "worker-instances-active"


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def templates() -> dict[str, dict]:
    return {"worker-pool": POOL_TEMPLATE, **GLOBAL_RECIPES}


def _name(name: object) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", name):
        raise RegistrarError("instance name: use 1-80 letters, digits, '-' or '_'")
    if name.upper() in {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(10)}, *{f"LPT{i}" for i in range(10)}}:
        raise RegistrarError("instance name: reserved device names are not portable")
    return name


def _regular(path: Path, *, directory: bool = False) -> None:
    """Refuse redirects before reading or replacing user-local configuration."""
    if path.is_symlink() or (path.exists() and rd._is_reparse(path.lstat())):
        raise RegistrarError(f"{path}: configuration must not be a symlink/reparse point")
    if path.exists() and (not path.is_dir() if directory else not path.is_file()):
        raise RegistrarError(f"{path}: unexpected configuration file type")


def _read(path: Path) -> dict:
    _regular(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistrarError(f"{path}: cannot read instance: {exc}") from exc
    if not isinstance(value, dict):
        raise RegistrarError(f"{path}: instance must be an object")
    if value.get("name") != path.stem or value.get("id") != f"instance:{path.stem}":
        raise RegistrarError(f"{path}: corrupt or mismatched instance identity")
    if value.get("revision") != digest(value.get("configuration")):
        raise RegistrarError(f"{path}: corrupt instance revision")
    configuration = value.get("configuration")
    if (
        not isinstance(configuration, dict)
        or not isinstance(configuration.get("declaration"), dict)
        or not isinstance(configuration.get("repo_root"), str)
    ):
        raise RegistrarError(f"{path}: corrupt instance configuration")
    source = value.get("source")
    if not isinstance(source, dict) or any(
        not isinstance(source.get(key), str) or not source[key] for key in ("id", "revision")
    ):
        raise RegistrarError(f"{path}: corrupt instance source")
    return value


def read_records(*, errors: list[str] | None = None) -> list[dict]:
    root = instance_dir()
    try:
        _regular(root, directory=True)
        paths = sorted(root.glob("*.json"))
    except (RegistrarError, OSError) as exc:
        if errors is None:
            raise RegistrarError(f"{root}: cannot read instance directory: {exc}") from exc
        errors.append(f"{root}: cannot read instance directory: {exc}")
        return []
    result = []
    for path in paths:
        try:
            result.append(_read(path))
        except (RegistrarError, OSError) as exc:
            if errors is None:
                raise RegistrarError(f"{path}: cannot read instance: {exc}") from exc
            errors.append(f"{path}: cannot read instance: {exc}")
    return result


def _atomic(path: Path, value: dict) -> None:
    _regular(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(f".{os.getpid()}.pending")
    _regular(staging)
    created = False
    try:
        with staging.open("x", encoding="utf-8") as stream:
            created = True
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    finally:
        if created:
            with contextlib.suppress(FileNotFoundError):
                staging.unlink()


def validate_configuration(name: str, document: object, repo_root: str = "") -> tuple[dict, tuple]:
    if not isinstance(document, dict):
        raise RegistrarError("declaration: expected a JSON object")
    if document.get("name") not in (None, name):
        raise RegistrarError("declaration name must match the immutable instance name")
    if document.get("owner") not in (None, OWNER):
        raise RegistrarError("instance owner is user-local and cannot be overridden")
    # Freeze built-in fragments in the new configuration; external relative
    # recipe inheritance would silently change meaning at the local destination.
    if "extends" in document:
        raise RegistrarError("local instances require a materialized declaration, not extends")
    root = Path(repo_root).expanduser() if repo_root else None
    if root is not None and (not root.is_absolute() or not root.is_dir()):
        raise RegistrarError("repo_root must be an existing absolute repository directory")
    if document.get("kind") in {"repository-issue-loop", "effort-driver-loop"} and root is None:
        raise RegistrarError("this template requires repo_root for worker identity and dependency resolution")
    data = {**document, "name": name, "owner": OWNER}
    declarations = rd.expand_declaration_document(
        data, directory=root or active_dir(), repo_root=root,
    )
    if not declarations:
        raise RegistrarError("configuration expands to no declarations")
    return data, declarations


def _validate_dependencies(
    declarations: tuple, repo_root: str, *, available_declarations: tuple | None = None,
) -> None:
    from .registrations import RegistrationKind
    for declaration in declarations:
        if declaration.fleet.enabled and not shutil.which("ssh"):
            raise RegistrarError("missing fleet dependency: ssh transport")
        command = declaration.spec.get("command")
        if isinstance(command, list) and command:
            executable = command[0]
            directory = Path(declaration.spec.get("cwd") or repo_root or active_dir())
            if not shutil.which(executable) and not (
                (Path(executable).is_absolute() or Path(executable).parent != Path("."))
                and (directory / executable).is_file()
            ):
                raise RegistrarError(f"missing command dependency: {executable}")
            if len(command) > 1 and Path(command[1]).suffix in {".py", ".sh", ".ps1"}:
                if not (directory / command[1]).is_file():
                    raise RegistrarError(f"missing script dependency: {command[1]}")
        if declaration.kind == RegistrationKind.SUPERVISED_LANE:
            # The registrar validates named identities on composed issue loops.
            # A basic lane may name a custom body agent; do not fabricate a
            # plugin dependency requirement for that existing contract.
            if not declaration.body.agent:
                raise RegistrarError("worker body agent is required")
        spec = declaration.spec
        if declaration.kind == RegistrationKind.EMITTER:
            spec = next(
                (spec[key] for key in ("repository_issue_loop", "effort_driver_loop")
                 if spec.get(key) is not None),
                spec,
            )
        if spec.get("evaluator_ref") and declaration.kind != RegistrationKind.EVALUATOR:
            if available_declarations is None:
                available_declarations = rd.RegistrarSources().refresh(
                    emit_warnings=False,
                ).combined.declarations
            if not any(
                candidate.kind == RegistrationKind.EVALUATOR
                and (
                    candidate.name == spec["evaluator_ref"]
                    or candidate.spec.get("evaluator_ref") == spec["evaluator_ref"]
                ) for candidate in available_declarations
            ) and not any(
                candidate.kind == RegistrationKind.EVALUATOR
                and (
                    candidate.name == spec["evaluator_ref"]
                    or candidate.spec.get("evaluator_ref") == spec["evaluator_ref"]
                ) for candidate in declarations
            ):
                raise RegistrarError(f"missing evaluator dependency: {spec['evaluator_ref']}")


def _check_collisions(name: str, declarations: tuple, existing: dict | None) -> None:
    wanted = [d.name for d in declarations]
    if len(wanted) != len(set(wanted)):
        raise RegistrarError("configuration expands to duplicate declaration names")
    known = rd.RegistrarSources().refresh(emit_warnings=False)
    if known.trusted_error or known.combined.findings:
        raise RegistrarError("registrar discovery has errors/ambiguities; repair with registrar doctor")
    current_publication = active_dir() / f"{name}.json"
    own_published = set()
    if existing and current_publication.exists():
        _regular(current_publication)
        own_published = {d.name for d in rd.read_declaration_file_set(current_publication)}
    occupied = {
        d.name for d in known.combined.declarations
        if d.owner != OWNER or d.name not in own_published
    }
    # A saved draft may no longer expand to the still-published names. Reserve
    # both, even if their discovery pointer is temporarily absent.
    for publication in active_dir().glob("*.json"):
        if existing and publication == current_publication:
            continue
        _regular(publication)
        occupied.update(d.name for d in rd.read_declaration_file_set(publication))
    for record in read_records():
        if record["name"] == name and existing:
            continue
        _, expanded = validate_configuration(
            record["name"], record["configuration"]["declaration"],
            record["configuration"]["repo_root"],
        )
        occupied.update(d.name for d in expanded)
    overlap = occupied.intersection(wanted)
    if overlap:
        raise RegistrarError(f"worker declaration name collision: {', '.join(sorted(overlap))}")


def _pointer() -> rd.Pointer:
    return rd.Pointer(POINTER, str(active_dir()), owner=OWNER)


def _check_pointer() -> None:
    current = [p for p in rd.load_pointers() if p.name == POINTER]
    if current and (len(current) != 1 or current[0] != _pointer()):
        raise RegistrarError("user worker discovery pointer collides with another source")


def save_instance(
    source_id: str, revision: str, fields: dict, *,
    activate: bool = False, consent: str | None = None,
) -> dict:
    """Persist a draft, optionally publish it; never start a service or task."""
    if activate and consent != ACTIVATION_CONSENT:
        raise RegistrarError(f"activation requires explicit --consent {ACTIVATION_CONSENT}")
    allowed = {"name", "declaration", "repo_root"}
    if set(fields) - allowed:
        raise RegistrarError(f"unknown configuration fields: {sorted(set(fields) - allowed)}")
    name = _name(fields.get("name"))
    document = fields.get("declaration")
    if isinstance(document, str):
        try:
            document = json.loads(document)
        except ValueError as exc:
            raise RegistrarError(f"declaration: invalid JSON: {exc}") from exc
    repo_root = fields.get("repo_root") or ""
    if not isinstance(repo_root, str):
        raise RegistrarError("repo_root must be text")
    source = source_id.removeprefix("template:")
    if source_id.startswith("template:"):
        if source not in templates() or revision != digest(templates()[source]):
            raise RegistrarError("template is absent or changed; refresh the catalog")
        if not isinstance(document, dict):
            raise RegistrarError("declaration: expected a JSON object")
        document = deep_merge(templates()[source], document)
    elif source_id != f"instance:{name}":
        raise RegistrarError("existing source is read-only or instance identity changed")
    data, declarations = validate_configuration(name, document, repo_root)
    _validate_dependencies(declarations, repo_root)
    configuration = {"declaration": data, "repo_root": repo_root}
    wanted_revision = digest(configuration)
    for root in (rd.registrar_dir(), instance_dir(), active_dir()):
        _regular(root, directory=True)
    _check_pointer()
    instance_dir().mkdir(parents=True, exist_ok=True)
    lock = SingleInstance(instance_dir() / ".write.lock")
    if not lock.acquire():
        raise RegistrarError("worker configuration is being changed; retry after refresh")
    try:
        path = instance_dir() / f"{name}.json"
        existing = _read(path) if path.exists() else None
        retry = bool(
            existing and existing["revision"] == wanted_revision
            and existing.get("last_request") == {"id": source_id, "revision": revision}
        )
        if not retry:
            if source_id.startswith("template:") and existing:
                raise RegistrarError("instance already exists; choose a distinct name")
            if source_id.startswith("instance:") and (
                existing is None or existing["revision"] != revision
            ):
                raise RegistrarError("stale or missing instance; refresh before editing")
        _check_collisions(name, declarations, existing)
        record = {
            "id": f"instance:{name}", "name": name, "configuration": configuration,
            "revision": wanted_revision,
            "source": existing["source"] if existing else {
                "id": source_id, "revision": revision, "provider": "agent-dispatch",
            },
            "last_request": {"id": source_id, "revision": revision},
            "saved_at": existing["saved_at"] if retry else time.time(),
        }
        if not retry:
            _atomic(path, record)
        receipt = {
            "id": record["id"], "revision": wanted_revision, "persisted": True,
            "activation": "not-requested", "retry": retry,
            "effects": "Saved user-local configuration only; no tasks or agents created.",
        }
        if activate:
            try:
                _publish(record, declarations)
            except (OSError, RegistrarError) as exc:
                receipt.update(
                    activation="failed", activation_error=str(exc),
                    effects="Saved locally. Activation is incomplete: publication or override "
                    "changes may have partially occurred. The existing supervisor may observe "
                    "a published version; inspect the catalog before retrying.",
                )
            else:
                receipt.update(
                    activation="pending",
                    effects="Published to existing local supervisor discovery. Reconciliation may "
                    "restart workers, bind queued tasks, or create agentic work from loop emitters. "
                    "No supervisor was started; running activity is not acknowledged.",
                )
        return receipt
    finally:
        lock.release()


def _publish(record: dict, declarations: tuple) -> None:
    from .config import overrides_path
    from .overrides import clear_override
    from .registrar_reconcile import declaration_to_registration
    from .supervisor_registration import registration_override_ids
    _check_pointer()
    active_dir().mkdir(parents=True, exist_ok=True)
    pointer = _pointer()
    # An empty directory can safely be registered first. A failed file commit
    # leaves no newly runnable declaration; a retry uses the same identity.
    rd.ensure_pointer(pointer)
    payload = dict(record["configuration"]["declaration"])
    root = record["configuration"]["repo_root"]
    if root:
        # Discovery of a dir pointer has no repository context. Materialize its
        # validated expansion rather than changing relative-path semantics.
        payload = {"kind": "user-worker-instance", "configuration": payload, "repo_root": root}
    _atomic(active_dir() / f"{record['name']}.json", payload)
    for declaration in declarations:
        for token in registration_override_ids(declaration_to_registration(declaration, machine=None)):
            clear_override(overrides_path(), token)
