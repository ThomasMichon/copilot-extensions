#!/usr/bin/env python3
"""Build a local "preview a release" tree for one plugin: what its payload
would look like if promoted right now, without touching any real repo state
or the operator's installed Copilot plugins.

This composes five generators built earlier in this effort:

* the same generalized file-pointer expansion ``materialize_main.py``
  performs (e.g. a mirrored Markdown doc under ``plugins/<plugin>/docs/``),
  same scoped-to-the-preview-copy guarantee (`vendored-doc-pointers` effort,
  Phase 1).
* the same `uv`-editable canonical-reference expansion
  ``materialize_main.py`` performs for a plugin whose ``pyproject.toml``
  points at repo-root ``libs/<lib>`` on ``dev``.
* the same installer-engine canonical-reference expansion
  ``materialize_main.py`` performs for a plugin whose ``install.sh`` /
  ``install.ps1`` source ``libs/installer-engine`` directly on ``dev``.
* the same packaged launch-wrapper materialization ``materialize_main.py``
  performs for `agent-worktrees`, copying its authoritative
  ``worktree-manager/bin`` launch assets into the plugin payload so the
  non-editable fallback install stays self-contained.
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


def _load_materialize_main():
    """Load ``tools/materialize_main.py`` relative to this module's own
    location, so an isolated test tree gets the isolated copy, never the
    real repo's."""
    path = Path(__file__).resolve().parent / "materialize_main.py"
    spec = importlib.util.spec_from_file_location("materialize_main_preview", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _materialize_file_pointers_into_preview(dest: Path) -> list[str]:
    """Expand every vendored-doc (or other file) pointer under ``dest`` from
    the real repo canonical source, writing only into ``dest`` -- same
    never-mutate-the-source guarantee as the other preview materializers."""
    mm = _load_materialize_main()
    return mm.materialize_file_pointers(dest, canonical_root=REPO)


def _materialize_uv_editable_refs_into_preview(dest: Path, plugin: str) -> list[str]:
    """Expand every `uv`-editable canonical-reference entry declared in the
    REAL ``plugins/<plugin>/pyproject.toml`` (never ``dest``'s own copy --
    ``dest`` sits directly under ``workdir``, a different nesting depth than
    the real ``plugins/<plugin>``, so the entry's authored relative path
    would resolve to the wrong place if read from ``dest`` itself) into
    ``dest``: copies canonical's complete lib tree and rewrites ``dest``'s
    own ``pyproject.toml`` entry to the local non-editable form, mirroring
    ``materialize_main.py``'s whole-repo promotion step for a single
    plugin's preview."""
    mm = _load_materialize_main()
    return mm.materialize_uv_editable_ref_into(
        source_consumer_dir=PLUGINS_DIR / plugin,
        dest_consumer_dir=dest,
        canonical_root=REPO,
    )


def _materialize_installer_engine_refs_into_preview(dest: Path, plugin: str) -> list[str]:
    """Expand a plugin's canonical installer-engine reference into the preview."""
    mm = _load_materialize_main()
    return mm.materialize_installer_engine_ref_into(
        source_consumer_dir=PLUGINS_DIR / plugin,
        dest_consumer_dir=dest,
        canonical_root=REPO,
    )


def _materialize_launch_wrapper_assets_into_preview(dest: Path, plugin: str) -> list[str]:
    """Expand a plugin's packaged launch-wrapper assets into the preview."""
    mm = _load_materialize_main()
    return mm.materialize_launch_wrapper_assets_into(
        source_consumer_dir=PLUGINS_DIR / plugin,
        dest_consumer_dir=dest,
        canonical_root=REPO,
    )


def _retired_pointer_log(dest: Path) -> list[str]:
    mm = _load_materialize_main()
    return [
        f"SKIP {pointer_path}: retired directory-pointer kind still present -- refusing"
        for pointer_path in mm.find_retired_directory_pointers(dest)
    ]


def build(plugin: str, workdir: Path) -> Path:
    src = PLUGINS_DIR / plugin
    if not src.is_dir():
        raise FileNotFoundError(f"no such plugin: plugins/{plugin}")

    dest = workdir / plugin
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=_ignore)

    retired_pointer_log = _retired_pointer_log(dest)
    if retired_pointer_log:
        raise RuntimeError("\n".join(retired_pointer_log))

    file_pointer_log = _materialize_file_pointers_into_preview(dest)
    uv_editable_log = _materialize_uv_editable_refs_into_preview(dest, plugin)
    installer_engine_log = _materialize_installer_engine_refs_into_preview(dest, plugin)
    launch_wrapper_log = _materialize_launch_wrapper_assets_into_preview(dest, plugin)

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
        "retired_directory_pointer_log": retired_pointer_log,
        "vendored_file_pointers_materialize_log": file_pointer_log,
        "vendored_uv_editable_refs_materialize_log": uv_editable_log,
        "vendored_installer_engine_materialize_log": installer_engine_log,
        "vendored_launch_wrapper_assets_materialize_log": launch_wrapper_log,
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
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"preview-release: {exc}", file=sys.stderr)
        return 1
    print(f"Preview built at {dest}")
    manifest = json.loads((dest / "PREVIEW.json").read_text())
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
