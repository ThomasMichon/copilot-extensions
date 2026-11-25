"""Materialize declared Invocation outputs inside a release candidate only."""
from __future__ import annotations

import importlib.util
import os
import stat
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path


def _publish_native(
    path: Path, content: bytes, root: Path,
    write: Callable[[Path, str | bytes], None],
) -> None:
    if path.exists() and path.read_bytes() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent, prefix=".native-") as directory:
        staged = Path(directory) / path.name
        write(staged, content)
        _ordinary(staged, path.parent)
        if staged.read_bytes() != content:
            raise ValueError(f"{path}: generated content is stale")
        _ordinary(path, root)
        os.replace(staged, path)


def _publish_index(path: Path, content: bytes) -> None:
    """Select a complete verified bundle with one owner-local atomic replace."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".index-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _ordinary(path: Path, root: Path, *, allow_hardlinks: bool = False) -> None:
    if ".." in path.parts or ".." in root.parts or not path.is_relative_to(root):
        raise ValueError(f"Invocation path escapes its declared root: {path}")
    for component in (path, *path.parents):
        if component.exists() or component.is_symlink():
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError(f"Invocation path is redirected: {component}")
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError(f"Invocation path is not ordinary: {component}")
            if not allow_hardlinks and stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
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
        native_declarations: list[tuple[Path, Path]] = []
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
            declaration = plugin / "native-command-errors.json"
            if declaration.exists() or declaration.is_symlink():
                source = (
                    canonical_root / declaration.relative_to(dest) if source_consumer is None
                    else source_consumer / declaration.name
                )
                for path, root in (
                    (declaration, dest), (source, canonical_root),
                    (plugin / "plugin.json", dest), (source.parent / "plugin.json", canonical_root),
                ):
                    _ordinary(path, root)
                if declaration.read_bytes() != source.read_bytes():
                    raise ValueError(f"Native evidence declaration differs from canonical source: {declaration}")
                native_declarations.append((declaration, source))
        if not manifests and not native_declarations:
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
        # uv may hard-link this read-only installed producer to its cache.
        for name in ("generate.py", "template_rendering.py", "command_evidence.py"):
            _ordinary(tooling_root / "libs/payload-invocation" / name, tooling_root, allow_hardlinks=True)
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
        indexes: set[Path] = set()
        native_resources: set[Path] = set()
        for manifest in manifests:
            outputs = generator.expected_files(manifest, canonical_root=canonical_root)
            if not isinstance(outputs, dict):
                raise TypeError("Invocation producer returned an invalid output inventory")
            generator.validate_output_paths(manifest, outputs)
            for path, content in outputs.items():
                if not isinstance(path, Path) or not isinstance(content, (str, bytes)):
                    raise TypeError("Invocation producer returned an invalid output")
                _ordinary(path, manifest.parent)
                _ordinary(path, dest)
                if path in planned:
                    raise ValueError(f"Invocation output has competing owners: {path}")
                if path.exists() and not path.is_file():
                    raise ValueError(f"Invocation output is not an ordinary file: {path}")
                planned[path] = content
        if native_declarations:
            _ordinary(
                tooling_root / "libs/payload-invocation/native_consumers.py",
                tooling_root, allow_hardlinks=True,
            )
            native_spec = importlib.util.spec_from_file_location(
                "_promotion_native_consumers",
                Path(__file__).resolve().parents[1] / "libs" / "payload-invocation" / "native_consumers.py",
            )
            assert native_spec and native_spec.loader
            native = importlib.util.module_from_spec(native_spec)
            sys.modules["_promotion_native_consumers"] = native
            previous_bytecode = sys.dont_write_bytecode
            try:
                sys.dont_write_bytecode = True
                native_spec.loader.exec_module(native)
            finally:
                sys.dont_write_bytecode = previous_bytecode
            for declaration, source in native_declarations:
                plan = native.plan_outputs(
                    declaration, canonical_declaration=source, canonical_root=canonical_root,
                )
                generator.validate_output_paths(declaration, plan.outputs)
                for path, content in plan.outputs.items():
                    _ordinary(path, dest)
                    if path in planned:
                        raise ValueError(f"Native evidence output has competing owners: {path}")
                    planned[path] = content
                    if path != plan.index:
                        native_resources.add(path)
                indexes.add(plan.index)
        if any(parent in planned for path in planned for parent in path.parents):
            raise ValueError("Invocation output has competing ancestor ownership")
        log = []
        for path, content in planned.items():
            if path in indexes:
                continue
            # Recompare immediately before the normal writer follows the path.
            _ordinary(path, dest)
            if path in native_resources:
                assert isinstance(content, bytes)
                _publish_native(path, content, dest, generator.write_generated)
            else:
                generator.write_generated(path, content)
            log.append(f"MATERIALIZE Invocation {path.relative_to(dest).as_posix()}")
        for path, content in planned.items():
            if path in indexes:
                continue
            _ordinary(path, dest)
            problems = generator.check_generated(path, content, exact_bytes=True)
            if problems:
                raise ValueError("; ".join(problems))
        for path in sorted(indexes):
            content = planned[path]
            assert isinstance(content, bytes)
            _ordinary(path, dest)
            _publish_index(path, content)
            problems = generator.check_generated(path, content, exact_bytes=True)
            if problems:
                raise ValueError("; ".join(problems))
            log.append(f"MATERIALIZE Invocation {path.relative_to(dest).as_posix()}")
        return log
    except (OSError, TypeError, ValueError) as error:
        return [f"SKIP Invocation materialization: {error}"]
