"""Filesystem and command-resolution helpers for the pivot registry."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import cast

from dropin_registry import EntryDecision, ScanAuthority
from plugin_activation import ActivationReport, ActivePlugin

from .pivot_actions import ManifestError, _as_argv
from .pivot_manifest import (
    MANAGED_SCHEMA_VERSION,
    _FILE_ATTRIBUTE_REPARSE_POINT,
    _MIGRATABLE_SCHEMA_VERSIONS,
    _read_json,
    _KNOWN_LEGACY_PIVOTS,
)

# Exact canonical schema-v1 Tasks template before category-specific presentation.
# Recognition is scoped to the existing canonical provider, never filename alone.
_KNOWN_LEGACY_TEMPLATE_HASHES = {
    "agent-dispatch.json": frozenset(
        {"ef0c01ef40c70500ec4ce4910abfea0927cb37ccdc6f69b8fec8f7ac18d527ef"}
    ),
}


def _known_legacy_owned(
    existing: object,
    *,
    source: str,
    template_name: str,
    current_template: Mapping[str, object] | None,
) -> bool:
    if (
        current_template is None
        or not isinstance(existing, dict)
        or type(existing.get("schema_version")) is not int
        or existing.get("schema_version") != 1
        or _KNOWN_LEGACY_PIVOTS.get(template_name) != source
    ):
        return False
    fingerprint = hashlib.sha256(
        json.dumps(existing, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    current_fingerprint = hashlib.sha256(
        json.dumps(dict(current_template), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return (
        fingerprint == current_fingerprint
        or fingerprint in _KNOWN_LEGACY_TEMPLATE_HASHES.get(template_name, ())
    )

class TargetUnusableError(ValueError):
    """A manifest command exists but cannot be executed safely."""


def _activation_from_plugins_root(root: Path) -> ActivationReport:
    """Build a synthetic active report for ``ensure_pivots`` unit tests."""
    decisions: dict[str, EntryDecision[ActivePlugin]] = {}
    try:
        manifests = sorted(root.glob("*/*/pivots/*.json"))
    except OSError:
        manifests = []
    for manifest in manifests:
        try:
            plugin_root = manifest.parents[1].resolve(strict=True)
        except OSError:
            continue
        marketplace = manifest.parents[2].name
        plugin = manifest.parents[1].name
        source = f"{plugin}@{marketplace}"
        decisions[source] = EntryDecision.active(
            ActivePlugin(
                source=source,
                name=plugin,
                marketplace=marketplace,
                root=plugin_root,
                scopes=("global",),
            )
        )
    return ActivationReport(
        authority=ScanAuthority.COMPLETE,
        decisions=decisions,
    )


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _payload_command(root: Path | None, command: str) -> Path | None:
    if root is None or Path(command).name != command:
        return None
    candidates = [root / "bin" / command]
    if os.name == "nt":
        candidates = [
            root / "bin" / f"{command}.cmd",
            *candidates,
        ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    try:
        declaration = json.loads((root / "payload-invocation.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ManifestError(f"Payload command declaration is unreadable: {exc}") from exc
    if not isinstance(declaration, dict):
        raise ManifestError("Payload command declaration must be an object")
    if declaration.get("command") == command:
        return candidates[0]
    return None


def _resolve_command(command: Sequence[str], *, root: Path | None = None) -> list[str]:
    if not command or any(not isinstance(item, str) or not item for item in command):
        raise ManifestError("command must be a non-empty array of strings")
    first = command[0]
    payload = _payload_command(root, first)
    candidate = Path(first).expanduser()
    has_path = candidate.is_absolute() or "/" in first or "\\" in first
    if payload is not None:
        resolved = str(payload)
    elif has_path:
        resolved = str(candidate if candidate.is_absolute() or root is None else root / candidate)
    else:
        resolved = shutil.which(first)
    if not resolved:
        raise FileNotFoundError(first)
    target = Path(resolved)
    canonical = target.resolve(strict=True)
    info = canonical.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
    ):
        raise TargetUnusableError("command must be a regular non-reparse file")
    if os.name != "nt" and not os.access(canonical, os.X_OK):
        raise TargetUnusableError("command is not executable")
    if root is not None and (
        payload is not None or (has_path and not candidate.is_absolute())
    ):
        try:
            canonical.relative_to(root)
        except ValueError as exc:
            raise TargetUnusableError(
                "relative command escapes the identity-verified plugin root"
            ) from exc
    if os.name == "nt" and canonical.suffix.casefold() == ".ps1":
        raise TargetUnusableError(
            "PowerShell scripts must be invoked through an executable wrapper"
        )
    # Python virtual environments and other multicall launchers depend on the
    # invocation path even when its validated target is a shared executable.
    return [str(target.absolute()), *command[1:]]


def _rewrite_manifest_commands(
    raw: Mapping[str, object],
    *,
    root: Path | None,
    require_targets: bool,
    reject_unusable: bool = False,
) -> dict[str, object]:
    """Validate executable targets and bind absolute invocation paths."""
    from worktree_manager.configuration_preview import validate_preview_actions
    try:
        validate_preview_actions(raw)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    data = deepcopy(dict(raw))

    def rewrite(container: dict[str, object], key: str, *, optional: bool = False) -> None:
        value = container.get(key)
        if value is None:
            return
        value = _as_argv(value, where=f"`{key}`")
        try:
            container[key] = _resolve_command(
                cast(Sequence[str], value),
                root=root,
            )
        except ManifestError:
            raise
        except (FileNotFoundError, OSError, TargetUnusableError) as exc:
            if not optional and (
                require_targets or (reject_unusable and isinstance(exc, TargetUnusableError))
            ):
                raise
            if root is not None:
                declared = _payload_command(root, cast(Sequence[str], value)[0])
                if declared is not None:
                    container[key] = [str(declared.absolute()), *cast(Sequence[str], value)[1:]]
            if optional and root is not None:
                first = cast(Sequence[str], value)[0]
                candidate = Path(first).expanduser()
                if not candidate.is_absolute() and ("/" in first or "\\" in first):
                    container[key] = [str(root / candidate), *cast(Sequence[str], value)[1:]]
            if optional:
                container["_preview_error"] = f"Preview command unavailable: {exc}"
        except ValueError as exc:
            # Covers an invalid-path argv head (e.g. an embedded null byte)
            # that `Path()`/`os.path` construction itself rejects before any
            # filesystem check -- re-raised as `TargetUnusableError` (already
            # a `ValueError` subclass) so it is caught by the SAME
            # `target-unusable` handling every other unusable target already
            # gets, rather than an uncaught plain `ValueError` aborting the
            # whole scan. `ManifestError` (also a `ValueError` subclass, see
            # above) is deliberately excluded from this conversion -- it
            # already has its own, distinct `invalid-entry` handling upstream
            # and must always propagate unconverted, regardless of
            # `require_targets` (e.g. `_resolve_command`'s own empty-argv
            # check).
            if (require_targets or reject_unusable) and not optional:
                raise TargetUnusableError(str(exc)) from exc
            if optional:
                container["_preview_error"] = f"Preview command unusable: {exc}"

    def rewrite_form(form: object, *, where: str) -> None:
        if not isinstance(form, dict):
            return
        if "run" in form:
            form["run"] = list(_as_argv(form.get("run"), where=f"`{where}.run`"))
            rewrite(form, "run")
        fields = form.get("fields")
        if not isinstance(fields, list):
            return
        for field_spec in fields:
            if not isinstance(field_spec, dict) or "options_command" not in field_spec:
                continue
            field_spec["options_command"] = list(
                _as_argv(
                    field_spec.get("options_command"),
                    where=f"`{where}.fields[].options_command`",
                )
            )
            rewrite(field_spec, "options_command")

    if isinstance(data.get("list"), Sequence) and not isinstance(
        data.get("list"), (str, bytes)
    ):
        rewrite(data, "list")
    for collection in ("actions", "view_actions", "worktree_actions", "config_sections"):
        entries = data.get(collection)
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict) or "run" not in item:
                continue
            if collection in {"actions", "view_actions"} and item.get("kind") in {"internal", "card"}:
                continue
            rewrite(item, "run")
            if collection in {"actions", "view_actions"} and "draft" in item:
                from worktree_manager.form_drafts import parse_draft
                try:
                    draft = parse_draft(item["draft"])
                except ValueError as exc:
                    raise ManifestError(str(exc)) from exc
                if item.get("kind") != "form" or draft is None:
                    raise ManifestError("draft requires a form action")
                item["draft"] = draft
                rewrite(draft, "save")
                rewrite(draft, "clear")
            if collection in {"actions", "view_actions"} and "request" in item:
                from worktree_manager.form_requests import parse_request
                try:
                    request = parse_request(item["request"])
                except ValueError as exc:
                    raise ManifestError(str(exc)) from exc
                if item.get("kind") != "form" or request is None:
                    raise ManifestError("request requires a form action")
                item["request"] = request
                rewrite(request, "prepare")
                rewrite(request, "receipt")
            if collection in {"actions", "view_actions"} and "preview" in item:
                item.pop("_preview_error", None)
                item["preview"] = list(_as_argv(item["preview"], where="`actions[].preview`"))
                rewrite(item, "preview", optional=True)
            if collection == "config_sections":
                rewrite_form(
                    item.get("configure_action"),
                    where="config_sections[].configure_action",
                )
    rewrite_form(data.get("create_action"), where="create_action")
    return data


def _managed_manifest_data(
    template: Mapping[str, object],
    *,
    source: str,
    root: Path,
    template_name: str,
    require_targets: bool,
) -> dict[str, object]:
    data = _rewrite_manifest_commands(
        template,
        root=root,
        require_targets=require_targets,
    )
    data["schema_version"] = MANAGED_SCHEMA_VERSION
    data["plugin"] = source
    data["plugin_root"] = str(root)
    data["template"] = template_name
    return data


def _managed_pointer_data(
    *, source: str, root: Path, template_name: str
) -> dict[str, object]:
    """The small attributed pointer actually persisted for a managed pivot.

    Deliberately carries none of the template's baked content (no ``list``/
    ``actions``) -- only enough to relocate the identity-verified template at
    read time (see ``_classify_managed``, which always re-resolves commands
    fresh via :func:`_managed_manifest_data`). Because this is a pure function
    of ``(source, root, template_name)``, it is stable across ordinary
    template-content edits and only changes when the plugin's active root
    itself changes (reinstall/version bump).
    """
    return {
        "schema_version": MANAGED_SCHEMA_VERSION,
        "plugin": source,
        "plugin_root": str(root),
        "template": template_name,
    }


def _read_verified_template(canonical_root: Path, template_name: str) -> object:
    """Read ``canonical_root/pivots/<template_name>``, applying the same
    regular-file/non-reparse/containment checks the materializer's own
    candidate scan uses.

    A managed pointer's ``template`` is re-read from the identity-verified
    plugin root on **every** scan -- it is the live source of the
    contribution, not just a materialization-time input -- so it must be
    just as hard to redirect via a symlinked file or a redirected ``pivots``
    directory as materialization already requires. Raises
    :class:`TargetUnusableError` for a non-regular/reparse target or one that
    resolves outside ``canonical_root``; ``FileNotFoundError`` /
    ``UnicodeDecodeError`` / ``json.JSONDecodeError`` propagate unchanged for
    the caller's existing handling.
    """
    template_path = canonical_root / "pivots" / template_name
    info = template_path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        raise TargetUnusableError("pivot template must be a regular non-reparse file")
    canonical_template = template_path.resolve(strict=True)
    try:
        canonical_template.relative_to(canonical_root)
    except ValueError as exc:
        raise TargetUnusableError(
            "pivot template escapes the identity-verified plugin root"
        ) from exc
    return _read_json(canonical_template)


def _exclusive_create_text(target: Path, content: str) -> bool:
    """Atomically publish ``content`` only when ``target`` is still absent."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _owned_pointer_identity(existing: object) -> tuple[str, str] | None:
    """``(plugin, template)`` if ``existing`` self-declares as our own pointer.

    An operator-authored file never carries a ``schema_version`` (see the
    "operator" entry class); any file that does, **and whose schema_version
    is one this materializer actually knows how to migrate**
    (:data:`_MIGRATABLE_SCHEMA_VERSIONS` -- the current pointer shape plus the
    one superseded baked shape), is unambiguously something a plugin
    materializer previously wrote for itself, so it is safe for this same
    materializer to refresh in place. A schema version outside that set --
    e.g. one a *newer* agent-worktrees introduced that this build doesn't
    understand yet -- is deliberately left untouched rather than silently
    downgraded. Exact known schema-v1 recognition is a separate, provider-scoped migration
    check in the publisher. Operator, foreign and future artifacts remain untouched.
    """
    if (
        not isinstance(existing, dict)
        or existing.get("schema_version") not in _MIGRATABLE_SCHEMA_VERSIONS
        or not isinstance(existing.get("plugin"), str)
        or not isinstance(existing.get("template"), str)
    ):
        return None
    return existing["plugin"], existing["template"]


