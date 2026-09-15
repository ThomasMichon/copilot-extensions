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

#: Filesystem-safe plugin id: mirrors the vendored resolver's own
#: ``_assert_plugin_id`` shape (alnum, ``._-`` interior, no leading/trailing
#: separator, no bare ``.``/``..``) so a value like ``"agent-foo/../../x"``
#: can never be interpolated into a legacy or namespaced root path.
_PLUGIN_ID_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


def _validate_plugin_id(plugin_id: str) -> None:
    if (
        not isinstance(plugin_id, str)
        or plugin_id in {".", ".."}
        or not _PLUGIN_ID_RE.fullmatch(plugin_id)
        or "/" in plugin_id
        or "\\" in plugin_id
    ):
        raise ValueError(f"Invalid filesystem-safe plugin id: {plugin_id!r}")


def _state_home() -> Path:
    override = os.environ.get("AGENT_HOME")
    if override:
        return Path(override)
    variable = "USERPROFILE" if os.name == "nt" else "HOME"
    return Path(os.environ.get(variable) or Path.home())


def legacy_plugin_root(plugin_id: str) -> Path:
    """The classic, non-cell-scoped install root: ``~/.<plugin-id>``."""
    _validate_plugin_id(plugin_id)
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


class _MissingType:
    """Sentinel distinguishing 'not attempted yet' from 'attempted and
    failed' so a load failure is cached too, instead of re-parsing the
    9,169-line vendored module on every single call within a process."""


_NOT_LOADED = _MissingType()
_LOADED_INSTALLATION_CONTEXT: "ModuleType | _MissingType" = _NOT_LOADED


def _load_installation_context() -> ModuleType | None:
    """Load the vendored, byte-identical installation-context primitive.

    Cached for the lifetime of the process (successful or failed) -- this
    module is looked up on every engine request, and re-parsing/executing a
    9,169-line file each time is avoidable latency for something that never
    changes mid-process.

    Returns ``None`` (never raises) when the vendored copy is somehow
    missing, so a corrupted/absent library never turns a read-only policy
    check into a hard failure -- the caller falls back to legacy exactly as
    if no policy existed, which is the resolver's own stated default.
    """
    global _LOADED_INSTALLATION_CONTEXT
    if _LOADED_INSTALLATION_CONTEXT is not _NOT_LOADED:
        return _LOADED_INSTALLATION_CONTEXT  # type: ignore[return-value]
    path = Path(__file__).with_name("_installation_context.py")
    module: ModuleType | None
    if not path.is_file():
        module = None
    else:
        try:
            spec = importlib.util.spec_from_file_location(
                _INSTALLATION_CONTEXT_MODULE, path
            )
            if spec is None or spec.loader is None:
                module = None
            else:
                module = importlib.util.module_from_spec(spec)
                sys.modules[_INSTALLATION_CONTEXT_MODULE] = module
                spec.loader.exec_module(module)
        except Exception:
            module = None
    _LOADED_INSTALLATION_CONTEXT = module
    return module


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


