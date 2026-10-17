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
_SHARDED_SMOKE_NAME = (
    "${{ matrix.shard_count > 1 && format('{0} ({1}/{2})', "
    "matrix.plugin, matrix.shard_index, matrix.shard_count) || matrix.plugin }}"
)
_NON_CANDIDATE = {
    "pr-gate": "github.event_name == 'pull_request'",
    "coverage-guided-selection-integration": "inputs.promotion_run == ''",
}


def _ci_shards(root: Path, plugins: set[str]) -> tuple[dict[str, int], int]:
    """Read the literal runner policy without executing candidate source."""
    try:
        module = ast.parse(regular(root, Path("tools/run-plugin-tests.py")).read_text("utf-8"))
        declarations = [
            node.value for node in module.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
            and node.target.id == "_CI_SHARD_COUNTS"
        ]
        matrices = [
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "_ci_matrix"
        ]
        if len(declarations) != 1 or len(matrices) != 1:
            raise ValueError("expected one literal shard policy and matrix function")
        defaults = [
            node.args[1] for node in ast.walk(matrices[0])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_CI_SHARD_COUNTS" and node.func.attr == "get"
            and len(node.args) == 2
        ]
        if len(defaults) != 1:
            raise ValueError("expected one literal matrix shard default")
        counts, default = ast.literal_eval(declarations[0]), ast.literal_eval(defaults[0])
        if not isinstance(counts, dict) or any(
            not isinstance(name, str) or not PLUGIN.fullmatch(name) or name not in plugins
            for name in counts
        ):
            raise ValueError("invalid shard consumer")
        if any(type(count) is not int or not 1 <= count <= 256
               for count in [default, *counts.values()]):
            raise ValueError("invalid shard count")
    except (OSError, UnicodeError, SyntaxError, ValueError, TypeError) as error:
        raise CandidateValidationError("canonical CI file-shard policy is unreadable") from error
    return counts, default


def _sharded_smoke_rows(
    root: Path, block: str, discovery: str, plugins: set[str], heavy: str,
) -> list[str]:
    if (
        "matrix: ${{ fromJson(needs.discover.outputs.smoke_matrix) }}" not in block
        or "--shard-index ${{ matrix.shard_index }}" not in block
        or "--shard-count ${{ matrix.shard_count }}" not in block
        or "smoke_matrix: ${{ steps.set.outputs.smoke_matrix }}" not in discovery
        or "--list --ci-matrix" not in discovery
    ):
        raise CandidateValidationError("canonical CI sharded smoke recipe is unavailable")
    counts, default = _ci_shards(root, plugins)
    consumers = sorted(plugins - {heavy})
    if sum(counts.get(name, default) for name in consumers) > 256:
        raise CandidateValidationError("canonical CI file-shard policy exceeds the matrix limit")
    rows = []
    for name in consumers:
        count = counts.get(name, default)
        rows.extend(
            f"{name} ({index}/{count})" if count > 1 else name
            for index in range(1, count + 1)
        )
    return rows


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
        if identifier == "smoke" and name == _SHARDED_SMOKE_NAME:
            rows = _sharded_smoke_rows(root, block, jobs["discover"], plugins, heavy[1])
        elif not substitutions:
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
    return expected, allowed_skips, hashlib.sha256(text.encode("utf-8")).hexdigest()
