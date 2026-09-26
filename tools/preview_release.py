#!/usr/bin/env python3
"""Build a local "preview a release" tree for one plugin: what its payload
would look like if promoted right now, without touching any real repo state
or the operator's installed Copilot plugins.

This composes three generators built earlier in this effort:

* the same canonical-`libs/<lib>` -> copy materialization
  ``sync-vendored-libs.py --materialize`` performs, but scoped to write only
  into the **preview copy** (never the real
  ``plugins/<plugin>/libs/<lib>`` in this checkout -- a preview must never
  mutate the source tree it is previewing).
* the same generalized file-pointer expansion ``materialize_main.py``
  performs (e.g. a mirrored Markdown doc under ``plugins/<plugin>/docs/``),
  same scoped-to-the-preview-copy guarantee (`vendored-doc-pointers` effort,
  Phase 1).
* ``accumulate_bumps.py``'s pure ``compute()`` (never ``apply()``) reports the
  version the plugin *would* get if its pending changefiles were consumed now.

The output is a plain directory (a copy of ``plugins/<plugin>``) plus a
``PREVIEW.json`` manifest recording the hypothetical version, the source
commit, and when it was built -- everything ``tools/dev_slot.py`` needs to
install it as a local override, and everything a human needs to inspect it
by hand instead.

Usage::

    python tools/preview_release.py agent-worktrees
    python tools/preview_release.py agent-worktrees --workdir /tmp/preview
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import accumulate_bumps as acc


def _git_head() -> str:
    r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                       capture_output=True, text=True, check=False)
    return r.stdout.strip() or "unknown"


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in {
        "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist",
    } or n.endswith((".pyc", ".pyo"))}


def _load_sync_vendored_libs():
    """Load ``tools/sync-vendored-libs.py`` by path (its filename has a
    hyphen, so it can't be a normal ``import``). Called relative to *this*
    module's location so a test that copies both scripts into an isolated
    tree gets the isolated copy, not the real repo's."""
    path = Path(__file__).resolve().parent / "sync-vendored-libs.py"
    spec = importlib.util.spec_from_file_location("sync_vendored_libs_preview", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_materialize_main():
    """Load ``tools/materialize_main.py`` relative to this module's own
    location, same rationale as ``_load_sync_vendored_libs`` (an isolated
    test tree gets the isolated copy, never the real repo's)."""
    path = Path(__file__).resolve().parent / "materialize_main.py"
    spec = importlib.util.spec_from_file_location("materialize_main_preview", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _materialize_into_preview(dest: Path) -> list[str]:
    """Materialize every vendored lib under ``dest/libs/<lib>`` from the real
    canonical ``libs/<lib>``, writing only into ``dest`` -- never back into
    this checkout's real ``plugins/<plugin>/libs/<lib>``."""
    log: list[str] = []
    libs_dir = dest / "libs"
    if not libs_dir.is_dir():
        return log
    svl = _load_sync_vendored_libs()
    for lib_copy in sorted(p for p in libs_dir.iterdir() if p.is_dir()):
        lib = lib_copy.name
        canonical = svl.LIBS_DIR / lib
        if not canonical.is_dir():
            log.append(f"{lib}: no canonical libs/{lib}/ -- preview keeps the current copy")
            continue
        # A DRY vendor-pointer copy (bare or src-passthrough) is never the
        # verified-agreeing "truth" sync-vendored-libs.py's own
        # cmd_materialize() compares against either -- its src/ is either
        # absent or a stub whose declared version/content have no bearing
        # on whether materializing canonical down is safe. Skip
        # _materialize_blocked()'s drift check entirely for a pointer copy,
        # matching cmd_materialize()'s own real/pointer distinction
        # (real = svl._real_copies([lib_copy])); previously this always ran
        # the check unconditionally and refused a real, already-adopted
        # src-passthrough copy (agent-worktrees/libs/lazy-cli-dispatch)
        # whenever canonical's declared version wasn't already strictly
        # ahead of the stub's own declared version.
        if not svl._is_pointer_copy(lib_copy):
            reason = svl._materialize_blocked(lib, canonical, lib_copy)
            if reason:
                log.append(reason)
                continue
        svl._copy_src(canonical, lib_copy)
        svl._sync_version(canonical, lib_copy)
        pointer = lib_copy / svl.POINTER_NAME
        if pointer.exists():
            pointer.unlink()
        log.append(f"{lib}: materialized into preview from canonical")
    return log


def _materialize_file_pointers_into_preview(dest: Path) -> list[str]:
    """Expand every vendored-doc (or other file) pointer under ``dest`` from
    the real repo canonical source, writing only into ``dest`` -- same
    never-mutate-the-source guarantee as ``_materialize_into_preview``."""
    mm = _load_materialize_main()
    return mm.materialize_file_pointers(dest, canonical_root=REPO)


def build(plugin: str, workdir: Path) -> Path:
    src = PLUGINS_DIR / plugin
    if not src.is_dir():
        raise FileNotFoundError(f"no such plugin: plugins/{plugin}")

    dest = workdir / plugin
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=_ignore)

    materialize_log = _materialize_into_preview(dest)
    file_pointer_log = _materialize_file_pointers_into_preview(dest)

    grouped = {p: t for p, t in acc.pending_bumps().items() if p == plugin}
    computed = acc.compute(grouped) if grouped else {}
    current = acc.read_plugin_json_version(plugin)
    if plugin in computed:
        _old, hypothetical_version = computed[plugin]
        pending = True
    else:
        hypothetical_version, pending = current, False

    manifest = {
        "plugin": plugin,
        "current_version": current,
        "hypothetical_version": hypothetical_version,
        "has_pending_changefiles": pending,
        "source_commit": _git_head(),
        "vendored_libs_materialize_log": materialize_log,
        "vendored_file_pointers_materialize_log": file_pointer_log,
    }
    (dest / "PREVIEW.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plugin", help="plugin name under plugins/<plugin>")
    ap.add_argument("--workdir", type=Path, default=None,
                     help="directory to build the preview into (default: a new temp dir)")
    args = ap.parse_args(argv)

    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="copilot-ext-preview-"))
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        dest = build(args.plugin, workdir)
    except FileNotFoundError as exc:
        print(f"preview-release: {exc}", file=sys.stderr)
        return 1
    print(f"Preview built at {dest}")
    manifest = json.loads((dest / "PREVIEW.json").read_text())
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
