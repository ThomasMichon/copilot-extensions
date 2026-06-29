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
# machine set from a control-harness manifest. Both the manifest filename and an
# optional anchor repo (searched via the repos registry when the current repo
# lacks the manifest) are overridable so any control harness can supply its own
# gate; the defaults match this repo's reference (facility) convention.
GATE_MANIFEST = os.environ.get("WORKTREE_GATE_MANIFEST", "external-repos.yaml")
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

def runtime_dir(name: str) -> Path:
    """Conventional runtime root for a plugin (``~/.<plugin-name>``)."""
    return _home() / f".{name}"


def runtime_deployed_version(name: str) -> str | None:
    """Version recorded in the plugin's runtime deploy manifest, if present."""
    data = _read_json(runtime_dir(name) / "deploy-manifest.json")
    if not data:
        return None
    src = data.get("source")
    if isinstance(src, dict) and src.get("version"):
        return str(src["version"])
    v = data.get("version")
    return str(v) if v else None


def runtime_installer_argv(plugin_dir: Path) -> tuple[str, list[str]] | None:
    """Build the (display, argv) to deploy/update a plugin's runtime.

    Prefers ``scripts/install.{sh,ps1} update``; falls back to
    ``scripts/init.{sh,ps1}`` (idempotent bootstrap) for plugins that ship
    only an init script. Platform-appropriate interpreter is chosen.
    """
    scripts = plugin_dir / "scripts"
    if platform.system() == "Windows":
        order = (("install.ps1", True), ("init.ps1", False))
        for fname, has_update in order:
            p = scripts / fname
            if p.is_file():
                argv = ["pwsh", "-File", str(p)] + (["update"] if has_update else [])
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

def load_runtime_gate(repo_dir: Path) -> dict[str, set[str]]:
    """Map plugin name -> allowed machine set from a control-harness manifest.

    Looks for the gate manifest (``GATE_MANIFEST``, default
    ``external-repos.yaml``; override with ``WORKTREE_GATE_MANIFEST``) in the
    current repo first, then -- if an anchor repo is configured
    (``GATE_ANCHOR``; override with ``WORKTREE_GATE_ANCHOR``) -- in that repo as
    resolved via the repos registry. Parses
    ``repos.<repo>.services[].{name, deploy_machines}``. Returns ``{}`` when no
    manifest is found, which makes every ``machine-gated`` runtime skip (the
    safe default).
    """
    candidates = [repo_dir / GATE_MANIFEST]
    if GATE_ANCHOR:
        try:
            from . import repos as _repos

            anchor = _repos.resolve_path(GATE_ANCHOR)
            if anchor:
                candidates.append(Path(anchor) / GATE_MANIFEST)
        except Exception:
            pass

    gate: dict[str, set[str]] = {}
    for path in candidates:
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except Exception:
            continue
        repos_block = raw.get("repos") or {}
        if not isinstance(repos_block, dict):
            continue
        for _repo, rdata in repos_block.items():
            if not isinstance(rdata, dict):
                continue
            for svc in rdata.get("services") or []:
                if not isinstance(svc, dict):
                    continue
                nm = svc.get("name")
                dm = svc.get("deploy_machines")
                if nm and isinstance(dm, list):
                    gate.setdefault(str(nm), set()).update(str(m) for m in dm)
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
            rver = runtime_deployed_version(name)
            if pver is None or rver != pver:
                built = runtime_installer_argv(pdir)
                if built is not None:
                    cmd, argv = built
                    updates.append({
                        "service": name,
                        "phase": "runtime",
                        "reason": "runtime-missing" if rver is None
                        else "runtime-version-drift",
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
