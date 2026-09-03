#!/usr/bin/env python3
"""Write a zero-turn INVALID record without depending on the source fixture."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jam", required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    cell = manifest["experiment"]
    task = manifest["expected_outcome"]["selected_task"]
    model = str(cell["model"])
    jam = str(args.jam)
    repetition = int(cell["repetition"])
    freeze_epoch = int(cell.get("freezeEpoch", 1))
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", model) is None:
        raise ValueError("invalid model identifier")
    if re.fullmatch(r"[a-z0-9-]{1,64}", jam) is None:
        raise ValueError("invalid jam identifier")
    if repetition < 1:
        raise ValueError("invalid repetition")
    if freeze_epoch < 1:
        raise ValueError("invalid freeze epoch")
    variant = "-".join(
        (
            str(cell["deferralLevel"]).lower(),
            REFERENCE_CODES[cell["referenceRepresentation"]],
            EMPHASIS_CODES[cell["emphasis"]],
            ASSEMBLY_CODES[cell["assembly"]],
        )
    )
    record = {
        "schema": "copilot-extensions.progressive-context-evidence",
        "version": 1,
        "runId": (
            f"e{freeze_epoch}-{variant}-{task['id']}-r{repetition}"
        ),
        "variantId": variant,
        "taskId": task["id"],
        "model": model,
        "venue": "acp",
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(record, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
