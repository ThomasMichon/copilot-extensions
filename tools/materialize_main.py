#!/usr/bin/env python3
"""Build the "main release form" from a `dev`-shaped checkout: snapshot the
whole tree, then expand every DRY vendor pointer back into a full copy from
its canonical source. This is the whole-repo counterpart of
`preview_release.py`'s single-plugin materialization, and a validated
prototype of the promotion pipeline's core step (Phase 3 of the
dev-branch-release-pipeline effort) -- confirmed byte-for-byte lossless
across all 56 vendored copies of this repo's 10 shared libs in a standalone
trial clone (see the effort's Journal).

Two pointer kinds are expanded, generalized under the
`vendored-doc-pointers` effort (see its README, Phase 1):

* **Directory (lib) pointers** -- `plugins/<plugin>/libs/<lib>/VENDOR_POINTER.json`
  (also `worktree-manager/libs/<lib>/VENDOR_POINTER.json`, the one extra
  top-level tree `sync-vendored-libs.py`/`check-vendored-libs-sync.py` also
  scan), a JSON sidecar next to a lib copy that has no `src/` of its own.
  Expanded from the canonical `libs/<lib>` directory (refusing any `source`
  that escapes the canonical root, the same containment guarantee the file
  pointer kind below already has); only `src/` and the declared
  `pyproject.toml` version are ever touched, matching
  `check-vendored-libs-sync.py`'s own existing invariant.
* **File pointers** -- any single vendored file (e.g. a mirrored Markdown
  doc under `plugins/<plugin>/docs/`) whose first line is an in-language
  HTML-comment marker:
  `<!-- VENDOR_POINTER: source=<repo-relative-path> kind=file -->`. Unlike a
  directory pointer, the pointer *is* the mirrored file itself (its content
  on `dev` is a short human/agent-readable stub, not empty) -- there is no
  separate real copy to remove, so materializing overwrites the stub's
  content in place with the canonical file's bytes.

No real plugin in this repo carries a lib pointer yet -- that conversion is
Phase 2 of dev-branch-release-pipeline, and this tool remains a no-op for
that kind against the real checkout until it lands. The file-pointer kind is
no longer hypothetical: `docs/patterns/entity-relationship-model.md`'s three
mirrors (`plugins/agent-worktrees/docs/`, `plugins/agent-bridge/docs/`,
`plugins/agent-dispatch/docs/`) were converted to real file pointers in
Phase 2 of vendored-doc-pointers, so this tool now expands real content on
every real promotion run, not just in tests.

Usage::

    python tools/materialize_main.py --dest /path/to/main-snapshot
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POINTER_NAME = "VENDOR_POINTER.json"
_VERSION_RE = re.compile(r'^(\s*version\s*=\s*")([^"]+)(")', re.MULTILINE)
_FILE_POINTER_RE = re.compile(
    r'^<!--\s*VENDOR_POINTER:\s*source=(\S+)\s+kind=file\s*-->\s*$'
)


def find_pointers(root: Path) -> list[Path]:
    """Every directory/lib pointer under ``root``: ``plugins/*/libs/*`` and
    the extra top-level ``worktree-manager/libs/*`` tree that
    ``sync-vendored-libs.py``/``check-vendored-libs-sync.py`` also scan (see
    those tools' own ``_lib_copies()``/extra-tree handling)."""
    return sorted(
        root.glob("plugins/*/libs/*/" + POINTER_NAME)
    ) + sorted(root.glob("worktree-manager/libs/*/" + POINTER_NAME))


def _file_pointer_source(path: Path) -> str | None:
    """The `source=` value if ``path``'s first line is a file-pointer marker."""
    try:
        with path.open(encoding="utf-8") as f:
            first_line = f.readline()
    except (UnicodeDecodeError, OSError):
        return None
    m = _FILE_POINTER_RE.match(first_line.rstrip("\n"))
    return m.group(1) if m else None


