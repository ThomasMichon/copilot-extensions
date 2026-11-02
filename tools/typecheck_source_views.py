"""Faithful checker-only views of fixed, shared-namespace Python fragments."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import io
from pathlib import Path
import re
import tokenize

INSTALLATION_ENTRY = "libs/installation-context/installation_context.py"
_WRAPPER = r'''
"""Resolve, validate, and explicitly mutate marketplace installation context."""
from __future__ import annotations
from pathlib import Path
_FRAGMENT_FILES = ()
def _fragment_directory(script: Path) -> Path:
    if script.drive.casefold() == "\\\\?\\globalroot" and script.root == "\\":
        return script.parent
    return script.resolve().parent
def _load_fragments() -> None:
    fragment_dir = _fragment_directory(Path(__file__))
    for fragment_name in _FRAGMENT_FILES:
        fragment_path = fragment_dir / fragment_name
        exec(compile(fragment_path.read_text(encoding="utf-8"), str(fragment_path), "exec"), globals())
_load_fragments()
if __name__ == "__main__":
    raise SystemExit(globals()["main"]())
'''


class SourceViewError(ValueError):
    """Source correspondence is incomplete or no longer matches the view."""


@dataclass(frozen=True)
class SourceLine:
    path: str
    number: int


@dataclass(frozen=True)
class SourceView:
    output: Path
    content: bytes
    inputs: tuple[tuple[str, bytes], ...]
    lines: tuple[SourceLine, ...]

    def verify(self, root: Path) -> None:
        """Do not credit changed inputs or a changed checker view."""
        prefix = INSTALLATION_ENTRY.rsplit("/", 1)[0] + "/"
        present = {
            prefix + path.name
            for path in (root / prefix).glob("_installation_context_*.py")
            if path.name != "_installation_context_worker.py"
        }
        if present != {path for path, _content in self.inputs[1:]}:
            raise SourceViewError(
                "Installation-context fragment membership changed during type checking"
            )
        for path, content in self.inputs:
            if _read(root, path) != content:
                raise SourceViewError(f"{path}: source changed during type checking")
        if self.output.read_bytes() != self.content:
            raise SourceViewError(
                "Composed checker source changed during type checking"
            )

    def diagnostics(self, text: str, root: Path) -> str:
        """Translate MyPy's composed line numbers back to physical source."""
        names = {str(self.output), self.output.as_posix()}
        if self.output.is_relative_to(root):
            relative = self.output.relative_to(root)
            names.update((str(relative), relative.as_posix()))
        expression = re.compile(
            r"^(?:"
            + "|".join(re.escape(name) for name in sorted(names))
            + r"):(\d+)(:.*)$",
            re.MULTILINE,
        )

        def replace(match: re.Match[str]) -> str:
            number = int(match.group(1))
            if not 1 <= number <= len(self.lines):
                raise SourceViewError(
                    "Checker diagnostic is outside the composed source map"
                )
            source = self.lines[number - 1]
            return f"{source.path}:{source.number}{match.group(2)}"

        return expression.sub(replace, text)


def _read(root: Path, relative: str) -> bytes:
    path = root / relative
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise SourceViewError(
            f"{relative}: source must be a file inside the repository"
        )
    return path.read_bytes()


def _parse(content: bytes, filename: str) -> ast.Module:
    try:
        return ast.parse(content, filename=filename)
    except SyntaxError as exc:
        raise SourceViewError(
            f"{filename}:{exc.lineno}: invalid composed source: {exc.msg}"
        ) from exc


def installation_view(root: Path, paths: tuple[str, ...], output: Path) -> SourceView:
    """Inline only the literal loader's actual call, without importing product code."""
    entry = _read(root, INSTALLATION_ENTRY)
    tree = _parse(entry, INSTALLATION_ENTRY)
    declarations = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_FRAGMENT_FILES"
            for target in node.targets
        )
    ]
    if len(declarations) != 1:
        raise SourceViewError(
            "Installation-context composition requires one fixed declaration"
        )
    value: object = ast.literal_eval(declarations[0].value)
    declarations[0].value = ast.Tuple(elts=[], ctx=ast.Load())
    if ast.dump(tree) != ast.dump(ast.parse(_WRAPPER)):
        raise SourceViewError(
            "Installation-context loader changed; qualify its new composition"
        )
    call = tree.body[-2]
    if (
        not isinstance(value, tuple)
        or not value
        or any(
            not isinstance(name, str)
            or not re.fullmatch(r"_installation_context_[a-z_]+\.py", name)
            for name in value
        )
    ):
        raise SourceViewError(
            "Installation-context fragments must be fixed, local Python filenames"
        )
    fragments = tuple(name for name in value if isinstance(name, str))
    if len(set(fragments)) != len(fragments):
        raise SourceViewError("Installation-context fragment list contains duplicates")
    prefix = INSTALLATION_ENTRY.rsplit("/", 1)[0] + "/"
    members = tuple(prefix + name for name in fragments)
    physical = {
        path
        for path in paths
        if path.startswith(prefix)
        and "/" not in path[len(prefix) :]
        and path.rsplit("/", 1)[-1].startswith("_installation_context_")
        and path != prefix + "_installation_context_worker.py"
    }
    if set(members) != physical or INSTALLATION_ENTRY not in paths:
        raise SourceViewError(
            "Installation-context fragment membership differs from physical inventory"
        )
    inputs = (
        (INSTALLATION_ENTRY, entry),
        *((name, _read(root, name)) for name in members),
    )
    lines: list[SourceLine] = []
    chunks: list[bytes] = []

    def append(
        path: str, content: bytes, *, start: int = 1, fragment: bool = False
    ) -> None:
        source_lines = content.splitlines(keepends=True)
        source_tree = _parse(content, path) if fragment else None
        for token in tokenize.tokenize(io.BytesIO(content).readline):
            if token.type == tokenize.COMMENT and (
                re.match(r"#\s*mypy:", token.string)
                or (
                    re.match(r"#\s*type:\s*ignore\b", token.string)
                    and not token.line[: token.start[1]].strip()
                )
            ):
                raise SourceViewError(
                    f"{path}: module-wide checker directives cannot qualify a composition"
                )
        future_lines: set[int] = set()
        if source_tree is not None:
            for node in source_tree.body:
                if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                    if [(alias.name, alias.asname) for alias in node.names] != [
                        ("annotations", None)
                    ]:
                        raise SourceViewError(
                            f"{path}: unsupported fragment future import"
                        )
                    if (
                        node.end_lineno != node.lineno
                        or source_lines[node.lineno - 1].split(b"#", 1)[0].strip()
                        != b"from __future__ import annotations"
                    ):
                        raise SourceViewError(
                            f"{path}: future import must occupy its own source line"
                        )
                    future_lines.add(node.lineno)
        for number, line in enumerate(source_lines, start):
            chunks.append(
                b"\n" if number in future_lines else line.rstrip(b"\r\n") + b"\n"
            )
            lines.append(SourceLine(path, number))

    entry_lines = entry.splitlines(keepends=True)
    append(INSTALLATION_ENTRY, b"".join(entry_lines[: call.lineno - 1]))
    append(INSTALLATION_ENTRY, b"\n", start=call.lineno)
    for name, content in inputs[1:]:
        append(name, content, fragment=True)
    append(
        INSTALLATION_ENTRY,
        b"".join(entry_lines[call.end_lineno or call.lineno :]),
        start=(call.end_lineno or call.lineno) + 1,
    )
    content = b"".join(chunks)
    _parse(content, str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    return SourceView(output, content, inputs, tuple(lines))
