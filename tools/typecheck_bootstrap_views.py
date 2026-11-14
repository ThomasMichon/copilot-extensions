"""Canonical bootstrap typing, with explicit materialized-consumer correspondence."""
from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path
import re

from tools.typecheck_catalog_views import (
    DECLARATION, MEMBERS, PRODUCER, _binding, _membership, _pairs, catalog_inputs,
)
from tools.typecheck_source_views import (
    SourceLine, SourceView, SourceViewError, _parse, _read, _reject_directives,
    _shape, _write_view,
)

MANIFEST = "plugins/agent-worktrees/payload-invocation.json"
CONSUMER = "plugins/agent-worktrees/scripts/emit-command-catalog.ps1"
HELPER = "plugins/agent-worktrees/scripts/command_binding.py"
TEMPLATE = "libs/payload-invocation/templates/catalog-powershell.tmpl"
INPUTS = tuple(dict.fromkeys((*MEMBERS, DECLARATION, MANIFEST, TEMPLATE)))
MATERIALIZED_INPUTS = (*INPUTS, CONSUMER, HELPER)
SELECTOR_TEMPLATES = tuple(
    "libs/payload-invocation/templates/" + name + "-shim.tmpl"
    for name in ("powershell", "posix")
)
SELECTOR_HELPER = "libs/agent-procutil/src/agent_procutil/python_cache.py"
SELECTOR_PACKAGE = "libs/agent-procutil/src/agent_procutil/__init__.py"
SELECTOR_INPUTS = (*MEMBERS, DECLARATION, *SELECTOR_TEMPLATES, SELECTOR_HELPER, SELECTOR_PACKAGE)
_SELECTOR_TRANSPORT = (
    (
        "function Select-PythonCache([string]$Python) {\n    $selector = @'\n",
        "\n'@\n    try {\n"
        '        $selected = & $Python -I -B -X utf8 -c $selector $_runtimeRoot\n'
        "        if ($LASTEXITCODE -eq 0 -and @($selected).Count -eq 1) {\n"
        "            $cacheArguments = ConvertFrom-Json -InputObject ([string]$selected)\n"
        "            if ($cacheArguments.Count -eq 1 -and $cacheArguments[0] -ceq '-B') { return '-B' }\n"
        "            if ($cacheArguments.Count -eq 2 -and $cacheArguments[0] -ceq '-X' -and\n"
        "                $cacheArguments[1] -is [string] -and\n"
        "                $cacheArguments[1].StartsWith('pycache_prefix=') -and\n"
        '                $cacheArguments[1].Length -gt 15 -and $cacheArguments[1] -notmatch "[`r`n]" -and\n'
        "                $cacheArguments[1].IndexOf([char]0) -lt 0) {\n"
        "                return $cacheArguments\n"
        "            }\n        }\n"
        "    } catch {}\n    return '-B'\n}",
    ),
    (
        '        _selector_root="$_runtime_root"\n'
        '        case "$AGENT_RT_PY" in\n'
        '            *.exe) _selector_root="$(cygpath -aw "$_runtime_root")" || _selector_root="" ;;\n'
        "        esac\n"
        '        if [ -n "$_selector_root" ]; then\n'
        '            _selected_cache="$("$AGENT_RT_PY" -I -B -X utf8 -c \'',
        '\' "$_selector_root" 2>/dev/null)" || _selected_cache=""\n'
        '            case "$_selected_cache" in\n'
        "                '-X\npycache_prefix='*)\n"
        '                    _python_cache_option="-X"\n'
        '                    _python_cache_value="${_selected_cache#*\n}" ;;\n'
        "            esac\n        fi\n",
    ),
)


@dataclass(frozen=True)
class ShimSelectorView(SourceView):
    members: tuple[str, ...]

    def verify(self, root: Path) -> None:
        if _membership(root) != self.members:
            raise SourceViewError("Shim selector canonical source membership changed")
        self._verify_bytes(root)


def powershell_selector_view(root: Path, paths: tuple[str, ...], output: Path) -> ShimSelectorView:
    """Expose the actual single-quoted PowerShell here-string without rewriting it."""
    return _selector_view(root, paths, output, 0)