def find_file_pointers(root: Path) -> list[Path]:
    """Every vendored *file* pointer under ``root`` (not directory/lib
    pointers, which are found by ``find_pointers``). ``root`` may be a whole
    repo checkout or a single plugin's directory -- unlike a lib pointer's
    fixed ``plugins/<plugin>/libs/<lib>`` location, a file pointer can live
    anywhere its marker line is found, so callers such as
    ``preview_release.py`` (which materializes one plugin directory, not the
    whole repo) can reuse this unchanged."""
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.name != POINTER_NAME and _file_pointer_source(p)
    )


def materialize(dest: Path, *, canonical_root: Path) -> list[str]:
    """Expand every pointer found under ``dest`` from ``canonical_root``.

    ``canonical_root`` is a parameter (not hardcoded to ``REPO``) so a test
    can point it at an isolated tree instead of the real checkout."""
    log: list[str] = []
    for pointer_path in find_pointers(dest):
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        source_rel = pointer["source"]  # e.g. "libs/zdd"
        lib_copy_dir = pointer_path.parent
        canonical = _resolve_within(canonical_root, source_rel)

        if canonical is None:
            log.append(f"SKIP {lib_copy_dir}: source {source_rel!r} escapes "
                        "the canonical root -- refusing")
            continue
        if not canonical.is_dir():
            log.append(f"SKIP {lib_copy_dir}: canonical {source_rel} not found")
            continue

        src_sub = canonical / "src"
        dst_sub = lib_copy_dir / "src"
        if dst_sub.exists():
            shutil.rmtree(dst_sub)
        if src_sub.is_dir():
            shutil.copytree(src_sub, dst_sub)

        canon_pp = canonical / "pyproject.toml"
        copy_pp = lib_copy_dir / "pyproject.toml"
        if canon_pp.exists() and copy_pp.exists():
            m = _VERSION_RE.search(canon_pp.read_text(encoding="utf-8"))
            if m:
                text = copy_pp.read_text(encoding="utf-8")
                copy_pp.write_text(_VERSION_RE.sub(rf"\g<1>{m.group(2)}\g<3>", text, count=1),
                                   encoding="utf-8")

        pointer_path.unlink()
        log.append(f"OK   {lib_copy_dir} <- {source_rel}")
    log.extend(materialize_file_pointers(dest, canonical_root=canonical_root))
    return log


def _resolve_within(canonical_root: Path, source_rel: str) -> Path | None:
    """Resolve ``source_rel`` against ``canonical_root``, refusing an absolute
    path or any ``../`` traversal that would escape ``canonical_root``
    (including via a symlink). Returns ``None`` when the candidate escapes."""
    if Path(source_rel).is_absolute():
        return None
    candidate = (canonical_root / source_rel).resolve()
    root = canonical_root.resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def materialize_file_pointers(dest: Path, *, canonical_root: Path) -> list[str]:
    """Expand every file pointer found under ``dest`` from ``canonical_root``.

    Unlike a directory/lib pointer, a file pointer *is* the mirrored file:
    there is no separate pointer sidecar to delete, so materializing
    overwrites the stub's content with the canonical file's bytes in place."""
    log: list[str] = []
    for pointer_path in find_file_pointers(dest):
        source_rel = _file_pointer_source(pointer_path)
        canonical = _resolve_within(canonical_root, source_rel)

        if canonical is None:
            log.append(f"SKIP {pointer_path} (file pointer): source {source_rel!r} "
                        "escapes the canonical root -- refusing")
            continue
        if not canonical.is_file():
            log.append(f"SKIP {pointer_path} (file pointer): canonical {source_rel} not found")
            continue

        pointer_path.write_bytes(canonical.read_bytes())
        log.append(f"OK   {pointer_path} (file pointer) <- {source_rel}")
    return log


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in {
        ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist",
    } or n.endswith((".pyc", ".pyo"))}


def build(dest: Path, *, source_root: Path = REPO) -> list[str]:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source_root, dest, ignore=_ignore)
    return materialize(dest, canonical_root=source_root)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, required=True,
                     help="directory to build the materialized 'main' snapshot into")
    args = ap.parse_args(argv)

    print(f"Snapshotting {REPO} -> {args.dest} ...")
    log = build(args.dest, source_root=REPO)
    for line in log:
        print(line)
    ok = sum(1 for line in log if line.startswith("OK"))
    print(f"\nMaterialized {ok} pointer(s) into {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
