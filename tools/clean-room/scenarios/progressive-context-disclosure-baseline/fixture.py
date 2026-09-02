#!/usr/bin/env python3
"""Validate and render the frozen progressive-context-disclosure fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
TOKEN_HEX_LENGTH = 48
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
    reference: dict[str, object], representation: str
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
            f"guide {guide_id}; resolve from the synthetic payload root: "
            f"`{locator}`"
        )
    if representation == "backtick-absolute-contained":
        if reference["disposition"] == "unsafe":
            absolute = f"/home/operator/outside/{PurePosixPath(locator).name}"
        else:
            absolute = f"/home/operator/progressive-context/{locator}"
        return f"guide {guide_id}: `{absolute}`"
    if representation == "bare-labeled-path":
        return f"guide {guide_id} locator: {locator}"
    if representation == "html-comment-locator":
        return f"<!-- guide={guide_id}; locator={locator} -->"
    if representation == "structured-reference":
        return json.dumps(
            {"guideId": guide_id, "locator": locator},
            sort_keys=True,
            separators=(",", ":"),
        )
    raise ValueError(f"unsupported reference representation: {representation}")


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
        if {cue_guides[cue] for cue in triggered} != required:
            raise ValueError(
                f"task applicability cues do not select required guides: {task_id}"
            )
        if not required_rules <= critical_rule_ids:
            raise ValueError(f"task has unknown critical rule: {task_id}")
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


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--source", type=Path, required=True)
    subparsers.add_parser("render-baselines")
    args = parser.parse_args()

    if args.command == "render-baselines":
        print(json.dumps(baseline_record(), indent=2))
        return 0
    result = verify(args.source)
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
