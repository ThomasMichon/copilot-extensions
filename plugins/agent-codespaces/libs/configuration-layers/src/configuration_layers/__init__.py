"""Inert ordinary-configuration composition; callers own resolution and eligibility."""

from __future__ import annotations

import copy
import json
import math
import ntpath
import os
from pathlib import Path


class ConfigurationError(ValueError):
    """Invalid configuration data, scope, binding or source."""


_TIERS = {"defaults": 0, "repository": 1, "machine": 2, "repository_machine": 3}
_ORIGINS = {
    "defaults": {"defaults"},
    "repository": {"base", "knowledge"},
    "machine": {"named_machine", "local_machine"},
    "repository_machine": {"repository_machine"},
}
_CONTEXT = {
    "repository_id", "execution_machine", "installation_cell", "binding",
    "base_root", "knowledge_root",
}
_LAYER = {
    "id", "tier", "origin", "values", "source",
    "repository_id", "execution_machine", "installation_cell",
}


def _json(value, ancestors=(), depth=0):
    if depth > 128:
        raise ConfigurationError("configuration nesting exceeds 128")
    if value is None or type(value) in (str, bool):
        if isinstance(value, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ConfigurationError("unpaired surrogate in string")
        return
    if type(value) in (int, float):
        if abs(value) > 9007199254740991 or not math.isfinite(value):
            raise ConfigurationError("number is not portable JSON")
        return
    if type(value) not in (dict, list) or id(value) in ancestors:
        raise ConfigurationError("configuration must be acyclic JSON data")
    chain = (*ancestors, id(value))
    if isinstance(value, dict):
        for key, child in value.items():
            if type(key) is not str:
                raise ConfigurationError("object keys must be strings")
            _json(key, chain, depth + 1)
            _json(child, chain, depth + 1)
    else:
        for child in value:
            _json(child, chain, depth + 1)


def _object(value, allowed=None):
    _json(value)
    if type(value) is not dict:
        raise ConfigurationError("document must be an object")
    if allowed is not None and set(value) - allowed:
        raise ConfigurationError("unknown contract field")


def _identity(value):
    if type(value) is not str or not value or value.strip() != value:
        raise ConfigurationError("identity must be a nonempty exact string")


def _root(value):
    if type(value) is not str or "\0" in value or not os.path.isabs(value):
        raise ConfigurationError("source root must be absolute")
    return os.path.normpath(value)


def _relative(value, *, allow_dots=False):
    if type(value) is not str or not value or "\0" in value:
        raise ConfigurationError("source path must be a nonempty relative path")
    parts = value.replace("\\", "/").split("/")
    if ntpath.splitdrive(value)[0] or value.startswith(("/", "\\")):
        raise ConfigurationError("absolute source path is not allowed")
    if any(
        part == "" or (part in (".", "..") and not allow_dots)
        or any(c in '<>:"|?*' or ord(c) < 32 for c in part)
        or (part not in (".", "..") and part.endswith((" ", ".")))
        or part.split(".")[0].upper() in {
            "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        for part in parts
    ):
        raise ConfigurationError("unsafe relative source path")
    return os.path.join(*parts)


def _source(value):
    _object(value, {"root", "path"})
    if set(value) != {"root", "path"}:
        raise ConfigurationError("source requires root and path")
    return {"root": _root(value["root"]), "path": _relative(value["path"])}


def plan_layers(context, layers):
    """Validate and return selected, ordered detached layer records; no I/O."""
    _object(context, _CONTEXT)
    for field in ("repository_id", "execution_machine", "installation_cell"):
        _identity(context.get(field))
    binding = context.get("binding")
    if binding == "unresolved":
        raise ConfigurationError("explicit knowledge binding is unresolved")
    if binding not in ("bound", "unbound"):
        raise ConfigurationError("binding must be bound, unbound or unresolved")
    for field in ("base_root", "knowledge_root"):
        if field in context:
            _root(context[field])
    if binding == "bound" and "knowledge_root" not in context:
        raise ConfigurationError("bound knowledge requires a resolved source root")
    if binding == "unbound" and "knowledge_root" in context:
        raise ConfigurationError("unbound context cannot supply knowledge root")
    if type(layers) is not list:
        raise ConfigurationError("layers must be an array")
    seen = set()
    selected = []
    for layer in layers:
        _object(layer, _LAYER)
        for field in ("id", "tier", "origin"):
            _identity(layer.get(field))
        if layer["id"] in seen:
            raise ConfigurationError("duplicate or conflicting layer id")
        seen.add(layer["id"])
        tier = layer["tier"]
        if tier not in _TIERS or layer["origin"] not in _ORIGINS[tier]:
            raise ConfigurationError("invalid tier/origin")
        _object(layer.get("values"))
        required = {
            "defaults": set(),
            "repository": {"repository_id"},
            "machine": {"execution_machine"},
            "repository_machine": {"repository_id", "execution_machine"},
        }[tier]
        scopes = {"repository_id", "execution_machine", "installation_cell"} & layer.keys()
        if not required <= scopes or scopes - required - (
            {"installation_cell"} if tier != "defaults" else set()
        ):
            raise ConfigurationError("missing or conflicting layer scope")
        for field in scopes:
            _identity(layer[field])
        entry = copy.deepcopy(layer)
        if "source" in layer:
            entry["source"] = _source(layer["source"])
        if tier == "defaults" and "source" in layer:
            raise ConfigurationError("built-in defaults have no source file")
        origin = layer["origin"]
        matches = all(layer[field] == context[field] for field in scopes)
        if origin == "knowledge" and binding == "unbound":
            matches = False
        if matches and origin in ("base", "knowledge") and "source" in entry:
            root_key = "base_root" if origin == "base" else "knowledge_root"
            if root_key in context and os.path.normcase(entry["source"]["root"]) != (
                os.path.normcase(_root(context[root_key]))
            ):
                raise ConfigurationError("repository source has the wrong root owner")
        if matches:
            selected.append(entry)
    # Named fragments and local settings retain explicit caller order within machine tier.
    return sorted(selected, key=lambda x: (
        _TIERS[x["tier"]], 1 if x["origin"] == "knowledge" else 0,
    ))


def _pointer(parent, key):
    return parent + "/" + key.replace("~", "~0").replace("/", "~1")


def compose(context, layers):
    """Return {values, provenance, layers}; arrays/scalars/null replace atomically."""
    plan = plan_layers(context, layers)
    values = {}
    provenance = {}

    def remove(pointer):
        for key in list(provenance):
            if key == pointer or key.startswith(pointer + "/"):
                del provenance[key]

    def merge(target, incoming, pointer, owner):
        # Empty mappings merge, rather than deleting existing mapping contents.
        if isinstance(incoming, dict):
            if not isinstance(target, dict):
                remove(pointer)
                target = {}
            if incoming:
                provenance.pop(pointer, None)
                for key, child in incoming.items():
                    child_pointer = _pointer(pointer, key)
                    target[key] = merge(target.get(key), child, child_pointer, owner)
            elif not target and pointer not in provenance:
                provenance[pointer] = copy.deepcopy(owner)
            return target
        remove(pointer)
        provenance[pointer] = copy.deepcopy(owner)
        return copy.deepcopy(incoming)

    for layer in plan:
        owner = {key: copy.deepcopy(value) for key, value in layer.items() if key != "values"}
        values = merge(values, layer["values"], "", owner)
    return {"values": values, "provenance": provenance, "layers": [x["id"] for x in plan]}


def _contained(root, relative, *, allow_dots=False):
    """Resolve physically, rejecting escapes; not a filesystem race/permission boundary."""
    base = Path(_root(root)).resolve(strict=True)
    if not base.is_dir():
        raise ConfigurationError("source root is not a directory")
    lexical = base
    for part in Path(_relative(relative, allow_dots=allow_dots)).parts:
        lexical = lexical / part
        if lexical.is_symlink() and not lexical.exists():
            raise ConfigurationError("broken source link")
    target = lexical.resolve(strict=False)
    if not target.is_relative_to(base):
        raise ConfigurationError("source path escapes its owning root")
    return target


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError("duplicate document key")
        result[key] = value
    return result


def read_source(root, relative, *, parser=None, optional=True):
    """Read one exact owner-selected document; parser(text) must return JSON object.

    Only a genuinely absent optional file returns None. Parsing and access failures
    remain errors. The default parser is strict JSON; native YAML/alias eligibility
    is an owner adapter responsibility.
    """
    try:
        target = _contained(root, relative)
        # lexists distinguishes a broken link from an absent optional file.
        lexical = Path(root).joinpath(_relative(relative))
        if lexical.is_symlink() and not lexical.exists():
            raise ConfigurationError("broken source link")
        try:
            text = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            if optional and not os.path.lexists(lexical):
                return None
            raise
        value = parser(text) if parser else json.loads(text, object_pairs_hook=_pairs)
        _object(value)
        return {"values": value, "source": _source({"root": root, "path": relative})}
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise ConfigurationError(f"cannot read selected source: {exc}") from exc


def resolve_path(result, pointer):
    """Resolve a winning string leaf relative to its source document, within root."""
    try:
        owner = result["provenance"][pointer]
        source = _source(owner["source"])
        value = result["values"]
        if pointer:
            if not pointer.startswith("/"):
                raise ConfigurationError("invalid JSON pointer")
            for token in pointer[1:].split("/"):
                value = value[token.replace("~1", "/").replace("~0", "~")]
        relative = os.path.normpath(os.path.join(
            os.path.dirname(source["path"]), _relative(value, allow_dots=True),
        ))
        return str(_contained(source["root"], relative, allow_dots=True))
    except (KeyError, TypeError, OSError, RuntimeError) as exc:
        raise ConfigurationError("path requires a winning source-owned string leaf") from exc
