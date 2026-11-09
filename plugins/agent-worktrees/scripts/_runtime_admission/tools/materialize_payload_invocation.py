"""Materialize declared Invocation outputs inside a release candidate only."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import stat
import sys


def _ordinary(path: Path, root: Path) -> None:
    if ".." in path.parts or ".." in root.parts or not path.is_relative_to(root):
        raise ValueError(f"Invocation path escapes its declared root: {path}")
    for component in (path, *path.parents):
        if component.exists() or component.is_symlink():
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError(f"Invocation path is redirected: {component}")
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError(f"Invocation path is not ordinary: {component}")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise ValueError(f"Invocation path has foreign hard-link custody: {component}")
        if component == root:
            break


def materialize(
    dest: Path, *, canonical_root: Path, source_consumer: Path | None = None,
) -> list[str]:
    """Use the canonical producer, never an old kit inventory or installed copy.

    The promotion owner supplies its private, version-stamped candidate.
    All destination paths are validated before the first write. Unrelated files
    are not removed; stale/absent declared outputs are replaced by their owner.
    """
    dest, canonical_root = dest.absolute(), canonical_root.absolute()
    try:
        _ordinary(dest, dest)
        _ordinary(canonical_root, canonical_root)
        if source_consumer is None:
            plugins = dest / "plugins"
            _ordinary(plugins, dest)
            if not plugins.exists():
                return []
            consumers = sorted(plugins.iterdir())
        else:
            source_consumer = source_consumer.absolute()
            _ordinary(source_consumer, canonical_root)
            consumers = [dest]
        manifests = []
        for plugin in consumers:
            _ordinary(plugin, dest)
            manifest = plugin / "payload-invocation.json"
            if manifest.exists() or manifest.is_symlink():
                _ordinary(manifest, dest)
                source = (
                    canonical_root / manifest.relative_to(dest) if source_consumer is None
                    else source_consumer / manifest.name
                )
                _ordinary(source, canonical_root)
                if manifest.read_bytes() != source.read_bytes():
                    raise ValueError(f"Invocation manifest differs from its canonical source: {manifest}")
                manifests.append(manifest)
        if not manifests:
            return []
        inputs = [
            *canonical_root.joinpath("libs/payload-invocation/templates").glob("*.tmpl"),
            canonical_root / "libs/payload-invocation/command_binding.py",
            canonical_root / "libs/payload-invocation/command_lifecycle.py",
            canonical_root / ".github/plugin/marketplace.json",
            *(canonical_root / "libs/update-telemetry/src/update_telemetry" / name
              for name in ("human.py", "redact.py", "capabilities.py")),
        ]
        for source in inputs:
            _ordinary(source, canonical_root)
        tooling_root = Path(__file__).resolve().parents[1]
        producer = tooling_root / "libs/payload-invocation/generate.py"
        _ordinary(producer, tooling_root)
        spec = importlib.util.spec_from_file_location(
            "_promotion_payload_invocation",
            Path(__file__).resolve().parents[1] / "libs" / "payload-invocation" / "generate.py",
        )
        assert spec is not None and spec.loader is not None
        generator = importlib.util.module_from_spec(spec)
        previous_bytecode = sys.dont_write_bytecode
        try:
            sys.dont_write_bytecode = True
            spec.loader.exec_module(generator)
        finally:
            sys.dont_write_bytecode = previous_bytecode
        planned: dict[Path, str | bytes] = {}
        for manifest in manifests:
            outputs = generator.expected_files(manifest, canonical_root=canonical_root)
            if not isinstance(outputs, dict):
                raise ValueError("Invocation producer returned an invalid output inventory")
            generator.validate_output_paths(manifest, outputs)
            for path, content in outputs.items():
                if not isinstance(path, Path) or not isinstance(content, (str, bytes)):
                    raise ValueError("Invocation producer returned an invalid output")
                _ordinary(path, manifest.parent)
                _ordinary(path, dest)
                if path in planned:
                    raise ValueError(f"Invocation output has competing owners: {path}")
                if path.exists() and not path.is_file():
                    raise ValueError(f"Invocation output is not an ordinary file: {path}")
                planned[path] = content
        log = []
        for path, content in planned.items():
            # Recompare immediately before the normal writer follows the path.
            _ordinary(path, dest)
            generator.write_generated(path, content)
            log.append(f"MATERIALIZE Invocation {path.relative_to(dest).as_posix()}")
        for path, content in planned.items():
            _ordinary(path, dest)
            problems = generator.check_generated(path, content, exact_bytes=True)
            if problems:
                raise ValueError("; ".join(problems))
        return log
    except (OSError, ValueError) as error:
        return [f"SKIP Invocation materialization: {error}"]
