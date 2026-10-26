"""Source-declared operator controls over the existing local Save lifecycle."""

from __future__ import annotations

from copy import deepcopy
import re

from .registrar import RegistrarError

CONTRACT = "agent-dispatch.worker-operator-inputs"
VERSION = 1
PREFIX = "param_"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_LIMIT = 4096


def _text(value: object, where: str, limit: int, *, blank: bool = False) -> str:
    if (
        not isinstance(value, str) or len(value) > limit or "\0" in value
        or (not blank and not value.strip())
    ):
        raise RegistrarError(f"{where}: expected bounded NUL-free text")
    return value


def parse_fields(raw: object) -> list[dict]:
    """A bounded subset of the native configuration form's field vocabulary."""
    if not isinstance(raw, list) or len(raw) > 32:
        raise RegistrarError("operator_fields must be an array of at most 32 fields")
    fields = []
    names = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) - {
            "name", "type", "label", "description", "default", "options",
        }:
            raise RegistrarError("operator_fields contains an unsupported field")
        name = item.get("name")
        if (
            not isinstance(name, str) or not _NAME.fullmatch(name)
            or name in {"name", "owner"} or name in names
        ):
            raise RegistrarError("operator_fields requires unique portable parameter names")
        names.add(name)
        kind = item.get("type")
        if kind not in ("text", "choice"):
            raise RegistrarError("operator_fields supports only text and choice")
        field = {
            "name": name, "type": kind,
            "label": _text(item.get("label"), f"{name}.label", 200),
            "description": _text(item.get("description"), f"{name}.description", 1000),
        }
        if any(ord(c) < 32 or ord(c) == 127 for c in field["label"]):
            raise RegistrarError(f"{name}.label must be single-line readable text")
        if kind == "choice":
            options = item.get("options")
            if not isinstance(options, list) or not 1 <= len(options) <= 64:
                raise RegistrarError(f"{name}.options requires 1-64 choices")
            options = [_text(value, f"{name}.options", _LIMIT) for value in options]
            if len(set(options)) != len(options):
                raise RegistrarError(f"{name}.options must be unique")
            field["options"] = options
        elif "options" in item:
            raise RegistrarError(f"{name}.options is only supported for choice fields")
        if "default" in item:
            default = _text(item["default"], f"{name}.default", _LIMIT, blank=True)
            if kind == "choice" and default not in field["options"]:
                raise RegistrarError(f"{name}.default must name a declared choice")
            field["default"] = default
        fields.append(field)
    return fields


def builtin_fields(key: str) -> list[dict] | None:
    if key != "worker-pool":
        return None
    from .worker_instances import POOL_TEMPLATE

    return [
        {"name": "label", "type": "text", "label": "Watched task label",
         "description": "Only tasks carrying this label are eligible.",
         "default": POOL_TEMPLATE["labels"][0]},
        {"name": "repos", "type": "text", "label": "Repository scope",
         "description": "The existing lane repository filter; all watches every repository.",
         "default": POOL_TEMPLATE["repos"]},
        {"name": "concurrency", "type": "text", "label": "Worker capacity",
         "description": "Maximum simultaneous workers, expressed as a positive whole number.",
         "default": str(POOL_TEMPLATE["concurrency"])},
    ]


def source_fields(selected: dict) -> list[dict]:
    raw = selected["row"].get("operator_parameters")
    if raw is None:
        raise RegistrarError("This template has not declared operator-safe controls.")
    return parse_fields(raw)


def values_for(fields: list[dict], supplied: dict) -> dict[str, str]:
    allowed = {PREFIX + field["name"] for field in fields}
    if set(supplied) - allowed:
        raise RegistrarError("Unknown operator parameter; declaration overrides are not allowed.")
    values = {}
    for field in fields:
        key = PREFIX + field["name"]
        value = supplied.get(key, field.get("default", ""))
        value = _text(value, key, _LIMIT, blank=True)
        if field["type"] == "choice" and value not in field["options"]:
            raise RegistrarError(f"{field['label']}: select a declared choice")
        values[field["name"]] = value
    return values


def form(fields: list[dict], *, name: str = "", repo_root: str = "", values=None) -> list[dict]:
    projected = [
        {"name": "name", "type": "text", "label": "Unique local instance name",
         "description": "Immutable after creation. Saving never edits the template.", "default": name},
        {"name": "repo_root", "type": "text", "label": "Repository root",
         "description": "Existing absolute directory for execution context, when required.",
         "default": repo_root},
    ]
    for field in fields:
        current = {**deepcopy(field), "name": PREFIX + field["name"]}
        if values is not None:
            current["default"] = values[field["name"]]
        projected.append(current)
    return projected


