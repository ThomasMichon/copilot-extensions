"""Derive required executed rows from the candidate's canonical CI aggregate."""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from materialized_candidate_inventory import (
    PLUGIN, CandidateValidationError, fleet_partitions, python_suites, regular,
)

_MATRIX = re.compile(r"\$\{\{\s*matrix\.([a-z][a-z0-9_]*)(?:\.name)?\s*\}\}")
_NON_CANDIDATE = {
    "pr-gate": "github.event_name == 'pull_request'",
    "coverage-guided-selection-integration": "inputs.promotion_run == ''",
}
_PARTITIONED_SMOKE_STEP = """      - name: Test ${{ matrix.plugin }}
        if: matrix.plugin != 'agent-dispatch' || github.event_name != 'pull_request'
        env:
          PLUGIN: ${{ matrix.plugin }}
          PARTITION: ${{ matrix.partition }}
          PARTITIONS: ${{ matrix.partitions }}
        run: |
          args=()
          if [ "$PARTITIONS" -gt 1 ]; then
            files=$(python tools/ci_plugin_partitions.py files \\
              --plugin "$PLUGIN" --partition "$PARTITION" --partitions "$PARTITIONS")
            mapfile -t args <<< "$files"
          fi
          python tools/run-plugin-tests.py "$PLUGIN" "${args[@]}"
"""


def _smoke_rows(root: Path, plugins: set[str]) -> tuple[list[str], str]:
    """Read candidate scheduling data without executing candidate Python."""
    try:
        text = regular(root, Path("tools/ci_plugin_partitions.py")).read_bytes().decode("utf-8")
        text = text.replace("\r\n", "\n")
        if "\r" in text:
            raise ValueError("unsupported lone carriage return")
        module = ast.parse(text)
        declarations = [
            node.value for node in module.body
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "_SMOKE_PARTITIONS"
                        for target in node.targets)
            ) or (
                isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == "_SMOKE_PARTITIONS"
            )
        ]
        declaration = declarations[0] if len(declarations) == 1 else None
        if not isinstance(declaration, ast.Dict):
            raise ValueError("expected one literal partition mapping")
        keys = [ast.literal_eval(key) for key in declaration.keys]
        counts = ast.literal_eval(declaration)
        if len(keys) != len(set(keys)) or any(
            not isinstance(key, str) or key not in plugins
            or type(count) is not int or not 2 <= count <= 256
            for key, count in counts.items()
        ):
            raise ValueError("invalid or duplicate partition declaration")
        rows = [
            plugin if count == 1 else f"{plugin} (partition {index + 1}/{count})"
            for plugin in sorted(plugins)
            for count in [counts.get(plugin, 1)]
            for index in range(count)
        ]
        if not rows or len(rows) > 256:
            raise ValueError("smoke matrix exceeds the supported job inventory")
    except (OSError, UnicodeError, SyntaxError, ValueError, TypeError) as error:
        raise CandidateValidationError("canonical smoke partition inventory is unreadable") from error
    return rows, text


