"""Control-plane provider manifest parsing and discovery.

Extracted from ``front_door_cli`` (copilot-extensions' own 1000-line module-
size cap left that module with zero headroom) rather than grown in place.
Owns the ``control-plane-providers.d/<provider>.json`` registration schema:
parsing a single manifest, discovering every manifest on disk, and the
leaked-pytest-tmp_path guard that rejects a manifest a misbehaving test
corrupted (copilot-extensions#5122 -- see
``_LeakedTestTmpPathManifestError`` below for the full incident writeup).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import config as cfg
from . import output

_CONTROL_PLANE_PROVIDERS_SUBDIR = "control-plane-providers.d"
_CONTROL_PLANE_PROVIDERS_DIR_ENV = "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"

_CONTROL_PLANE_PROVIDER_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class _ControlPlaneProviderManifest:
    provider: str
    command: tuple[str, ...]
    minimum_version: tuple[int, int, int, int]
    minimum_version_text: str
    description: str = ""
    provider_root: str = ""
    source_path: str = ""


def _parse_comparable_version(text: str) -> tuple[int, int, int, int] | None:
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?\b", text)
    if match is None:
        return None
    major, minor, patch = (int(match.group(i)) for i in range(1, 4))
    dev = int(match.group(4)) if match.group(4) is not None else 1_000_000
    return (major, minor, patch, dev)


def _control_plane_providers_dir() -> Path:
    configured = os.environ.get(_CONTROL_PLANE_PROVIDERS_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return cfg.install_dir() / _CONTROL_PLANE_PROVIDERS_SUBDIR


class _LeakedTestTmpPathManifestError(ValueError):
    """A manifest field resolves into a pytest sandbox, not a real install.

    Raised by ``_parse_control_plane_provider_manifest`` when ``command``/
    ``provider_root`` carries a ``pytest-of-<user>`` path segment -- the
    unmistakable signature of a test run (``self_install()``/
    ``self_update(dry_run=False)`` in the ``worktree-manager`` payload, or
    any similarly-shaped test elsewhere) that failed to isolate
    ``control_plane_providers_dir()`` and wrote its own ``tmp_path`` straight
    through to the real, shared registry (copilot-extensions#5122). Once
    written, the corrupt manifest wedges every project's interactive launch
    on this machine until someone notices and repairs it by hand.

    Caught specifically (not folded into the generic parse-error skip) so
    ``_discover_control_plane_provider_manifests`` can surface a pointed
    diagnostic instead of silently falling through to the "no Worktree
    Manager installed" install-trigger message, which would be actively
    misleading when a real install is sitting right there, just not
    currently selectable because its own registration entry is corrupt.
    """


def _pytest_sandbox_marker(value: str) -> str | None:
    """Return the ``pytest-of-<user>/pytest-<n>`` segment in ``value``, if any."""
    match = re.search(r"pytest-of-[^/\\]+[/\\]pytest-\d+", value.replace("\\", "/"), re.IGNORECASE)
    return match.group(0) if match else None


def _leaked_pytest_tmp_path_segment(source_path: str, *values: str) -> str | None:
    """Return the first ``values`` entry carrying a pytest-sandbox marker that
    ``source_path`` itself does not, if any.

    A manifest that lives *inside* a pytest sandbox is a normal, correctly-
    isolated test fixture (every test in this suite does exactly this via
    ``_CONTROL_PLANE_PROVIDERS_DIR_ENV``, and its own ``command``/
    ``provider_root`` legitimately point back into that same sandbox) -- not
    a bug, so ``source_path`` carrying a marker at all is enough to skip the
    check entirely; comparing the two markers for exact equality would be
    fragile (a value nested one level deeper under the same real tmp_path
    still carries a *different* literal marker than a synthetic one crafted
    inside that value, as this guard's own unit tests show, while being a
    perfectly legitimate test fixture).

    The real-world incident this guards against (copilot-extensions#5122) is
    different: the manifest FILE sits in the real, production registry (no
    pytest-sandbox marker in ``source_path`` at all), but its ``command``/
    ``provider_root`` values carry one anyway --
    i.e. a test failed to isolate ``control_plane_providers_dir()`` and
    overwrote the real file with its own ``tmp_path``. Flagging a marker in
    ``values`` only when ``source_path`` has none catches exactly that,
    without ever tripping on a legitimately-isolated test fixture (whose
    ``source_path`` always carries a marker of its own).
    """
    if _pytest_sandbox_marker(source_path):
        return None
    for value in values:
        if _pytest_sandbox_marker(value):
            return value
    return None


def _parse_control_plane_provider_manifest(
    payload: object, *, source_path: str
) -> _ControlPlaneProviderManifest:
    if not isinstance(payload, dict):
        raise ValueError("manifest root must be a JSON object")
    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise ValueError("`schema_version` must be the integer 1")
    provider = payload.get("provider")
    if not isinstance(provider, str) or not _CONTROL_PLANE_PROVIDER_TOKEN_RE.fullmatch(provider):
        raise ValueError("`provider` must be a safe-token string")
    command = payload.get("command")
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part for part in command)
    ):
        raise ValueError("`command` must be a non-empty array of strings")
    minimum_version_text = payload.get("minimum_version")
    if not isinstance(minimum_version_text, str) or not minimum_version_text.strip():
        raise ValueError("`minimum_version` must be a non-empty version string")
    minimum_version = _parse_comparable_version(minimum_version_text.strip())
    if minimum_version is None:
        raise ValueError("`minimum_version` must parse as MAJOR.MINOR.PATCH[-devN]")
    description = payload.get("description", "")
    if not isinstance(description, str):
        raise ValueError("`description` must be a string when present")
    provider_root = payload.get("provider_root", "")
    if not isinstance(provider_root, str):
        raise ValueError("`provider_root` must be a string when present")
    leaked = _leaked_pytest_tmp_path_segment(source_path, *command, provider_root)
    if leaked:
        raise _LeakedTestTmpPathManifestError(
            f"path {leaked!r} looks like a leaked pytest tmp_path -- a test run "
            f"likely overwrote {source_path} instead of using an isolated "
            "sandbox; delete it and re-run the real install/update to repair."
        )
    return _ControlPlaneProviderManifest(
        provider=provider,
        command=tuple(command),
        minimum_version=minimum_version,
        minimum_version_text=minimum_version_text.strip(),
        description=description,
        provider_root=provider_root,
        source_path=source_path,
    )


def _discover_control_plane_provider_manifests() -> dict[str, _ControlPlaneProviderManifest]:
    # Local import: avoids a module-load-time cycle with front_door_cli (which
    # imports this module). By call time front_door_cli is always fully
    # loaded, so this only exists to preserve the long-standing test-patching
    # seam (monkeypatching `m._control_plane_providers_dir`).
    from .front_door_cli import _core_helper

    manifests: dict[str, _ControlPlaneProviderManifest] = {}
    root = _core_helper("_control_plane_providers_dir", _control_plane_providers_dir)()
    try:
        entries = sorted(root.glob("*.json"))
    except OSError:
        return manifests
    for path in entries:
        try:
            manifest = _parse_control_plane_provider_manifest(
                json.loads(path.read_text(encoding="utf-8")),
                source_path=str(path),
            )
        except _LeakedTestTmpPathManifestError as exc:
            output.warn(f"Ignoring a corrupt control-plane provider manifest at {path}: {exc}")
            continue
        except (OSError, ValueError, TypeError):
            continue
        manifests.setdefault(manifest.provider, manifest)
    return manifests