def _paths_equal(left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(
        os.path.normpath(right)
    )


def _namespaced_resolution(plugin_id: str) -> dict | None:
    """The raw ``resolve_installation_mode`` result for an explicit
    ``COPILOT_EXTENSIONS_CONTEXT`` naming ``plugin_id``, after validating the
    receipt via ``validate_context_receipt`` -- or ``None`` when there is no
    context, it is unreadable, or it fails that validation (schema/version,
    canonical marketplace-id format, and that the receipt sits at the exact
    canonical path derived from its own declared identity under the real
    durable home -- never merely "some file whose JSON happens to say the
    right pluginId").
    """
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
    # COPILOT_PLUGIN_ROOT (when present) is cross-checked against the
    # receipt's own payload root -- a real protection when a plugin resolves
    # its OWN context. Worktree Manager is not that plugin: any
    # COPILOT_PLUGIN_ROOT it happens to have inherited describes an
    # unrelated ambient context, not this lookup's target, so it must not
    # leak in and spuriously reject an otherwise-valid receipt.
    env.pop("COPILOT_PLUGIN_ROOT", None)
    durable = _durable_home(ic, env)
    if durable is None:
        return None
    try:
        validated = ic.validate_context_receipt(
            pointer, durable, expected_plugin_id=plugin_id, environment=env,
        )
    except Exception:
        return None
    if not isinstance(validated, dict):
        return None
    plugin_root = validated.get("pluginRoot")
    payload_root = validated.get("payloadRoot")
    if not plugin_root or not payload_root:
        return None
    profile = _canonical_os_profile(env)
    try:
        resolution = ic.resolve_installation_mode(
            legacy_root=legacy_plugin_root(plugin_id),
            plugin_id=plugin_id,
            context=str(pointer),
            payload_root=payload_root,
            expected_payload_root=payload_root,
            expected_plugin_id=plugin_id,
            durable_home=durable,
            os_profile=profile,
            environment=env,
        )
    except Exception:
        return None
    if not isinstance(resolution, dict):
        return None
    resolution = dict(resolution)
    resolution["_pluginRoot"] = plugin_root
    return resolution


def _namespaced_plugin_root(plugin_id: str) -> Path | None:
    """The cell-scoped plugin root named by ``COPILOT_EXTENSIONS_CONTEXT``,
    when the shared installation-mode policy, evaluated for this EXACT
    plugin/marketplace (global -> marketplace -> plugin precedence), reports
    the receipt as the actually-active namespaced install -- mirroring
    ``libs/peer-launch``'s own governance gate, with one deliberate
    narrowing: peer-launch also allows "deactivation-required" (policy now
    says legacy, but the runtime is still actively namespaced) because it
    is invoking an ALREADY-RUNNING same-cell peer that must finish cleanly.
    Worktree Manager is choosing which install to launch NEXT, not
    continuing an in-flight execution -- so once policy says legacy, a
    still-namespaced-but-deactivating cell must not be selected either. This
    still does not re-validate activation/generation state the way
    ``libs/peer-launch`` does before actually launching a process -- it is
    the read-only "which root should I look under" question, not an
    invocation-time governance gate.
    """
    resolution = _namespaced_resolution(plugin_id)
    if resolution is None:
        return None
    plugin_root = resolution.get("_pluginRoot")
    if (
        resolution.get("status") != "ready"
        or resolution.get("reason") != "namespaced-active"
        or resolution.get("actualMode") != "namespaced"
        or not _paths_equal(resolution.get("runtimeRoot"), plugin_root)
    ):
        return None
    return Path(plugin_root)


def _namespaced_transition_blocks_legacy(plugin_id: str) -> bool:
    """True when a real, currently-active namespaced runtime exists for
    ``plugin_id`` (``actualMode == "namespaced"``) but was rejected by
    ``_namespaced_plugin_root`` for some OTHER reason (a deactivation in
    progress, a pending migration, maintenance, ...). In every such case the
    live runtime the plugin's own dispatcher is actually using is still the
    namespaced one, and the legacy root may itself be a retired, tombstoned
    artifact of that same transition -- so falling through to it would
    select an inert or wrong install rather than correctly reporting
    "unavailable right now".
    """
    resolution = _namespaced_resolution(plugin_id)
    if resolution is None:
        return False
    return resolution.get("actualMode") == "namespaced"


def _validated_legacy_root(root: Path, plugin_id: str) -> Path | None:
    """Confirm ``root`` is an attributable LEGACY install root for
    ``plugin_id``: only the ``deploy-manifest.json`` shape (``service`` +
    ``source.plugin``) is ever trusted here. The namespaced ``install.json``
    shape is validated separately -- and far more strictly, via the vendored
    ``validate_context_receipt`` -- by ``_namespaced_plugin_root``. Accepting
    a bare ``install.json`` here too would let a forged receipt dropped
    directly into the legacy root bypass that validation entirely.
    """
    manifest_path = root / "deploy-manifest.json"
    if not manifest_path.is_file():
        return None
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


def _select_complete_slot(root: Path) -> Path | None:
    """The first marker-selected slot under ``root`` that is both marked
    complete AND actually has its interpreter present, skipping a
    complete-but-damaged slot in favor of the next candidate."""
    for slot in _runtime_candidates(root):
        if not (slot / ".install-complete.json").is_file():
            continue
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if python.is_file():
            return slot
    return None


def _policy_is_invalid() -> bool:
    """True when the installation-mode policy FILE EXISTS but is malformed
    or an unsupported version -- as distinct from simply being absent (the
    default, always-legacy case). agent-* runtime gates fail closed on an
    invalid policy rather than silently falling back to legacy; Worktree
    Manager mirrors that here so a corrupt policy blocks resolution outright
    instead of masking itself as an ordinary legacy install.
    """
    ic = _load_installation_context()
    if ic is None:
        return False
    env = dict(os.environ)
    profile = _canonical_os_profile(env)
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
    return policy.get("state") in {"invalid", "unsupported"}


def resolve_installed_plugin_slot(plugin_id: str) -> Path | None:
    """The exact marker-selected immutable runtime slot for ``plugin_id``.

    Never PATH, never a bare command name: only an attributable slot under a
    validated install root. The namespaced root (when policy is enabled and
    an explicit context validates via ``_namespaced_plugin_root``) is
    already fully validated and is tried first -- and once selected, it is
    authoritative: an incomplete or damaged slot under it fails closed
    rather than falling through to legacy, since the plugin's own runtime
    gate has already established the namespaced install as active and would
    not silently prefer an older legacy runtime either. A real, currently-
    active namespaced runtime that was rejected for some OTHER reason (a
    deactivation or migration in progress, maintenance, ...) also blocks the
    legacy fallback -- the plugin's own dispatcher is still using that
    namespaced runtime, and the legacy root may itself be a retired,
    tombstoned artifact of that same transition. Legacy is only considered
    when no namespaced context is in play at all. The legacy root itself is
    trusted only via the ``deploy-manifest.json`` shape, never a bare
    ``install.json``, so a forged receipt cannot masquerade as either kind
    of root. A malformed (present-but-invalid) policy file blocks
    resolution entirely, fail-closed, rather than silently degrading to
    legacy.
    """
    _validate_plugin_id(plugin_id)
    if _policy_is_invalid():
        return None
    namespaced_root = _namespaced_plugin_root(plugin_id)
    if namespaced_root is not None:
        return _select_complete_slot(namespaced_root)
    if _namespaced_transition_blocks_legacy(plugin_id):
        return None
    legacy_root = legacy_plugin_root(plugin_id)
    if _validated_legacy_root(legacy_root, plugin_id) is not None:
        slot = _select_complete_slot(legacy_root)
        if slot is not None:
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
