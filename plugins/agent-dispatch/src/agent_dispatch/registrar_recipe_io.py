"""Recipe document reads and optional selected-revision binding."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .registrar import RegistrarError

RECIPE_SUFFIXES = (".yaml", ".yml", ".json")


class RecipeRevisionError(RegistrarError):
    """A materialization read no longer belongs to the selected recipe closure."""


def read_recipe_document(path: Path, *, ref: str) -> Mapping[str, Any]:
    from .registrar_discovery import RegistrarIndeterminateError, _decode

    if path.suffix not in RECIPE_SUFFIXES:
        raise RegistrarError(
            f"extends: recipe file {ref!r} (resolved {path}) has unrecognized "
            f"suffix {path.suffix!r}; expected one of {list(RECIPE_SUFFIXES)}"
        )
    try:
        if path.exists() and not path.is_file():
            raise RegistrarError(f"extends: recipe file {ref!r} must be a regular file")
        text = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise RegistrarError(
            f"extends: recipe file {ref!r} (resolved {path}) has invalid encoding: {exc}"
        ) from exc
    except ValueError as exc:
        raise RegistrarError(
            f"extends: recipe ref {ref!r} could not be resolved to a path: {exc}"
        ) from exc
    except OSError as exc:
        # Preserve last-known declarations for transient read failures.
        raise RegistrarIndeterminateError(
            f"extends: could not read recipe file {ref!r} (resolved {path}): {exc}"
        ) from exc
    return _decode(text, path.suffix, where=str(path))


def read_resolved_recipe(
    identity: str, path: Path | None, *, ref: str, base_dir: Path,
    expected_context: Mapping | None = None,
) -> Mapping[str, Any]:
    from .registrar_recipes import _read_recipe_document, resolve_recipe_ref

    if expected_context is not None and identity not in expected_context:
        raise RecipeRevisionError("template recipe source is absent or changed; refresh the catalog")
    document = (
        _read_recipe_document(path, ref=ref)
        if path is not None else resolve_recipe_ref(ref, base_dir=base_dir)
    )
    if expected_context is not None:
        try:
            matches = json.dumps(document, sort_keys=True) == json.dumps(
                expected_context[identity]["document"], sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise RecipeRevisionError(
                "template recipe source is absent or changed; refresh the catalog"
            ) from exc
        if not matches:
            raise RecipeRevisionError("template recipe source is absent or changed; refresh the catalog")
    return document