def selected_source(source_id: str, revision: str, *, definitions=None, activation=None) -> dict:
    from .worker_instances import digest, templates

    if source_id.startswith("template:provider:"):
        if definitions is None:
            from .worker_template_materialization import selected_template
            return selected_template(source_id, revision)
        selected = definitions.get(source_id)
        if selected is None or selected["row"]["revision"] != revision:
            raise RegistrarError("template is absent or changed; refresh the catalog")
        if selected["row"]["instantiable"] != "true":
            raise RegistrarError(selected["row"]["unsupported_reason"])
        return {**selected, "activation": activation}
    key = source_id.removeprefix("template:")
    if source_id != f"template:{key}" or key not in templates() or digest(templates()[key]) != revision:
        raise RegistrarError("template is absent or changed; refresh the catalog")
    fields = builtin_fields(key)
    return {"builtin": key, "row": {
        "id": source_id, "revision": revision, "operator_parameters": fields,
    }}


def materialize(selected: dict, values: dict, name: str, repo_root: str, *, snapshot=None) -> dict:
    if selected.get("builtin") == "worker-pool":
        from .worker_instances import POOL_TEMPLATE
        if not values["label"].strip():
            raise RegistrarError("Watched task label must not be empty.")
        if not re.fullmatch(r"[1-9][0-9]*", values["concurrency"]):
            raise RegistrarError("Worker capacity must be a positive whole number.")
        return {
            **deepcopy(POOL_TEMPLATE), "labels": [values["label"]],
            "repos": values["repos"], "concurrency": int(values["concurrency"]),
        }
    from .worker_template_materialization import materialize_template
    return materialize_template(selected, {"params": values}, name, repo_root, snapshot=snapshot)


def verify_record(record: dict, *, definitions=None, activation=None, snapshot=None) -> tuple[dict, dict]:
    from .worker_instances import digest, validate_configuration

    binding = record.get("operator_inputs")
    if (
        not isinstance(binding, dict) or binding.get("contract") != CONTRACT
        or type(binding.get("version")) is not int or binding["version"] != VERSION
        or binding.get("configuration_revision") != record["revision"]
        or not isinstance(binding.get("values"), dict)
        or any(not isinstance(key, str) for key in binding["values"])
    ):
        raise RegistrarError("This instance has no current operator-input binding; use its advanced owning flow.")
    selected = selected_source(
        record["source"]["id"], record["source"]["revision"],
        definitions=definitions, activation=activation,
    )
    fields = source_fields(selected)
    values = values_for(fields, {PREFIX + key: value for key, value in binding["values"].items()})
    if values != binding["values"]:
        raise RegistrarError("Stored operator inputs do not match the declared controls.")
    root = record["configuration"]["repo_root"]
    data, _ = validate_configuration(
        record["name"], materialize(selected, values, record["name"], root, snapshot=snapshot),
        root, snapshot=snapshot,
    )
    if digest({"declaration": data, "repo_root": root}) != record["revision"]:
        raise RegistrarError("Stored operator inputs do not describe the saved configuration.")
    return selected, values


def project(
    row: dict, fields: list[dict] | None, *, record=None, definitions=None, activation=None, snapshot=None,
) -> None:
    row.update(operator_instantiable="false", operator_editable="false")
    if record is None:
        if fields is None:
            row["operator_unsupported_reason"] = "This template has not declared operator-safe controls."
            return
        row["operator_instantiable"] = row.get("instantiable", "false")
        row["operator_fields"] = form(fields, repo_root=row.get("source", {}).get("root", ""))
    else:
        try:
            selected, values = verify_record(
                record, definitions=definitions, activation=activation, snapshot=snapshot,
            )
            row["operator_fields"] = form(
                source_fields(selected), name=record["name"],
                repo_root=record["configuration"]["repo_root"], values=values,
            )
        except (RegistrarError, OSError, ValueError) as exc:
            row["operator_unsupported_reason"] = str(exc)
            return
        row["operator_editable"] = "true"
    row["operator_contract"] = {"id": CONTRACT, "version": VERSION}
    row["operator_contract_version"] = str(VERSION)
