"""Generic installed-runtime resolver for any agent-* plugin.

Worktree Manager control-plane, Phase 3b/4 follow-on. Extracted from
``engine_client.py``'s agent-worktrees-specific resolution so every agent-*
peer is located the same way, and so the legacy-vs-namespaced decision agrees
with what the plugin itself would decide -- by calling the same vendored
installation-context resolver every agent-* plugin's own bootstrap/doctor
path consults (see ``tools/sync-installation-context.py``), never a bespoke
Worktree Manager heuristic.

Two things this module deliberately does NOT do:

- It does not perform a full peer-launch-style invocation (environment
  rebinding, activation compare-and-swap, receipt re-validation at execution
  time). ``libs/peer-launch/peer_launch.py`` remains the canonical mechanism
  for a **plugin** invoking a same-cell sibling plugin. Worktree Manager is
  not a marketplace plugin (no ``plugin.json``, not in the marketplace) --
  it is the vision's own "explicit management context": a management surface
  that locates an installed plugin runtime without itself owning a cell
  identity. This module is that lighter, read-only counterpart.
- It does not provision, activate, or migrate anything. Every function here
  is read-only discovery.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from types import ModuleType

_INSTALLATION_CONTEXT_MODULE = "worktree_manager._installation_context"


def _state_home() -> Path:
    override = os.environ.get("AGENT_HOME")
    if override:
        return Path(override)
    variable = "USERPROFILE" if os.name == "nt" else "HOME"
    return Path(os.environ.get(variable) or Path.home())


def legacy_plugin_root(plugin_id: str) -> Path:
    """The classic, non-cell-scoped install root: ``~/.<plugin-id>``."""
    return _state_home() / f".{plugin_id}"


def _version_key(version: str):
    supported = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?", version)
    if supported:
        major, minor, patch, dev = supported.groups()
        return (
            1,
            int(major),
            int(minor),
            int(patch),
            1 if dev is None else 0,
            int(dev or 0),
        )
    tokens = re.split(r"(\d+)", version.casefold())
    return (0, tuple((1, int(t)) if t.isdigit() else (0, t) for t in tokens))


def _runtime_candidates(root: Path) -> list[Path]:
    """Every plausible immutable version slot under ``root``, marker-first.

    Generic over ``root``: works identically for a legacy ``~/.<plugin>``
    root and a namespaced cell-scoped plugin root, since both lay out
    ``current-version`` / ``last-known-good`` markers and ``versions/<ver>/``
    slots the same way.
    """
    versions = root / "versions"
    candidates: list[Path] = []

    def contained_slot(version: str) -> Path | None:
        if (
            not version
            or version in {".", ".."}
            or Path(version).name != version
        ):
            return None
        try:
            versions_root = versions.resolve()
            candidate = (versions / version).resolve()
        except OSError:
            return None
        if candidate.parent != versions_root:
            return None
        return candidate

    for marker_name in ("current-version", "last-known-good"):
        try:
            version = (root / marker_name).read_text(encoding="utf-8").strip()
        except OSError:
            version = ""
        candidate = contained_slot(version)
        if candidate is not None:
            candidates.append(candidate)
    try:
        fallback = sorted(
            (path for path in versions.iterdir() if path.is_dir()),
            key=lambda path: _version_key(path.name),
            reverse=True,
        )
    except OSError:
        fallback = []
    candidates.extend(
        candidate
        for path in fallback
        if (candidate := contained_slot(path.name)) is not None
    )
    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate.resolve()))
        if key not in seen:
            seen.add(key)
            out.append(candidate)
    return out


def _load_installation_context() -> ModuleType | None:
    """Load the vendored, byte-identical installation-context primitive.

    Returns ``None`` (never raises) when the vendored copy is somehow
    missing, so a corrupted/absent library never turns a read-only policy
    check into a hard failure -- the caller falls back to legacy exactly as
    if no policy existed, which is the resolver's own stated default.
    """
    path = Path(__file__).with_name("_installation_context.py")
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            _INSTALLATION_CONTEXT_MODULE, path
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[_INSTALLATION_CONTEXT_MODULE] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _canonical_os_profile(environment: dict[str, str]) -> Path | None:
    """The OS account profile to pass as ``os_profile``, or ``None``.

    On Windows this is ``USERPROFILE`` -- the exact selection
    ``installation_context._current_environment`` makes itself, so passing it
    explicitly changes nothing. On POSIX, the resolver deliberately selects
    the real passwd-database home when ``os_profile`` is omitted, precisely
    to avoid trusting a possibly-unset or spoofed ``HOME``; returning ``None``
    here (rather than substituting ``HOME``) lets it do that canonical
    lookup, so Worktree Manager reads the identical policy file an agent-*
    plugin's own bootstrap would. Deliberately distinct from ``AGENT_HOME``,
    which overrides Worktree Manager's own state root, not the shared OS
    identity policy is scoped to.
    """
    if os.name == "nt":
        value = environment.get("USERPROFILE")
        return Path(value) if value else None
    return None


def marketplace_cells_enabled(
    *, os_profile: Path | None = None, environment: dict[str, str] | None = None,
) -> bool:
    """The global ``installationMode.enabled`` policy bit.

    Reads ``~/.copilot-extensions/installation-mode.json`` through the exact
    same vendored resolver every agent-* plugin's own bootstrap/doctor path
    calls (``resolve_installation_mode``'s ``policy`` sub-result), so
    Worktree Manager can never disagree with what a plugin itself would
    compute for the same file. Absent, invalid, or unreadable policy (or a
    missing vendored library) resolves to ``False`` -- the resolver's own
    documented "absent policy selects legacy" default.
    """
    ic = _load_installation_context()
    if ic is None:
        return False
    env = environment if environment is not None else dict(os.environ)
    # On POSIX, `_canonical_os_profile` returns None BY DESIGN so the
    # resolver derives the canonical passwd-database home itself; passing
    # that None through (never substituting anything here) is required, not
    # a failure case -- an early return on None would disable every POSIX
    # policy outright.
    profile = os_profile or _canonical_os_profile(env)
    try:
        resolution = ic.resolve_installation_mode(
            legacy_root=legacy_plugin_root("worktree-manager"),
            os_profile=profile,
            environment=env,
        )
    except Exception:
        return False
    policy = resolution.get("policy") if isinstance(resolution, dict) else None
    if not isinstance(policy, dict):
        return False
    return bool(policy.get("enabled"))


def _durable_home(ic: ModuleType, environment: dict[str, str]) -> Path | None:
    """The canonical ``~/.copilot-extensions`` durable home.

    Delegates entirely to the vendored resolver's own environment/profile
    selection (``_current_environment``) instead of re-deriving the OS
    account profile a second time, which is exactly the kind of duplicated,
    driftable logic that caused the POSIX policy bug above.
    """
    try:
        _current, profile = ic._current_environment(
            environment=environment, os_profile=None, platform=None, wsl_distro=None,
        )
    except Exception:
        return None
    return profile / ".copilot-extensions"


def _namespaced_plugin_root(plugin_id: str) -> Path | None:
    """The cell-scoped plugin root named by ``COPILOT_EXTENSIONS_CONTEXT``.

    Only returned when: the global policy gate is on, an explicit context
    points at a receipt, and that receipt validates as an attributable
    installation for this exact plugin via the vendored
    ``validate_context_receipt`` -- schema/version, canonical marketplace-id
    format, and (crucially) that the receipt sits at the exact canonical
    path derived from its own declared identity under the real durable home,
    not merely "some file whose JSON happens to say the right pluginId".
    This still does not re-validate activation/generation state the way
    ``libs/peer-launch`` does before actually launching a process -- it is
    the read-only "which root should I look under" question, not an
    invocation-time governance gate.
    """
    if not marketplace_cells_enabled():
        return None
    context = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()
    if not context:
        return None
    pointer = Path(context)
    if not pointer.is_absolute():
        return None
    ic = _load_installation_context()
    if ic is None:
        return None
    env = dict(os.environ)
    durable = _durable_home(ic, env)
    if durable is None:
        return None
    # COPILOT_PLUGIN_ROOT (when present) is cross-checked against the
    # receipt's own payload root -- a real protection when a plugin resolves
    # its OWN context. Worktree Manager is not that plugin: any
    # COPILOT_PLUGIN_ROOT it happens to have inherited describes an
    # unrelated ambient context, not this lookup's target, so it must not
    # leak in and spuriously reject an otherwise-valid receipt.
    env.pop("COPILOT_PLUGIN_ROOT", None)
    try:
        validated = ic.validate_context_receipt(
            pointer, durable, expected_plugin_id=plugin_id, environment=env,
        )
    except Exception:
        return None
    plugin_root = validated.get("pluginRoot") if isinstance(validated, dict) else None
    if not plugin_root:
        return None
    return Path(plugin_root)


def _validated_plugin_root(root: Path, plugin_id: str) -> Path | None:
    """Confirm ``root`` is an attributable install root for ``plugin_id``.

    Accepts either receipt shape: the legacy ``deploy-manifest.json``
    (``service`` + ``source.plugin``) or the namespaced ``install.json``
    (``pluginId``). A root with neither, or a receipt naming a different
    plugin, is never trusted.
    """
    manifest_path = root / "deploy-manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(manifest, dict):
            return None
        source = manifest.get("source")
        if (
            manifest.get("service") == plugin_id
            and isinstance(source, dict)
            and source.get("plugin") == plugin_id
        ):
            return root
        return None
    install_path = root / "install.json"
    if install_path.is_file():
        try:
            install = json.loads(install_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if isinstance(install, dict) and install.get("pluginId") == plugin_id:
            return root
    return None


def candidate_plugin_roots(plugin_id: str) -> list[Path]:
    """Roots to check, namespaced first (when policy-enabled and named by an
    explicit context), then the always-available legacy root."""
    roots: list[Path] = []
    namespaced = _namespaced_plugin_root(plugin_id)
    if namespaced is not None:
        roots.append(namespaced)
    roots.append(legacy_plugin_root(plugin_id))
    return roots


def resolve_installed_plugin_slot(plugin_id: str) -> Path | None:
    """The exact marker-selected immutable runtime slot for ``plugin_id``.

    Never PATH, never a bare command name: only an attributable, marker-
    selected slot under a validated install root (namespaced when policy and
    an explicit context agree, else legacy) that is BOTH marked complete AND
    actually has its interpreter present. A slot with a stale/damaged
    interpreter is skipped in favor of the next candidate (``last-known-good``,
    then the newest remaining ``versions/*``) rather than failing the whole
    lookup -- matching the original single-root resolver's fallback loop.
    """
    for root in candidate_plugin_roots(plugin_id):
        if _validated_plugin_root(root, plugin_id) is None:
            continue
        for slot in _runtime_candidates(root):
            if not (slot / ".install-complete.json").is_file():
                continue
            python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            if python.is_file():
                return slot
    return None


def resolve_installed_plugin_command(
    plugin_id: str, module: str | None = None,
) -> list[str] | None:
    """The exact ``[python, -m, module]`` argv for ``plugin_id``'s installed
    runtime, or ``None`` if no attributable, complete install was found."""
    slot = resolve_installed_plugin_slot(plugin_id)
    if slot is None:
        return None
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return [str(python), "-m", module or plugin_id.replace("-", "_")]
