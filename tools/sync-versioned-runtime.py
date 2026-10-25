#!/usr/bin/env python3
"""Fan the canonical versioned-runtime files out to the Python runtime plugins.

The immutable-versioned runtime primitive (dotfiles #581) must physically ship
inside each plugin (`scripts/versioned_runtime.py`) because plugins are pulled
**independently** from the marketplace -- it cannot be a shared runtime import.
To keep one source of truth, the canonical copy lives at
``libs/versioned-runtime/versioned_runtime.py`` and this script vendors it,
**byte-identically**, into every Python runtime plugin's ``scripts/`` dir.

It also vendors the canonical parameterized shell resolvers
(``libs/versioned-runtime/resolve-runtime.sh`` / ``.ps1`` -- the one marker-only
way a binstub/hook/service launcher resolves the versioned interpreter;
uniform-runtime-resolution, #765), but **opt-in**: only to plugins that already
carry a ``scripts/resolve-runtime.*`` copy (a plugin adopts the resolver by
dropping the file in; sync then keeps it byte-identical). Bespoke-resolver
plugins (``agent-worktrees``) are excluded so their specialized variant is never
overwritten.

Usage::

    python tools/sync-versioned-runtime.py          # copy canonical -> plugins
    python tools/sync-versioned-runtime.py --check   # verify in sync (CI/pre-push)

``--check`` writes nothing and exits non-zero if any vendored copy is missing or
drifted (``tools/check-install-contract.py`` independently enforces the
``versioned_runtime.py`` half). A "Python runtime plugin" is one that ships a
``pyproject.toml`` **and** a runtime installer (``install.*`` or ``init.*``) --
the exact set the install contract requires to carry the primitive.
Both modes refuse linked destinations and ancestors before reading or writing.
Containers also adopts the canonical ``standalone_build.py`` resource builder.
Its synchronized scripts are complete build inputs, so package construction
does not discover a sibling checkout or import a mutable installer at runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uv_editable_ref as uer
import installer_engine_ref as ier

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
CANONICAL = REPO / "libs" / "versioned-runtime" / "versioned_runtime.py"
VRT_NAME = "versioned_runtime.py"
COMPONENT_NAMES = (
    "versioned_runtime_leases.py", "versioned_runtime_construction.py",
    "versioned_runtime_source.py",
    "versioned_runtime_publication.py",
    "versioned_runtime_native.ps1",
)
DEPENDENCY_POLICY = REPO / "libs" / "installer-engine" / "installer_dependency_policy.py"
DEPENDENCY_CONFIG = DEPENDENCY_POLICY.with_name("installer_dependency_config.py")

# Canonical parameterized shell resolvers (uniform-runtime-resolution, #765). The
# one marker-only way a binstub / hook / service launcher resolves the versioned
# interpreter, service-parameterized via AGENT_RT_ROOT -> AGENT_RT_PY. Fanned out
# byte-identically -- like versioned_runtime.py -- but **opt-in**: only to plugins
# that already carry a copy in scripts/ (a plugin adopts the resolver by dropping
# in the file; migration then keeps it in sync). This lets the migration land one
# plugin at a time without forcing the resolver onto not-yet-migrated plugins.
RESOLVER_DIR = REPO / "libs" / "versioned-runtime"
RESOLVER_NAMES = ("resolve-runtime.sh", "resolve-runtime.ps1")
# agent-worktrees ships a *bespoke* resolver (the pre-existing $AW_PY variant with
# a hardcoded root and its own deploy path, #1106/#742); it is intentionally not
# the parameterized canonical, so the fan-out never overwrites it.
RESOLVER_BESPOKE = frozenset({"agent-worktrees"})
BUILD_HELPER_CONSUMERS = frozenset({"agent-containers"})


def _has_installer(plugin: Path) -> bool:
    scripts = plugin / "scripts"
    return any(
        (scripts / f"{base}.{ext}").exists()
        for base in ("install", "init")
        for ext in ("ps1", "sh")
    )


def _runtime_plugins() -> list[Path]:
    """Python runtime plugins: a pyproject.toml + a runtime installer."""
    return sorted(
        p for p in PLUGINS_DIR.iterdir()
        if p.is_dir() and (p / "pyproject.toml").exists() and _has_installer(p)
    )


def _resolver_plugins() -> list[Path]:
    """Plugins that have opted in to the canonical resolver.

    Opt-in = the plugin already carries at least one ``scripts/resolve-runtime.*``
    copy. The bespoke-resolver plugins (``agent-worktrees``) are excluded so the
    fan-out never clobbers their specialized variant.
    """
    out: list[Path] = []
    for p in sorted(PLUGINS_DIR.iterdir()):
        if not p.is_dir() or p.name in RESOLVER_BESPOKE:
            continue
        if any((p / "scripts" / name).exists() for name in RESOLVER_NAMES):
            out.append(p)
    return out


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check", action="store_true",
        help="verify every plugin copy matches the canonical; write nothing",
    )
    args = ap.parse_args()

    if not CANONICAL.exists():
        print(f"canonical missing: {CANONICAL.relative_to(REPO)}", file=sys.stderr)
        return 1
    if any(source.is_symlink() or not source.is_file() for source in (DEPENDENCY_POLICY, DEPENDENCY_CONFIG)):
        print("canonical installer dependency policy component missing or linked", file=sys.stderr)
        return 1

    plugins = _runtime_plugins()
    if not plugins:
        print("No Python runtime plugins found.", file=sys.stderr)
        return 1

    # Build the full set of (canonical_bytes, dest) vendor pairs: the primitive
    # into every runtime plugin, plus each opt-in plugin's resolver copies.
    pairs: list[tuple[bytes, Path]] = []
    canonical_bytes = CANONICAL.read_bytes()
    policy_bytes = DEPENDENCY_POLICY.read_bytes()
    config_bytes = DEPENDENCY_CONFIG.read_bytes()
    component_bytes = {}
    for name in COMPONENT_NAMES:
        source = CANONICAL.with_name(name)
        if source.is_symlink() or not source.is_file():
            print(f"canonical runtime component missing or linked: {name}", file=sys.stderr)
            return 1
        component_bytes[name] = source.read_bytes()
    standalone = ier.standalone_python_adopter_dirs(REPO)
    consumers = (*plugins, *standalone)
    for plugin in consumers:
        pairs.append((canonical_bytes, plugin / "scripts" / VRT_NAME))
        pairs.append((policy_bytes, plugin / "scripts" / DEPENDENCY_POLICY.name))
        pairs.append((config_bytes, plugin / "scripts" / DEPENDENCY_CONFIG.name))
        pairs.extend((data, plugin / "scripts" / name) for name, data in component_bytes.items())
        if plugin.name in BUILD_HELPER_CONSUMERS:
            helper = RESOLVER_DIR / "standalone_build.py"
            if helper.is_symlink() or not helper.is_file():
                print("canonical runtime build helper missing or linked", file=sys.stderr)
                return 1
            pairs.append((helper.read_bytes(), plugin / "scripts" / helper.name))

    resolver_plugins = _resolver_plugins()
    resolver_bytes: dict[str, bytes] = {}
    for name in RESOLVER_NAMES:
        src = RESOLVER_DIR / name
        if not src.exists():
            print(f"canonical resolver missing: {src.relative_to(REPO)}", file=sys.stderr)
            return 1
        resolver_bytes[name] = src.read_bytes()
    for plugin in resolver_plugins:
        for name in RESOLVER_NAMES:
            pairs.append((resolver_bytes[name], plugin / "scripts" / name))

    for _want_bytes, dest in pairs:
        bad_destination = uer._find_symlinked_ancestor(dest, REPO.resolve())
        if bad_destination is not None:
            print(f"{bad_destination} is a symlink -- refusing", file=sys.stderr)
            return 1

    drifted: list[str] = []
    written: list[str] = []
    for want_bytes, dest in pairs:
        current = dest.read_bytes() if dest.exists() else None
        if current is not None and _sha(current) == _sha(want_bytes):
            continue
        rel = dest.relative_to(REPO).as_posix()
        if args.check:
            drifted.append(f"{rel} ({'missing' if current is None else 'drifted'})")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(want_bytes)
        written.append(rel)

    n_plugins = len(plugins)
    n_res = len(resolver_plugins)
    scope = f"{n_plugins} plugins (+ resolvers in {n_res}); {len(standalone)} standalone adopter(s)"
    if args.check:
        if drifted:
            print(
                "vendored versioned-runtime files are out of sync with the "
                f"canonical sources in {RESOLVER_DIR.relative_to(REPO).as_posix()}:",
                file=sys.stderr,
            )
            for d in drifted:
                print(f"  - {d}", file=sys.stderr)
            print("\nRun: python tools/sync-versioned-runtime.py", file=sys.stderr)
            return 1
        print(f"versioned-runtime files in sync across {scope}.")
        return 0

    if written:
        print(f"Synced versioned-runtime files ({len(written)} file(s)):")
        for w in written:
            print(f"  + {w}")
    else:
        print(f"Already in sync across {scope}; nothing to do.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
