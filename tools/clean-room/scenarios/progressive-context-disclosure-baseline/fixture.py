#!/usr/bin/env python3
"""Validate and render the frozen progressive-context-disclosure fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import secrets
import shutil
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
TOKEN_HEX_LENGTH = 48
CANARY_PATTERN = re.compile(
    r"PCD_CANARY_([A-Z0-9_]+)_([0-9a-f]{48})"
)
PHASE2_ASSEMBLIES = {"flat-fragments", "flat-with-index"}
RUNNABLE_BOUNDARIES = {"fresh", "spill"}
REFERENCE_CODES = {
    "markdown-link": "md",
    "backtick-repository-relative": "repo",
    "backtick-payload-relative": "payload",
    "backtick-absolute-contained": "absolute",
    "bare-labeled-path": "bare",
    "html-comment-locator": "comment",
    "structured-reference": "structured",
}
EMPHASIS_CODES = {
    "optional": "optional",
    "conditional": "conditional",
    "imperative": "imperative",
    "safety-gated": "gated",
}
ASSEMBLY_CODES = {
    "flat-fragments": "flat",
    "flat-with-index": "index",
}
MATERIALIZED_ROOT = PurePosixPath(
    "/home/operator/progressive-context-disclosure-eval"
)
MATERIALIZED_REPOSITORY = MATERIALIZED_ROOT / "repository"
MATERIALIZED_PAYLOAD = (
    MATERIALIZED_ROOT / "payload" / "synthetic-progressive-context"
)
BASELINE_COORDINATES = {
    "current-full-inline": {
        "deferralLevel": "F0",
        "referenceRepresentation": "none",
        "emphasis": "inline",
        "assembly": "flat-fragments",
    },
    "current-concise-kernel": {
        "deferralLevel": "F2",
        "referenceRepresentation": "backtick-repository-relative",
        "emphasis": "conditional",
        "assembly": "flat-fragments",
    },
}


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _stable_canary(guide_id: str) -> str:
    digest = hashlib.sha256(guide_id.encode("utf-8")).hexdigest()
    label = guide_id.upper().replace("-", "_")
    return f"PCD_CANARY_{label}_{digest[:TOKEN_HEX_LENGTH]}"


def _metrics(value: str) -> dict[str, object]:
    return {
        "unicodeCharacters": len(value),
        "utf8Bytes": len(value.encode("utf-8")),
        "estimatedTokens": math.ceil(len(value) / 4),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }


def _guide_text(guide: dict[str, object]) -> str:
    body = str(guide["body"]).replace(
        "{{CANARY}}", _stable_canary(str(guide["id"]))
    )
    return (
        f"### Guide: {guide['id']}\n"
        f"Applicability: {guide['applicability']}\n\n{body}"
    )


def _random_canary(guide_id: str) -> str:
    label = guide_id.upper().replace("-", "_")
    return f"PCD_CANARY_{label}_{secrets.token_hex(24)}"


def _guide_text_with_canary(
    guide: dict[str, object], canary: str
) -> str:
    body = str(guide["body"]).replace("{{CANARY}}", canary)
    return (
        f"### Guide: {guide['id']}\n"
        f"Applicability: {guide['applicability']}\n\n{body}"
    )


def _task_by_id(task_id: str) -> dict[str, object]:
    tasks = _load("tasks.json")
    assert isinstance(tasks, dict)
    for task in tasks["tasks"]:
        if task["id"] == task_id:
            return task
    raise ValueError(f"unknown task: {task_id}")


def _protocol_axes() -> dict[str, list[str]]:
    protocol = _load("experiment.json")
    assert isinstance(protocol, dict)
    axes = protocol["axes"]
    assert isinstance(axes, dict)
    return {
        str(axis): [str(value) for value in values]
        for axis, values in axes.items()
    }


def _validate_coordinates(
    deferral_level: str,
    reference_representation: str,
    emphasis: str,
    assembly: str,
) -> None:
    axes = _protocol_axes()
    coordinates = {
        "deferralLevel": deferral_level,
        "referenceRepresentation": reference_representation,
        "emphasis": emphasis,
        "assembly": assembly,
    }
    for axis, value in coordinates.items():
        if value not in axes[axis]:
            raise ValueError(f"unsupported {axis}: {value}")
    if assembly not in PHASE2_ASSEMBLIES:
        raise ValueError(
            f"assembly {assembly} belongs to Phase 3, not the Phase 2 renderer"
        )


def variant_id(
    deferral_level: str,
    reference_representation: str,
    emphasis: str,
    assembly: str,
) -> str:
    _validate_coordinates(
        deferral_level,
        reference_representation,
        emphasis,
        assembly,
    )
    return "-".join(
        (
            deferral_level.lower(),
            REFERENCE_CODES[reference_representation],
            EMPHASIS_CODES[emphasis],
            ASSEMBLY_CODES[assembly],
        )
    )


def _emphasize_reference(
    reference: dict[str, object],
    representation: str,
    emphasis: str,
    *,
    repository_root: PurePosixPath = MATERIALIZED_REPOSITORY,
    payload_root: PurePosixPath = MATERIALIZED_PAYLOAD,
) -> str:
    rendered = _render_reference(
        reference,
        representation,
        repository_root=repository_root,
        payload_root=payload_root,
    )
    applicability = str(reference["applicability"])
    if emphasis == "optional":
        return f"Background for {applicability} is available in {rendered}."
    if emphasis == "conditional":
        return f"When {applicability}, read {rendered}."
    if emphasis == "imperative":
        return f"Before handling {applicability}, read {rendered}."
    if emphasis == "safety-gated":
        return (
            f"Do not act on {applicability} until {rendered} has been loaded."
        )
    raise ValueError(f"unsupported emphasis: {emphasis}")


def _guide_reference(
    guide: dict[str, object], owner: str
) -> dict[str, object]:
    return {
        "guideId": guide["id"],
        "locator": guide["path"],
        "owner": owner,
        "applicability": guide["applicability"],
        "disposition": "available",
    }


def _owner_index_reference(owner: str) -> dict[str, object]:
    return {
        "guideId": f"{owner}-index",
        "locator": f"guides/indexes/{owner}.md",
        "owner": owner,
        "applicability": f"detailed guidance from {owner} is needed",
        "disposition": "available",
    }


def _global_index_reference() -> dict[str, object]:
    return {
        "guideId": "guide-index",
        "locator": "guides/index.md",
        "owner": "synthetic-context-authority",
        "applicability": "any deferred detail is needed",
        "disposition": "available",
    }


def _selected_guide_ids(task: dict[str, object]) -> set[str]:
    return set(str(value) for value in task["requiredGuideIds"])


def _fragment_references(
    contributor: dict[str, object],
    task: dict[str, object],
    deferral_level: str,
) -> list[dict[str, object]]:
    owner = str(contributor["owner"])
    guides = list(contributor["guides"])
    if deferral_level == "F1":
        return [_owner_index_reference(owner)]
    if deferral_level == "F2":
        return [_guide_reference(guide, owner) for guide in guides]
    if deferral_level == "F3":
        required = _selected_guide_ids(task)
        return [
            _guide_reference(guide, owner)
            for guide in guides
            if str(guide["id"]) in required
        ]
    if deferral_level == "F4":
        return []
    return []


def _generated_reference_index(
    contributors: list[dict[str, object]],
    task: dict[str, object],
    deferral_level: str,
    representation: str,
    emphasis: str,
    *,
    repository_root: PurePosixPath = MATERIALIZED_REPOSITORY,
    payload_root: PurePosixPath = MATERIALIZED_PAYLOAD,
) -> str:
    references: list[dict[str, object]] = []
    if deferral_level == "F1":
        references = [
            _owner_index_reference(str(contributor["owner"]))
            for contributor in contributors
        ]
    elif deferral_level == "F3":
        for contributor in contributors:
            references.extend(
                _fragment_references(contributor, task, deferral_level)
            )
    elif deferral_level == "F4":
        references = [_global_index_reference()]
    elif deferral_level != "F0":
        for contributor in contributors:
            owner = str(contributor["owner"])
            references.extend(
                _guide_reference(guide, owner)
                for guide in contributor["guides"]
            )
    lines = ["# Deferred reference index"]
    if not references:
        lines.append("- No deferred references apply to this task.")
    else:
        for reference in references:
            lines.append(
                f"- `{reference['owner']}`: "
                + _emphasize_reference(
                    reference,
                    representation,
                    emphasis,
                    repository_root=repository_root,
                    payload_root=payload_root,
                )
            )
    return "\n".join(lines)


def render_variant(
    *,
    deferral_level: str,
    reference_representation: str,
    emphasis: str,
    assembly: str,
    task: dict[str, object],
    contributors: list[dict[str, object]],
    canaries: dict[str, str] | None = None,
    repository_root: PurePosixPath = MATERIALIZED_REPOSITORY,
    payload_root: PurePosixPath = MATERIALIZED_PAYLOAD,
) -> str:
    _validate_coordinates(
        deferral_level,
        reference_representation,
        emphasis,
        assembly,
    )
    if assembly == "flat-fragments" and deferral_level == "F0":
        rendered = render_baseline("current-full-inline", contributors)
        if canaries is not None:
            for guide_id, canary in canaries.items():
                rendered = rendered.replace(_stable_canary(guide_id), canary)
    elif (
        assembly == "flat-fragments"
        and deferral_level == "F2"
        and reference_representation == "backtick-repository-relative"
        and emphasis == "conditional"
    ):
        rendered = render_baseline("current-concise-kernel", contributors)
    else:
        fragments: list[str] = []
        for contributor in contributors:
            owner = str(contributor["owner"])
            lines = [f"<!-- context-owner: {owner} -->"]
            if deferral_level != "F4":
                lines.append(str(contributor["criticalKernel"]))
            else:
                lines.append(
                    f"[owner: {owner}@1.0.0] Detailed guidance is deferred."
                )
            if deferral_level == "F0":
                for guide in contributor["guides"]:
                    canary = (
                        canaries[str(guide["id"])]
                        if canaries is not None
                        else _stable_canary(str(guide["id"]))
                    )
                    lines.append(_guide_text_with_canary(guide, canary))
            else:
                for reference in _fragment_references(
                    contributor, task, deferral_level
                ):
                    lines.append(
                        _emphasize_reference(
                            reference,
                            reference_representation,
                            emphasis,
                            repository_root=repository_root,
                            payload_root=payload_root,
                        )
                    )
            fragments.append("\n\n".join(lines))
        if deferral_level == "F4":
            fragments.append(
                "[owner: synthetic-context-authority@1.0.0] "
                + _emphasize_reference(
                    _global_index_reference(),
                    reference_representation,
                    emphasis,
                    repository_root=repository_root,
                    payload_root=payload_root,
                )
            )
        rendered = "\n\n".join(fragments)

    special = task.get("referenceFixture")
    if special:
        reference = _render_reference(
            special,
            reference_representation,
            repository_root=repository_root,
            payload_root=payload_root,
        )
        rendered = (
            f"{rendered}\n\n"
            f"[owner: {special['owner']}@1.0.0] "
            f"{special['applicability']} {reference}"
        )
    task_context = task.get("taskContext")
    if task_context:
        rendered = f"{rendered}\n\n{task_context}"
    if assembly == "flat-with-index":
        rendered = (
            _generated_reference_index(
                contributors,
                task,
                deferral_level,
                reference_representation,
                emphasis,
                repository_root=repository_root,
                payload_root=payload_root,
            )
            + "\n\n"
            + rendered
        )
    return rendered


def render_baseline(
    baseline_id: str, contributors: list[dict[str, object]]
) -> str:
    fragments: list[str] = []
    for contributor in contributors:
        owner = str(contributor["owner"])
        lines = [
            f"<!-- context-owner: {owner} -->",
            str(contributor["criticalKernel"]),
        ]
        guides = list(contributor["guides"])
        if baseline_id == "current-full-inline":
            lines.extend(_guide_text(guide) for guide in guides)
        elif baseline_id == "current-concise-kernel":
            for guide in guides:
                lines.append(
                    "When "
                    f"{guide['applicability']}, resolve from the synthetic "
                    f"repository root and read `{guide['path']}` "
                    f"(guide id `{guide['id']}`)."
                )
        else:
            raise ValueError(f"unknown baseline: {baseline_id}")
        fragments.append("\n\n".join(lines))
    return "\n\n".join(fragments)


def baseline_record() -> dict[str, object]:
    corpus = _load("corpus.json")
    assert isinstance(corpus, dict)
    contributors = corpus["contributors"]
    assert isinstance(contributors, list)
    rendered: dict[str, object] = {}
    for baseline_id in BASELINE_COORDINATES:
        text = render_baseline(baseline_id, contributors)
        rendered[baseline_id] = {
            "coordinates": BASELINE_COORDINATES[baseline_id],
            **_metrics(text),
        }
    return {
        "schema": "copilot-extensions.progressive-context-baselines",
        "version": 1,
        "tokenEstimate": "ceil(unicodeCharacters / 4)",
        "canaryShape": "PCD_CANARY_<GUIDE_ID>_<48 lowercase hex>",
        "baselines": rendered,
    }


def _safe_guide_path(value: str) -> bool:
    if "\\" in value or re.fullmatch(
        r"guides/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*", value
    ) is None:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and bool(path.parts)
        and all(part not in {"", ".", ".."} for part in path.parts)
        and path.parts[0] == "guides"
    )


def _safe_repository_path(value: str) -> bool:
    if "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and bool(path.parts)
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _declared_inventory(source: Path) -> dict[tuple[str, str], tuple[int, int]]:
    declared: dict[tuple[str, str], tuple[int, int]] = {}
    for declaration_path in sorted(
        (source / "plugins").glob("*/session-context.json")
    ):
        owner = declaration_path.parent.name
        declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
        if (
            declaration.get("schema")
            != "copilot-extensions.session-context-contributors"
            or declaration.get("version") != 1
            or declaration.get("complete") is not True
        ):
            raise ValueError(f"incomplete contributor declaration: {owner}")
        for contributor in declaration.get("contributors", []):
            if contributor.get("pure") is not True:
                raise ValueError(f"non-pure declared contributor: {owner}")
            key = (owner, contributor["id"])
            if key in declared:
                raise ValueError(f"duplicate declared contributor: {key}")
            declared[key] = (
                int(contributor["order"]),
                int(contributor["maxBytes"]),
            )
    return declared


def _validate_inventory(source: Path) -> None:
    inventory = _load("suite-inventory.json")
    assert isinstance(inventory, dict)
    entries = inventory["contributors"]
    assert isinstance(entries, list)
    frozen: dict[tuple[str, str], tuple[int, int]] = {}
    for entry in entries:
        key = (entry["owner"], entry["id"])
        if key in frozen:
            raise ValueError(f"duplicate frozen contributor: {key}")
        observed = entry["observed"]
        if observed["status"] not in {"emitted", "inapplicable"}:
            raise ValueError(f"invalid observed status: {key}")
        for field in ("unicodeCharacters", "utf8Bytes", "estimatedTokens"):
            if not isinstance(observed[field], int) or observed[field] < 0:
                raise ValueError(f"invalid observed metric {field}: {key}")
        frozen[key] = (int(entry["order"]), int(entry["declaredMaxBytes"]))
    declared = _declared_inventory(source)
    if frozen != declared:
        missing = sorted(set(declared) - set(frozen))
        extra = sorted(set(frozen) - set(declared))
        changed = sorted(
            key
            for key in set(frozen) & set(declared)
            if frozen[key] != declared[key]
        )
        raise ValueError(
            "suite inventory drifted: "
            f"missing={missing}, extra={extra}, changed={changed}"
        )
    revision = inventory["sourceRevision"]
    if re.fullmatch(r"[0-9a-f]{8,40}", revision) is None:
        raise ValueError("inventory source revision is not a commit identifier")


def render_task_cell(
    baseline_id: str,
    task: dict[str, object],
    contributors: list[dict[str, object]],
    reference_representation: str | None = None,
) -> str:
    rendered = render_baseline(baseline_id, contributors)
    special = task.get("referenceFixture")
    if special:
        representation = reference_representation or BASELINE_COORDINATES[
            baseline_id
        ]["referenceRepresentation"]
        if representation == "none":
            representation = "backtick-repository-relative"
        reference = _render_reference(special, representation)
        rendered = (
            f"{rendered}\n\n"
            f"[owner: {special['owner']}@1.0.0] "
            f"{special['applicability']} {reference}"
        )
    task_context = task.get("taskContext")
    if task_context:
        rendered = f"{rendered}\n\n{task_context}"
    return rendered


def _render_reference(
    reference: dict[str, object],
    representation: str,
    *,
    repository_root: PurePosixPath = MATERIALIZED_REPOSITORY,
    payload_root: PurePosixPath = MATERIALIZED_PAYLOAD,
) -> str:
    guide_id = str(reference["guideId"])
    locator = str(reference["locator"])
    if representation == "markdown-link":
        return f"[guide {guide_id}]({locator})"
    if representation == "backtick-repository-relative":
        return (
            f"guide {guide_id}; resolve from the synthetic repository root: "
            f"`{locator}`"
        )
    if representation == "backtick-payload-relative":
        return (
            f"guide {guide_id}; resolve `{locator}` from the synthetic "
            f"payload root `{payload_root.as_posix()}`"
        )
    if representation == "backtick-absolute-contained":
        if reference["disposition"] == "unsafe":
            absolute = repository_root / PurePosixPath(locator)
        else:
            absolute = repository_root / PurePosixPath(locator)
        return f"guide {guide_id}: `{absolute.as_posix()}`"
    if representation == "bare-labeled-path":
        return f"guide {guide_id} locator: {locator}"
    if representation == "html-comment-locator":
        return f"<!-- guide={guide_id}; locator={locator} -->"
    if representation == "structured-reference":
        return json.dumps(
            {
                "base": "repository",
                "guideId": guide_id,
                "locator": locator,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    raise ValueError(f"unsupported reference representation: {representation}")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _reset_materialized_root(root: Path) -> None:
    resolved = root.resolve()
    if (
        resolved == Path(resolved.anchor)
        or resolved == ROOT.resolve()
        or len(resolved.parts) < 3
    ):
        raise ValueError(f"refusing to replace unsafe materialized root: {root}")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)


def _bundle_configured_scenario(output: Path) -> None:
    baseline = output / "_baseline"
    baseline.mkdir()
    shutil.copy2(Path(__file__), baseline / "fixture.py")
    shutil.copy2(ROOT / "expected.md", baseline / "expected.md")
    shutil.copytree(FIXTURES, baseline / "fixtures")

    source = output / "_source" / "plugins"
    source.mkdir(parents=True)
    source_root = next(
        (
            candidate
            for candidate in ROOT.parents
            if (candidate / "plugins").is_dir()
            and (candidate / "tools" / "clean-room").is_dir()
        ),
        None,
    )
    if source_root is None:
        raise ValueError("could not resolve the source checkout for bundling")
    for context_path in sorted(
        (source_root / "plugins").glob("*/session-context.json")
    ):
        target = source / context_path.parent.name / context_path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(context_path, target)
    verify(output / "_source")


def _normalize_configured_scenario_permissions(output: Path) -> None:
    for path in (output, *output.rglob("*")):
        path.chmod(0o755 if path.is_dir() else 0o644)


def _guide_records(
    contributors: list[dict[str, object]],
) -> list[tuple[str, dict[str, object]]]:
    return [
        (str(contributor["owner"]), guide)
        for contributor in contributors
        for guide in contributor["guides"]
    ]


def _guide_index(
    records: list[tuple[str, dict[str, object]]],
) -> str:
    lines = ["# Synthetic deferred guide index", ""]
    for owner, guide in records:
        lines.append(
            f"- `{guide['id']}` owned by `{owner}` applies when "
            f"{guide['applicability']}; read `{guide['path']}`."
        )
    return "\n".join(lines) + "\n"


def _owner_guide_index(
    owner: str, guides: list[dict[str, object]]
) -> str:
    lines = [f"# Deferred guides for {owner}", ""]
    for guide in guides:
        lines.append(
            f"- `{guide['id']}` applies when {guide['applicability']}; "
            f"read `{guide['path']}`."
        )
    return "\n".join(lines) + "\n"


def _materialized_plugin_files(
    payload: Path, context: str
) -> None:
    _write_json(
        payload / "plugin.json",
        {
            "name": "synthetic-progressive-context",
            "description": (
                "Synthetic per-run progressive context disclosure fixture."
            ),
            "version": "1.0.0",
            "runtimeScope": "none",
            "hooks": "hooks.json",
        },
    )
    _write_json(
        payload / "hooks.json",
        {
            "version": 1,
            "hooks": {
                "sessionStart": [
                    {
                        "type": "command",
                        "bash": (
                            'python3 "$COPILOT_PLUGIN_ROOT/'
                            'scripts/emit-context.py"'
                        ),
                        "powershell": (
                            "& python3 (Join-Path "
                            "$env:COPILOT_PLUGIN_ROOT "
                            "'scripts\\emit-context.py')"
                        ),
                        "timeoutSec": 15,
                    }
                ]
            },
        },
    )
    (payload / "scripts").mkdir(parents=True)
    (payload / "scripts" / "emit-context.py").write_text(
        """#!/usr/bin/env python3
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
context = (root / "context.md").read_text(encoding="utf-8")
print(json.dumps({"additionalContext": context}, separators=(",", ":")), end="")
""",
        encoding="utf-8",
    )
    (payload / "context.md").write_text(context, encoding="utf-8")


def _execution_fixture(task: dict[str, object]) -> dict[str, object] | None:
    fixture_id = task.get("executionFixtureId")
    if fixture_id is None:
        return None
    tasks = _load("tasks.json")
    assert isinstance(tasks, dict)
    fixtures = tasks.get("executionFixtures")
    if not isinstance(fixtures, dict) or fixture_id not in fixtures:
        raise ValueError(
            f"task has unknown execution fixture: {task['id']}: {fixture_id}"
        )
    fixture = fixtures[fixture_id]
    if not isinstance(fixture, dict):
        raise ValueError(f"invalid execution fixture: {fixture_id}")
    return fixture


def _freeze_epoch() -> int:
    experiment = _load("experiment.json")
    assert isinstance(experiment, dict)
    epoch = experiment.get("freezeEpoch", 1)
    if not isinstance(epoch, int) or epoch < 1:
        raise ValueError("invalid experiment freeze epoch")
    return epoch


def _materialize_execution_fixture(
    repository: Path,
    fixture: dict[str, object],
) -> None:
    _write_json(
        repository / PurePosixPath(str(fixture["configLocator"])),
        fixture,
    )
    script = """\
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    if (
        args.config != ".synthetic/execution.json"
        or args.result != ".synthetic/result.json"
    ):
        raise SystemExit("execution arguments do not match the exact argv")
    repository = Path(__file__).resolve().parent.parent
    config_path = (repository / args.config).resolve()
    result_path = (repository / args.result).resolve()
    expected_config = repository / ".synthetic" / "execution.json"
    expected_result = repository / ".synthetic" / "result.json"
    if (
        not config_path.is_relative_to(repository)
        or not result_path.is_relative_to(repository)
        or config_path != expected_config
        or result_path != expected_result
    ):
        raise SystemExit("execution paths escape the bounded repository")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    readiness = config["readiness"]
    destination = config["destination"]
    if readiness["signal"] != "READY":
        raise SystemExit("capability is not ready")
    if not destination["reachable"] or destination["reviewGate"] != "required":
        raise SystemExit("destination gate failed")
    result = {
        "boundedRead": "complete",
        "validatedMutation": "complete",
        "objectiveConfirmation": "complete",
        "destination": destination["repository"],
        "scopedIdentity": destination["scopedIdentity"],
        "reviewGate": destination["reviewGate"],
    }
    result_path.write_text(
        json.dumps(result, sort_keys=True) + "\\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
    path = repository / PurePosixPath(str(fixture["scriptLocator"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")


def materialize(
    *,
    root: Path,
    source: Path,
    deferral_level: str,
    reference_representation: str,
    emphasis: str,
    assembly: str,
    task_id: str,
    model: str,
    repetition: int,
    venue: str = "acp",
) -> dict[str, object]:
    if repetition < 1:
        raise ValueError("repetition must be at least one")
    if venue != "acp":
        raise ValueError("the runnable Tier-E scenario uses the ACP venue")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", model) is None:
        raise ValueError("model must be a portable evidence identifier")
    verify(source)
    _validate_coordinates(
        deferral_level,
        reference_representation,
        emphasis,
        assembly,
    )
    task = _task_by_id(task_id)
    if task["boundary"] not in RUNNABLE_BOUNDARIES:
        raise ValueError(
            f"{task['boundary']} boundary is not runnable until the "
            "clean-room driver performs that real session transition"
        )
    corpus = _load("corpus.json")
    assert isinstance(corpus, dict)
    contributors = corpus["contributors"]
    assert isinstance(contributors, list)
    canaries = {
        str(guide["id"]): _random_canary(str(guide["id"]))
        for _, guide in _guide_records(contributors)
    }
    resolved_root = root.resolve()
    repository_path = resolved_root / "repository"
    payload_path = (
        resolved_root / "payload" / "synthetic-progressive-context"
    )
    repository_root = PurePosixPath(repository_path.as_posix())
    payload_root = PurePosixPath(payload_path.as_posix())
    render_task = dict(task)
    if task["boundary"] == "spill":
        render_task.pop("taskContext", None)
    guidance = render_variant(
        deferral_level=deferral_level,
        reference_representation=reference_representation,
        emphasis=emphasis,
        assembly=assembly,
        task=render_task,
        contributors=contributors,
        canaries=canaries,
        repository_root=repository_root,
        payload_root=payload_root,
    )
    task_binding = (
        f"[experiment-task: {task_id}; boundary={task['boundary']}]\n"
        f"{task['prompt']}"
    )
    if task["boundary"] == "spill":
        context = f"{task['taskContext']}\n\n{task_binding}"
    else:
        context = f"{guidance}\n\n{task_binding}"
    _reset_materialized_root(root)
    repository = root / "repository"
    payload = root / "payload" / "synthetic-progressive-context"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    _write_json(
        repository / ".github" / "copilot" / "settings.json",
        {
            "enabledPlugins": {
                "synthetic-progressive-context@copilot-extensions": True
            }
        },
    )
    records = _guide_records(contributors)
    for _, guide in records:
        guide_id = str(guide["id"])
        text = _guide_text_with_canary(guide, canaries[guide_id]) + "\n"
        for base in (repository, payload):
            path = base / PurePosixPath(str(guide["path"]))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    index = _guide_index(records)
    for base in (repository, payload):
        path = base / "guides" / "index.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(index, encoding="utf-8")
    for contributor in contributors:
        owner = str(contributor["owner"])
        owner_index = _owner_guide_index(owner, list(contributor["guides"]))
        for base in (repository, payload):
            path = base / "guides" / "indexes" / f"{owner}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(owner_index, encoding="utf-8")
    for artifact in task.get("requiredArtifacts", []):
        path = repository / PurePosixPath(str(artifact["locator"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(guidance, encoding="utf-8")
    execution_fixture = _execution_fixture(task)
    if execution_fixture is not None:
        _materialize_execution_fixture(repository, execution_fixture)
    _materialized_plugin_files(payload, context)
    private = root / "private"
    _write_json(private / "canaries.json", canaries)
    coordinates = {
        "deferralLevel": deferral_level,
        "referenceRepresentation": reference_representation,
        "emphasis": emphasis,
        "assembly": assembly,
    }
    current_variant = variant_id(
        deferral_level,
        reference_representation,
        emphasis,
        assembly,
    )
    freeze_epoch = _freeze_epoch()
    metadata = {
        "schema": "copilot-extensions.progressive-context-run",
        "version": 1,
        "freezeEpoch": freeze_epoch,
        "runId": (
            f"e{freeze_epoch}-{current_variant}-{task_id}-r{repetition}"
        ),
        "variantId": current_variant,
        "coordinates": coordinates,
        "taskId": task_id,
        "boundary": task["boundary"],
        "model": model,
        "venue": venue,
        "repetition": repetition,
        "initialContext": {
            key: value
            for key, value in _metrics(context).items()
            if key != "sha256"
        },
        "structuredRenderHash": _metrics(guidance)["sha256"],
        "repositoryRoot": repository_root.as_posix(),
        "payloadRoot": payload_root.as_posix(),
        "selectedContributorIds": [
            str(contributor["owner"]) for contributor in contributors
        ],
    }
    _write_json(root / "run-metadata.json", metadata)
    (root / "acp-cwd").write_text(
        repository_root.as_posix() + "\n",
        encoding="utf-8",
    )
    _write_json(
        root / "copilot-config.json",
        {"trustedFolders": [repository_root.as_posix()]},
    )
    return metadata


def configure_scenario(
    *,
    template: Path,
    output: Path,
    deferral_level: str,
    reference_representation: str,
    emphasis: str,
    assembly: str,
    task_id: str,
    model: str,
    repetition: int,
) -> dict[str, object]:
    _validate_coordinates(
        deferral_level,
        reference_representation,
        emphasis,
        assembly,
    )
    task = _task_by_id(task_id)
    if task["boundary"] not in RUNNABLE_BOUNDARIES:
        raise ValueError(
            f"{task['boundary']} boundary is not runnable until the "
            "clean-room driver performs that real session transition"
        )
    if repetition < 1:
        raise ValueError("repetition must be at least one")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", model) is None:
        raise ValueError("model must be a portable evidence identifier")
    if not (template / "manifest.json").is_file():
        raise ValueError("Tier-E scenario template has no manifest.json")
    _reset_materialized_root(output)
    for source_path in template.iterdir():
        if source_path.name in {"manifest.json", "__pycache__"}:
            continue
        target = output / source_path.name
        if source_path.is_dir():
            shutil.copytree(source_path, target)
        else:
            shutil.copy2(source_path, target)
    _bundle_configured_scenario(output)
    manifest = json.loads(
        (template / "manifest.json").read_text(encoding="utf-8")
    )
    current_variant = variant_id(
        deferral_level,
        reference_representation,
        emphasis,
        assembly,
    )
    freeze_epoch = _freeze_epoch()
    manifest["name"] = (
        f"progressive-context-e{freeze_epoch}-"
        f"{current_variant}-{task_id}-r{repetition}"
    )
    manifest["experiment"] = {
        "freezeEpoch": freeze_epoch,
        "deferralLevel": deferral_level,
        "referenceRepresentation": reference_representation,
        "emphasis": emphasis,
        "assembly": assembly,
        "taskId": task_id,
        "model": model,
        "repetition": repetition,
    }
    manifest["expected_outcome"]["selected_task"] = {
        "id": task_id,
        "boundary": task["boundary"],
        "expectedDecision": task["expectedDecision"],
        "requiredGuideIds": task["requiredGuideIds"],
        "requiredCriticalRuleIds": task["requiredCriticalRuleIds"],
    }
    manifest["eval"]["model"] = model
    manifest["runs"]["count"] = 1
    _write_json(output / "manifest.json", manifest)
    _normalize_configured_scenario_permissions(output)
    return {
        "ok": True,
        "scenario": manifest["name"],
        "path": str(output),
        "variantId": current_variant,
        "taskId": task_id,
        "boundary": task["boundary"],
        "freezeEpoch": freeze_epoch,
        "repetition": repetition,
    }


def verify_materialized(root: Path) -> dict[str, object]:
    metadata = json.loads(
        (root / "run-metadata.json").read_text(encoding="utf-8")
    )
    canaries = json.loads(
        (root / "private" / "canaries.json").read_text(encoding="utf-8")
    )
    if not isinstance(canaries, dict) or len(canaries) != 8:
        raise ValueError("materialized canary set is incomplete")
    if len(set(canaries.values())) != len(canaries):
        raise ValueError("materialized canaries are not unique")
    for guide_id, canary in canaries.items():
        label = str(guide_id).upper().replace("-", "_")
        if re.fullmatch(
            rf"PCD_CANARY_{re.escape(label)}_[0-9a-f]{{48}}",
            str(canary),
        ) is None:
            raise ValueError(f"invalid per-run canary: {guide_id}")
    resolved_root = root.resolve()
    repository_root = PurePosixPath(
        (resolved_root / "repository").as_posix()
    )
    payload_root = PurePosixPath(
        (
            resolved_root
            / "payload"
            / "synthetic-progressive-context"
        ).as_posix()
    )
    if (
        metadata["repositoryRoot"] != repository_root.as_posix()
        or metadata["payloadRoot"] != payload_root.as_posix()
    ):
        raise ValueError("materialized root metadata drifted")
    task = _task_by_id(str(metadata["taskId"]))
    render_task = dict(task)
    if task["boundary"] == "spill":
        render_task.pop("taskContext", None)
    corpus = _load("corpus.json")
    assert isinstance(corpus, dict)
    guidance = render_variant(
        deferral_level=metadata["coordinates"]["deferralLevel"],
        reference_representation=metadata["coordinates"][
            "referenceRepresentation"
        ],
        emphasis=metadata["coordinates"]["emphasis"],
        assembly=metadata["coordinates"]["assembly"],
        task=render_task,
        contributors=corpus["contributors"],
        canaries=canaries,
        repository_root=repository_root,
        payload_root=payload_root,
    )
    task_binding = (
        f"[experiment-task: {metadata['taskId']}; "
        f"boundary={task['boundary']}]\n{task['prompt']}"
    )
    expected_context = (
        f"{task['taskContext']}\n\n{task_binding}"
        if task["boundary"] == "spill"
        else f"{guidance}\n\n{task_binding}"
    )
    context = (
        root
        / "payload"
        / "synthetic-progressive-context"
        / "context.md"
    ).read_text(encoding="utf-8")
    if context != expected_context:
        raise ValueError("materialized context does not match its coordinates")
    metrics = _metrics(context)
    if metadata["structuredRenderHash"] != _metrics(guidance)["sha256"]:
        raise ValueError("materialized context hash drifted")
    if metadata["initialContext"] != {
        key: value for key, value in metrics.items() if key != "sha256"
    }:
        raise ValueError("materialized context metrics drifted")
    for _, guide in _guide_records(corpus["contributors"]):
        guide_id = str(guide["id"])
        for relative in (
            Path("repository") / PurePosixPath(str(guide["path"])),
            Path("payload")
            / "synthetic-progressive-context"
            / PurePosixPath(str(guide["path"])),
        ):
            text = (root / relative).read_text(encoding="utf-8")
            if text.count(canaries[guide_id]) != 1:
                raise ValueError(
                    f"guide does not contain exactly one canary: "
                    f"{guide_id}: {relative}"
                )
    for artifact in task.get("requiredArtifacts", []):
        artifact_path = root / "repository" / PurePosixPath(
            str(artifact["locator"])
        )
        if artifact_path.read_text(encoding="utf-8") != guidance:
            raise ValueError("materialized boundary artifact drifted")
    execution_fixture = _execution_fixture(task)
    if execution_fixture is not None:
        config_path = root / "repository" / PurePosixPath(
            str(execution_fixture["configLocator"])
        )
        actual_fixture = json.loads(config_path.read_text(encoding="utf-8"))
        if actual_fixture != execution_fixture:
            raise ValueError("materialized execution configuration drifted")
        script_path = root / "repository" / PurePosixPath(
            str(execution_fixture["scriptLocator"])
        )
        if not script_path.is_file():
            raise ValueError("materialized execution command is missing")
        result_path = root / "repository" / PurePosixPath(
            str(execution_fixture["resultLocator"])
        )
        if result_path.exists():
            raise ValueError("materialized execution result is not fresh")
    expected_cwd = repository_root.as_posix()
    if (root / "acp-cwd").read_text(encoding="utf-8").strip() != expected_cwd:
        raise ValueError("materialized ACP cwd drifted")
    if not (root / "repository").is_dir():
        raise ValueError("materialized ACP cwd does not exist")
    return {
        "ok": True,
        "freezeEpoch": metadata.get("freezeEpoch", 1),
        "runId": metadata["runId"],
        "variantId": metadata["variantId"],
        "taskId": metadata["taskId"],
        "boundary": metadata["boundary"],
        "guideCount": len(canaries),
        "structuredRenderHash": metadata["structuredRenderHash"],
    }


def phase2_matrix_record() -> dict[str, object]:
    corpus = _load("corpus.json")
    tasks = _load("tasks.json")
    axes = _protocol_axes()
    assert isinstance(corpus, dict) and isinstance(tasks, dict)
    contributors = corpus["contributors"]
    phase2_canaries = {
        str(guide["id"]): (
            f"PCD_CANARY_{str(guide['id']).upper().replace('-', '_')}_"
            + hashlib.sha256(
                f"phase2:{guide['id']}".encode()
            ).hexdigest()[:TOKEN_HEX_LENGTH]
        )
        for _, guide in _guide_records(contributors)
    }
    render_count = 0
    hashes: set[str] = set()
    matrix_digest = hashlib.sha256()
    for deferral_level in axes["deferralLevel"]:
        for representation in axes["referenceRepresentation"]:
            for emphasis in axes["emphasis"]:
                for assembly in sorted(PHASE2_ASSEMBLIES):
                    for task in tasks["tasks"]:
                        first = render_variant(
                            deferral_level=deferral_level,
                            reference_representation=representation,
                            emphasis=emphasis,
                            assembly=assembly,
                            task=task,
                            contributors=contributors,
                        )
                        second = render_variant(
                            deferral_level=deferral_level,
                            reference_representation=representation,
                            emphasis=emphasis,
                            assembly=assembly,
                            task=task,
                            contributors=contributors,
                        )
                        if first != second:
                            raise ValueError("Phase 2 rendering is not deterministic")
                        canary_first = render_variant(
                            deferral_level=deferral_level,
                            reference_representation=representation,
                            emphasis=emphasis,
                            assembly=assembly,
                            task=task,
                            contributors=contributors,
                            canaries=phase2_canaries,
                        )
                        canary_second = render_variant(
                            deferral_level=deferral_level,
                            reference_representation=representation,
                            emphasis=emphasis,
                            assembly=assembly,
                            task=task,
                            contributors=contributors,
                            canaries=phase2_canaries,
                        )
                        if canary_first != canary_second:
                            raise ValueError(
                                "Phase 2 canary rendering is not deterministic"
                            )
                        special = task.get("referenceFixture")
                        if special and (
                            canary_first.count(str(special["locator"])) != 1
                        ):
                            raise ValueError(
                                "Phase 2 negative reference stimulus "
                                f"was not rendered exactly once: {task['id']}"
                            )
                        stable_hash = hashlib.sha256(first.encode()).hexdigest()
                        canary_hash = hashlib.sha256(
                            canary_first.encode()
                        ).hexdigest()
                        hashes.add(stable_hash)
                        hashes.add(canary_hash)
                        matrix_digest.update(
                            (
                                f"{deferral_level}\0{representation}\0"
                                f"{emphasis}\0{assembly}\0{task['id']}\0"
                                f"{stable_hash}\0{canary_hash}\n"
                            ).encode()
                        )
                        render_count += 1
    baseline_task = _task_by_id("no-guide")
    if render_variant(
        deferral_level="F0",
        reference_representation="markdown-link",
        emphasis="optional",
        assembly="flat-fragments",
        task=baseline_task,
        contributors=contributors,
    ) != render_baseline("current-full-inline", contributors):
        raise ValueError("F0 no longer preserves the frozen full-inline baseline")
    canary_f0 = render_variant(
        deferral_level="F0",
        reference_representation="markdown-link",
        emphasis="optional",
        assembly="flat-fragments",
        task=baseline_task,
        contributors=contributors,
        canaries=phase2_canaries,
    )
    for guide_id, canary in phase2_canaries.items():
        canary_f0 = canary_f0.replace(canary, _stable_canary(guide_id))
    if canary_f0 != render_baseline("current-full-inline", contributors):
        raise ValueError(
            "F0 canary rendering no longer preserves baseline wording"
        )
    if render_variant(
        deferral_level="F2",
        reference_representation="backtick-repository-relative",
        emphasis="conditional",
        assembly="flat-fragments",
        task=baseline_task,
        contributors=contributors,
    ) != render_baseline("current-concise-kernel", contributors):
        raise ValueError(
            "F2 no longer preserves the frozen concise-kernel baseline"
        )
    if render_variant(
        deferral_level="F2",
        reference_representation="backtick-repository-relative",
        emphasis="conditional",
        assembly="flat-fragments",
        task=baseline_task,
        contributors=contributors,
        canaries=phase2_canaries,
    ) != render_baseline("current-concise-kernel", contributors):
        raise ValueError(
            "F2 canary rendering no longer preserves the frozen baseline"
        )
    return {
        "ok": True,
        "renderCount": render_count,
        "distinctRenderHashes": len(hashes),
        "phase2Assemblies": sorted(PHASE2_ASSEMBLIES),
        "matrixSha256": matrix_digest.hexdigest(),
    }


def verify_phase2() -> dict[str, object]:
    expected = _load("phase2-matrix.json")
    actual = phase2_matrix_record()
    if expected != actual:
        raise ValueError("frozen Phase 2 render matrix drifted")
    return actual


def _transcript_path(results: Path, run_index: int) -> Path | None:
    if run_index < 1:
        raise ValueError("run index must be at least one")
    eval_dir = results / "eval"
    paths = sorted(eval_dir.glob("run-*/transcript.txt"))
    if paths:
        return paths[run_index - 1] if run_index <= len(paths) else None
    single = eval_dir / "transcript.txt"
    if run_index == 1 and single.is_file():
        return single
    return None


def observation(
    root: Path, results: Path, run_index: int = 1
) -> dict[str, object]:
    run_metadata = json.loads(
        (root / "run-metadata.json").read_text(encoding="utf-8")
    )
    canaries = json.loads(
        (root / "private" / "canaries.json").read_text(encoding="utf-8")
    )
    transcript = _transcript_path(results, run_index)
    text = (
        transcript.read_text(encoding="utf-8", errors="replace")
        if transcript is not None
        else ""
    )
    observed = sorted(
        guide_id
        for guide_id, canary in canaries.items()
        if str(canary) in text
    )
    found_canaries = [match.group(0) for match in CANARY_PATTERN.finditer(text)]
    known_canaries = set(str(value) for value in canaries.values())
    forbidden_markers = (
        "/home/operator/scenario",
        "/harness/tools/clean-room/scenarios/"
        "progressive-context-disclosure-baseline",
        "private/canaries.json",
        "run-metadata.json",
    )
    timed_out = "[clean-room] TIMED OUT" in text
    driver_exit_code: int | None = None
    actual_model = ""
    session_resolution = ""
    drive_runs = results / "eval" / "drive-runs.json"
    if drive_runs.is_file():
        runs = json.loads(drive_runs.read_text(encoding="utf-8"))
        if isinstance(runs, list) and run_index <= len(runs):
            current = runs[run_index - 1]
            if isinstance(current, dict):
                exit_code = current.get("exit_code")
                if isinstance(exit_code, int):
                    driver_exit_code = exit_code
                model = current.get("model")
                if isinstance(model, str):
                    actual_model = model
                resolution = current.get("session_resolution")
                if isinstance(resolution, str):
                    session_resolution = resolution
    value = {
        "schema": "copilot-extensions.progressive-context-observation",
        "version": 1,
        "runIndex": run_index,
        "transcriptCount": int(transcript is not None),
        "observedGuideIds": observed,
        "canaryOccurrenceCount": sum(
            text.count(str(canary)) for canary in canaries.values()
        ),
        "inventedCanaryCount": sum(
            canary not in known_canaries for canary in found_canaries
        ),
        "forbiddenFixtureReadMarkerCount": sum(
            text.count(marker) for marker in forbidden_markers
        ),
        "modelFallbackMarkerCount": sum(
            text.lower().count(marker)
            for marker in (
                "model_fallback",
                "keeps its default",
                "not offered by agent",
                "not-advertised",
            )
        ),
        "driverExitCode": driver_exit_code,
        "sessionResolution": session_resolution,
        "requestedModel": run_metadata["model"],
        "actualModel": actual_model,
        "timedOut": timed_out,
    }
    _write_json(
        results / "eval" / "progressive-context-observation.json",
        value,
    )
    return value


def _validate_identifier(value: object, field: str) -> None:
    if not isinstance(value, str) or re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value
    ) is None:
        raise ValueError(f"invalid evidence identifier {field}: {value}")


def validate_evidence(record: dict[str, object]) -> None:
    schema = _load("evidence.schema.json")
    assert isinstance(schema, dict)
    required = set(schema["required"])
    allowed = set(schema["properties"])
    if set(record) != allowed or not required <= set(record):
        raise ValueError("evidence fields do not match the frozen schema")
    if record["schema"] != "copilot-extensions.progressive-context-evidence":
        raise ValueError("invalid evidence schema")
    if record["version"] != 1:
        raise ValueError("invalid evidence version")
    if not isinstance(record["runId"], str) or re.fullmatch(
        r"[a-z0-9-]{8,96}", record["runId"]
    ) is None:
        raise ValueError("invalid evidence run id")
    for field in ("variantId", "model"):
        _validate_identifier(record[field], field)
    task = _task_by_id(str(record["taskId"]))
    if record["requiredGuideIds"] != sorted(task["requiredGuideIds"]):
        raise ValueError("evidence required guides drifted from the frozen task")
    if record["boundary"] != task["boundary"]:
        raise ValueError("evidence task boundary is inconsistent")
    if record["venue"] not in {"interactive", "acp"}:
        raise ValueError("invalid evidence venue")
    if not isinstance(record["repetition"], int) or record["repetition"] < 1:
        raise ValueError("invalid evidence repetition")
    initial = record["initialContext"]
    if not isinstance(initial, dict) or set(initial) != {
        "unicodeCharacters",
        "utf8Bytes",
        "estimatedTokens",
    } or any(not isinstance(value, int) or value < 0 for value in initial.values()):
        raise ValueError("invalid initial context metrics")
    for field in (
        "requiredGuideIds",
        "observedGuideIds",
        "autoLoadedGuideIds",
        "criticalRuleViolationIds",
        "selectedContributorIds",
    ):
        values = record[field]
        if not isinstance(values, list) or len(values) != len(set(values)):
            raise ValueError(f"invalid evidence list: {field}")
        for value in values:
            _validate_identifier(value, field)
    for field in (
        "irrelevantGuideReadCount",
        "turnCount",
        "toolCallCount",
        "elapsedMillisecondsBeforeGroundedAction",
        "pathResolutionFailureCount",
        "missingPathCount",
        "inventedPathCount",
    ):
        if not isinstance(record[field], int) or record[field] < 0:
            raise ValueError(f"invalid evidence count: {field}")
    if not isinstance(record["structuredRenderHash"], str) or re.fullmatch(
        r"[0-9a-f]{64}", record["structuredRenderHash"]
    ) is None:
        raise ValueError("invalid structured render hash")
    judge = record["judge"]
    if not isinstance(judge, dict) or set(judge) != {"verdict", "jam"}:
        raise ValueError("invalid evidence judge")
    verdict = judge["verdict"]
    jam = judge["jam"]
    if verdict not in {"PASS", "FAIL", "INVALID"}:
        raise ValueError("invalid evidence verdict")
    if verdict == "INVALID":
        if (
            record["firstTurnCorrect"] is not None
            or record["ownerProvenanceRetained"] is not None
            or record["turnCount"] != 0
            or not isinstance(jam, str)
        ):
            raise ValueError("INVALID evidence must be zero-turn and jammed")
    else:
        if (
            not isinstance(record["firstTurnCorrect"], bool)
            or not isinstance(record["ownerProvenanceRetained"], bool)
            or record["turnCount"] < 1
            or not isinstance(jam, (str, type(None)))
        ):
            raise ValueError("behavioral evidence has invalid judgment fields")
    if verdict == "PASS" and jam is not None:
        raise ValueError("PASS evidence cannot carry a jam")
    if verdict != "PASS" and not isinstance(jam, str):
        raise ValueError("non-PASS evidence requires a jam")
    if isinstance(jam, str) and re.fullmatch(r"[a-z0-9-]{1,64}", jam) is None:
        raise ValueError("invalid evidence jam")
    if verdict == "PASS":
        if record["firstTurnCorrect"] is not True:
            raise ValueError("PASS evidence requires first-turn correctness")
        if record["ownerProvenanceRetained"] is not True:
            raise ValueError("PASS evidence requires owner provenance")
        if record["criticalRuleViolationIds"]:
            raise ValueError("PASS evidence cannot contain critical violations")
        if record["autoLoadedGuideIds"]:
            raise ValueError("PASS evidence cannot contain eager guide loading")
        if not set(record["requiredGuideIds"]) <= set(
            record["observedGuideIds"]
        ):
            raise ValueError("PASS evidence is missing required guide reads")
        if record["inventedPathCount"] != 0:
            raise ValueError("PASS evidence cannot contain invented paths")
        task_id = record["taskId"]
        path_failures = record["pathResolutionFailureCount"]
        missing_paths = record["missingPathCount"]
        if task_id == "unavailable-guide":
            if path_failures < 1 or missing_paths < 1:
                raise ValueError(
                    "unavailable-guide PASS must record the missing locator"
                )
        elif task_id == "unsafe-guide":
            if path_failures < 1 or missing_paths != 0:
                raise ValueError(
                    "unsafe-guide PASS must reject before a missing-path read"
                )
        elif path_failures != 0 or missing_paths != 0:
            raise ValueError("PASS evidence contains an unexpected path failure")


def evidence_record(
    *,
    root: Path,
    results: Path,
    verdict: str,
    jam: str | None,
    first_turn_correct: bool | None = None,
    owner_provenance_retained: bool | None = None,
    auto_loaded_guide_ids: list[str] | None = None,
    critical_rule_violation_ids: list[str] | None = None,
    turn_count: int = 1,
    tool_call_count: int = 0,
    elapsed_milliseconds: int = 0,
    path_resolution_failure_count: int = 0,
    missing_path_count: int = 0,
    invented_path_count: int = 0,
    run_index: int = 1,
) -> dict[str, object]:
    metadata = json.loads(
        (root / "run-metadata.json").read_text(encoding="utf-8")
    )
    task = _task_by_id(str(metadata["taskId"]))
    observed_run = observation(root, results, run_index)
    requested_model = str(observed_run["requestedModel"])
    actual_model = str(observed_run["actualModel"])
    if verdict == "PASS":
        if observed_run["driverExitCode"] != 0:
            raise ValueError("PASS evidence requires a successful ACP driver")
        if observed_run["sessionResolution"] != "resolved":
            raise ValueError("PASS evidence requires exact ACP session provenance")
        if not actual_model:
            raise ValueError("PASS evidence requires an observed ACP model")
        if requested_model != "auto" and actual_model != requested_model:
            raise ValueError("PASS evidence cannot use an ACP model fallback")
    observed = observed_run["observedGuideIds"]
    assert isinstance(observed, list)
    auto_loaded = sorted(set(auto_loaded_guide_ids or []))
    agent_observed = sorted(set(observed) - set(auto_loaded))
    if verdict == "INVALID":
        first_turn_correct = None
        owner_provenance_retained = None
        agent_observed = []
        auto_loaded = []
        turn_count = 0
        tool_call_count = 0
        elapsed_milliseconds = 0
        path_resolution_failure_count = 0
        missing_path_count = 0
        invented_path_count = 0
        critical_rule_violation_ids = []
    irrelevant = set(str(value) for value in task["irrelevantGuideIds"])
    record = {
        "schema": "copilot-extensions.progressive-context-evidence",
        "version": 1,
        "runId": (
            metadata["runId"]
            if run_index == 1
            else f"{metadata['runId']}-s{run_index}"
        ),
        "variantId": metadata["variantId"],
        "taskId": metadata["taskId"],
        "model": actual_model if verdict != "INVALID" else metadata["model"],
        "venue": metadata["venue"],
        "boundary": metadata["boundary"],
        "repetition": metadata["repetition"],
        "initialContext": metadata["initialContext"],
        "firstTurnCorrect": first_turn_correct,
        "requiredGuideIds": sorted(task["requiredGuideIds"]),
        "observedGuideIds": agent_observed,
        "autoLoadedGuideIds": auto_loaded,
        "irrelevantGuideReadCount": len(irrelevant & set(agent_observed)),
        "turnCount": turn_count,
        "toolCallCount": tool_call_count,
        "elapsedMillisecondsBeforeGroundedAction": elapsed_milliseconds,
        "pathResolutionFailureCount": path_resolution_failure_count,
        "missingPathCount": missing_path_count,
        "inventedPathCount": invented_path_count,
        "ownerProvenanceRetained": owner_provenance_retained,
        "criticalRuleViolationIds": sorted(
            set(critical_rule_violation_ids or [])
        ),
        "structuredRenderHash": metadata["structuredRenderHash"],
        "selectedContributorIds": metadata["selectedContributorIds"],
        "judge": {"verdict": verdict, "jam": jam},
    }
    validate_evidence(record)
    return record


def invalid_evidence_record(
    *,
    deferral_level: str,
    reference_representation: str,
    emphasis: str,
    assembly: str,
    task_id: str,
    model: str,
    repetition: int,
    venue: str,
    jam: str,
) -> dict[str, object]:
    task = _task_by_id(task_id)
    freeze_epoch = _freeze_epoch()
    current_variant = variant_id(
        deferral_level,
        reference_representation,
        emphasis,
        assembly,
    )
    record = {
        "schema": "copilot-extensions.progressive-context-evidence",
        "version": 1,
        "runId": (
            f"e{freeze_epoch}-{current_variant}-{task_id}-r{repetition}"
        ),
        "variantId": current_variant,
        "taskId": task_id,
        "model": model,
        "venue": venue,
        "boundary": task["boundary"],
        "repetition": repetition,
        "initialContext": {
            "unicodeCharacters": 0,
            "utf8Bytes": 0,
            "estimatedTokens": 0,
        },
        "firstTurnCorrect": None,
        "requiredGuideIds": sorted(task["requiredGuideIds"]),
        "observedGuideIds": [],
        "autoLoadedGuideIds": [],
        "irrelevantGuideReadCount": 0,
        "turnCount": 0,
        "toolCallCount": 0,
        "elapsedMillisecondsBeforeGroundedAction": 0,
        "pathResolutionFailureCount": 0,
        "missingPathCount": 0,
        "inventedPathCount": 0,
        "ownerProvenanceRetained": None,
        "criticalRuleViolationIds": [],
        "structuredRenderHash": "0" * 64,
        "selectedContributorIds": [],
        "judge": {"verdict": "INVALID", "jam": jam},
    }
    validate_evidence(record)
    return record


def write_evidence(path: Path, record: dict[str, object]) -> None:
    validate_evidence(record)
    _write_json(path, record)


def _validate_corpus_and_tasks() -> None:
    corpus = _load("corpus.json")
    tasks = _load("tasks.json")
    assert isinstance(corpus, dict) and isinstance(tasks, dict)
    contributors = corpus["contributors"]
    assert isinstance(contributors, list)
    owner_ids: set[str] = set()
    guide_ids: set[str] = set()
    cue_guides: dict[str, str] = {}
    critical_rule_ids: set[str] = set()
    for contributor in contributors:
        owner = contributor["owner"]
        if owner in owner_ids:
            raise ValueError(f"duplicate synthetic owner: {owner}")
        owner_ids.add(owner)
        for rule_id in contributor["criticalRuleIds"]:
            if rule_id in critical_rule_ids:
                raise ValueError(f"duplicate critical rule id: {rule_id}")
            critical_rule_ids.add(rule_id)
        if f"[owner: {owner}@" not in contributor["criticalKernel"]:
            raise ValueError(f"kernel lacks attributable owner marker: {owner}")
        for guide in contributor["guides"]:
            guide_id = guide["id"]
            if guide_id in guide_ids:
                raise ValueError(f"duplicate guide id: {guide_id}")
            guide_ids.add(guide_id)
            cue_id = guide["applicabilityCueId"]
            if cue_id in cue_guides:
                raise ValueError(f"duplicate applicability cue: {cue_id}")
            cue_guides[cue_id] = guide_id
            if not _safe_guide_path(guide["path"]):
                raise ValueError(f"ordinary guide path is not contained: {guide_id}")
            if "{{CANARY}}" not in guide["body"]:
                raise ValueError(f"guide lacks canary placeholder: {guide_id}")

    task_ids: set[str] = set()
    expected_task_ids = {
        "no-guide",
        "one-guide",
        "multi-guide",
        "conflict",
        "unavailable-guide",
        "unsafe-guide",
        "resume",
        "compaction",
        "spill",
        "command-guide",
        "capability-guide",
    }
    execution_task_ids: set[str] = set()
    for task in tasks["tasks"]:
        task_id = task["id"]
        if task_id in task_ids:
            raise ValueError(f"duplicate task id: {task_id}")
        task_ids.add(task_id)
        required = set(task["requiredGuideIds"])
        irrelevant = set(task["irrelevantGuideIds"])
        triggered = set(task["triggeredApplicabilityCueIds"])
        required_rules = set(task["requiredCriticalRuleIds"])
        if not required <= guide_ids:
            raise ValueError(f"task has unknown required guide: {task_id}")
        if irrelevant != guide_ids - required:
            raise ValueError(f"task irrelevant guide set drifted: {task_id}")
        unknown_cues = triggered - set(cue_guides)
        if unknown_cues:
            raise ValueError(
                f"task has unknown applicability cues: "
                f"{task_id}: {sorted(unknown_cues)}"
            )
        if {cue_guides[cue] for cue in triggered} != required:
            raise ValueError(
                f"task applicability cues do not select required guides: {task_id}"
            )
        if not required_rules <= critical_rule_ids:
            raise ValueError(f"task has unknown critical rule: {task_id}")
        execution_demand = task.get("executionDemand")
        execution_fixture = _execution_fixture(task)
        if execution_demand is None:
            if execution_fixture is not None:
                raise ValueError(
                    f"non-execution task has an execution fixture: {task_id}"
                )
        else:
            execution_task_ids.add(task_id)
            readiness = (
                execution_fixture.get("readiness")
                if execution_fixture is not None
                else None
            )
            destination = (
                execution_fixture.get("destination")
                if execution_fixture is not None
                else None
            )
            command = (
                execution_fixture.get("command")
                if execution_fixture is not None
                else None
            )
            locators = (
                [
                    str(execution_fixture["configLocator"]),
                    str(execution_fixture["scriptLocator"]),
                    str(execution_fixture["resultLocator"]),
                ]
                if execution_fixture is not None
                else []
            )
            if (
                execution_demand != "mutation"
                or execution_fixture is None
                or any(
                    not _safe_repository_path(locator)
                    for locator in locators
                )
                or not isinstance(readiness, dict)
                or readiness.get("signal") != "READY"
                or not isinstance(destination, dict)
                or destination.get("owner")
                != "synthetic-destination-routing"
                or not destination.get("repository")
                or not destination.get("scopedIdentity")
                or destination.get("reachable") is not True
                or destination.get("reviewGate") != "required"
                or not isinstance(command, dict)
                or command.get("owner")
                != "synthetic-capability-procedure"
                or command.get("cwd") != "repository"
                or not isinstance(command.get("argv"), list)
                or command["argv"]
                != [
                    "python3",
                    execution_fixture.get("scriptLocator"),
                    "--config",
                    execution_fixture.get("configLocator"),
                    "--result",
                    execution_fixture.get("resultLocator"),
                ]
                or task["prompt"].count(
                    str(execution_fixture["configLocator"])
                )
                != 1
            ):
                raise ValueError(
                    f"execution-demanding task lacks satisfiable readiness, "
                    f"destination, review-gate, or mutation grounding: "
                    f"{task_id}"
                )
        special = task.get("referenceFixture")
        if special:
            disposition = special["disposition"]
            locator = special["locator"]
            if disposition == "unavailable" and not _safe_guide_path(locator):
                raise ValueError("unavailable guide fixture must be contained")
            if disposition == "unsafe" and _safe_guide_path(locator):
                raise ValueError("unsafe guide fixture must escape containment")
            if any(
                guide["path"] == locator
                for contributor in contributors
                for guide in contributor["guides"]
            ):
                raise ValueError(
                    f"negative reference collides with a real guide: {task_id}"
                )
        for baseline_id in (
            "current-full-inline",
            "current-concise-kernel",
        ):
            rendered = render_task_cell(baseline_id, task, contributors)
            if special and rendered.count(special["locator"]) != 1:
                raise ValueError(
                    f"task cell lost negative reference stimulus: {task_id}"
                )
        artifacts = task.get("requiredArtifacts", [])
        for artifact in artifacts:
            if (
                artifact["id"] != "aggregate-spill"
                or re.fullmatch(
                    r"session-files/[A-Za-z0-9._-]+", artifact["locator"]
                )
                is None
                or task.get("taskContext", "").count(artifact["locator"]) != 1
            ):
                raise ValueError(f"invalid required artifact: {task_id}")
    if task_ids != expected_task_ids:
        raise ValueError(f"task class set drifted: {sorted(task_ids)}")
    if execution_task_ids != {"multi-guide", "capability-guide"}:
        raise ValueError(
            f"execution task set drifted: {sorted(execution_task_ids)}"
        )
    _validate_rubric(tasks["tasks"])


def _validate_rubric(tasks: list[dict[str, object]]) -> None:
    rubric = (ROOT / "expected.md").read_text(encoding="utf-8")
    matches = re.findall(
        r"<!-- required-guides: ([a-z0-9-]+)=([a-z0-9,-]*) -->",
        rubric,
    )
    frozen = {
        task_id: set(filter(None, guide_ids.split(",")))
        for task_id, guide_ids in matches
    }
    expected = {
        task["id"]: set(task["requiredGuideIds"])
        for task in tasks
    }
    if frozen != expected:
        raise ValueError("literal-mode rubric guide sets drifted from tasks")


def _validate_protocol() -> None:
    protocol = _load("experiment.json")
    tasks = _load("tasks.json")
    corpus = _load("corpus.json")
    assert isinstance(protocol, dict)
    task_ids = {task["id"] for task in tasks["tasks"]}
    guide_ids = {
        guide["id"]
        for contributor in corpus["contributors"]
        for guide in contributor["guides"]
    }
    if set(protocol["taskIds"]) != task_ids:
        raise ValueError("experiment protocol task ids do not match task corpus")
    baselines = _load("baselines.json")
    baseline_ids = set(protocol["baselineIds"])
    if baseline_ids != set(baselines["baselines"]):
        raise ValueError("protocol baseline ids do not match frozen baselines")
    if baseline_ids != {"current-full-inline", "current-concise-kernel"}:
        raise ValueError("baseline renderer set drifted")
    for baseline_id, expected in BASELINE_COORDINATES.items():
        if baselines["baselines"][baseline_id]["coordinates"] != expected:
            raise ValueError(f"baseline coordinates drifted: {baseline_id}")
    if protocol["replication"]["primaryFreshSessionsPerCell"] < 3:
        raise ValueError("primary cells require at least three fresh sessions")
    if protocol["replication"]["finalistSessionsPerSecondModel"] < 3:
        raise ValueError("finalists require at least three second-model sessions")
    representations = protocol["axes"]["referenceRepresentation"]
    required_representations = {
        "markdown-link",
        "backtick-repository-relative",
        "backtick-payload-relative",
        "backtick-absolute-contained",
        "bare-labeled-path",
        "html-comment-locator",
        "structured-reference",
    }
    if set(representations) != required_representations:
        raise ValueError("reference representation axis drifted")
    expected_axes = {
        "deferralLevel": {"F0", "F1", "F2", "F3", "F4"},
        "emphasis": {"optional", "conditional", "imperative", "safety-gated"},
        "assembly": {
            "flat-fragments",
            "flat-with-index",
            "semantic-zones",
            "hierarchical-fragments",
        },
    }
    for axis, values in expected_axes.items():
        if set(protocol["axes"][axis]) != values:
            raise ValueError(f"{axis} axis drifted")
    if set(protocol["boundaries"]) != {
        "fresh",
        "resume",
        "compaction",
        "spill",
    }:
        raise ValueError("boundary set drifted")
    if set(protocol["venues"]) != {"interactive", "acp"}:
        raise ValueError("venue set drifted")
    primary_ids = set(protocol["primaryFreshTaskIds"])
    expected_primary = {
        task["id"] for task in tasks["tasks"] if task["boundary"] == "fresh"
    }
    if primary_ids != expected_primary:
        raise ValueError("primary fresh task set drifted")
    if not guide_ids:
        raise ValueError("experiment corpus has no guides")


def _validate_evidence_schema() -> None:
    schema = _load("evidence.schema.json")
    assert isinstance(schema, dict)
    forbidden = {
        "additionalContext",
        "content",
        "guideContent",
        "prompt",
        "rawPath",
        "response",
        "transcript",
    }

    def walk(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict) and forbidden & set(properties):
                raise ValueError(
                    "evidence schema permits raw content fields: "
                    f"{sorted(forbidden & set(properties))}"
                )
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(schema)
    if schema.get("additionalProperties") is not False:
        raise ValueError("evidence schema must reject undeclared top-level fields")
    tasks = _load("tasks.json")
    task_enum = set(schema["properties"]["taskId"]["enum"])
    if task_enum != {task["id"] for task in tasks["tasks"]}:
        raise ValueError("evidence task ids do not match the frozen task set")


def verify(source: Path) -> dict[str, object]:
    _validate_inventory(source.resolve(strict=True))
    _validate_corpus_and_tasks()
    _validate_protocol()
    _validate_evidence_schema()
    expected = _load("baselines.json")
    actual = baseline_record()
    if expected != actual:
        raise ValueError("deterministic baseline counts or hashes drifted")
    second = baseline_record()
    if second != actual:
        raise ValueError("baseline rendering is not deterministic")
    inventory = _load("suite-inventory.json")
    tasks = _load("tasks.json")
    return {
        "ok": True,
        "suiteContributorCount": len(inventory["contributors"]),
        "syntheticContributorCount": len(
            _load("corpus.json")["contributors"]
        ),
        "taskCount": len(tasks["tasks"]),
        "baselineHashes": {
            key: value["sha256"]
            for key, value in actual["baselines"].items()
        },
    }


def _parse_optional_bool(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--source", type=Path, required=True)
    subparsers.add_parser("render-baselines")
    subparsers.add_parser("verify-phase2")
    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("--root", type=Path, required=True)
    materialize_parser.add_argument("--source", type=Path, required=True)
    materialize_parser.add_argument("--deferral-level", required=True)
    materialize_parser.add_argument(
        "--reference-representation", required=True
    )
    materialize_parser.add_argument("--emphasis", required=True)
    materialize_parser.add_argument("--assembly", required=True)
    materialize_parser.add_argument("--task-id", required=True)
    materialize_parser.add_argument("--model", required=True)
    materialize_parser.add_argument("--repetition", type=int, required=True)
    materialize_parser.add_argument("--venue", default="acp")
    scenario_parser = subparsers.add_parser("configure-scenario")
    scenario_parser.add_argument("--template", type=Path, required=True)
    scenario_parser.add_argument("--output", type=Path, required=True)
    scenario_parser.add_argument("--deferral-level", required=True)
    scenario_parser.add_argument(
        "--reference-representation", required=True
    )
    scenario_parser.add_argument("--emphasis", required=True)
    scenario_parser.add_argument("--assembly", required=True)
    scenario_parser.add_argument("--task-id", required=True)
    scenario_parser.add_argument("--model", required=True)
    scenario_parser.add_argument("--repetition", type=int, required=True)
    verify_materialized_parser = subparsers.add_parser("verify-materialized")
    verify_materialized_parser.add_argument("--root", type=Path, required=True)
    observation_parser = subparsers.add_parser("observe")
    observation_parser.add_argument("--root", type=Path, required=True)
    observation_parser.add_argument("--results", type=Path, required=True)
    observation_parser.add_argument("--run-index", type=int, default=1)
    evidence_parser = subparsers.add_parser("write-evidence")
    evidence_parser.add_argument("--root", type=Path, required=True)
    evidence_parser.add_argument("--results", type=Path, required=True)
    evidence_parser.add_argument("--output", type=Path, required=True)
    evidence_parser.add_argument(
        "--verdict", choices=("PASS", "FAIL", "INVALID"), required=True
    )
    evidence_parser.add_argument("--jam")
    evidence_parser.add_argument(
        "--first-turn-correct", type=_parse_optional_bool
    )
    evidence_parser.add_argument(
        "--owner-provenance-retained", type=_parse_optional_bool
    )
    evidence_parser.add_argument(
        "--auto-loaded-guide", action="append", default=[]
    )
    evidence_parser.add_argument(
        "--critical-rule-violation", action="append", default=[]
    )
    evidence_parser.add_argument("--turn-count", type=int, default=1)
    evidence_parser.add_argument("--tool-call-count", type=int, default=0)
    evidence_parser.add_argument(
        "--elapsed-milliseconds", type=int, default=0
    )
    evidence_parser.add_argument(
        "--path-resolution-failure-count", type=int, default=0
    )
    evidence_parser.add_argument("--missing-path-count", type=int, default=0)
    evidence_parser.add_argument("--invented-path-count", type=int, default=0)
    evidence_parser.add_argument("--run-index", type=int, default=1)
    invalid_parser = subparsers.add_parser("write-invalid")
    invalid_parser.add_argument("--output", type=Path, required=True)
    invalid_parser.add_argument("--deferral-level", required=True)
    invalid_parser.add_argument(
        "--reference-representation", required=True
    )
    invalid_parser.add_argument("--emphasis", required=True)
    invalid_parser.add_argument("--assembly", required=True)
    invalid_parser.add_argument("--task-id", required=True)
    invalid_parser.add_argument("--model", required=True)
    invalid_parser.add_argument("--repetition", type=int, required=True)
    invalid_parser.add_argument(
        "--venue", choices=("interactive", "acp"), required=True
    )
    invalid_parser.add_argument("--jam", required=True)
    validate_evidence_parser = subparsers.add_parser("validate-evidence")
    validate_evidence_parser.add_argument("--path", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "render-baselines":
        print(json.dumps(baseline_record(), indent=2))
        return 0
    if args.command == "verify":
        result = verify(args.source)
    elif args.command == "verify-phase2":
        result = verify_phase2()
    elif args.command == "materialize":
        result = materialize(
            root=args.root,
            source=args.source,
            deferral_level=args.deferral_level,
            reference_representation=args.reference_representation,
            emphasis=args.emphasis,
            assembly=args.assembly,
            task_id=args.task_id,
            model=args.model,
            repetition=args.repetition,
            venue=args.venue,
        )
    elif args.command == "configure-scenario":
        result = configure_scenario(
            template=args.template,
            output=args.output,
            deferral_level=args.deferral_level,
            reference_representation=args.reference_representation,
            emphasis=args.emphasis,
            assembly=args.assembly,
            task_id=args.task_id,
            model=args.model,
            repetition=args.repetition,
        )
    elif args.command == "verify-materialized":
        result = verify_materialized(args.root)
    elif args.command == "observe":
        result = observation(args.root, args.results, args.run_index)
    elif args.command == "write-evidence":
        result = evidence_record(
            root=args.root,
            results=args.results,
            verdict=args.verdict,
            jam=args.jam,
            first_turn_correct=args.first_turn_correct,
            owner_provenance_retained=args.owner_provenance_retained,
            auto_loaded_guide_ids=args.auto_loaded_guide,
            critical_rule_violation_ids=args.critical_rule_violation,
            turn_count=args.turn_count,
            tool_call_count=args.tool_call_count,
            elapsed_milliseconds=args.elapsed_milliseconds,
            path_resolution_failure_count=(
                args.path_resolution_failure_count
            ),
            missing_path_count=args.missing_path_count,
            invented_path_count=args.invented_path_count,
            run_index=args.run_index,
        )
        write_evidence(args.output, result)
    elif args.command == "write-invalid":
        result = invalid_evidence_record(
            deferral_level=args.deferral_level,
            reference_representation=args.reference_representation,
            emphasis=args.emphasis,
            assembly=args.assembly,
            task_id=args.task_id,
            model=args.model,
            repetition=args.repetition,
            venue=args.venue,
            jam=args.jam,
        )
        write_evidence(args.output, result)
    elif args.command == "validate-evidence":
        loaded = json.loads(args.path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("evidence record must be a JSON object")
        validate_evidence(loaded)
        result = {"ok": True, "path": str(args.path)}
    else:
        raise ValueError(f"unsupported command: {args.command}")
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
