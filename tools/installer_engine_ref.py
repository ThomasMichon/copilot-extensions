"""Shared helpers for the installer-engine canonical-reference form.

Phase 2 of ``vendor-pointer-generalization`` re-expresses the shared
installer engine the same way Phase 1 re-expressed shared Python libs:
on ``dev``, a consumer's wrapper directly sources the canonical
``libs/installer-engine/installer-engine.{ps1,sh}`` by relative path, with
no plugin-local ``scripts/installer-engine.*`` copy at all. At promotion,
``materialize_main.py`` copies canonical back into the plugin's own
``scripts/`` directory and rewrites the source line to the local form,
restoring the shipped self-contained payload.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import uv_editable_ref as uer


REPO = Path(__file__).resolve().parent.parent
CANONICAL_DIR = REPO / "libs" / "installer-engine"
FILES = ("installer-engine.ps1", "installer-engine.sh")
INSTALLER_SCRIPTS = ("install.ps1", "install.sh")
ADOPTERS = ("agent-pull-requests", "agent-logger", "agent-vault")

_LOCAL_LINES = {
    "ps1": ". (Join-Path $PSScriptRoot 'installer-engine.ps1')",
    "sh": '. "$SCRIPT_DIR/installer-engine.sh"',
}
_CANONICAL_RAW_PATHS = {
    "ps1": r"..\..\..\libs\installer-engine\installer-engine.ps1",
    "sh": "../../../libs/installer-engine/installer-engine.sh",
}
_SOURCE_PATTERNS = {
    "ps1": re.compile(
        r"""^(?P<indent>[ \t]*)\.[ \t]*\(Join-Path[ \t]+\$PSScriptRoot[ \t]+(?P<quote>['"])(?P<path>[^'"]*installer-engine\.ps1)(?P=quote)\)(?P<suffix>[ \t]*(?:#.*)?)$""",
        re.MULTILINE,
    ),
    "sh": re.compile(
        r"""^(?P<indent>[ \t]*)(?:source|\.)[ \t]+(?P<quote>["']?)\$SCRIPT_DIR/(?P<path>[^"'\s]*installer-engine\.sh)(?P=quote)(?P<suffix>[ \t]*(?:#.*)?)$""",
        re.MULTILINE,
    ),
}


@dataclass(frozen=True)
class EngineRef:
    ext: str
    script_path: Path
    raw_path: str

    @property
    def file_name(self) -> str:
        return f"installer-engine.{self.ext}"

    @property
    def local_path(self) -> Path:
        return self.script_path.parent / self.file_name

    def resolved(self) -> Path:
        parts = [part for part in re.split(r"[\\/]+", self.raw_path) if part]
        return (self.script_path.parent.joinpath(*parts)).resolve()


def canonical_file(ext: str, *, repo_root: Path = REPO) -> Path:
    return repo_root / "libs" / "installer-engine" / f"installer-engine.{ext}"


def local_line(ext: str) -> str:
    return _LOCAL_LINES[ext]


def canonical_raw_path(ext: str) -> str:
    return _CANONICAL_RAW_PATHS[ext]


def canonical_line(ext: str) -> str:
    if ext == "ps1":
        return f". (Join-Path $PSScriptRoot '{canonical_raw_path(ext)}')"
    return f'. "$SCRIPT_DIR/{canonical_raw_path(ext)}"'


def source_match_count(script_path: Path, ext: str) -> int:
    if not script_path.is_file():
        return 0
    return source_match_count_text(script_path.read_text(encoding="utf-8"), ext)


def source_match_count_text(text: str, ext: str) -> int:
    return len(list(_SOURCE_PATTERNS[ext].finditer(text)))


def find_engine_ref(script_path: Path, ext: str) -> EngineRef | None:
    if not script_path.is_file():
        return None
    matches = list(_SOURCE_PATTERNS[ext].finditer(script_path.read_text(encoding="utf-8")))
    if len(matches) != 1:
        return None
    match = matches[0]
    return EngineRef(ext=ext, script_path=script_path, raw_path=match.group("path"))


def plugin_ref_map(plugin_dir: Path) -> dict[str, EngineRef]:
    refs: dict[str, EngineRef] = {}
    scripts_dir = plugin_dir / "scripts"
    for ext in ("ps1", "sh"):
        ref = find_engine_ref(scripts_dir / f"install.{ext}", ext)
        if ref is not None:
            refs[ext] = ref
    return refs


def ref_escapes_plugin_root(ref: EngineRef, plugin_dir: Path) -> bool:
    return uer.escapes_root(ref.resolved(), plugin_dir.resolve())


def is_canonical_ref(ref: EngineRef, plugin_dir: Path, *, repo_root: Path = REPO) -> bool:
    return (
        ref.raw_path == canonical_raw_path(ref.ext)
        and ref_escapes_plugin_root(ref, plugin_dir)
        and ref.resolved() == canonical_file(ref.ext, repo_root=repo_root).resolve()
    )


def is_local_ref(ref: EngineRef, plugin_dir: Path) -> bool:
    return (
        ref.raw_path == ref.file_name
        and (not ref_escapes_plugin_root(ref, plugin_dir))
        and ref.resolved() == ref.local_path.resolve()
    )


def rewrite_to_local(script_path: Path, ext: str) -> bool:
    text = script_path.read_text(encoding="utf-8")
    matches = list(_SOURCE_PATTERNS[ext].finditer(text))
    if len(matches) != 1:
        return False
    replacement = local_line(ext)
    rewritten, count = _SOURCE_PATTERNS[ext].subn(
        lambda match: f"{match.group('indent')}{replacement}"
        f"{match.group('suffix')}",
        text,
        count=1,
    )
    if count != 1:
        return False
    if rewritten != text:
        script_path.write_text(rewritten, encoding="utf-8")
    return True