def required_jobs(root: Path) -> tuple[set[str], set[str], str]:
    """Reject unaccounted jobs/matrices instead of shrinking release coverage."""
    payload = regular(root, Path(".github/workflows/ci.yml")).read_bytes()
    text = payload.decode("utf-8").replace("\r\n", "\n")
    if "\r" in text:
        raise CandidateValidationError("canonical CI source has an unsupported lone carriage return")
    parts = re.split(r"(?m)^  ([a-z][a-z0-9_-]*):\n", text.split("\njobs:\n", 1)[-1])
    jobs = dict(zip(parts[1::2], parts[2::2]))
    if not jobs or len(jobs) != len(parts[1::2]):
        raise CandidateValidationError("canonical CI job inventory is absent or ambiguous")
    gate = jobs.get("promotion-gate", "")
    dependencies = re.search(r"(?m)^    needs:\n((?:      - [a-z][a-z0-9_-]*\n)+)", gate)
    if dependencies is None:
        raise CandidateValidationError("canonical CI promotion aggregate has no literal portfolio")
    needs = re.findall(r"(?m)^      - ([a-z][a-z0-9_-]*)$", dependencies[1])
    if len(needs) != len(set(needs)) or not {"checks", "discover", "smoke", "full"} <= set(needs):
        raise CandidateValidationError("canonical CI promotion portfolio is incomplete or duplicated")
    if set(jobs) != set(needs) | {"promotion-gate", *_NON_CANDIDATE}:
        raise CandidateValidationError("canonical CI has jobs outside its qualification portfolio")
    heavy = re.search(r"(?m)^  HEAVY_PLUGIN: ([a-z][a-z0-9-]*)$", text)
    plugins = python_suites(root)
    if heavy is None or heavy[1] not in plugins:
        raise CandidateValidationError("canonical CI heavy-plugin declaration is unavailable")
    expected: set[str] = set()
    allowed_skips: set[str] = set()
    digest = hashlib.sha256(text.encode("utf-8"))
    for identifier in [*needs, "promotion-gate", *_NON_CANDIDATE]:
        block = jobs.get(identifier)
        if block is None:
            raise CandidateValidationError(f"canonical CI required job is absent: {identifier}")
        declared = re.search(r"(?m)^    name: ([^\n]+)$", block)
        name = declared[1] if declared else identifier
        if identifier in _NON_CANDIDATE:
            if _NON_CANDIDATE[identifier] not in block or _MATRIX.search(name):
                raise CandidateValidationError("non-candidate CI job no longer has its explicit scope")
            allowed_skips.add(name)
            continue
        substitutions = list(_MATRIX.finditer(name))
        if not substitutions:
            if "\n      matrix:" in block or "${{" in name:
                raise CandidateValidationError(f"unsupported CI job name/matrix: {identifier}")
            rows = [name]
        elif len(substitutions) == 1:
            expression = substitutions[0]
            axis = expression[1]
            if identifier == "smoke" and axis == "plugin":
                if "plugin: ${{ fromJson(needs.discover.outputs.smoke) }}" not in block:
                    raise CandidateValidationError("canonical smoke job no longer uses discovered suites")
                values = sorted(plugins - {heavy[1]})
            elif identifier == "smoke" and axis == "name":
                if (
                    name != "${{ matrix.name }}"
                    or not re.search(
                        r"(?m)^      matrix:\n"
                        r"        include: \$\{\{ fromJson\(needs\.discover\.outputs\.smoke_matrix\) \}\}\n"
                        r"(?=    \S)", block,
                    )
                    or 'SMOKE_MATRIX=$(python tools/ci_plugin_partitions.py matrix "$SMOKE")'
                    not in jobs["discover"]
                    or 'echo "smoke_matrix=$SMOKE_MATRIX" >> "$GITHUB_OUTPUT"'
                    not in jobs["discover"]
                    or re.findall(
                        r"(?ms)^      - name: Test \$\{\{ matrix\.plugin \}\}\n.*?(?=^      - |\Z)",
                        block,
                    ) != [_PARTITIONED_SMOKE_STEP]
                ):
                    raise CandidateValidationError("canonical smoke job no longer uses discovered partitions")
                values, partition_source = _smoke_rows(root, plugins - {heavy[1]})
                digest.update(b"\0tools/ci_plugin_partitions.py\0")
                digest.update(partition_source.encode("utf-8"))
            elif identifier == "full" and axis == "plugin":
                entries = re.search(r"(?m)^        plugin:\n((?:          - [a-z][a-z0-9-]*\n)+)", block)
                values = re.findall(r"(?m)^          - ([a-z][a-z0-9-]*)$", entries[1]) if entries else []
                if (
                    not values or len(values) != len(set(values)) or heavy[1] not in values
                    or any(not PLUGIN.fullmatch(value) or value not in plugins for value in values)
                ):
                    raise CandidateValidationError("canonical full-suite matrix is incomplete")
            elif identifier in ("fleet-native-linux", "fleet-native-windows") and axis == "partition":
                values = list(fleet_partitions(root))
            else:
                raise CandidateValidationError(f"unsupported canonical CI matrix: {identifier}")
            rows = [name[:expression.start()] + value + name[expression.end():] for value in values]
        else:
            raise CandidateValidationError(f"ambiguous canonical CI matrix name: {identifier}")
        if not rows or expected.intersection(rows) or len(set(rows)) != len(rows):
            raise CandidateValidationError("canonical CI qualification rows are empty or duplicated")
        expected.update(rows)
    return expected, allowed_skips, digest.hexdigest()