def _publish_managed_pointer(
    target: Path, content: str, *, source: str, template_name: str,
    legacy_template: Mapping[str, object] | None = None,
) -> bool:
    """Create ``target``, or refresh it in place if we already own it.

    Exclusive-create covers the common "not published yet" case atomically
    (unchanged race-safety from :func:`_exclusive_create_text`). When
    ``target`` already exists, this only overwrites it after confirming --
    via :func:`_owned_pointer_identity` -- that the existing content is
    itself a materializer-owned pointer for this exact ``(source,
    template_name)`` identity, or an exact recognized canonical legacy
    template for that provider; an operator-authored file, or one belonging to
    a different plugin/template, is never touched. Returns whether a write
    actually happened -- ``False`` both when refused and when the file
    already held this exact content (idempotent no-op), so a caller can use
    the result to report only genuine changes.
    """
    if _exclusive_create_text(target, content):
        return True

    def _owned_and_stale() -> bool | None:
        """``None`` => already correct (no-op); ``True`` => ours, safe to
        refresh; ``False`` => not ours, or unreadable -- never touch it."""
        try:
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                return False
            existing_raw = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        if existing_raw == content:
            return None
        try:
            existing = json.loads(existing_raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        return (
            _owned_pointer_identity(existing) == (source, template_name)
            or _known_legacy_owned(
                existing, source=source, template_name=template_name,
                current_template=legacy_template,
            )
        )

    if _owned_and_stale() is not True:
        return False
    # Prepare the replacement payload *before* the final ownership recheck,
    # so the window between "confirmed ours" and the atomic os.replace is as
    # small as possible -- a single stat+read immediately below, not the
    # whole preceding validate-and-build sequence. This does not fully
    # eliminate a concurrent operator write landing in that exact instant (no
    # cross-platform advisory lock is in play, and an operator's editor
    # wouldn't respect one either), but it shrinks the exposure from "the
    # entire refresh" down to two syscalls.
    fd, temporary = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if _owned_and_stale() is not True:
            return False
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return True


def _materialize_active_pivots(
    destination: Path,
    activation: ActivationReport,
) -> list[str]:
    """Refresh managed entries for active roots without deleting any file."""
    if activation.authority is ScanAuthority.INDETERMINATE:
        return []
    candidates: dict[str, list[tuple[str, Path, dict[str, object]]]] = {}
    for source, active in sorted(activation.active.items()):
        seen_roots: set[Path] = set()
        for selected in active.live_roots:
            if selected.root in seen_roots:
                continue
            seen_roots.add(selected.root)
            pivot_dir = selected.root / "pivots"
            try:
                templates = sorted(pivot_dir.glob("*.json"))
            except OSError:
                continue
            for template_path in templates:
                try:
                    info = template_path.lstat()
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or stat.S_ISLNK(info.st_mode)
                        or _is_reparse(info)
                    ):
                        continue
                    raw = _read_json(template_path)
                    if not isinstance(raw, dict):
                        continue
                    candidates.setdefault(template_path.name, []).append(
                        (source, selected.root, raw)
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue

    changed: list[str] = []
    for name, owners in sorted(candidates.items()):
        sources = {source for source, _root, _template in owners}
        if len(sources) != 1:
            continue
        # live_roots is precedence-ordered, so a project-local directory wins
        # over an installed copy without weakening cross-plugin collision safety.
        source, root, template = owners[0]
        try:
            # Validation only -- this proves the template is a well-formed
            # manifest before we publish a pointer to it; the fully-resolved
            # result is discarded, since only the small pointer is persisted
            # (see _managed_pointer_data).
            _managed_manifest_data(
                template,
                source=source,
                root=root,
                template_name=name,
                require_targets=False,
            )
        except ManifestError:
            continue
        data = _managed_pointer_data(source=source, root=root, template_name=name)
        content = json.dumps(
            data,
            indent=2,
            ensure_ascii=True,
            sort_keys=True,
        ) + "\n"
        target = destination / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if _publish_managed_pointer(
                target, content, source=source, template_name=name, legacy_template=template,
            ):
                changed.append(target.name)
        except OSError:
            continue
    return changed
