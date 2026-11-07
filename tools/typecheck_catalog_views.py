"""Closed, source-bound typing witnesses for the in-process command catalog."""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re

from tools.typecheck_source_views import (
    SourceLine, SourceView, SourceViewError, _parse, _read, _reject_directives,
    _shape, _write_view,
)

PREFIX = "libs/payload-invocation/"
PRODUCER = PREFIX + "generate.py"
TEMPLATE = PREFIX + "templates/catalog-python.tmpl"
DECLARATION = PREFIX + "python-catalog-inputs.json"
MEMBERS = tuple(PREFIX + name for name in (
    "command_binding.py", "command_lifecycle.py", "generate.py", "runtime_admission.py",
))
TOKENS = (
    "CATALOG_CONTRACT_JSON", "CATALOG_FILE_SHA256", "CATALOG_PLUGIN_PYTHON",
    "CATALOG_SPECS_PYTHON", "CATALOG_INSTALLER_PYTHON", "CATALOG_WINDOWS_SUFFIX_PYTHON",
    "CATALOG_WINDOWS_SHELL_PYTHON", "CATALOG_MULTI_PYTHON", "CATALOG_FLIGHT_IN_PROCESS",
)
_REGEXES = {
    "_PLUGIN": r"^[a-z][a-z0-9-]*$", "_COMMAND": r"^[a-z][a-z0-9-]*$",
    "_MODULE": r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$",
    "_RUNTIME_ROOT": r"^\.[a-z0-9-]+$", "_PURPOSE": r"^[A-Za-z0-9 ._/-]+$",
    "_OUTPUT_DIR": r"^[a-z0-9][a-z0-9_./-]*$", "_INSTALLER": r"^[a-z][a-z0-9-]*$",
}
_DATA = {
    "CATALOG_PLUGIN_PYTHON": 'repr(data["plugin"])',
    "CATALOG_SPECS_PYTHON": "repr(catalog_specs)",
    "CATALOG_INSTALLER_PYTHON": 'repr(data["installer"])',
    "CATALOG_WINDOWS_SUFFIX_PYTHON": "repr(windows_catalog_suffix)",
    "CATALOG_WINDOWS_SHELL_PYTHON": "repr(windows_catalog_shell)",
    "CATALOG_MULTI_PYTHON": 'repr(data["multiCommandManifest"])',
}


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise SourceViewError("Duplicate catalog declaration field")
        result[key] = value
    return result


def _membership(root: Path) -> tuple[str, ...]:
    directory = root / PREFIX
    return tuple(sorted(
        p.relative_to(root).as_posix()
        for p in (*directory.glob("*.py"), *(directory / "templates").glob("*.tmpl"))
    ))


def _binding(body: list[ast.stmt], name: str) -> ast.expr:
    definitions = [
        n for n in body if isinstance(n, ast.Assign)
        and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == name
    ]
    stores = [n for statement in body for n in ast.walk(statement)
              if isinstance(n, ast.Name) and n.id == name
              and isinstance(n.ctx, (ast.Store, ast.Del))]
    if len(definitions) != 1 or len(stores) != 1:
        raise SourceViewError(f"Ambiguous catalog producer binding: {name}")
    return definitions[0].value


def _flight(expression: ast.expr, root_value: str) -> tuple[str, tuple[int, ...]]:
    if not isinstance(expression, ast.JoinedStr):
        raise SourceViewError("Catalog flight must be the closed string expression")
    text = ""
    origins: list[int] = []
    formatted = 0
    for part in expression.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            value = part.value
        elif (
            isinstance(part, ast.FormattedValue) and part.conversion == -1
            and part.format_spec is None
            and _shape(part.value) == _shape(ast.parse("data['runtimeRoot']", mode="eval").body)
        ):
            formatted += 1
            value = root_value
        else:
            raise SourceViewError("Unsupported catalog flight interpolation")
        text += value
    if formatted != 1 or not re.fullmatch(_REGEXES["_RUNTIME_ROOT"], root_value):
        raise SourceViewError("Catalog flight requires exactly one validated runtimeRoot")
    # Adjacent Python literals share an AST span; retain that complete authored range.
    first, last = expression.lineno, expression.end_lineno or expression.lineno
    origins.extend(first for _line in text.splitlines())
    if not first <= last or not text.endswith("\n"):
        raise SourceViewError("Catalog flight transport changed")
    return text, tuple(origins)


