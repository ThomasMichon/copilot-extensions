"""Faithful checker-only views of fixed, shared-namespace Python fragments."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import io
import hashlib
import json
import os
from pathlib import Path
import re
import tokenize

INSTALLATION_ENTRY = "libs/installation-context/installation_context.py"
GUARD_ENTRY = "plugins/copilot-extensions-harness/scripts/copilot-mention-guard.py"
GUARD_MODULE = "copilot_mention_guard"
HARNESS_PREFIX = "plugins/copilot-extensions-harness/"
HARNESS_DECLARATION = HARNESS_PREFIX + "python-emitted-inputs.json"
_WRAPPER = r'''
"""Resolve, validate, and explicitly mutate marketplace installation context."""
from __future__ import annotations
import os
from pathlib import Path
_FRAGMENT_FILES = ()
def _fragment_directory(script: Path) -> Path:
    if script.drive.casefold() == "\\\\?\\globalroot" and script.root == "\\":
        return script.parent
    return script.resolve().parent
def _native_fragment(path: Path) -> Path:
    value = str(path)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return path
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
def _load_fragments() -> None:
    fragment_dir = _fragment_directory(Path(__file__))
    for fragment_name in _FRAGMENT_FILES:
        fragment_path = fragment_dir / fragment_name
        exec(compile(_native_fragment(fragment_path).read_text(encoding="utf-8"), str(fragment_path), "exec"), globals())
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
    identity: tuple[int, int]

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
        self._verify_bytes(root)

    def _verify_bytes(self, root: Path) -> None:
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


@dataclass(frozen=True)
class GuardAliasView(SourceView):
    def verify(self, root: Path) -> None:
        if (
            tuple(path for path, _content in self.inputs) != (GUARD_ENTRY,)
            or self.output.name != GUARD_MODULE + ".py"
        ):
            raise SourceViewError("Guard alias requires its exact single-source binding")
        self._verify_bytes(root)


def _write_view(output: Path, content: bytes) -> tuple[int, int]:
    output.parent.mkdir(parents=True, exist_ok=True)
    identity: tuple[int, int] | None = None
    try:
        with output.open("xb") as stream:
            info = os.fstat(stream.fileno())
            identity = info.st_dev, info.st_ino
            stream.write(content)
    except BaseException:
        if identity is not None:
            current = output.lstat()
            if (current.st_dev, current.st_ino) != identity:
                raise SourceViewError("Checker output replaced during creation; retained")
            output.unlink()
        raise
    assert identity is not None
    return identity


def _reject_directives(path: str, content: bytes) -> None:
    normalized = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    for token in tokenize.tokenize(io.BytesIO(normalized).readline):
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


def guard_alias_view(root: Path, paths: tuple[str, ...], output: Path) -> GuardAliasView:
    """Expose exactly one real hyphenated source without rewriting any bytes."""
    if paths.count(GUARD_ENTRY) != 1 or output.name != GUARD_MODULE + ".py":
        raise SourceViewError("Guard alias requires its exact owned physical source")
    content = _read(root, GUARD_ENTRY)
    _parse(content, GUARD_ENTRY)
    _reject_directives(GUARD_ENTRY, content)
    identity = _write_view(output, content)
    return GuardAliasView(
        output, content, ((GUARD_ENTRY, content),),
        tuple(SourceLine(GUARD_ENTRY, number) for number in range(1, len(content.splitlines()) + 1)),
        identity,
    )


def _read(root: Path, relative: str) -> bytes:
    path = root / relative
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise SourceViewError(
            f"{relative}: source must be a file inside the repository"
        )
    return path.read_bytes()


def _parse(
    content: bytes, filename: str, *, feature_version: tuple[int, int] | None = None,
) -> ast.Module:
    try:
        compile(content, filename, "exec", dont_inherit=True)
        return ast.parse(content, filename=filename, feature_version=feature_version)
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
        _reject_directives(path, content)
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
    identity = _write_view(output, content)
    return SourceView(output, content, inputs, tuple(lines), identity)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _shape(node: ast.AST) -> str:
    def semantic(value: object) -> object:
        if isinstance(value, ast.AST):
            fields = {}
            for name, child in ast.iter_fields(value):
                if (
                    name == "type_params" and child == []
                    and isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ):
                    continue
                fields[name] = semantic(child)
            return {"node": type(value).__name__, "fields": fields}
        if isinstance(value, list):
            return [semantic(child) for child in value]
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, bytes):
            return {"bytes": value.hex()}
        if isinstance(value, float):
            return {"float": value.hex()}
        if isinstance(value, complex):
            return {"complex": [value.real.hex(), value.imag.hex()]}
        if value is Ellipsis:
            return {"ellipsis": True}
        raise SourceViewError("Unsupported semantic AST field")

    return _digest(json.dumps(semantic(node), sort_keys=True, separators=(",", ":")).encode("utf-8"))


_WITNESSES = {
    "pid_file": "/__harness_typing_witness__/descendant.pid",
    "procutil_src": "/__harness_typing_witness__/repo/libs/agent-procutil/src",
    "source": "/__harness_typing_witness__/repo/plugins/copilot-extensions-harness/scripts/copilot-mention-guard.py",
}


def _emitted_string(node: ast.expr, values: dict[str, str]) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _emitted_string(node.left, values) + _emitted_string(node.right, values)
    if isinstance(node, ast.JoinedStr):
        result = ""
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                result += part.value
            elif (
                isinstance(part, ast.FormattedValue) and part.conversion == 114
                and part.format_spec is None
            ):
                value = part.value
                if (
                    isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                    and value.func.id == "str" and len(value.args) == 1
                    and not value.keywords and isinstance(value.args[0], ast.Name)
                    and value.args[0].id in values
                ):
                    result += repr(values[value.args[0].id])
                else:
                    raise SourceViewError("Unsupported harness interpolation")
            else:
                raise SourceViewError("Unsupported harness interpolation")
        return result
    if (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "repr" and len(node.args) == 1 and not node.keywords
        and isinstance(node.args[0], ast.Name) and node.args[0].id in values
    ):
        return repr(values[node.args[0].id])
    raise SourceViewError("Unknown harness emitted expression; review its producer")


def _witness_positions(text: str, values: dict[str, str]) -> ast.Module:
    tree = _parse(
        text.encode("utf-8"), "<portable-harness-typing-witness>", feature_version=(3, 10),
    )
    sensitive = {"open", "pathlib", "runpy", "sys"}
    for binding in ast.walk(tree):
        if (
            isinstance(binding, ast.Name) and isinstance(binding.ctx, ast.Store) and binding.id in sensitive
        ) or (
            isinstance(binding, (ast.FunctionDef, ast.ClassDef)) and binding.name in sensitive
        ) or (isinstance(binding, ast.arg) and binding.arg in sensitive):
            raise SourceViewError("Shadowed value-insensitive fixture binding")
        if isinstance(binding, ast.alias) and binding.asname in sensitive:
            raise SourceViewError("Shadowed fixture import binding")
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node.value not in values.values():
            if "__harness_typing_witness__" in node.value:
                raise SourceViewError("Fixture value escaped its single string literal")
            continue
        parent = parents.get(node)
        if not isinstance(parent, ast.Call):
            raise SourceViewError("Fixture value occurs in a value-dependent position")
        allowed = {"open": 0, "pathlib.Path": 0, "runpy.run_path": 0, "sys.path.insert": 1}
        name = ast.unparse(parent.func)
        if name not in allowed or len(parent.args) <= allowed[name] or parent.args[allowed[name]] is not node:
            raise SourceViewError("Fixture value occurs in an unreviewed call position")
        node.value = "__portable_parameter__"
    return tree


@dataclass(frozen=True)
class HarnessProgramView(SourceView):
    members: tuple[str, ...]
    assets: tuple[str, ...]
    origin: tuple[str, int, int]

    def verify(self, root: Path) -> None:
        present = tuple(sorted(
            path.relative_to(root).as_posix()
            for path in (root / HARNESS_PREFIX).rglob("*.py")
        ))
        assets = _harness_assets(root)
        if present != self.members or assets != self.assets:
            raise SourceViewError("Harness producer/member/asset membership changed")
        self._verify_bytes(root)

    def diagnostics(self, text: str, root: Path) -> str:
        _path, first, last = self.origin
        result = []
        for line in text.splitlines(keepends=True):
            mapped = super().diagnostics(line, root)
            if mapped != line:
                body = mapped.rstrip("\r\n")
                ending = mapped[len(body):]
                mapped = body + f" [emitted expression range {first}-{last}; portable typing witness]" + ending
            result.append(mapped)
        return "".join(result)


def _harness_assets(root: Path) -> tuple[str, ...]:
    return tuple(sorted(
        p.relative_to(root).as_posix()
        for p in (root / HARNESS_PREFIX).rglob("*")
        if p.is_file() and (p.suffix in {".sh", ".ps1", ".js", ".mjs", ".cjs"} or p.name == "hooks.json")
    ))


def _harness_binding(fn: ast.FunctionDef, name: str, site: ast.AST) -> ast.expr:
    parents = {child: node for node in ast.walk(fn) for child in ast.iter_child_nodes(node)}

    def local(node: ast.AST) -> bool:
        while node in parents:
            node = parents[node]
            if node is fn:
                return True
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return False
        return False

    if not local(site):
        raise SourceViewError("Ambiguous nested producer binding")
    arguments = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    arguments.extend(arg for arg in (fn.args.vararg, fn.args.kwarg) if arg is not None)
    if any(arg.arg == name for arg in arguments):
        raise SourceViewError("Parameter-backed producer binding")
    definitions = [
        node for node in ast.walk(fn) if isinstance(node, ast.Assign) and local(node)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    ]
    stores = [
        node for node in ast.walk(fn) if isinstance(node, ast.Name)
        and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == name and local(node)
    ]
    for node in ast.walk(fn):
        if isinstance(node, ast.Nonlocal) and name in node.names:
            raise SourceViewError("Ambiguous nonlocal producer binding")
        if not local(node):
            continue
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node is not fn and node.name == name
        ) or (
            isinstance(node, ast.alias) and (node.asname or node.name.split(".")[0]) == name
        ) or (
            isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names
        ) or (
            isinstance(node, ast.ExceptHandler) and node.name == name
        ) or (
            isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name
        ) or (
            isinstance(node, ast.MatchMapping) and node.rest == name
        ):
            raise SourceViewError("Ambiguous lexical producer binding")
    if len(definitions) != 1 or len(stores) != 1:
        raise SourceViewError("Unbound or ambiguous reassigned producer binding")
    suites: dict[ast.stmt, tuple[list[ast.AST], int]] = {}
    for node in ast.walk(fn):
        for _field, children in ast.iter_fields(node):
            if isinstance(children, list):
                for position, child in enumerate(children):
                    if isinstance(child, ast.stmt):
                        suites[child] = children, position
    definition = definitions[0]
    suite, position = suites[definition]
    statement = site
    while statement in parents:
        if isinstance(statement, ast.stmt) and statement in suites:
            use_suite, use_position = suites[statement]
            if use_suite is suite and use_position > position:
                return definition.value
        statement = parents[statement]
    raise SourceViewError("Producer binding must precede its use in the same or ancestor suite")


def _required_harness_programs(
    trees: dict[str, ast.Module], data_writes: list[object],
) -> dict[tuple[str, str, str], list[list[int]]]:
    required: dict[tuple[str, str, str], list[list[int]]] = {}
    observed_data_writes: list[object] = []
    for path, tree in trees.items():
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

        def context(node: ast.AST) -> ast.FunctionDef:
            producer = node
            chain: list[ast.AST] = []
            while node in parents:
                node = parents[node]
                chain.append(node)
            functions = [n for n in chain if isinstance(n, ast.FunctionDef)]
            container = parents.get(producer)
            nested_body: ast.expr | None = None
            if isinstance(producer, ast.Constant) and producer.value == "-c" and isinstance(container, (ast.List, ast.Tuple)):
                offset = container.elts.index(producer)
                if offset + 1 < len(container.elts):
                    nested_body = container.elts[offset + 1]
            literal_nested = (
                isinstance(nested_body, ast.Constant) and isinstance(nested_body.value, str)
            )
            if not functions or (len(functions) != 1 and not literal_nested) or any(
                isinstance(n, (ast.ClassDef, ast.AsyncFunctionDef, ast.Lambda)) for n in chain
            ):
                raise SourceViewError("Unsupported module/class/async/nested Python producer context")
            return functions[-1]

        def source(fn: ast.FunctionDef, expression: ast.expr, site: ast.expr) -> None:
            if isinstance(expression, ast.Name):
                expression = _harness_binding(fn, expression.id, site)
            if isinstance(expression, ast.IfExp):
                if ast.unparse(expression.test) != "name == 'startup'":
                    raise SourceViewError("Unknown value-dependent Python producer")
                source(fn, expression.body, site)
                source(fn, expression.orelse, site)
                return
            span = [site.lineno, site.col_offset, site.end_lineno or site.lineno, site.end_col_offset or 0]
            required.setdefault((path, fn.name, _shape(expression)), []).append(span)

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "-c":
                fn = context(node)
                container = parents.get(node)
                if not isinstance(container, (ast.List, ast.Tuple)):
                    raise SourceViewError("Unsupported Python-c command form")
                offset = container.elts.index(node)
                if offset + 1 >= len(container.elts):
                    raise SourceViewError("Missing or indirect Python-c body")
                if ast.unparse(container.elts[0]) != "sys.executable":
                    if fn.name != "test_shipped_wrapper_fails_open_on_unavailable_guard":
                        raise SourceViewError("Unknown Python/shell-c invocation")
                    continue
                source(fn, container.elts[offset + 1], container)
            if (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"write_text", "write_bytes"}
            ):
                fn = context(node)
                receiver = node.func.value
                if isinstance(receiver, ast.Name):
                    receiver = _harness_binding(fn, receiver.id, node)
                python_file = any(
                    isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.endswith(".py")
                    for n in ast.walk(receiver)
                )
                if python_file:
                    if node.func.attr != "write_text" or not node.args:
                        raise SourceViewError("Unsupported generated Python write")
                    source(fn, node.args[0], node)
                else:
                    observed_data_writes.append({
                        "path": path, "function": fn.name,
                        "site": [node.lineno, node.col_offset, node.end_lineno, node.end_col_offset],
                        "structure": _shape(node),
                    })
    if observed_data_writes != data_writes:
        raise SourceViewError("Unknown or changed file-write producer/epoch")
    return required


def harness_emitted_inputs(
    root: Path, paths: tuple[str, ...],
) -> tuple[tuple[tuple[str, bytes], ...], tuple[tuple[str, bytes, int, int], ...]]:
    """Recognize only this owner's declared source expressions, without execution."""
    raw = _read(root, HARNESS_DECLARATION)
    declaration: object = json.loads(raw)
    if not isinstance(declaration, dict) or set(declaration) != {"version", "members", "assets", "programs", "data_writes"}:
        raise SourceViewError("Invalid harness declaration fields; private capture records are not declarations")
    if declaration["version"] != 1:
        raise SourceViewError("Unknown harness declaration")
    members = tuple(sorted(p for p in paths if p.startswith(HARNESS_PREFIX)))
    rows = declaration["members"]
    if not isinstance(rows, list) or [r["path"] for r in rows] != list(members):
        raise SourceViewError("Harness declaration requires every current owned member")
    inputs: list[tuple[str, bytes]] = [(HARNESS_DECLARATION, raw)]
    trees: dict[str, ast.Module] = {}
    for row in rows:
        if set(row) != {"path", "sha256", "structure"}:
            raise SourceViewError("Unknown harness member fields")
        content = _read(root, row["path"])
        tree = _parse(content, row["path"], feature_version=(3, 10))
        for node in ast.walk(tree):
            names: tuple[str, ...] = ()
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names = (node.id,)
            elif isinstance(node, ast.arg):
                names = (node.arg,)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = (node.name,)
            elif isinstance(node, ast.alias):
                names = (node.asname or node.name.split(".")[0],)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                names = tuple(node.names)
            elif isinstance(node, ast.ExceptHandler) and node.name is not None:
                names = (node.name,)
            elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
                names = (node.name,)
            elif isinstance(node, ast.MatchMapping) and node.rest is not None:
                names = (node.rest,)
            if {"str", "repr"}.intersection(names):
                raise SourceViewError("Shadowed fixture conversion binding")
        if _digest(content) not in row["sha256"] or _shape(tree) != row["structure"]:
            raise SourceViewError(f"{row['path']}: producer bytes/structure changed; review all sinks")
        inputs.append((row["path"], content))
        trees[row["path"]] = tree
    assets = _harness_assets(root)
    asset_rows = declaration["assets"]
    if not isinstance(asset_rows, list) or [r["path"] for r in asset_rows] != list(assets):
        raise SourceViewError("Harness executable asset membership changed")
    for row in asset_rows:
        if set(row) != {"path", "sha256", "structure"}:
            raise SourceViewError("Unknown harness asset fields")
        content = _read(root, row["path"])
        if _digest(content) not in row["sha256"] or _digest(content) not in row["structure"]:
            raise SourceViewError("Harness shell/hook structure changed")
        inputs.append((row["path"], content))
    programs = declaration["programs"]
    if not isinstance(programs, list) or not programs:
        raise SourceViewError("Empty harness program declaration")
    rendered: list[tuple[str, bytes, int, int]] = []
    seen: set[tuple[str, str, str, int]] = set()
    covered: dict[tuple[str, str, str], list[list[int]]] = {}
    nested_required: set[tuple[str, str, str]] = set()
    nested_covered: set[tuple[str, str, str]] = set()
    shell_count = 0
    for row in programs:
        if set(row) != {"path", "function", "selector", "index", "expression", "origins", "transport", "role", "classification", "sites"}:
            raise SourceViewError("Unknown harness program fields")
        path, function, selector, index = row["path"], row["function"], row["selector"], row["index"]
        if row["classification"] not in {
            "portable-parameterized-source-not-native-input",
            "static-parent-constant-relation-only", "static-shell-body-unobserved",
        }:
            raise SourceViewError("Unknown or private native provenance classification")
        if (selector == "write-text") != (row["transport"] == "windows-write-text-utf8-epoch"):
            raise SourceViewError("Generated Python write epoch transport changed")
        key = path, function, selector, index
        if key in seen:
            raise SourceViewError("Duplicate harness program role")
        seen.add(key)
        if selector == "quoted-heredoc":
            shell_count += 1
            shell_text = dict(inputs)[path].decode("utf-8")
            matches = list(re.finditer(r"<<'PY'\r?\n(.*?)\r?\nPY\r?\n", shell_text, re.S))
            if len(matches) != 1 or index != 0:
                raise SourceViewError("Unknown or additional shell Python producer")
            match = matches[0]
            body = match[1] + "\n"
            first = shell_text[:match.start(1)].count("\n") + 1
            last = first + body.count("\n") - 1
            if _digest(body.encode()) != row["expression"] or row["origins"]:
                raise SourceViewError("Shell Python body changed")
        else:
            functions = [n for n in ast.walk(trees[path]) if isinstance(n, ast.FunctionDef) and n.name == function]
            if len(functions) != 1:
                raise SourceViewError("Ambiguous harness producer function")
            fn = functions[0]
            candidates: list[ast.expr] = []
            if selector.startswith(("assignment:", "nested:")):
                name = selector.partition(":")[2]
                candidates = [n.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
                              and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
            elif selector == "python-c":
                for node in ast.walk(fn):
                    if isinstance(node, (ast.List, ast.Tuple)):
                        for offset, item in enumerate(node.elts[:-1]):
                            if isinstance(item, ast.Constant) and item.value == "-c":
                                candidates.append(node.elts[offset + 1])
            elif selector == "write-text":
                candidates = [n.args[0] for n in ast.walk(fn) if isinstance(n, ast.Call)
                              and isinstance(n.func, ast.Attribute) and n.func.attr == "write_text" and n.args]
            elif selector == "startup":
                for n in ast.walk(fn):
                    if not isinstance(n, (ast.List, ast.Tuple)):
                        continue
                    for i, item in enumerate(n.elts[:-1]):
                        value = n.elts[i + 1]
                        if (
                            isinstance(item, ast.Constant) and item.value == "-c"
                            and isinstance(value, ast.IfExp)
                            and ast.unparse(value.test) == "name == 'startup'"
                        ):
                            candidates.append(value.body)
            else:
                raise SourceViewError("Unknown fixed harness selector")
            if not isinstance(index, int) or not 0 <= index < len(candidates):
                raise SourceViewError("Missing harness expression/epoch")
            expression = candidates[index]
            if _shape(expression) != row["expression"]:
                raise SourceViewError("Harness expression/epoch structure changed")
            expression_key = path, function, _shape(expression)
            if selector.startswith("nested:"):
                nested_covered.add(expression_key)
            else:
                if expression_key in covered:
                    raise SourceViewError("Duplicate emitted body identity")
                if not isinstance(row["sites"], list) or not row["sites"]:
                    raise SourceViewError("Missing emitted callsite/write epoch identities")
                covered[expression_key] = row["sites"]
            values: dict[str, str] = {}
            for name, origin in row["origins"].items():
                if name not in _WITNESSES:
                    raise SourceViewError("Unknown or shadowed fixture origin")
                definition = _harness_binding(fn, name, expression)
                if ast.unparse(definition) != origin:
                    raise SourceViewError("Fixture component relation changed")
                values[name] = _WITNESSES[name]
            if any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id in {"str", "repr"} for n in ast.walk(fn)):
                raise SourceViewError("Shadowed fixture conversion")
            body = _emitted_string(expression, values)
            if not selector.startswith("nested:"):
                try:
                    emitted_tree = _parse(body.encode("utf-8"), path, feature_version=(3, 10))
                except SourceViewError as error:
                    raise SourceViewError(f"{row['role']}: {body!r}: {error}") from error
                if any(isinstance(n, ast.Constant) and n.value == "-c" for n in ast.walk(emitted_tree)):
                    nested_required.add(expression_key)
            if selector.startswith("nested:"):
                parent = _parse(body.encode("utf-8"), path, feature_version=(3, 10))
                nested = [
                    n.elts[i + 1] for n in ast.walk(parent) if isinstance(n, (ast.List, ast.Tuple))
                    for i, item in enumerate(n.elts[:-1])
                    if isinstance(item, ast.Constant) and item.value == "-c"
                ]
                if len(nested) != 1 or not isinstance(nested[0], ast.Constant) or not isinstance(nested[0].value, str):
                    raise SourceViewError("Unknown nested Python producer")
                body = nested[0].value
                leaf = _parse(body.encode("utf-8"), path, feature_version=(3, 10))
                if any(
                    (isinstance(node, ast.Constant) and node.value == "-c")
                    or (
                        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"write_text", "write_bytes"}
                    )
                    for node in ast.walk(leaf)
                ):
                    raise SourceViewError("Unsupported deeper nested Python producer")
                values = {}
            normal = _witness_positions(body, values)
            escaped = {name: value + "'\"\\\n{injection}" for name, value in values.items()}
            escaped_body = body if selector.startswith("nested:") else _emitted_string(expression, escaped)
            if ast.dump(normal) != ast.dump(_witness_positions(escaped_body, escaped)):
                raise SourceViewError("Fixture escaping changed executable AST structure")
            first, last = expression.lineno, expression.end_lineno or expression.lineno
        encoded = body.encode("utf-8")
        if row["transport"] == "windows-write-text-utf8-epoch":
            encoded = encoded.replace(b"\n", b"\r\n")
        elif row["transport"] not in {"unicode-python-c", "quoted-shell-utf8", "nested-parent-constant"}:
            raise SourceViewError("Unknown emitted transport")
        _reject_directives(path, encoded)
        _parse(encoded, path, feature_version=(3, 10))
        rendered.append((path, encoded, first, last))
    if covered != _required_harness_programs(trees, declaration["data_writes"]) or nested_required != nested_covered or shell_count != 1:
        raise SourceViewError("Incomplete/extra harness Python producer, nested body or write epoch")
    return tuple(inputs), tuple(rendered)


def harness_emitted_views(
    root: Path, paths: tuple[str, ...], output: Path,
) -> tuple[HarnessProgramView, ...]:
    """Materialize fixed portable typing witnesses, never actual native-input claims."""
    inputs, programs = harness_emitted_inputs(root, paths)
    members = tuple(sorted(p for p in paths if p.startswith(HARNESS_PREFIX)))
    assets = _harness_assets(root)
    result: list[HarnessProgramView] = []
    try:
        for index, (path, content, first, last) in enumerate(programs):
            target = output / f"harness_program_{index:03}.py"
            identity = _write_view(target, content)
            result.append(HarnessProgramView(
                target, content, inputs,
                tuple(SourceLine(path, first) for _ in content.splitlines()),
                identity, members, assets, (path, first, last),
            ))
    except BaseException:
        for view in result:
            if view.output.exists() and (view.output.stat().st_dev, view.output.stat().st_ino) == view.identity:
                view.output.unlink()
        raise
    return tuple(result)
