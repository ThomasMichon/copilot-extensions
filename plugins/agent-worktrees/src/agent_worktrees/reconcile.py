"""Repo-configured plugin reconciliation.

At session launch, agent-worktrees reconciles the anchor repo's
``.github/copilot/settings.json`` ``enabledPlugins`` against the local
machine: for each plugin from the ``copilot-extensions`` marketplace it
ensures the **payload** (skills/agents/hooks/MCP config) is installed, and
ensures the plugin's **runtime** (venv/service/extension) is deployed per a
*runtime-scope* policy and a machine gate.

The expensive hazard is "install the runtime for every repo-configured
plugin" -- wrong for machine-specific plugins. Each plugin declares its own
nature via a ``runtimeScope`` field in its ``plugin.json``:

* ``none``          -- the reconciler never touches the runtime (payload only;
                       any runtime is managed out-of-band).
* ``universal``     -- the runtime is reconciled on every machine.
* ``machine-gated`` -- the runtime is reconciled only on machines in the
                       plugin's allowed set, sourced from a control-harness
                       gate manifest (by default ``external-repos.yaml`` with
                       ``deploy_machines``; both the filename and an optional
                       anchor repo are overridable via env -- see
                       ``load_runtime_gate``).

Runtime reconciliation is **local and version-keyed**: it compares the
installed payload version (``plugin.json``) against the deployed runtime
version (``~/.<plugin>/deploy-manifest.json`` -> ``source.version``) and only
acts on drift, so a re-launch with no version change does ~no work. The
marketplace **payload** install/refresh (``copilot plugin install/update``, a
network pull that also holds the Windows payload dir open) is **off by default**
and emitted only when a caller opts in via ``include_payload_refresh`` -- the
Picker/operator "update flow". The programmatic path (the ``provision-check``
sessionStart hook + the launch reconcile) is therefore **runtime-only, pull-free
and lock-free** (#1393): agents never run ``copilot plugin update``; the operator
does that outside-in (``<repo> update`` via the Worktree Manager).

This module emits a JSON action plan with the same shape as ``pre-launch``
so the shell/PowerShell launchers can execute the ``argv`` vectors and
re-invoke for a second pass (payload, then runtime).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml

from . import config as cfg

MARKETPLACE = "copilot-extensions"
SELF_PLUGIN = "agent-worktrees"
CACHE_NAME = "plugin-reconcile-cache.json"
VALID_SCOPES = ("universal", "machine-gated", "none")

# Candidate locations for the Copilot CLI executable when a bare ``copilot`` is
# not resolvable on PATH. We never *install* Copilot: on WSL the Windows Copilot
# CLI ships a stub that auto-installs the Linux binary on first run (to
# ``~/.local/share/gh/copilot/copilot``), but nothing puts that binary on the
# Linux PATH -- and a bare ``copilot`` there resolves via WSL interop to the
# non-executable Windows stub (dotfiles#990). Checked in order.
_COPILOT_FALLBACK_PATHS: tuple[Path, ...] = (
    Path.home() / ".local" / "bin" / "copilot",
    Path.home() / ".local" / "share" / "gh" / "copilot" / "copilot",
)


def resolve_copilot() -> str | None:
    """Resolve a runnable Copilot CLI executable, or ``None``.

    Prefers a real bare ``copilot`` on PATH (``shutil.which`` -- POSIX, so it
    never appends ``.exe`` and never matches the Windows interop stub), then
    falls back to the known auto-install locations. Resolving to an absolute
    path also sidesteps WSL interop resolving ``copilot`` to the non-executable
    Windows stub. Returns ``None`` when no runnable Copilot CLI is found, so
    callers degrade to a graceful skip rather than crashing (dotfiles#990).

    This only *finds* Copilot; it never installs it -- the Windows-side stub
    owns auto-install.
    """
    found = shutil.which("copilot")
    if found:
        return found
    for p in _COPILOT_FALLBACK_PATHS:
        try:
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
        except OSError:
            continue
    return None

# Machine-gate source (pluggable). The reconciler reads the per-plugin allowed
# machine set from a control-harness manifest. The manifest filename(s) and an
# optional anchor repo (searched via the repos registry when the current repo
# lacks the manifest) are overridable so any control harness can supply its own
# gate; the defaults match this repo's reference (multi-machine system) convention.
#
# The preferred name is ``services.yaml`` -- a coherently-named plugin/service
# runtime-placement registry -- with ``external-repos.yaml`` kept as a legacy
# alias read for backward compatibility, so a harness migrates without a flag
# day (both may briefly coexist; ``services.yaml`` wins). An explicit
# ``WORKTREE_GATE_MANIFEST`` pins a single filename and disables the search list.
DEFAULT_GATE_MANIFESTS = ("services.yaml", "external-repos.yaml")
_GATE_MANIFEST_OVERRIDE = os.environ.get("WORKTREE_GATE_MANIFEST")
GATE_MANIFESTS = (
    (_GATE_MANIFEST_OVERRIDE,) if _GATE_MANIFEST_OVERRIDE else DEFAULT_GATE_MANIFESTS
)
# Back-compat alias (the preferred name); external callers referenced this.
GATE_MANIFEST = GATE_MANIFESTS[0]
GATE_ANCHOR = os.environ.get("WORKTREE_GATE_ANCHOR", "test-chamber")

# Throttle (hours) for the network payload refresh (`copilot plugin update`).
# Runtime reconciliation is version-keyed and not throttled.
DEFAULT_PAYLOAD_UPDATE_INTERVAL_H = 24.0


# --------------------------------------------------------------------------
# Small IO helpers
# --------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON file, returning ``None`` on any error or absence."""
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _home() -> Path:
    """Home directory (indirection point for tests)."""
    return Path.home()


def _copilot_home() -> Path:
    return _home() / ".copilot"


def _versions_equal(a: str | None, b: str | None) -> bool:
    """Compare two version strings for PEP 440 equality, tolerating spelling.

    A runtime service reports its version via ``importlib.metadata`` (PEP 440
    *normalized*, e.g. ``0.4.0.dev176``) while a ``plugin.json`` payload version
    keeps the source spelling (``0.4.0-dev176``). These are the **same** version,
    so a raw string compare would wrongly see drift and redeploy on every launch
    (found deploying agent-bridge's running-version marker, dotfiles #533). Fast
    path on exact match; then ``packaging`` semantics when available; else a
    separator-canonical fallback (``-``/``_`` -> ``.``) so this needs no hard dep.
    """
    if a == b:
        return True
    if not a or not b:
        return False
    try:
        from packaging.version import InvalidVersion, Version
        try:
            return Version(a) == Version(b)
        except InvalidVersion:
            pass
    except ImportError:
        pass
    na = a.strip().lower().replace("-", ".").replace("_", ".")
    nb = b.strip().lower().replace("-", ".").replace("_", ".")
    return na == nb


def _version_lt(a: str | None, b: str | None) -> bool:
    """Return True only when version ``a`` is *confidently* older than ``b``.

    Used to keep runtime reconciliation **monotonic**: a payload version that is
    strictly older than the running/deployed build must never be redeployed --
    that would silently REVERT a newer local build toward a stale/throttled
    marketplace payload (dotfiles #1366). Returns ``False`` on any ambiguity
    (missing, equal, or unorderable versions) so a genuine *forward* update is
    never suppressed. Tolerates the ``0.4.0-dev5`` vs ``0.4.0.dev5`` spelling via
    ``packaging`` (same normalization as :func:`_versions_equal`); without
    ``packaging`` we cannot order reliably, so we conservatively return ``False``.
    """
    if a is None or b is None or a == b:
        return False
    try:
        from packaging.version import InvalidVersion, Version
        try:
            return Version(a) < Version(b)
        except InvalidVersion:
            return False
    except ImportError:
        return False


# --------------------------------------------------------------------------
# Repo settings -> enabled copilot-extensions plugins
# --------------------------------------------------------------------------

def _ce_plugin_names(enabled: dict[str, bool]) -> set[str]:
    """copilot-extensions plugin names from an ``enabledPlugins`` map.

    Keeps truthy ``"<name>@<marketplace>"`` specs whose marketplace is this
    one, excluding ``agent-worktrees`` itself (managed by the self-update path).
    """
    names: set[str] = set()
    for spec, val in enabled.items():
        if not val or "@" not in spec:
            continue
        name, _, mkt = spec.partition("@")
        if mkt != MARKETPLACE or name == SELF_PLUGIN:
            continue
        names.add(name)
    return names


def read_enabled_plugins(repo_dir: Path) -> list[str]:
    """Return copilot-extensions plugin names enabled in repo settings.

    Reads the repo's plugin settings across both the Copilot-native and Claude
    conventions (native preferred, Claude fallback) via ``plugin_resolve`` --
    ``.github/copilot/settings.json`` (+ ``settings.local.json``) and, as a
    fallback, ``.claude/settings.json`` (+ ``.claude/settings.local.json``), with
    the local file overriding per key and native winning over Claude on a key
    conflict. Excludes ``agent-worktrees`` itself (managed by the self-update path).
    """
    from plugin_resolve import read_repo_settings

    return sorted(_ce_plugin_names(read_repo_settings(repo_dir).enabled))


def read_user_enabled_plugins() -> list[str]:
    """Return copilot-extensions plugin names enabled in the USER-GLOBAL settings.

    The user-global enabled set -- ``<copilot-home>/settings.json`` (+ a
    ``settings.local.json`` override) ``enabledPlugins`` -- is the set that
    ``copilot plugin list`` reflects. It is **not** covered by
    :func:`read_enabled_plugins` (which reads a *repo's*
    ``.github/copilot/settings.json``), so a plugin enabled **only** user-global
    would otherwise never be refreshed by ``update`` and would silently go stale
    (#653). Same marketplace filter + ``agent-worktrees`` self-exclusion.
    Fail-safe -> ``[]``.
    """
    home = _copilot_home()
    enabled: dict[str, bool] = {}
    for fname in ("settings.json", "settings.local.json"):  # local overrides base
        p = home / fname
        try:
            if not p.is_file():
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        en = data.get("enabledPlugins")
        if isinstance(en, dict):
            for k, v in en.items():
                if isinstance(k, str):
                    enabled[k] = bool(v)
    return sorted(_ce_plugin_names(enabled))


# --------------------------------------------------------------------------
# Installed payload discovery + version/scope
# --------------------------------------------------------------------------

def installed_payload_dir(name: str) -> Path | None:
    """Locate an installed plugin payload (marketplace or _direct layout)."""
    mkt = _copilot_home() / "installed-plugins" / MARKETPLACE / name
    if (mkt / "plugin.json").is_file():
        return mkt
    direct = _copilot_home() / "installed-plugins" / "_direct"
    if direct.is_dir():
        for d in sorted(direct.iterdir()):
            data = _read_json(d / "plugin.json")
            if data and data.get("name") == name:
                return d
    return None


def payload_version(plugin_dir: Path) -> str | None:
    data = _read_json(plugin_dir / "plugin.json") or {}
    v = data.get("version")
    return str(v) if v else None


def manifest_runtime_scope(plugin_dir: Path) -> str | None:
    """Return the ``runtimeScope`` declared in a plugin's manifest, if valid."""
    data = _read_json(plugin_dir / "plugin.json") or {}
    scope = data.get("runtimeScope")
    if isinstance(scope, str) and scope in VALID_SCOPES:
        return scope
    return None


# --------------------------------------------------------------------------
# Deployed runtime version (local, no network)
# --------------------------------------------------------------------------

def runtime_dir(name: str, home: Path | None = None) -> Path:
    """Conventional runtime root for a plugin (``~/.<plugin-name>``)."""
    return (home or _home()) / f".{name}"


def runtime_deployed_version(name: str, home: Path | None = None) -> str | None:
    """Version recorded in the plugin's runtime deploy manifest, if present."""
    data = _read_json(runtime_dir(name, home) / "deploy-manifest.json")
    if not data:
        return None
    src = data.get("source")
    if isinstance(src, dict) and src.get("version"):
        return str(src["version"])
    v = data.get("version")
    return str(v) if v else None


# Hook shims deployed to ``~/.agent-worktrees/bin/`` by install.ps1's
# ``Deploy-Wrappers`` (and install.sh). These deploy INDEPENDENTLY of the
# runtime version: a payload can add a new shim (e.g. ``resolve-runtime.ps1``,
# #1106) or rewrite one while the runtime devN version is unchanged -- or a
# partial/interrupted path can bump the runtime slot without redeploying them.
# Keep this list in sync with ``Deploy-Wrappers`` in ``scripts/install.ps1``.
HOOK_SHIM_FILES = (
    "resolve-runtime.ps1", "resolve-runtime.sh",
    "session-conduct.ps1", "session-conduct.sh",
    "session-machine.ps1", "session-machine.sh",
    "bootstrap-check.ps1", "bootstrap-check.sh",
    "project-hooks.ps1", "project-hooks.sh",
    "register-session.ps1", "register-session.sh",
    "deregister-session.ps1", "deregister-session.sh",
    "anchor-hygiene-check.ps1", "anchor-hygiene-check.sh",
    "marketplace-overrides.ps1", "marketplace-overrides.sh",
    "provision-check.ps1", "provision-check.sh",
    "statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py",
)


def hook_shims_drifted(plugin_dir: Path, home: Path | None = None) -> bool:
    """True if any deployed ``bin/`` hook shim is missing or differs from the payload.

    The ``cmd_update`` quick-skip compares only the runtime *version*, but the
    ``bin/`` hook shims deploy independently of it. A payload can add a new shim
    (``resolve-runtime.ps1``, #1106) or rewrite one while the runtime devN
    version is unchanged, and a partial/interrupted deploy can bump the runtime
    slot without redeploying the shims -- so a version-match skip can leave the
    shims stale. That is the failure behind an empty Mux status bar: the
    sessionStart reseed shim early-exits on the retired ``.venv`` python and
    never re-asserts the status-updater (dotfiles #1171). This check lets the
    skip branch re-deploy on shim drift, mirroring the LIVE Windows-Terminal
    carve-out already there.

    Compared by content (bytes). A shim absent from the payload is skipped (not
    every shim exists on every payload); a shim present in the payload but
    missing or differing in ``bin/`` is drift. Any unreadable file counts as
    drift (safer to redeploy than to skip). Returns ``False`` when either
    directory is absent (nothing to compare -> never force a redeploy).
    """
    scripts = plugin_dir / "scripts"
    bin_dir = runtime_dir(SELF_PLUGIN, home) / "bin"
    if not scripts.is_dir() or not bin_dir.is_dir():
        return False
    for name in HOOK_SHIM_FILES:
        src = scripts / name
        if not src.exists():
            continue
        dst = bin_dir / name
        if not dst.exists():
            return True
        try:
            if src.read_bytes() != dst.read_bytes():
                return True
        except OSError:
            return True
    return False


def _pid_alive(pid: int) -> bool:
    """Best-effort: is a process with ``pid`` currently running?

    Used to treat a stale ``running-version.json`` (whose process has exited) as
    absent. Errs toward *alive* on ambiguity (e.g. a permission error querying a
    foreign process) so we never wrongly redeploy over a live daemon.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if platform.system() == "Windows":
        try:
            import ctypes

            process_query = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
            still_active = 259  # STILL_ACTIVE
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(process_query, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok) and code.value == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True  # ambiguous -> assume alive; never redeploy over a live daemon
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours
    except OSError:
        return False


def runtime_running_version(name: str, home: Path | None = None) -> str | None:
    """Version the *running* runtime reported on boot, if its pid is still alive.

    Reads ``~/.<plugin>/running-version.json`` (``{version, pid, started_at}``),
    which a runtime service writes on startup. Returns the version only when the
    recorded pid is still alive; a missing file, malformed content, or a dead pid
    all yield ``None`` so callers fall back to the on-disk deploy manifest. This is
    the truthful "what is actually serving" signal -- a live daemon can lag its
    installed plugin while the on-disk manifest already matches (dotfiles #533).
    """
    data = _read_json(runtime_dir(name, home) / "running-version.json")
    if not data:
        return None
    ver = data.get("version")
    pid = data.get("pid")
    if not ver or not _pid_alive(pid):
        return None
    return str(ver)


def running_version_lag(repo_dir: Path) -> list[dict[str, Any]]:
    """Enabled runtime plugins whose *live* process lags the installed payload.

    Part C (#533): the launch path already heals runtime drift (running-aware
    reconcile + the Part B zero-downtime cutover), but a running session can't
    restart/cut-over its own daemon mid-turn -- so a `copilot plugin update`
    applied *during* a session leaves the daemon lagging until the next launch.
    This read-only diagnostic surfaces that gap for ``doctor``/``status`` so the
    operator can `service restart` sooner rather than lag silently.

    For each enabled copilot-extensions plugin that exposes a *live*
    running-version signal, report ``{service, running, payload}`` when the
    running version differs from the installed payload (PEP 440-aware, so the
    ``0.4.0-dev5`` vs ``0.4.0.dev5`` spelling never reads as a false lag).
    Plugins with no live process (dead/absent running-version) are omitted --
    there is nothing serving to nudge about. Never raises.
    """
    lags: list[dict[str, Any]] = []
    try:
        names = read_enabled_plugins(repo_dir)
    except Exception:
        return lags
    for name in names:
        try:
            pdir = installed_payload_dir(name)
            if pdir is None:
                continue
            payload = payload_version(pdir)
            running = runtime_running_version(name)
            if (running is not None and payload is not None
                    and not _versions_equal(running, payload)):
                lags.append({
                    "service": name,
                    "running": running,
                    "payload": payload,
                })
        except Exception:
            continue
    return lags


def _zero_downtime_update(plugin_dir: Path) -> bool:
    """Whether the plugin supports a zero-downtime in-place update (#533 Part B).

    A daemon that ships a ZDD cutover (`install.ps1 update -ZeroDowntime` -> an
    in-place venv update handed off via `agent-bridge deploy`) sets
    ``"zeroDowntimeUpdate": true`` in its plugin.json.
    """
    data = _read_json(plugin_dir / "plugin.json") or {}
    return bool(data.get("zeroDowntimeUpdate"))


def runtime_installer_argv(plugin_dir: Path) -> tuple[str, list[str]] | None:
    """Build the (display, argv) to deploy/update a plugin's runtime.

    Prefers ``scripts/install.{sh,ps1} update``; falls back to
    ``scripts/init.{sh,ps1}`` (idempotent bootstrap) for plugins that ship
    only an init script. Platform-appropriate interpreter is chosen.

    A plugin that supports a zero-downtime redeploy declares
    ``"zeroDowntimeUpdate": true`` in its plugin.json; the reconcile-driven
    ``install.ps1 update`` then carries ``-ZeroDowntime`` so a routine version
    bump updates in place and hands off via the ZDD cutover (`agent-bridge
    deploy`) rather than a stop-and-swap (#533 Part B). An operator's manual
    ``update`` never passes the flag, so its behavior is unchanged.
    """
    scripts = plugin_dir / "scripts"
    zero_downtime = _zero_downtime_update(plugin_dir)
    if platform.system() == "Windows":
        order = (("install.ps1", True), ("init.ps1", False))
        for fname, has_update in order:
            p = scripts / fname
            if p.is_file():
                argv = ["pwsh", "-File", str(p)] + (["update"] if has_update else [])
                if has_update and zero_downtime:
                    argv.append("-ZeroDowntime")
                return " ".join(argv), argv
        return None
    order = (("install.sh", True), ("init.sh", False))
    for fname, has_update in order:
        p = scripts / fname
        if p.is_file():
            argv = ["bash", str(p)] + (["update"] if has_update else [])
            return " ".join(argv), argv
    return None


# --------------------------------------------------------------------------
# Machine gate (control-harness manifest -> per-plugin deploy_machines)
# --------------------------------------------------------------------------

def _ingest_gate_entries(entries: Any, gate: dict[str, set[str]]) -> None:
    """Merge a list of ``{name, deploy_machines}`` service entries into ``gate``.

    Best-effort: malformed entries (non-dict, missing name, non-list machines)
    are skipped so a partially-bad manifest degrades to a smaller gate rather
    than raising.
    """
    if not isinstance(entries, list):
        return
    for svc in entries:
        if not isinstance(svc, dict):
            continue
        nm = svc.get("name")
        dm = svc.get("deploy_machines")
        if nm and isinstance(dm, list):
            gate.setdefault(str(nm), set()).update(str(m) for m in dm)


def _parse_gate_manifest(raw: Any, gate: dict[str, set[str]]) -> None:
    """Populate ``gate`` from one parsed manifest, accepting either schema.

    * **Native** (``services.yaml``): a top-level ``plugins:`` list of
      ``{name, deploy_machines}`` -- the coherently-named shape.
    * **Legacy** (``external-repos.yaml``): ``repos.<group>.services[]`` --
      whose top-level ``repos``/``<group>`` keys are a free-form bucket the
      reconciler flattens (grouped by concern, not by source repo).

    A top-level ``services:`` key is deliberately NOT read as a gate list: it is
    reserved for a future non-plugin (dotfiles-service) section so the two
    concerns never collide.
    """
    if not isinstance(raw, dict):
        return
    _ingest_gate_entries(raw.get("plugins"), gate)  # native services.yaml shape
    repos_block = raw.get("repos")
    if isinstance(repos_block, dict):
        for _repo, rdata in repos_block.items():
            if isinstance(rdata, dict):
                _ingest_gate_entries(rdata.get("services"), gate)


def _gate_candidate_paths(repo_dir: Path) -> list[Path]:
    """Ordered gate-manifest candidate paths: the current repo first, then the
    configured anchor repo (resolved via the repos registry), each tried for
    every name in ``GATE_MANIFESTS`` (``services.yaml`` before the legacy
    ``external-repos.yaml``). Shared by :func:`load_runtime_gate` and
    :func:`gate_manifest_present` so presence detection and parsing agree.
    """
    search_dirs = [repo_dir]
    if GATE_ANCHOR:
        try:
            from . import repos as _repos

            anchor = _repos.resolve_path(GATE_ANCHOR)
            if anchor:
                search_dirs.append(Path(anchor))
        except Exception:
            pass
    return [d / name for d in search_dirs for name in GATE_MANIFESTS]


def gate_manifest_present(repo_dir: Path) -> bool:
    """True if *any* gate manifest file exists (current repo or anchor).

    Distinguishes "the harness configured gating" from "no gating configured at
    all". When no manifest exists anywhere, an explicitly-enabled
    ``machine-gated`` runtime provisions on the local machine (dotfiles #693
    Phase 3) rather than silently skipping -- because *enabling* the plugin is
    the whole intent and there is no gate to defer to. When a manifest **is**
    present it is authoritative and the strict machine check applies.
    """
    return any(p.is_file() for p in _gate_candidate_paths(repo_dir))


def load_runtime_gate(repo_dir: Path) -> dict[str, set[str]]:
    """Map plugin name -> allowed machine set from a control-harness manifest.

    Looks for a gate manifest -- ``services.yaml`` (preferred) or the legacy
    ``external-repos.yaml`` (both overridable to a single name via
    ``WORKTREE_GATE_MANIFEST``) -- in the current repo first, then -- if an
    anchor repo is configured (``GATE_ANCHOR``; override with
    ``WORKTREE_GATE_ANCHOR``) -- in that repo as resolved via the repos
    registry. Accepts either the native top-level ``plugins:`` schema or the
    legacy ``repos.<group>.services[].{name, deploy_machines}`` schema. Returns
    ``{}`` when no manifest is found. A ``{}`` gate no longer *unconditionally*
    skips every ``machine-gated`` runtime: :func:`runtime_allowed` also consults
    :func:`gate_manifest_present`, so an explicitly-enabled runtime still
    provisions locally when **no** manifest exists at all (#693 Phase 3), while a
    manifest that simply omits a plugin/machine remains conservative.

    Precedence: within a directory ``services.yaml`` is tried before
    ``external-repos.yaml``, and the current repo before the anchor; the first
    manifest that yields a non-empty gate wins (so a migrated ``services.yaml``
    shadows a lingering legacy file during transition).
    """
    candidates = _gate_candidate_paths(repo_dir)

    gate: dict[str, set[str]] = {}
    for path in candidates:
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except Exception:
            continue
        _parse_gate_manifest(raw, gate)
        if gate:
            break
    return gate


def runtime_allowed(scope: str, name: str, machine: str,
                    gate: dict[str, set[str]], *, gate_present: bool = True) -> bool:
    """Whether a plugin's runtime should be reconciled on this machine.

    ``universal`` always reconciles. ``machine-gated`` reconciles when the gate
    manifest lists this machine for the plugin. The #693 Phase 3 refinement: when
    **no** gate manifest exists anywhere (``gate_present`` False), an
    explicitly-enabled ``machine-gated`` runtime is provisioned on the local
    machine -- there is no gate to defer to and enabling the plugin is the whole
    intent. A manifest that is *present* stays authoritative: a plugin/machine it
    omits is still skipped (conservative). ``gate_present`` defaults to True so a
    caller that does not thread it keeps the strict, pre-Phase-3 behavior.
    """
    if scope == "universal":
        return True
    if scope == "machine-gated":
        allowed = gate.get(name)
        if allowed is not None:
            return machine in allowed
        # Not named in the gate: provision locally only when no gate manifest
        # exists at all; a present-but-silent manifest stays conservative.
        return not gate_present
    return False


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def cache_path() -> Path:
    return cfg.install_dir() / CACHE_NAME


def load_cache() -> dict[str, Any]:
    return _read_json(cache_path()) or {}


def save_cache(cache: dict[str, Any]) -> None:
    p = cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Plan builder
# --------------------------------------------------------------------------

def build_plan(
    repo_dir: Path,
    *,
    machine: str | None = None,
    now: float | None = None,
    payload_update_interval_h: float = DEFAULT_PAYLOAD_UPDATE_INTERVAL_H,
    include_payload_refresh: bool = False,
    cache: dict[str, Any] | None = None,
    save: bool = True,
) -> dict[str, Any]:
    """Return a reconciliation action plan.

    Shape mirrors ``pre-launch``::

        {"action": "continue", "machine": "..."}
        {"action": "reconcile", "machine": "...", "updates": [
            {"service": "agent-bridge", "phase": "runtime",
             "reason": "runtime-version-drift", "command": "...",
             "argv": ["bash", ".../install.sh", "update"]},
            ...]}

    ``updates`` are ordered so payload operations for a plugin precede its
    runtime operation. The launcher runs them in order and re-invokes for a
    second pass (so a freshly installed payload's runtime is picked up).
    """
    now = time.time() if now is None else now
    if machine is None:
        machine = cfg.detect_machine(repo_dir)
    cache = load_cache() if cache is None else cache
    plugins_cache: dict[str, Any] = cache.setdefault("plugins", {})

    names = read_enabled_plugins(repo_dir)
    gate = load_runtime_gate(repo_dir)
    gate_present = gate_manifest_present(repo_dir)
    updates: list[dict[str, Any]] = []

    for name in names:
        entry: dict[str, Any] = plugins_cache.setdefault(name, {})
        pdir = installed_payload_dir(name)

        if pdir is None:
            # Payload not installed yet. Installing it is a MARKETPLACE PULL
            # (``copilot plugin install``) -- an operator / Worktree-Manager /
            # Picker "update flow" action, NOT something a programmatic
            # session-launch may do. The default programmatic reconcile path
            # (the ``provision-check`` sessionStart hook + launch reconcile) is
            # runtime-only + pull-free + lock-free (#1393); only a caller that
            # explicitly opts in (``--with-payload-refresh``, the Picker/operator
            # update flow) emits a marketplace pull.
            if include_payload_refresh:
                updates.append({
                    "service": name,
                    "phase": "payload",
                    "reason": "payload-missing",
                    "command": f"copilot plugin install {name}@{MARKETPLACE}",
                    "argv": ["copilot", "plugin", "install", f"{name}@{MARKETPLACE}"],
                })
                entry["last_payload_update"] = now
            continue

        pver = payload_version(pdir)
        entry["payload_version"] = pver

        # Throttled payload refresh (network / MARKETPLACE PULL). Same rule as
        # payload-missing above: the Picker/operator update flow owns it; the
        # programmatic reconcile path never pulls (so it can't stall or hold the
        # Windows payload dir open -- #1366/#1393). Opt in via
        # ``include_payload_refresh``; the throttle clock is only advanced when a
        # refresh is actually emitted, so a pull-free launch doesn't reset it.
        if include_payload_refresh:
            last_update = float(entry.get("last_payload_update", 0) or 0)
            if (now - last_update) >= payload_update_interval_h * 3600:
                updates.append({
                    "service": name,
                    "phase": "payload",
                    "reason": "payload-refresh",
                    "command": f"copilot plugin update {name}@{MARKETPLACE}",
                    "argv": ["copilot", "plugin", "update", f"{name}@{MARKETPLACE}"],
                })
                entry["last_payload_update"] = now

        # Runtime reconciliation (local, version-keyed, gated).
        scope = manifest_runtime_scope(pdir) or "none"
        if scope != "none" and runtime_allowed(
            scope, name, machine, gate, gate_present=gate_present
        ):
            rdep = runtime_deployed_version(name)
            rrun = runtime_running_version(name)
            # Prefer the *running* version when a live service reports one, so a
            # daemon that lags its installed plugin is healed even though the
            # on-disk manifest already matches the payload (dotfiles #533). No
            # running-version.json (or a dead pid) -> fall back to on-disk.
            rver = rrun if rrun is not None else rdep
            if pver is None or not _versions_equal(rver, pver):
                # Monotonic guard (#1366): never redeploy a payload that is
                # strictly OLDER than the running/deployed build -- doing so would
                # REVERT a newer local `source: local` deploy toward a stale /
                # throttled marketplace payload on the next reconcile pass. Only
                # a confidently-older payload is suppressed; runtime-missing
                # (rver is None) and forward/ambiguous cases still deploy.
                if _version_lt(pver, rver):
                    continue
                built = runtime_installer_argv(pdir)
                if built is not None:
                    cmd, argv = built
                    if rver is None:
                        reason = "runtime-missing"
                    elif rrun is not None and _versions_equal(rdep, pver):
                        # on-disk looks current; the live process is the laggard.
                        reason = "runtime-running-drift"
                    else:
                        reason = "runtime-version-drift"
                    updates.append({
                        "service": name,
                        "phase": "runtime",
                        "reason": reason,
                        "from_version": rver,
                        "to_version": pver,
                        "scope": scope,
                        "command": cmd,
                        "argv": argv,
                    })

    if save:
        save_cache(cache)

    if updates:
        return {"action": "reconcile", "machine": machine, "updates": updates}
    return {"action": "continue", "machine": machine}


# --------------------------------------------------------------------------
# In-process plan execution (the session-start self-provisioning path)
# --------------------------------------------------------------------------

def apply_plan(
    repo_dir: Path,
    *,
    machine: str | None = None,
    passes: int = 2,
    include_payload_refresh: bool = False,
    log: Callable[[str], None] | None = None,
    runner: Callable[[Sequence[str]], int] | None = None,
) -> dict[str, Any]:
    """Execute the reconciliation plan **in-process** (the launcher's 2-pass loop).

    The worktree launchers run ``reconcile-plugins`` and execute the emitted
    ``argv`` vectors themselves, re-invoking for a second pass so a freshly
    installed payload's runtime is picked up. This function is that same loop in
    Python, so a session that does **not** go through the worktree launcher can
    still self-provision an enabled plugin's runtime (dotfiles #693). The
    ``provision-check`` sessionStart shim spawns ``reconcile-plugins --apply``
    (which calls this) **detached**, so a slow first-run venv build never blocks
    session start.

    Best-effort: a step failure is logged and the loop continues; nothing here
    raises for a bad step. A ``copilot ...`` step is skipped when ``copilot`` is
    not on ``PATH`` (mirrors the launcher, which cannot install a payload without
    the CLI). Returns a summary dict ``{"executed": [...], "passes": N,
    "action": "reconcile"|"continue"}``.
    """
    _log = log or (lambda _m: None)

    def _default_runner(argv: Sequence[str]) -> int:
        proc = subprocess.run(  # noqa: S603 -- argv from our own plan builder
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if proc.stdout:
            for line in proc.stdout.splitlines():
                _log(f"    {line}")
        return proc.returncode

    run = runner or _default_runner
    executed: list[dict[str, Any]] = []
    final_action = "continue"

    for pass_no in range(1, passes + 1):
        plan = build_plan(
            repo_dir, machine=machine,
            include_payload_refresh=include_payload_refresh,
        )
        if plan.get("action") != "reconcile":
            if pass_no == 1:
                _log("provision: nothing to do (runtimes current)")
            break
        final_action = "reconcile"
        for upd in plan.get("updates", []):
            service = upd.get("service", "?")
            argv = list(upd.get("argv") or [])
            if not argv:
                continue
            if argv[0] == "copilot":
                copilot = resolve_copilot()
                if copilot is None:
                    _log(f"provision: skipping {service} (copilot not found)")
                    continue
                argv[0] = copilot
            _log(f"provision: {service} [{upd.get('reason', '?')}] -> {' '.join(argv)}")
            try:
                rc = run(argv)
            except Exception as exc:  # never raise from a background provision
                _log(f"provision: step FAILED for {service}: {exc}")
                executed.append({"service": service, "argv": argv, "ok": False})
                continue
            ok = rc == 0
            if not ok:
                _log(f"provision: step for {service} exited {rc}")
            executed.append({"service": service, "argv": argv, "ok": ok})

    return {"action": final_action, "passes": passes, "executed": executed}