@dataclass(frozen=True)
class CatalogProgramView(SourceView):
    members: tuple[str, ...]
    flight_range: tuple[int, int]

    def verify(self, root: Path) -> None:
        if _membership(root) != self.members:
            raise SourceViewError("Catalog producer/template membership changed")
        self._verify_bytes(root)

    def diagnostics(self, text: str, root: Path) -> str:
        result = []
        for line in text.splitlines(keepends=True):
            mapped = super().diagnostics(line, root)
            if mapped != line and mapped.startswith(PRODUCER + ":"):
                ending = "\n" if mapped.endswith("\n") else ""
                mapped = mapped.rstrip("\r\n") + (
                    f" [catalog flight expression range {self.flight_range[0]}-"
                    f"{self.flight_range[1]}; portable typing witness]" + ending
                )
            result.append(mapped)
        return "".join(result)


def catalog_inputs(
    root: Path, paths: tuple[str, ...],
) -> tuple[tuple[tuple[str, bytes], ...], ast.expr, str, tuple[str, ...]]:
    """Validate the fixed normal producer without importing or executing it."""
    members = _membership(root)
    if set(members) != {
        *MEMBERS,
        *(PREFIX + "templates/" + name for name in (
            "catalog-multi-posix.tmpl", "catalog-multi-powershell.tmpl",
            "catalog-posix-shell.tmpl", "catalog-posix.tmpl", "catalog-powershell.tmpl",
            "catalog-python.tmpl", "cmd-shim.tmpl", "dispatcher-posix.tmpl",
            "dispatcher-powershell.tmpl", "posix-shim.tmpl", "powershell-shim.tmpl",
        )),
    } or not set(MEMBERS) <= set(paths):
        raise SourceViewError("Catalog source membership differs from owned inventory")
    inputs = tuple((path, _read(root, path)) for path in (*members, DECLARATION))
    sources = dict(inputs)
    declaration = json.loads(sources[DECLARATION], object_pairs_hook=_pairs)
    if not isinstance(declaration, dict) or set(declaration) != {"kind", "module", "functions"}:
        raise SourceViewError("Invalid closed catalog declaration")
    if declaration["kind"] != "in-process-catalog-v1":
        raise SourceViewError("Unknown catalog recipe")
    tree = _parse(sources[PRODUCER], PRODUCER)
    if declaration["module"] != _shape(tree):
        raise SourceViewError("Catalog module/validator/renderer binding changed")
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    if len(functions) != sum(isinstance(n, ast.FunctionDef) for n in tree.body):
        raise SourceViewError("Duplicate catalog producer function")
    shapes = declaration["functions"]
    if not isinstance(shapes, dict) or set(shapes) != {"_load_command", "load_manifest", "render", "expected_files"}:
        raise SourceViewError("Incomplete catalog producer/validator binding")
    for name, shape in shapes.items():
        if name not in functions or _shape(functions[name]) != shape:
            raise SourceViewError(f"Catalog producer/validator structure changed: {name}")
    for name, pattern in _REGEXES.items():
        expected = ast.parse(f"re.compile({pattern!r})", mode="eval").body
        if _shape(_binding(tree.body, name)) != _shape(expected):
            raise SourceViewError(f"Catalog validator changed: {name}")
    if _shape(_binding(tree.body, "_WINDOWS_CATALOG_SHIMS")) != _shape(
        ast.parse("{'powershell', 'cmd'}", mode="eval").body
    ):
        raise SourceViewError("Catalog shim enum changed")
    body = functions["render"].body
    values = _binding(body, "values")
    if not isinstance(values, ast.Dict):
        raise SourceViewError("Catalog renderer requires its fixed values table")
    replacements: dict[str, ast.expr] = {}
    for key, value in zip(values.keys, values.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str) or key.value in replacements:
            raise SourceViewError("Ambiguous renderer replacement key")
        replacements[key.value] = value
    for token_name, expression in _DATA.items():
        if token_name not in replacements or _shape(replacements[token_name]) != _shape(ast.parse(expression, mode="eval").body):
            raise SourceViewError(f"Catalog data conversion changed: {token_name}")
    flight = _binding(body, "flight_python")
    _flight(flight, ".agent-example")
    transport = ast.parse(
        '"\\n".join("    " + line if line else "" for line in flight_python.splitlines()) + "\\n"',
        mode="eval",
    ).body
    if _shape(replacements.get("CATALOG_FLIGHT_IN_PROCESS", ast.Constant(None))) != _shape(transport):
        raise SourceViewError("Catalog flight indentation transport changed")
    template = sources[TEMPLATE].decode("utf-8")
    observed = Counter(re.findall(r"@@([^@\r\n]+)@@", template))
    if observed != Counter({name: 1 for name in TOKENS}):
        raise SourceViewError("Unknown, missing or duplicate catalog template token")
    for token, prefix in (
        ("CATALOG_CONTRACT_JSON", "# payload-command-catalog-contract: "),
        ("CATALOG_FILE_SHA256", "# payload-command-catalog-sha256: "),
    ):
        if prefix + "@@" + token + "@@" not in template.splitlines():
            raise SourceViewError("Catalog metadata must remain a complete comment line")
    _reject_directives(TEMPLATE, sources[TEMPLATE])
    return inputs, flight, template, members