def posix_selector_view(root: Path, paths: tuple[str, ...], output: Path) -> ShimSelectorView:
    """Expose the actual shell single-quoted -c argument, including its newlines."""
    return _selector_view(root, paths, output, 1)


def _selector_view(
    root: Path, paths: tuple[str, ...], output: Path, index: int,
) -> ShimSelectorView:
    inputs, _flight, _template, members = catalog_inputs(root, paths)
    if not {SELECTOR_HELPER, SELECTOR_PACKAGE} <= set(paths):
        raise SourceViewError("Shim selectors require their actual inventoried cache helper")
    sources = dict(inputs)
    sources.update((name, _read(root, name)) for name in (SELECTOR_HELPER, SELECTOR_PACKAGE))
    template_path = SELECTOR_TEMPLATES[index]
    template = sources[template_path].decode("utf-8").replace("\r\n", "\n")
    prefix, suffix = _SELECTOR_TRANSPORT[index]
    matches = list(re.finditer(re.escape(prefix) + r"(.*?)" + re.escape(suffix), template, re.DOTALL))
    if (
        len(matches) != 1
        or template.count(prefix) != 1 or template.count(suffix) != 1
        or template.count("from agent_procutil.python_cache import POLICY_VERSION, policy_arguments") != 1
        or (index == 0 and template.count("$selector") != 2)
    ):
        raise SourceViewError(f"{template_path}: shim selector quote/argv transport changed")
    match = matches[0]
    body = match.group(1)
    if (
        "@@" in body
        or (index == 0 and any(line.startswith("'@") for line in body.splitlines()))
        or (index == 1 and "'" in body)
    ):
        raise SourceViewError(f"{template_path}: shim selector interpolation/quoting changed")
    content = body.encode("utf-8")
    tree = _parse(content, template_path, feature_version=(3, 10))
    expected_import = ast.parse(
        "from agent_procutil.python_cache import POLICY_VERSION, policy_arguments",
    ).body[0]
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
               and n.module == "agent_procutil.python_cache"]
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "policy_arguments"]
    versions = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)
                and _shape(n) == _shape(ast.parse("assert POLICY_VERSION == 2").body[0])]
    expected_call = ast.parse(
        "policy_arguments(sys.argv[1], runtime=sys.executable)", mode="eval",
    ).body
    if (
        len(imports) != 1 or _shape(imports[0]) != _shape(expected_import)
        or len(calls) != 1 or _shape(calls[0]) != _shape(expected_call) or len(versions) != 1
        or any(isinstance(n, ast.Name) and n.id in {"policy_arguments", "POLICY_VERSION"}
               and isinstance(n.ctx, (ast.Store, ast.Del)) for n in ast.walk(tree))
    ):
        raise SourceViewError(f"{template_path}: shim selector cache-helper call changed")
    _reject_directives(template_path, content)
    first = template[:match.start(1)].count("\n") + 1
    identity = _write_view(output, content)
    return ShimSelectorView(
        output, content, tuple(sources.items()),
        tuple(SourceLine(template_path, first + n) for n in range(len(content.splitlines()))),
        identity, members,
    )


def require_bootstrap_version(level: str, versions: tuple[str, ...]) -> None:
    if level == "strict" and "3.11" not in versions:
        raise SourceViewError("Bootstrap admission requires the persistent Python 3.11 body analysis")


def _literal(quoted: str) -> str:
    if not quoted.startswith("'") or not quoted.endswith("'"):
        raise SourceViewError("Bootstrap must use one PowerShell single-quoted literal")
    value = quoted[1:-1]
    decoded = value.replace("''", "'")
    if "'" + decoded.replace("'", "''") + "'" != quoted:
        raise SourceViewError("Bootstrap PowerShell quote transport changed")
    return decoded


