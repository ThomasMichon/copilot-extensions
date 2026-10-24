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

import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re

import uv_editable_ref as uer


REPO = Path(__file__).resolve().parent.parent
CANONICAL_DIR = REPO / "libs" / "installer-engine"
FILES = ("installer-engine.ps1", "installer-engine.sh")
SUPPORT_FILES = ("installer_dependency_policy.py", "installer_dependency_config.py")
_NATIVE_BEGIN = "# native-executable:begin"
_NATIVE_END = "# native-executable:end"
PYTHON_FILES = (
    "installer_engine.py", "installer_engine_process.py", "runtime_launcher.py",
    "installer_wheel_inputs.py",
    "installer_wheel_snapshot.py",
)
PYTHON_ADOPTERS = ("agent-worktrees", "agent-vault", "agent-pull-requests", "agent-ssh")
STANDALONE_PYTHON_ADOPTERS = ("worktree-manager", "libs/update-driver")
STANDALONE_NATIVE_ADOPTERS = ("libs/update-driver",)
STANDALONE_NATIVE_FILES = ("installer-engine.ps1", "installer-engine.sh")


def standalone_python_adopter_dirs(repo: Path) -> tuple[Path, ...]:
    return tuple(repo / name for name in STANDALONE_PYTHON_ADOPTERS if os.path.lexists(repo / name))


def python_adopter_dirs(repo: Path, *, plugins: tuple[str, ...] | None = None) -> tuple[Path, ...]:
    return (
        *(repo / "plugins" / name for name in (PYTHON_ADOPTERS if plugins is None else plugins)),
        *standalone_python_adopter_dirs(repo),
    )


def native_adopter_dirs(repo: Path) -> tuple[Path, ...]:
    return tuple(repo / name for name in STANDALONE_NATIVE_ADOPTERS if os.path.lexists(repo / name))


INSTALLER_SCRIPTS = ("install.ps1", "install.sh")
ADOPTERS = (
    "agent-pull-requests",
    "agent-logger",
    "agent-vault",
    "agent-ssh",
    "agent-bridge",
    "agent-codespaces",
    "agent-dispatch",
    "agent-index",
)
HUMAN_HELPER = "installer-human-output.ps1"
HUMAN_SOURCE_LINE = ". (Join-Path $PSScriptRoot 'installer-human-output.ps1')"
HUMAN_PROLOGUE = (
    "if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8 -eq '1') {\n"
    "    if (-not [IO.File]::Exists((Join-Path $PSScriptRoot 'installer-human-output.ps1'))) {\n"
    "        throw 'Required installer human-output initializer is unavailable.'\n"
    "    }\n"
    f"    {HUMAN_SOURCE_LINE}\n"
    "}"
)
HUMAN_STANDALONE_ENTRYPOINTS = {
    "agent-worktrees": "install.ps1",
    "agent-containers": "init.ps1",
    "agent-mcp": "init.ps1",
    "agent-machines": "init.ps1",
    "budget-guidance": "install.ps1",
}


def human_output_enabled(canonical_dir: Path) -> bool:
    engine = canonical_dir / "installer-engine.ps1"
    return engine.is_file() and (
        f"# Packaged human-output initialization: {HUMAN_HELPER}"
        in engine.read_text(encoding="utf-8")
    )


def human_output_adopters(repo: Path, adopters) -> tuple[str, ...]:
    # Standalone installers share only the output initializer, not the engine.
    return (
        *adopters,
        *(name for name, entrypoint in HUMAN_STANDALONE_ENTRYPOINTS.items()
          if (repo / "plugins" / name / "scripts" / entrypoint).is_file()),
    )


def human_output_entrypoint(consumer: Path) -> Path:
    return consumer / "scripts" / HUMAN_STANDALONE_ENTRYPOINTS.get(
        consumer.name, "install.ps1",
    )


def human_output_declaration(script: Path) -> dict:
    helper = script.parent / HUMAN_HELPER
    text = script.read_text(encoding="utf-8")
    if text.count(HUMAN_SOURCE_LINE) != 1:
        raise ValueError(f"{script}: expected one native human-output initializer")
    prologue = "$ErrorActionPreference = 'Stop'\n\n" + HUMAN_PROLOGUE
    if prologue not in text:
        raise ValueError(f"{script}: human-output initializer must follow strict error setup")

    def digest(path: Path) -> str:
        # Git checkouts may use CRLF; this is a content declaration, not a
        # replacement for PowerShell's signature/execution-policy enforcement.
        return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()

    return {
        "version": 1, "encoding": "utf-8", "entrypoint": script.name,
        "entrypoint_sha256": digest(script), "initializer": HUMAN_HELPER,
        "initializer_sha256": digest(helper),
    }


def human_output_json(script: Path) -> str:
    return json.dumps(human_output_declaration(script), indent=2) + "\n"


def native_executable_config(canonical_dir: Path) -> str | None:
    """Project the canonical standalone primitive without another runtime dependency."""
    config = canonical_dir / "installer_dependency_config.py"
    root = canonical_dir.parent.parent.resolve()
    if uer._find_symlinked_ancestor(config, root) is not None:
        raise ValueError(f"{config}: linked native executable projection refused")
    if not config.is_file():
        return None
    source = root / "libs" / "agent-procutil" / "src" / "agent_procutil" / "_interpreter.py"
    text = config.read_text(encoding="utf-8")
    if _NATIVE_BEGIN not in text and _NATIVE_END not in text:
        if "def select_uv_executable(" in text:
            raise ValueError(f"{config}: native executable projection markers are missing")
        return None
    if (
        text.count(_NATIVE_BEGIN) != 1 or text.count(_NATIVE_END) != 1
        or text.index(_NATIVE_BEGIN) > text.index(_NATIVE_END)
    ):
        raise ValueError(f"{config}: native executable projection markers are invalid")
    if uer._find_symlinked_ancestor(source, root) is not None:
        raise ValueError(f"{source}: linked native executable source refused")
    body = source.read_text(encoding="utf-8")
    functions = [
        node for node in ast.parse(body).body
        if isinstance(node, ast.FunctionDef) and node.name == "physical_executable"
    ]
    if len(functions) != 1:
        raise ValueError(f"{source}: expected one canonical physical_executable")
    function = functions[0]
    projected = "".join(body.splitlines(keepends=True)[function.lineno - 1:function.end_lineno])
    before, remaining = text.split(_NATIVE_BEGIN)
    _previous, after = remaining.split(_NATIVE_END)
    return before + _NATIVE_BEGIN + "\n" + projected.rstrip() + "\n" + _NATIVE_END + after


def support_files(canonical_dir: Path) -> tuple[str, ...]:
    """Assets referenced by this engine generation (older fixtures need none)."""
    bodies = [
        (canonical_dir / name).read_text(encoding="utf-8")
        for name in FILES if (canonical_dir / name).is_file()
    ]
    files = SUPPORT_FILES if any(SUPPORT_FILES[0] in body for body in bodies) else ()
    if human_output_enabled(canonical_dir):
        files += (HUMAN_HELPER,)
    return files

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