def catalog_views(
    root: Path, paths: tuple[str, ...], output: Path,
) -> tuple[CatalogProgramView, ...]:
    """Compose four public data witnesses with actual source line custody."""
    inputs, flight, template, members = catalog_inputs(root, paths)
    result: list[CatalogProgramView] = []
    try:
        for multi in (False, True):
            for shim in ("powershell", "cmd"):
                specs = [{"id": "agent-example", "relativePath": "bin/agent-example",
                          "purpose": "Exercise an example runtime"}]
                if multi:
                    specs.append({"id": "example-helper", "relativePath": "bin/example-helper",
                                  "purpose": "Exercise an example helper"})
                values = {
                    "CATALOG_PLUGIN_PYTHON": repr("agent-example"),
                    "CATALOG_SPECS_PYTHON": repr(specs),
                    "CATALOG_INSTALLER_PYTHON": repr("install"),
                    "CATALOG_WINDOWS_SUFFIX_PYTHON": repr(".cmd" if shim == "cmd" else ".ps1"),
                    "CATALOG_WINDOWS_SHELL_PYTHON": repr("cmd" if shim == "cmd" else "direct"),
                    "CATALOG_MULTI_PYTHON": repr(multi),
                    "CATALOG_CONTRACT_JSON": "{}",
                    "CATALOG_FILE_SHA256": "0" * 64,
                }
                code, origins = _flight(flight, ".agent-example")
                chunks: list[str] = []
                lines: list[SourceLine] = []
                for number, line in enumerate(template.splitlines(), 1):
                    if line == "@@CATALOG_FLIGHT_IN_PROCESS@@":
                        for text, origin in zip(code.splitlines(), origins):
                            chunks.append(("    " + text if text else "") + "\n")
                            lines.append(SourceLine(PRODUCER, origin))
                    else:
                        if "@@CATALOG_FLIGHT_IN_PROCESS@@" in line:
                            raise SourceViewError("Catalog flight must occupy its own line")
                        for key, value in values.items():
                            line = line.replace("@@" + key + "@@", value)
                        chunks.append(line + "\n")
                        lines.append(SourceLine(TEMPLATE, number))
                content = "".join(chunks).encode("utf-8")
                if b"@@" in content:
                    raise SourceViewError("Residual catalog placeholder")
                _reject_directives(TEMPLATE, content)
                parsed = _parse(content, TEMPLATE, feature_version=(3, 10))
                assignments = {n.targets[0].id: n.value for n in parsed.body
                               if isinstance(n, ast.Assign) and len(n.targets) == 1
                               and isinstance(n.targets[0], ast.Name)}
                for name, token in (
                    ("_PLUGIN", "CATALOG_PLUGIN_PYTHON"), ("_SPECS", "CATALOG_SPECS_PYTHON"),
                    ("_INSTALLER", "CATALOG_INSTALLER_PYTHON"),
                    ("_WINDOWS_SUFFIX", "CATALOG_WINDOWS_SUFFIX_PYTHON"),
                    ("_WINDOWS_SHELL", "CATALOG_WINDOWS_SHELL_PYTHON"),
                    ("_MULTI", "CATALOG_MULTI_PYTHON"),
                ):
                    if name not in assignments or _shape(assignments[name]) != _shape(ast.parse(values[token], mode="eval").body):
                        raise SourceViewError("Catalog data escaped its literal assignment")
                target = output / f"catalog_{'multi' if multi else 'single'}_{shim}.py"
                identity = _write_view(target, content)
                result.append(CatalogProgramView(
                    target, content, inputs, tuple(lines), identity, members,
                    (flight.lineno, flight.end_lineno or flight.lineno),
                ))
    except BaseException:
        for view in result:
            if view.output.exists() and (view.output.stat().st_dev, view.output.stat().st_ino) == view.identity:
                view.output.unlink()
        raise
    return tuple(result)
