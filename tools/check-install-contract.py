#!/usr/bin/env python3
"""Enforce the install contract (docs/install-contract.md) across plugins.

Each plugin with a runtime installer must, per language variant:
  1. install the package via `uv pip install` (no file-copy of the package),
  2. emit no binstub that sets PYTHONPATH to a runtime lib/ dir,
  3. write a schema_version 3 deploy manifest with a `source` block,
  4. carry a source-kind resolver identical (per language) across plugins,
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
# Baseline of runtime plugins that predate the invariant and do NOT yet comply
# (tracked in dotfiles#779). New runtime plugins are REQUIRED to comply -- do NOT
# add to this set to silence the check; wire the hook and remove the plugin here.
# Burn this set down to empty.
EXEMPT_SESSION_HOOK: frozenset[str] = frozenset({
    "agent-bridge",
    "agent-codespaces",
    "agent-containers",
    "agent-dispatch",
    "agent-index",
    "agent-logger",
    "agent-mcp",
    "agent-vault",
})


def _session_hook_problem(plugin: Path) -> str | None:
    """Return a violation string if a runtime plugin lacks the sessionStart
    reconcile hook, else None."""
    try:
        data = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    except Exception:
        return "plugin.json unreadable (cannot verify sessionStart reconcile hook)"
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
        return f"sessionStart hook does not run a bootstrap-check runtime reconcile"
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

    plugins = sorted(
        p for p in PLUGINS_DIR.iterdir()
        if p.is_dir() and _entrypoint_base(p) is not None
    )
    if not plugins:
        print("No plugins with install scripts found.", file=sys.stderr)
        return 1

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

    _check_identical("Get-SourceKind (ps1)", ps1_resolvers, violations)
    _check_identical("_source_kind (sh)", sh_resolvers, violations)

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
        violations.append(f"{k}: missing {label} source-kind resolver")
    distinct = set(present.values())
    if len(distinct) > 1:
        violations.append(f"{label} resolver differs across plugins: {sorted(present)}")


if __name__ == "__main__":
    raise SystemExit(check())