@dataclass(frozen=True)
class BootstrapProgramView(SourceView):
    members: tuple[str, ...]
    producer_range: tuple[int, int]
    materialized: bool

    def verify(self, root: Path) -> None:
        if _membership(root) != self.members:
            raise SourceViewError("Bootstrap canonical source membership changed")
        self._verify_bytes(root)

    def diagnostics(self, text: str, root: Path) -> str:
        result = []
        for line in text.splitlines(keepends=True):
            mapped = super().diagnostics(line, root)
            if mapped != line:
                ending = "\n" if mapped.endswith("\n") else ""
                context = "materialized consumer" if self.materialized else "canonical source"
                mapped = mapped.rstrip("\r\n") + (
                    f" [bootstrap {context}; producer {PRODUCER}:{self.producer_range[0]}-"
                    f"{self.producer_range[1]}; full body requires Python 3.11]" + ending
                )
            result.append(mapped)
        return "".join(result)


def bootstrap_view(root: Path, paths: tuple[str, ...], output: Path) -> BootstrapProgramView:
    """Type the actual canonical program without reading derived Dev mirrors."""
    return _bootstrap_view(root, paths, output, materialized=False)


def materialized_bootstrap_view(
    root: Path, paths: tuple[str, ...], output: Path,
) -> BootstrapProgramView:
    """Explicitly bind an already-materialized consumer; never synthesize it here."""
    return _bootstrap_view(root, paths, output, materialized=True)


