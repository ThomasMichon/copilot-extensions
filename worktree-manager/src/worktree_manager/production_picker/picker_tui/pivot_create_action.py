"""Phase B's pivot-**level** "create new entry" action (no row selected).

Every existing :class:`~.pivot_manifest.PivotAction` (including
``kind:"form"``) is row-scoped by design -- its field spec is resolved from an
*already selected* entry via ``fields_from``. A pivot that wants a "New …"
affordance (no row to resolve against yet) declares a :class:`CreateAction`
instead: ``fields`` is a static, manifest-declared field spec (the same
``{name,type,options,allow_other,show_when}`` shape
``steering._normalize_form_fields`` already accepts for a row's dynamic
``request_input``), and ``run`` is substituted via the same
``format_form_template`` machinery the row-scoped form action uses
(``{field.<name>}`` tokens; no entry-derived tokens are available since there
is no row). Kept in its own module (not ``pivot_manifest.py``) purely to
control that module's size.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .pivot_actions import ManifestError, _as_argv


@dataclass(frozen=True)
class CreateAction:
    """A pivot-level "create" affordance -- see module docstring."""

    label: str
    key: str
    fields: tuple[Mapping[str, object], ...]
    run: tuple[str, ...]
    confirm: bool = False


def _parse_create_fields(raw: object, *, where: str) -> tuple[Mapping[str, object], ...]:
    """Strictly validate a :class:`CreateAction`'s static field spec.

    Raises :class:`ManifestError` on a malformed entry (this spec is
    manifest-authored, not operator/runtime data, so a typo should surface at
    discovery time -- the same "skip one bad manifest" contract
    ``parse_manifest`` uses elsewhere still applies one level up, in
    :func:`parse_create_action`).
    """
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ManifestError(f"{where} must be an array")
    valid_types = {"text", "textarea", "choice", "multichoice"}
    choice_types = {"choice", "multichoice"}
    out: list[dict] = []
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ManifestError(f"{where}[{i}] must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ManifestError(f"{where}[{i}].name is required")
        normalized_name = name.strip()
        if any(f["name"] == normalized_name for f in out):
            raise ManifestError(
                f"{where}[{i}].name {normalized_name!r} duplicates an earlier "
                "field (after whitespace normalization)"
            )
        ftype = item.get("type", "text")
        if not isinstance(ftype, str) or ftype not in valid_types:
            raise ManifestError(f"{where}[{i}].type must be one of {sorted(valid_types)}")
        field: dict = {"name": normalized_name, "type": ftype}
        allow_other = item.get("allow_other", False)
        if not isinstance(allow_other, bool):
            raise ManifestError(
                f"{where}[{i}].allow_other must be a boolean when present"
            )
        if ftype in choice_types:
            opts = item.get("options")
            if not isinstance(opts, Sequence) or isinstance(opts, (str, bytes)) or not opts:
                raise ManifestError(
                    f"{where}[{i}].options is required (non-empty array) for a "
                    f"{ftype} field"
                )
            if any(not isinstance(o, str) or not o.strip() for o in opts):
                raise ManifestError(
                    f"{where}[{i}].options must be an array of non-blank strings"
                )
            field["options"] = tuple(opts)
            if allow_other:
                field["allow_other"] = True
        condition = item.get("show_when")
        if condition is not None:
            if (
                not isinstance(condition, Mapping)
                or not isinstance(condition.get("field"), str)
                or not condition.get("field", "").strip()
                or not isinstance(condition.get("equals"), str)
                or not condition.get("equals", "").strip()
            ):
                raise ManifestError(
                    f"{where}[{i}].show_when must be "
                    '{"field": str, "equals": str} when present'
                )
            field["show_when"] = {
                "field": condition["field"].strip(),
                "equals": condition["equals"].strip(),
            }
        out.append(field)
    # A second pass (after every field is known) validates each show_when
    # actually names a field the runtime evaluator can match against --
    # steering_form.PivotFormScreen._condition_value only resolves a
    # controller's current answer when it is a *choice* field, and
    # ``_condition_matches`` looks the controller up by name among the SAME
    # field list. A predicate naming a text/textarea/multichoice controller,
    # an unknown name, itself, a controller with its own show_when (chained
    # conditionals the evaluator doesn't support), or an ``equals`` value
    # that isn't one of the controller's declared options can never match --
    # the dependent field would be permanently hidden once UI wiring
    # consumes this schema. Reject it here instead.
    by_name = {f["name"]: f for f in out}
    for i, f in enumerate(out):
        condition = f.get("show_when")
        if condition is None:
            continue
        controller_name = condition["field"]
        controller = by_name.get(controller_name)
        if (
            controller is None
            or controller_name == f["name"]
            or controller["type"] != "choice"
            or "show_when" in controller
            or condition["equals"] not in controller.get("options", ())
        ):
            raise ManifestError(
                f"{where}[{i}].show_when.field must name a different, "
                "unconditional 'choice' field (declared earlier or later in "
                "'fields') whose 'options' include show_when.equals"
            )
    return tuple(out)


def parse_create_action(raw: object, *, where: str) -> CreateAction | None:
    """Validate the optional pivot-level ``create_action`` manifest key.

    ``None``/absent => ``None`` (no create affordance for this pivot).
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ManifestError(f"{where} must be an object when present")
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ManifestError(f"{where}.label is required and must be a non-empty string")
    key = raw.get("key", "create")
    if not isinstance(key, str) or not key.strip():
        raise ManifestError(f"{where}.key must be a non-empty string when present")
    fields = _parse_create_fields(raw.get("fields", []), where=f"{where}.fields")
    run = _as_argv(raw.get("run"), where=f"{where}.run")
    confirm = raw.get("confirm", False)
    if not isinstance(confirm, bool):
        raise ManifestError(f"{where}.confirm must be a boolean when present")
    return CreateAction(
        label=label.strip(), key=key.strip(), fields=fields, run=run, confirm=confirm
    )
