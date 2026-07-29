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
payload refresh (``copilot plugin update``, a network call) is throttled via a
small cache so it does not run on every launch.

This module emits a JSON action plan with the same shape as ``pre-launch``
so the shell/PowerShell launchers can execute the ``argv`` vectors and
re-invoke for a second pass (payload, then runtime).
"""

from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path
from typing import Any

import yaml

from . import config as cfg

MARKETPLACE = "copilot-extensions"
SELF_PLUGIN = "agent-worktrees"
CACHE_NAME = "plugin-reconcile-cache.json"
VALID_SCOPES = ("universal", "machine-gated", "none")

# Machine-gate source (pluggable). The reconciler reads the per-plugin allowed
# machine set from a control-harness manifest. The manifest filename(s) and an
# optional anchor repo (searched via the repos registry when the current repo
# lacks the manifest) are overridable so any control harness can supply its own
# gate; the defaults match this repo's reference (facility) convention.
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
GATE_ANCHOR = os.environ.get("WORKTREE_GATE_ANCHOR", "aperture-labs")

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


# --------------------------------------------------------------------------
# Repo settings -> enabled copilot-extensions plugins
# --------------------------------------------------------------------------

def read_enabled_plugins(repo_dir: Path) -> list[str]:
    """Return copilot-extensions plugin names enabled in repo settings.

    Reads ``.github/copilot/settings.json`` then ``settings.local.json``
    (the local file overrides per key, matching Copilot's resolution).
    Excludes ``agent-worktrees`` itself (managed by the self-update path).
    """
    enabled: dict[str, bool] = {}
    base = repo_dir / ".github" / "copilot"
    for fname in ("settings.json", "settings.local.json"):
        data = _read_json(base / fname) or {}
        ep = data.get("enabledPlugins")
        if isinstance(ep, dict):
            for spec, val in ep.items():
                enabled[spec] = bool(val)

    names: set[str] = set()
    for spec, val in enabled.items():
        if not val or "@" not in spec:
            continue
        name, _, mkt = spec.partition("@")
        if mkt != MARKETPLACE or name == SELF_PLUGIN:
            continue
        names.add(name)
    return sorted(names)


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


def load_runtime_gate(repo_dir: Path) -> dict[str, set[str]]:
    """Map plugin name -> allowed machine set from a control-harness manifest.

    Looks for a gate manifest -- ``services.yaml`` (preferred) or the legacy
    ``external-repos.yaml`` (both overridable to a single name via
    ``WORKTREE_GATE_MANIFEST``) -- in the current repo first, then -- if an
    anchor repo is configured (``GATE_ANCHOR``; override with
    ``WORKTREE_GATE_ANCHOR``) -- in that repo as resolved via the repos
    registry. Accepts either the native top-level ``plugins:`` schema or the
    legacy ``repos.<group>.services[].{name, deploy_machines}`` schema. Returns
    ``{}`` when no manifest is found, which makes every ``machine-gated`` runtime
    skip (the safe default).

    Precedence: within a directory ``services.yaml`` is tried before
    ``external-repos.yaml``, and the current repo before the anchor; the first
    manifest that yields a non-empty gate wins (so a migrated ``services.yaml``
    shadows a lingering legacy file during transition).
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

    candidates = [d / name for d in search_dirs for name in GATE_MANIFESTS]

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
                    gate: dict[str, set[str]]) -> bool:
    """Whether a plugin's runtime should be reconciled on this machine."""
    if scope == "universal":
        return True
    if scope == "machine-gated":
        allowed = gate.get(name)
        return bool(allowed) and machine in allowed
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
    updates: list[dict[str, Any]] = []

    for name in names:
        entry: dict[str, Any] = plugins_cache.setdefault(name, {})
        pdir = installed_payload_dir(name)

        if pdir is None:
            # Payload not installed yet -- install it. The runtime (if any)
            # is reconciled on the next pass once the manifest is readable.
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

        # Throttled payload refresh (network). Skipped within the throttle
        # window so the common re-launch case stays near-zero work.
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
        if scope != "none" and runtime_allowed(scope, name, machine, gate):
            rdep = runtime_deployed_version(name)
            rrun = runtime_running_version(name)
            # Prefer the *running* version when a live service reports one, so a
            # daemon that lags its installed plugin is healed even though the
            # on-disk manifest already matches the payload (dotfiles #533). No
            # running-version.json (or a dead pid) -> fall back to on-disk.
            rver = rrun if rrun is not None else rdep
            if pver is None or not _versions_equal(rver, pver):
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
