"""The steering form's field list: one owner-neutral schema and validator.

A card's ``request_input`` is a list of fields, each
``{"name", "type", "options"?, "allow_other"?, "show_when"?}``. The
``--request-input`` parser (``steering.parse_request_input``) produces it and
checks it here, and so does any reader that receives one (the attention
contract's ``input``), so both agree on exactly one shape. Standard library
only, so a client can validate a form without depending on a coordinator.
"""

from __future__ import annotations

import re
from typing import Any

#: Supported field types in a ``request-input`` spec.
FIELD_TEXT = "text"
FIELD_TEXTAREA = "textarea"
FIELD_CHOICE = "choice"
FIELD_MULTICHOICE = "multichoice"
FIELD_TYPES = frozenset({FIELD_TEXT, FIELD_TEXTAREA, FIELD_CHOICE, FIELD_MULTICHOICE})
#: The choice-family types (they carry ``options`` and may allow an ``other``).
FIELD_CHOICE_TYPES = frozenset({FIELD_CHOICE, FIELD_MULTICHOICE})
FIELD_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_FIELD_KEYS = frozenset({"name", "type", "options", "allow_other", "show_when"})


def field_list_problem(fields: Any) -> str | None:
    """Why ``fields`` isn't a valid steering field list, or ``None``. An empty
    list is valid (a card with no form)."""
    if not isinstance(fields, list):
        return "a form is a list of fields"
    seen: set[str] = set()
    for field in fields:
        if not isinstance(field, dict) or set(field) - _FIELD_KEYS:
            return "a field has keys name, type, options?, allow_other?, show_when? only"
        name = field.get("name")
        if not isinstance(name, str) or not FIELD_NAME_RE.match(name):
            return f"invalid field name {name!r} (letters, digits, _-, leading letter)"
        if name in seen:
            return f"duplicate field {name!r}"
        seen.add(name)
        if field.get("type") not in FIELD_TYPES:
            return f"field {name!r}: unknown type {field.get('type')!r}"
        choice = field["type"] in FIELD_CHOICE_TYPES
        options = field.get("options")
        if choice != (options is not None):
            return f"field {name!r}: options belong to a choice or multichoice, which needs them"
        if choice and not (isinstance(options, list) and options
                           and all(isinstance(o, str) and o for o in options)):
            return f"{field['type']} field {name!r} has no options"
        if "allow_other" in field and (not choice or not isinstance(field["allow_other"], bool)):
            return f"field {name!r}: allow_other is a boolean on a choice or multichoice only"
        when = field.get("show_when")
        if when is not None and not (isinstance(when, dict) and set(when) == {"field", "equals"}
                                     and all(isinstance(v, str) and v for v in when.values())):
            return f"field {name!r}: condition must be '?choice_field=value'"
    by_name = {field["name"]: field for field in fields}
    for field in fields:
        condition = field.get("show_when")
        if not condition:
            continue
        source = condition["field"]
        source_field = by_name.get(source)
        if source_field is None:
            return f"field {field['name']!r}: condition references unknown field {source!r}"
        if source == field["name"]:
            return f"field {field['name']!r}: condition cannot reference itself"
        if source_field.get("type") != FIELD_CHOICE:
            return f"field {field['name']!r}: condition source {source!r} must be a choice"
        if source_field.get("show_when"):
            return f"field {field['name']!r}: condition source {source!r} cannot itself be conditional"
        if condition["equals"] not in (source_field.get("options") or []):
            return (f"field {field['name']!r}: condition value {condition['equals']!r} "
                    f"is not an option of {source!r}")
    return None
