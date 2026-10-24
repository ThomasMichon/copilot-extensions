"""Read-only provider discovery and current local-instantiation capabilities."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat

from plugin_activation import ActivationReport
from dropin_registry import ScanAuthority

from . import registrar_discovery as rd
from .registrar import RegistrarError
from .registrations import RegistrationKind
from .repo_config import layered_repo_surface_dirs, surface_entry_present
from .repository_resource_capture import run_agent_worktrees_capture
from .repository_resources import RepositoryResourceSnapshot, split_repository_resource
from .worker_instances import digest, templates

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_MAX_BYTES = 1024 * 1024
UNSUPPORTED = "Provider templates are discoverable only; safe local instantiation is not supported."


@dataclass(frozen=True)
class TemplateSource:
    """An inert index anchored to an existing owning authority."""

    provider: str
    root: Path
    index: Path
    scopes: tuple[str, ...]
    kind: str

    def identity(self) -> dict:
        return {
            "provider": self.provider, "root": str(self.root),
            "index": str(self.index), "scopes": list(self.scopes),
            "root_kind": self.kind,
        }


def _finding(findings: list[dict], source: str, reason: str, detail: str) -> None:
    findings.append({
        "source": source.replace("\0", "\\0")[:4096], "reason": reason,
        "detail": detail.replace("\0", "\\0")[:4096],
    })


def _repository_sources(findings: list[dict]) -> list[TemplateSource]:
    roots: dict[str, Path] = {}
    try:
        result = run_agent_worktrees_capture("repos", "map", "--json", timeout=5)
    except RegistrarError as exc:
        result = None
        _finding(findings, "registered-repositories", "unavailable", str(exc))
    names: set[str] = set()
    ambiguous: set[str] = set()
    registered = {}
    if result is None or result.returncode:
        detail = "Optional registered-repository discovery is unavailable or timed out."
        if result is not None:
            detail += " " + (result.stderr or result.stdout or "").strip()[:1000]
        _finding(findings, "registered-repositories", "unavailable", detail)
    else:
        try:
            data = json.loads(result.stdout)
            entries = data.get("repos") if isinstance(data, dict) else None
            if not isinstance(entries, list):
                raise ValueError("expected a repository map")
            if (
                type(data.get("repository_map_version")) is not int
                or data["repository_map_version"] != 1
                or data.get("inventory_authority") != "complete"
                or not isinstance(data.get("projection"), dict)
                or data["projection"].get("quality") != "current"
                or data.get("availability") is not None
            ):
                _finding(findings, "registered-repositories", "inventory-authority-unavailable",
                         "A current resident repository map is unavailable; discovery is partial.")
                entries = []
            valid_names = []
            for offset, entry in enumerate(entries):
                try:
                    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                        raise RegistrarError("expected a registered repository name")
                    split_repository_resource(f"repo:{entry['name']}:.")
                except RegistrarError:
                    _finding(findings, f"registered-repositories#{offset}", "invalid", "Expected a portable registered name.")
                else:
                    valid_names.append(entry["name"])
            counts = Counter(valid_names)
            names.update(name for name, count in counts.items() if count == 1)
            registered = {
                entry["name"]: entry for entry in entries
                if isinstance(entry, dict) and isinstance(entry.get("name"), str)
                and entry["name"] in names
            }
            for name, count in counts.items():
                if count > 1:
                    ambiguous.add(name)
                    _finding(findings, f"repo:{name}", "ambiguous", "Duplicate registered repository identity.")
        except (ValueError, TypeError) as exc:
            _finding(findings, "registered-repositories", "invalid", f"Cannot read repository inventory: {exc}")
    legacy = []
    try:
        pointers = rd.load_pointers()
    except RegistrarError as exc:
        _finding(findings, "repository-pointers", "unavailable", str(exc))
    else:
        for pointer in pointers:
            if pointer.kind != "repo":
                continue
            if pointer.repository and pointer.repository not in ambiguous:
                names.add(pointer.repository)
            elif pointer.repository is None:
                legacy.append(pointer)
    for name in sorted(names):
        try:
            entry = registered.get(name)
            if entry is None:
                raise RegistrarError("repository is not present in the resident registered map")
            if entry.get("registration") != "ready":
                raise RegistrarError(entry.get("reason") or "registered local root is unavailable")
            raw = entry.get("path")
            if (
                entry.get("class") not in {"reference", "singleton", "worktree", "knowledge"}
                or not isinstance(raw, str) or not Path(raw).is_absolute()
            ):
                raise RegistrarError("repository map has an invalid class or local root")
            roots[f"repo:{name}"] = Path(raw)
        except (RegistrarError, TypeError, ValueError) as exc:
            _finding(findings, f"repo:{name}", "unavailable", str(exc))
    for pointer in legacy:
        roots[f"repo-pointer:{pointer.name}"] = Path(pointer.location).expanduser()
    sources = []
    for provider, root in roots.items():
        try:
            root = root.resolve(strict=True)
            if not root.is_dir():
                raise ValueError("repository root is not a directory")
            layers = layered_repo_surface_dirs(root, "templates", include_unavailable=True)
            for layer in layers:
                try:
                    if not layer.is_dir():
                        raise ValueError("Template layer must be a directory.")
                    sources.append(TemplateSource(
                        provider, root, layer / "index.json", (provider,),
                        f"repository-layer:{layer.relative_to(root).as_posix()}",
                    ))
                except (OSError, ValueError, RuntimeError) as exc:
                    _finding(findings, str(layer), "invalid-index", str(exc))
        except (OSError, ValueError, RuntimeError) as exc:
            _finding(findings, provider, "unavailable", str(exc))
    return sources


def _json_object(path: Path) -> dict:
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("template document must be a regular file")
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("template document exceeds 1 MiB")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field {key!r}")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("template document must be a JSON object")
    return value


def _text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > limit:
        raise ValueError(f"{field} must be nonempty NUL-free text of at most {limit} characters")
    return value


def _entry(
    source: TemplateSource, entry: object, root: Path, index: Path, *,
    activation: ActivationReport, definitions: dict | None = None,
    snapshot: RepositoryResourceSnapshot,
) -> dict:
    if not isinstance(entry, dict) or set(entry) - {
        "id", "title", "description", "kind", "declaration", "requirements",
    }:
        raise ValueError("template entry must be an object with supported fields")
    name = entry.get("id")
    if not isinstance(name, str) or not _ID.fullmatch(name):
        raise ValueError("template id must use 1-80 portable letters, digits, '.', '_' or '-'")
    title = _text(entry.get("title"), "title", 200)
    description = _text(entry.get("description"), "description", 2000)
    kind = entry.get("kind")
    supported = RegistrationKind.DIRECT | {fragment["kind"] for fragment in templates().values()} | {"composite"}
    if not isinstance(kind, str) or kind not in supported:
        raise ValueError("template kind is not supported")
    requirements = entry.get("requirements", [])
    if not isinstance(requirements, list) or len(requirements) > 32:
        raise ValueError("requirements must be a list of at most 32 text items")
    requirements = [_text(value, "requirement", 1000) for value in requirements]
    relative = _text(entry.get("declaration"), "declaration", 1000)
    posix = PurePosixPath(relative.replace("\\", "/"))
    if posix.is_absolute() or PureWindowsPath(relative).anchor or "\0" in relative:
        raise ValueError("declaration must be a provider-relative JSON path")
    document_path = (index.parent / str(posix)).resolve(strict=True)
    document_path.relative_to(root)
    if document_path.suffix != ".json":
        raise ValueError("template declaration must be JSON")
    document = _json_object(document_path)
    if "kind" in document and document["kind"] != kind:
        raise ValueError("template kind differs from its declaration fragment")
    identity = {**source.identity(), "root": str(root), "index": str(index)}
    row_id = f"template:provider:{digest(identity)[:32]}:{name}"
    from .worker_template_materialization import inspect_template

    try:
        if source.provider.startswith("repo-pointer:"):
            raise RegistrarError("template instantiation requires a canonical registered repository identity")
        resolved, recipe_context = inspect_template(document_path, activation, snapshot)
        if resolved.get("kind", "supervised-lane") != kind:
            raise RegistrarError("template kind differs from its resolved recipe")
    except (RegistrarError, OSError, ValueError, RuntimeError) as exc:
        recipe_context = {"error": str(exc)}
        unsupported = f"Template materialization unavailable: {exc}".replace("\0", "\\0")[:4096]
    else:
        unsupported = ""
    revision = digest({
        "entry": entry, "document": document, "context": identity,
        "declaration": str(document_path), "recipes": recipe_context,
    })
    body = (
        f"{description}\n\nProvider: {source.provider}\n\nIndex: {index}"
        f"\n\nDeclaration: {document_path}\n\nApplicability: {json.dumps(list(source.scopes))}"
        f"\n\nRoot kind: {source.kind}\n\nRevision: {revision}"
        "\n\nThis is an inert fragment, not an effective registration."
        "\n\n" + (unsupported or
            "Instantiate through the existing local Save lifecycle. Supply declaration overrides "
            "(including existing recipe params) and repository context. Saving validates "
            "dependencies and refuses any source-relative meaning that cannot be preserved. "
            "Save alone never publishes, starts agents or creates tasks.")
    )
    if requirements:
        body += "\n\nDeclared requirements:\n" + "\n".join(f"- {value}" for value in requirements)
    row = {
        "id": row_id, "title": title, "group": "Available templates",
        "description": description, "kind": kind, "revision": revision,
        "provenance": source.provider, "source": identity,
        "source_path": str(document_path), "repo_scopes": list(source.scopes),
        "validity": "fragment", "enabled": "not-instantiated", "activity": "not-applicable",
        "editable": "false", "instantiable": "false" if unsupported else "true",
        "unsupported_reason": unsupported,
        "requirements": requirements, "prerequisites": "\n".join(requirements),
        "dependency_status": "unsupported" if unsupported else "inputs-required",
        "dependency_errors": [unsupported] if unsupported else [],
        "resolution": "unsupported" if unsupported else "recipe-resolved",
        "components": [], "components_basis": "template",
        "card": {"title": title, "body": body},
    }
    if definitions is not None:
        reference = (
            f"{source.provider}:{document_path.relative_to(root).as_posix()}"
            if source.provider.startswith("repo:") else str(document_path)
        )
        definitions[row_id] = {
            "row": row, "path": document_path, "reference": reference, "document": document,
            "recipe_context": recipe_context,
        }
    return row


def _read_source(
    source: TemplateSource, findings: list[dict], *,
    activation: ActivationReport, definitions: dict | None = None,
    snapshot: RepositoryResourceSnapshot,
) -> list[dict]:
    try:
        if source.kind == "user-local" and not surface_entry_present(source.root):
            return []
        root = source.root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("provider root is not a directory")
        source.index.parent.resolve().relative_to(root)
        if not surface_entry_present(source.index.parent):
            return []
        if not source.index.parent.is_dir():
            raise ValueError("template index parent must be a directory")
        try:
            source.index.lstat()
        except FileNotFoundError:
            return []
        index = source.index.resolve(strict=True)
        index.relative_to(root)
        data = _json_object(index)
        if (
            set(data) != {"schema", "version", "templates"}
            or data.get("schema") != "agent-dispatch.template-index"
            or type(data.get("version")) is not int or data["version"] != 1
        ):
            raise ValueError("unsupported template index schema, version or fields")
        entries = data["templates"]
        if not isinstance(entries, list) or len(entries) > 256:
            raise ValueError("templates must be a list of at most 256 entries")
    except (OSError, ValueError, RuntimeError) as exc:
        _finding(findings, str(source.index), "invalid-index", str(exc))
        return []
    names = Counter(
        entry["id"] for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    )
    rows = []
    for offset, entry in enumerate(entries):
        try:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str) and names[entry["id"]] > 1:
                raise ValueError(f"duplicate template id {entry['id']!r}")
            rows.append(_entry(
                source, entry, root, index, activation=activation,
                definitions=definitions, snapshot=snapshot,
            ))
        except (OSError, ValueError, RuntimeError) as exc:
            _finding(findings, f"{source.index}#{offset}", "invalid-template", str(exc))
    return rows


def discover_templates(
    activation: ActivationReport, *, _definitions: dict | None = None,
) -> tuple[list[dict], dict]:
    """Enumerate inert provider rows and separately scoped completeness findings."""
    findings: list[dict] = []
    snapshot = RepositoryResourceSnapshot(execution=False)
    sources = []
    for finding in activation.findings:
        _finding(findings, "plugin-activation", "activation-finding", str(finding))
    if activation.authority is ScanAuthority.INDETERMINATE and not activation.findings:
        _finding(findings, "plugin-activation", "partial", "Plugin activation evidence is incomplete.")
    for plugin in activation.active.values():
        for selected in plugin.live_roots:
            sources.append(TemplateSource(
                f"plugin:{plugin.source}", selected.root,
                selected.root / "dispatch-templates.json", selected.scopes, selected.kind,
            ))
    try:
        sources.extend(_repository_sources(findings))
    except RegistrarError as exc:
        _finding(findings, "registered-repositories", "unavailable", str(exc))
    base = rd.registrar_dir()
    sources.append(TemplateSource("user-local:templates", base, base / "templates" / "index.json", ("local",), "user-local"))
    rows = []
    seen = set()
    for source in sources:
        token = digest(source.identity())
        if token in seen:
            continue
        seen.add(token)
        rows.extend(_read_source(
            source, findings, activation=activation, definitions=_definitions, snapshot=snapshot,
        ))
    return rows, {
        "state": "partial" if findings else "complete",
        "provider_templates": len(rows), "findings": findings,
        "instantiation": "existing-local-save",
    }
