#!/usr/bin/env python3
"""Enforce the install contract (docs/install-contract.md) across plugins.

Each plugin with a runtime installer must, per language variant:
  1. install the package via `uv pip install` (no file-copy of the package),
  2. emit no binstub that sets PYTHONPATH to a runtime lib/ dir,
  3. write a schema_version 3 deploy manifest with a `source` block,
  4. carry a source-kind resolver identical (per language) across plugins,
     and — for the update-flow robustness contract (dotfiles #935) — the
     byte-identical `install-contract:v4` self-stage prologue and smoke seam
     (per language), so a concurrent `copilot plugin update` never fights a
     wedged installer for the singleton payload and a stalled install
     self-terminates,
  5. adopt the immutable-versioned venv layout (dotfiles #581): ship a
     `scripts/versioned_runtime.py` primitive that is byte-identical to the
     canonical source (`libs/versioned-runtime/versioned_runtime.py`, vendored in
     by `tools/sync-versioned-runtime.py`) AND wire it in the installer (the
     `install-contract:v3 versioned-venv` block), so a version bump builds a
     fresh versions/<v> slot and swaps a `.venv`/`venv` link instead of ever
     mutating a live runtime's venv.

The enforced entrypoint pair is the plugin's *canonical* installer: `install.*`
when present (it carries an `update` action), otherwise `init.*` for plugins
that ship only an idempotent bootstrap (agent-mcp, agent-containers). A plugin
with both has `init.*` delegate to `install.*`, so only `install.*` is checked.

Payload-runtime plugins (no pyproject.toml -- e.g. a JS extension copied to
~/.copilot/extensions/) are exempt from rule 1 (there is no Python package to
install); rules 2-4 still apply. See docs/install-contract.md
§ "Payload runtime (non-Python)".

Run manually:  python tools/check-install-contract.py
Exit code 0 = conformant, 1 = violations (suitable for a pre-push hook).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from install_contract_guard import (
    PERSISTENT_ENV_END,
    PERSISTENT_ENV_START,
    persistent_environment_violations,
)

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"

# Immutable-versioned-runtime invariant (dotfiles #581): every Python runtime
# plugin ships the byte-identical scripts/versioned_runtime.py primitive AND
# wires it in its installer (the block bearing this marker). The layout builds
# each version into versions/<v> and points a `.venv`/`venv` junction/symlink at
# the active one, so a version bump never mutates a live runtime's venv. See
# docs/patterns/README.md § "Runtime installs are immutable and versioned".
VERSIONED_MARKER = "install-contract:v3 versioned-venv"
VERSIONED_RUNTIME_FILE = "versioned_runtime.py"
# The one canonical source of the primitive. Every Python runtime plugin's
# scripts/versioned_runtime.py is vendored byte-identically from here by
# tools/sync-versioned-runtime.py; this check enforces it (drift => run sync).
VERSIONED_RUNTIME_CANONICAL = REPO / "libs" / "versioned-runtime" / VERSIONED_RUNTIME_FILE

# A binstub/install script must not point PYTHONPATH at a runtime lib/ dir.
FORBIDDEN_PYTHONPATH = re.compile(r"PYTHONPATH[^\n]*\.agent-[a-z]+[\\/]lib", re.IGNORECASE)

# A Windows install.ps1 must NOT launch the unsigned console-script trampoline
# (…\Scripts\<name>.exe) -- Smart App Control blocks it (CodeIntegrity 3077).
# Launch "<venv>\Scripts\python.exe -m <pkg>" instead. The legacy .exe may still
# be *matched* (Get-RunningProcess) but never *launched*: launching shows up as
# the trampoline followed by an argument list (`" %*`, `" start`, `" version`).
# python.exe / pythonw.exe are explicitly allowed.
FORBIDDEN_TRAMPOLINE = re.compile(
    r"Scripts[\\/](?!python\.exe)(?!pythonw\.exe)[\w.-]+\.exe[\"']?\s+(?:%\*|start\b|version\b)",
    re.IGNORECASE,
)

# Session-start runtime-reconcile invariant: every Python runtime plugin (it
# installs a ~/.local/bin binstub) MUST wire a sessionStart hook that reconciles
# that runtime at launch, so a `copilot plugin update` redeploys the binstub
# without a manual reinstall. Detected structurally: plugin.json "hooks" -> a
# hooks file with a non-empty hooks.sessionStart whose command runs a
# `bootstrap-check`. See docs/install-contract.md § "Runtime self-reconcile".
#
# The baseline is now EMPTY -- every runtime plugin complies (dotfiles#779 burned
# it down). Do NOT add plugins here to silence the check: wire the hook instead.
# This set exists only as the explicit, greppable seam for a deliberate,
# time-boxed exemption should one ever be genuinely needed.
EXEMPT_SESSION_HOOK: frozenset[str] = frozenset()

# Thread-A self-provision invariant (dotfiles#1393): every Python runtime plugin's
# canonical entrypoint (install.* if present, else init.*) MUST declare a `stamp`
# action (fast base install: snapshot/pointer + a self-provisioning binstub, NO
# inline venv) and a `provision` action (the deferred heavy build the binstub runs
# on first use). This keeps a sessionStart install fast and never wedges/pins the
# marketplace payload. See docs/install-contract.md § "Fast install + deferred
# self-provision" and the correct-install-flows effort.
#
# BASELINE_NO_STAMP is the explicit, greppable, time-boxed exemption for plugins
# whose Windows `install.ps1` lane has NOT yet been ported -- the Thread-B service
# runtimes (detached daemons + graceful cutover), tracked separately. Their POSIX
# `.sh` already declares stamp/provision, so the exemption is ps1-only. SHRINK this
# set as each is ported; do NOT add plugins here to silence the check.
# EMPTY: all Thread-B service runtimes (agent-dispatch, agent-vault, agent-index,
# agent-bridge) are ported -- their install.ps1 now declares stamp/provision.
BASELINE_NO_STAMP_PS1: frozenset[str] = frozenset()


def _declares_stamp_provision(text: str, ext: str) -> bool:
    """True if an entrypoint declares BOTH a `stamp` and a `provision` action.

    ps1: the tokens ``'stamp'`` / ``'provision'`` appear (ValidateSet + switch).
    sh:  ``stamp`` / ``provision`` appear as a case label -- immediately followed
    by ``)`` or ``|`` -- so a combined ``stamp|provision|init)`` label and the
    incidental phrase "stamp build info" are handled correctly.
    """
    if ext == "ps1":
        return ("'stamp'" in text) and ("'provision'" in text)
    return bool(re.search(r"\bstamp[)|]", text)) and bool(re.search(r"\bprovision[)|]", text))


def _session_hook_problem(plugin: Path) -> str | None:
    """Return a violation string if a runtime plugin lacks the sessionStart
    reconcile hook, else None."""
    try:
        data = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    except Exception:
        return "plugin.json unreadable (cannot verify sessionStart reconcile hook)"
    try:
        invocation = json.loads(
            (plugin / "payload-invocation.json").read_text(encoding="utf-8")
        )
    except Exception:
        invocation = {}
    if invocation.get("sessionStartBootstrap") is False:
        dispatcher = invocation.get("payloadDispatcher")
        if not isinstance(invocation.get("catalogGate"), str) or not invocation[
            "catalogGate"
        ]:
            return (
                "sessionStartBootstrap false requires a fail-closed catalogGate"
            )
        if not isinstance(dispatcher, dict) or not all(
            isinstance(dispatcher.get(platform), str)
            and dispatcher[platform]
            for platform in ("posix", "windows")
        ):
            return (
                "sessionStartBootstrap false requires gated payload dispatchers "
                "for both platforms"
            )
        return None
    hooks_ref = data.get("hooks")
    if not hooks_ref:
        return ('no sessionStart runtime-reconcile hook -- set plugin.json "hooks" to a '
                "hooks file with a sessionStart bootstrap-check (docs/install-contract.md "
                "\u00a7 'Runtime self-reconcile')")
    try:
        hooks_doc = json.loads((plugin / hooks_ref).read_text(encoding="utf-8"))
    except Exception:
        return f"hooks file '{hooks_ref}' is missing or unreadable"
    session = (hooks_doc.get("hooks") or {}).get("sessionStart")
    if not session:
        return f"hooks file '{hooks_ref}' has no sessionStart entry"
    if "bootstrap-check" not in json.dumps(session):
        return "sessionStart hook does not run a bootstrap-check runtime reconcile"
    return None


def _extract_block(text: str, start_marker: str, open_char: str, close_char: str) -> str | None:
    """Return the balanced {...} block beginning at the first start_marker line."""
    idx = text.find(start_marker)
    if idx < 0:
        return None
    brace = text.find(open_char, idx)
    if brace < 0:
        return None
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == open_char:
            depth += 1
        elif text[i] == close_char:
            depth -= 1
            if depth == 0:
                return text[idx : i + 1]
    return None


def _norm(s: str | None) -> str | None:
    if s is None:
        return None
    return re.sub(r"\s+", " ", s).strip()


def _extract_marker(text: str, start_marker: str, end_marker: str) -> str | None:
    """Return the substring from the line bearing start_marker through the line
    bearing end_marker (inclusive), or None. Used for the install-contract:v4
    self-stage / smoke-seam blocks, which are byte-identical across plugins per
    language (they are delimited by comment markers, not a single brace)."""
    i = text.find(start_marker)
    if i < 0:
        return None
    j = text.find(end_marker, i)
    if j < 0:
        return None
    return text[i : j + len(end_marker)]


def _entrypoint_base(plugin: Path) -> str | None:
    """Return the runtime entrypoint base for a plugin, or None.

    Prefers ``install`` (the canonical installer with an ``update`` action);
    falls back to ``init`` for plugins that ship only an idempotent bootstrap
    (e.g. agent-mcp, agent-containers). When a plugin has both, ``init``
    delegates to ``install`` -- the canonical pair -- so only ``install`` is
    enforced. Returns None for plugins with no runtime installer at all.
    """
    scripts = plugin / "scripts"
    if (scripts / "install.ps1").exists() or (scripts / "install.sh").exists():
        return "install"
    if (scripts / "init.ps1").exists() or (scripts / "init.sh").exists():
        return "init"
    return None


def check() -> int:
    violations: list[str] = []
    ps1_resolvers: dict[str, str | None] = {}
    sh_resolvers: dict[str, str | None] = {}
    vrt_hashes: dict[str, str] = {}
    # install-contract:v4 self-stage / smoke-seam blocks -- byte-identical across
    # plugins, per language (#935). Keyed by plugin name; value is the exact block
    # text (or None if absent). Non-payload plugins must carry both.
    v4_selfstage: dict[str, dict[str, str | None]] = {"ps1": {}, "sh": {}}
    v4_smoke: dict[str, dict[str, str | None]] = {"ps1": {}, "sh": {}}

    plugins = sorted(
        p for p in PLUGINS_DIR.iterdir()
        if p.is_dir() and _entrypoint_base(p) is not None
    )
    if not plugins:
        print("No plugins with install scripts found.", file=sys.stderr)
        return 1

    persistent_environment_blocks: dict[str, str | None] = {}
    for path in sorted(PLUGINS_DIR.rglob("*.ps1")):
        text = path.read_text(encoding="utf-8", errors="replace")
        direct_access = persistent_environment_violations(text)
        uses_adapter = (
            "Get-CopilotPersistentEnvironmentVariable" in text
            or "Set-CopilotPersistentEnvironmentVariable" in text
        )
        if (
            PERSISTENT_ENV_START not in text
            and not uses_adapter
            and not direct_access
        ):
            continue
        relative = path.relative_to(REPO).as_posix()
        block = _extract_marker(text, PERSISTENT_ENV_START, PERSISTENT_ENV_END)
        persistent_environment_blocks[relative] = _norm(block)
        if block is None:
            violations.append(
                f"{relative}: direct User/Machine environment access is not "
                "test-virtualized"
            )
            continue
        if not uses_adapter:
            violations.append(
                f"{relative}: test-persistent-environment adapter is present "
                "but no persistent access routes through it"
            )
        for problem in direct_access:
            violations.append(
                f"{relative}: {problem} outside the shared "
                "test-persistent-environment adapter"
            )

    for plugin in plugins:
        name = plugin.name
        # Payload-runtime plugins ship a non-Python runtime (a JS extension
        # copied to ~/.copilot/extensions/, etc.) and carry no pyproject.toml.
        # The venv / uv-pip-install / SAC-launcher rules do not apply to them;
        # they must still write a schema_version 3 manifest with a source block
        # and carry the shared source-kind resolver. See docs/install-contract.md
        # § "Payload runtime (non-Python)".
        is_payload = not (plugin / "pyproject.toml").exists()
        # Immutable-versioned-runtime invariant (#581): every Python runtime plugin
        # (pyproject + an installer) MUST ship the byte-identical
        # versioned_runtime.py primitive. Payload (non-Python) runtimes are exempt
        # -- they build no venv.
        if not is_payload:
            vrt = plugin / "scripts" / VERSIONED_RUNTIME_FILE
            if not vrt.exists():
                violations.append(
                    f"{name}: missing scripts/{VERSIONED_RUNTIME_FILE} -- every Python "
                    f"runtime must adopt the immutable-versioned venv layout (#581)"
                )
            else:
                vrt_hashes[name] = hashlib.sha256(vrt.read_bytes()).hexdigest()
        # Session-start reconcile invariant (dotfiles#779): a Python runtime plugin
        # must self-reconcile its binstub at launch. Payload runtimes are exempt
        # (no binstub); the baseline set tracks pre-existing gaps.
        if not is_payload and name not in EXEMPT_SESSION_HOOK:
            hook_problem = _session_hook_problem(plugin)
            if hook_problem:
                violations.append(f"{name}: {hook_problem}")
        # Enforce the canonical entrypoint pair (install.* if present, else
        # init.*). Both language variants of that base must exist and conform.
        base = _entrypoint_base(plugin)
        for ext in ("ps1", "sh"):
            script = f"{base}.{ext}"
            path = plugin / "scripts" / script
            if not path.exists():
                violations.append(f"{name}: missing scripts/{script}")
                continue
            text = path.read_text(encoding="utf-8", errors="replace")

            if not is_payload and "uv pip install" not in text:
                violations.append(f"{name}/{script}: no 'uv pip install' (package must not be file-copied)")
            if not is_payload and VERSIONED_MARKER not in text:
                violations.append(
                    f"{name}/{script}: missing the '{VERSIONED_MARKER}' block -- the "
                    f"installer must wire the immutable-versioned venv layout (#581)"
                )
            # Thread-A fast-install + deferred self-provision (#1393): the
            # entrypoint must declare both a `stamp` and a `provision` action.
            # ps1-lane exemptions (not yet ported) are tracked in
            # BASELINE_NO_STAMP_PS1; the POSIX .sh lane is fully ported (no
            # exemptions). Payload runtimes build no venv and are exempt.
            if not is_payload:
                exempt = ext == "ps1" and name in BASELINE_NO_STAMP_PS1
                if not exempt and not _declares_stamp_provision(text, ext):
                    violations.append(
                        f"{name}/{script}: missing a 'stamp' and/or 'provision' action "
                        "-- Thread-A requires a fast 'stamp' (self-provisioning binstub, "
                        "no inline venv) + a deferred 'provision' (#1393)"
                    )
            if FORBIDDEN_PYTHONPATH.search(text):
                violations.append(f"{name}/{script}: binstub sets PYTHONPATH to a runtime lib/ dir")
            if "schema_version" not in text or '"source"' not in text and "source " not in text:
                violations.append(f"{name}/{script}: no schema_version 3 manifest with a source block")
            elif not re.search(r"schema_version[\"'=:\s]+3", text):
                violations.append(f"{name}/{script}: manifest is not schema_version 3")

            if ext == "ps1":
                if FORBIDDEN_TRAMPOLINE.search(text):
                    violations.append(
                        f"{name}/{script}: launches the unsigned console-script .exe "
                        "trampoline (Smart App Control blocks it -- CodeIntegrity 3077); "
                        "launch '<venv>\\Scripts\\python.exe -m <pkg>' instead"
                    )
                ps1_resolvers[name] = _norm(_extract_block(text, "function Get-SourceKind", "{", "}"))
            else:
                sh_resolvers[name] = _norm(_extract_block(text, "_source_kind()", "{", "}"))

            # install-contract:v4 self-stage + smoke seam (#935). Byte-identical
            # per language across every Python-runtime plugin, so a concurrent
            # `copilot plugin update` never fights a wedged installer for the
            # singleton payload (self-stage) and a stalled install self-terminates
            # (watchdog). Payload (non-Python) runtimes are exempt.
            if not is_payload:
                v4_selfstage[ext][name] = _extract_marker(
                    text, "# === install-contract:v4 self-stage", "# === end install-contract:v4 self-stage ==="
                )
                v4_smoke[ext][name] = _extract_marker(
                    text, "# === install-contract:v4 smoke seam", "# === end install-contract:v4 smoke seam ==="
                )

    _check_identical("Get-SourceKind (ps1)", ps1_resolvers, violations)
    _check_identical("_source_kind (sh)", sh_resolvers, violations)
    for ext in ("ps1", "sh"):
        _check_identical(f"install-contract:v4 self-stage ({ext})", v4_selfstage[ext], violations)
        _check_identical(f"install-contract:v4 smoke seam ({ext})", v4_smoke[ext], violations)
    _check_identical(
        "test-persistent-environment (ps1)",
        persistent_environment_blocks,
        violations,
    )

    # The versioned_runtime.py primitive is a self-contained per-plugin copy
    # vendored byte-identically from the canonical source
    # (libs/versioned-runtime/versioned_runtime.py) by
    # tools/sync-versioned-runtime.py -- it cannot be a shared runtime import
    # because plugins are pulled independently from the marketplace. Enforce that
    # every copy matches the canonical (drift => run the sync script).
    if not VERSIONED_RUNTIME_CANONICAL.exists():
        violations.append(
            f"missing canonical {VERSIONED_RUNTIME_CANONICAL.relative_to(REPO).as_posix()} "
            f"-- the shared source of {VERSIONED_RUNTIME_FILE}"
        )
    elif vrt_hashes:
        canonical_hash = hashlib.sha256(VERSIONED_RUNTIME_CANONICAL.read_bytes()).hexdigest()
        drifted = sorted(k for k, v in vrt_hashes.items() if v != canonical_hash)
        if drifted:
            violations.append(
                f"scripts/{VERSIONED_RUNTIME_FILE} differs from the canonical source "
                f"({VERSIONED_RUNTIME_CANONICAL.relative_to(REPO).as_posix()}) in: "
                f"{', '.join(drifted)} -- run 'python tools/sync-versioned-runtime.py'"
            )

    if violations:
        print("Install-contract violations:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print("\nSee docs/install-contract.md.", file=sys.stderr)
        return 1
    print(f"Install contract OK ({len(plugins)} plugins).")
    return 0


def _check_identical(label: str, resolvers: dict[str, str | None], violations: list[str]) -> None:
    present = {k: v for k, v in resolvers.items() if v}
    missing = [k for k, v in resolvers.items() if not v]
    for k in missing:
        violations.append(f"{k}: missing {label} block")
    distinct = set(present.values())
    if len(distinct) > 1:
        violations.append(f"{label} block differs across plugins: {sorted(present)}")


if __name__ == "__main__":
    raise SystemExit(check())