def _bootstrap_view(
    root: Path, paths: tuple[str, ...], output: Path, *, materialized: bool,
) -> BootstrapProgramView:
    catalog_sources, _flight, _template, members = catalog_inputs(root, paths)
    sources = dict(catalog_sources)
    sources[MANIFEST] = _read(root, MANIFEST)
    record = json.loads(sources[MANIFEST], object_pairs_hook=_pairs)
    if (
        not isinstance(record, dict) or record.get("command") != "agent-worktrees"
        or record.get("module") != "agent_worktrees" or record.get("version") != 2
        or record.get("catalogBinding") is not True or "commands" in record
        or record.get("inProcessCatalog", False) or record.get("catalogGate", "")
        or record.get("catalogBindingModule") != "src/agent_worktrees/_command_binding.py"
    ):
        raise SourceViewError("Bootstrap consumer selection changed")
    plugin = record["command"]
    if not isinstance(plugin, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", plugin):
        raise SourceViewError("Bootstrap plugin is not a safe Python string-literal value")
    runtime_root = record.get("legacyRuntimeRoot")
    if not isinstance(runtime_root, str) or not re.fullmatch(r"\.[a-z0-9-]+", runtime_root):
        raise SourceViewError("Bootstrap legacy-root data slot changed")
    tree = _parse(sources[PRODUCER], PRODUCER)
    render = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render")
    values = _binding(render.body, "values")
    assert isinstance(values, ast.Dict)
    replacement = next(
        value for key, value in zip(values.keys, values.values)
        if isinstance(key, ast.Constant) and key.value == "CATALOG_BINDING_POWERSHELL"
    )
    if (
        not isinstance(replacement, ast.IfExp)
        or _shape(replacement.test) != _shape(ast.parse('data["catalogBinding"]', mode="eval").body)
        or not isinstance(replacement.orelse, ast.Constant) or replacement.orelse.value != ""
        or not isinstance(replacement.body, ast.JoinedStr)
    ):
        raise SourceViewError("Bootstrap producer selection or string composition changed")
    block = ""
    slots = {"plugin": 0, "runtimeRoot": 0}
    for part in replacement.body.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            block += part.value
        elif (
            isinstance(part, ast.FormattedValue) and part.conversion == -1
            and part.format_spec is None
        ):
            for name, value in (("plugin", plugin), ("runtimeRoot", runtime_root)):
                if _shape(part.value) == _shape(ast.parse(f"data[{name!r}]", mode="eval").body):
                    block += value
                    slots[name] += 1
                    break
            else:
                raise SourceViewError("Unknown bootstrap data slot")
        else:
            raise SourceViewError("Unknown bootstrap interpolation, conversion or format")
    if slots != {"plugin": 5, "runtimeRoot": 1}:
        raise SourceViewError("Bootstrap plugin-slot membership changed")
    template = sources[TEMPLATE].decode("utf-8")
    if template.count("@@CATALOG_BINDING_POWERSHELL@@") != 1:
        raise SourceViewError("Bootstrap template slot membership changed")
    slot = "@@CATALOG_BINDING_POWERSHELL@@@@CATALOG_FLIGHT_POWERSHELL@@"
    if not any(line.startswith(slot + "$catalogJson = ") for line in template.splitlines()):
        raise SourceViewError("Bootstrap template insertion transport changed")
    expected_launches = [line for line in block.splitlines() if "-c $bindingBootstrap " in line]
    arguments = (
        "-I -B -E -X utf8 -c $bindingBootstrap $bindingHelper --payload-root $selfRoot "
        f"--plugin-id '{plugin}' --legacy-root $legacyRoot --flight-check"
    )
    if (
        len(expected_launches) != 2 or block.count("$bindingBootstrap") != 3
        or expected_launches != [
            f"    $candidate = @(& $bindingLauncher.Source -3 {arguments})",
            f"            $response = @(& $candidate.Source {arguments})",
        ]
        or block.count("$bindingHelper = Join-Path $selfRoot 'scripts\\command_binding.py'\n") != 1
    ):
        raise SourceViewError("Bootstrap argv/helper transport changed")
    assignments = [line for line in block.splitlines() if line.startswith("$bindingBootstrap = ")]
    if len(assignments) != 1:
        raise SourceViewError("Bootstrap assignment membership changed")
    assignment = assignments[0]
    program = _literal(assignment.removeprefix("$bindingBootstrap = "))
    if "\n" in program or "\r" in program:
        raise SourceViewError("Bootstrap is not the original single-line Python transport")
    parsed = _parse(program.encode(), PRODUCER, feature_version=(3, 11))
    guard = ast.parse("sys.exit(124) if sys.version_info < (3,11) else None").body[0]
    tail = ast.parse("sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')").body
    if (
        len(parsed.body) < 4 or _shape(parsed.body[1]) != _shape(guard)
        or [_shape(n) for n in parsed.body[-2:]] != [_shape(n) for n in tail]
        or _shape(parsed.body[0]) != _shape(ast.parse("import os,runpy,sys,time").body[0])
    ):
        raise SourceViewError("Bootstrap interpreter guard or real helper forwarding changed")
    _reject_directives(PRODUCER, program.encode())
    authored = sources[PRODUCER].decode("utf-8").splitlines()
    starts = [n for n, line in enumerate(authored, 1) if '"$bindingBootstrap = ' in line]
    ends = [n for n, line in enumerate(authored, 1) if "runpy.run_path(sys.argv[0]" in line]
    if len(starts) != 1 or len(ends) != 1 or not starts[0] <= ends[0]:
        raise SourceViewError("Bootstrap producer span is ambiguous")
    origin = SourceLine(PRODUCER, starts[0])
    if materialized:
        sources.update((name, _read(root, name)) for name in (CONSUMER, HELPER))
        if sources[HELPER] != sources[MEMBERS[0]]:
            raise SourceViewError("Bootstrap helper is not the actual canonical binding source")
        consumer = sources[CONSUMER].decode("utf-8").replace("\r\n", "\n")
        if consumer.count(block) != 1 or consumer.count("$bindingBootstrap") != 3:
            raise SourceViewError("Actual bootstrap consumer or two launch sites changed")
        if any(consumer.count(line) != 1 for line in expected_launches):
            raise SourceViewError("Materialized bootstrap argv/helper transport changed")
        consumer_lines = [n for n, line in enumerate(consumer.splitlines(), 1) if line == assignment]
        if len(consumer_lines) != 1:
            raise SourceViewError("Bootstrap consumer source line is ambiguous")
        origin = SourceLine(CONSUMER, consumer_lines[0])
    content = (program + "\n").encode()
    identity = _write_view(output, content)
    return BootstrapProgramView(
        output, content, tuple(sources.items()),
        (origin,), identity, members, (starts[0], ends[0]), materialized,
    )
