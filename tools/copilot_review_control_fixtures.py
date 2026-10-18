"""Exercise the prepared review-preservation overlay without activating policy."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
from pathlib import Path
from unittest import mock

import yaml

from copilot_review_setup_fixtures import SETUP

REVIEW = ".github/workflows/copilot-code-review.yml"
MANIFEST = ".github/promotion-control.json"
CONTROLS = [
    ".github/workflows/base-branch-reminder.yml",
    ".github/workflows/ci.yml",
    ".github/workflows/validate-and-promote.yml",
]
CASES = [
    "legacy-controls", "review", "foreign", "arbitrary-workflow", "extra-source",
    "missing-review", "symlink-review", "executable-review", "noncanonical",
    "removed-control", "case-alias", "unchanged-manifest", "self-authorizing",
]
ORIGINAL_ALLOWLIST = 'allowed = required | {".github/workflows/base-branch-reminder.yml"}'
REVIEW_ALLOWLIST = (
    'allowed = required | {".github/workflows/base-branch-reminder.yml", '
    '".github/workflows/copilot-code-review.yml"}'
)


def prepare_review_allowlist(text: str) -> str:
    if text.count(ORIGINAL_ALLOWLIST) + text.count(REVIEW_ALLOWLIST) != 1:
        raise ValueError("Expected exactly one named Main control allowlist")
    return text.replace(ORIGINAL_ALLOWLIST, REVIEW_ALLOWLIST)


def _admission_body(text: str) -> str:
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["main-gate"]["steps"]
    scripts = [step["run"] for step in steps
               if "declared = set(_paths(raw))" in step.get("run", "")]
    if len(scripts) != 1:
        raise ValueError("Expected exactly one actual Main control-admission step")
    bodies = re.findall(r"python -I -B - <<'PY'\n(.*?)\nPY", scripts[0], re.DOTALL)
    matches = [body for body in bodies if "declared = set(_paths(raw))" in body]
    if len(matches) != 1:
        raise ValueError("Expected exactly one literal control-admission body")
    ast.parse(matches[0])
    return matches[0]


def check_control_case(root: Path, case: str, *, prepared: bool) -> None:
    spec = importlib.util.spec_from_file_location(
        "arc_fixture_promotion_control", root / "tools" / "promotion_control.py",
    )
    assert spec is not None and spec.loader is not None
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    text = (root / ".github/workflows/base-branch-reminder.yml").read_text(encoding="utf-8")
    proposed = prepare_review_allowlist(text)
    selected = proposed if prepared else proposed.replace(REVIEW_ALLOWLIST, ORIGINAL_ALLOWLIST)
    code = _admission_body(selected)
    blobs: dict[str, bytes] = {}
    entries: dict[str, tuple[str, str, str]] = {}

    def put(path: str, raw: bytes, mode: str = "100644") -> None:
        blob = hashlib.sha256(raw).hexdigest()[:40]
        blobs[blob] = raw
        entries[path] = (mode, "blob", blob)

    def manifest(paths: list[str]) -> None:
        value = {"schema": control.SCHEMA, "version": 1, "files": sorted(paths)}
        put(MANIFEST, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())

    for path in CONTROLS:
        put(path, b"name: existing control\n")
    manifest(CONTROLS[1:] if case == "legacy-controls" else CONTROLS)
    before = entries.copy()
    paths = CONTROLS.copy()
    if case != "legacy-controls":
        paths.append(REVIEW)
        put(REVIEW, SETUP.encode())
    manifest(paths)
    if case == "arbitrary-workflow":
        paths.append(".github/workflows/other.yml")
        put(paths[-1], b"name: other\n")
        manifest(paths)
    elif case == "extra-source":
        put("tools/unrelated.py", b"raise AssertionError('candidate code')\n")
    elif case == "missing-review":
        entries.pop(REVIEW)
    elif case in ("symlink-review", "executable-review"):
        put(REVIEW, SETUP.encode(), "120000" if case == "symlink-review" else "100755")
    elif case == "noncanonical":
        put(MANIFEST, blobs[entries[MANIFEST][2]].rstrip())
    elif case == "removed-control":
        manifest([path for path in paths if path != CONTROLS[0]])
    elif case == "case-alias":
        put(".github/workflows/Copilot-code-review.yml", SETUP.encode())
    elif case == "unchanged-manifest":
        entries[MANIFEST] = before[MANIFEST]
    elif case == "self-authorizing":
        put(CONTROLS[0], proposed.encode())
    elif case not in ("legacy-controls", "review", "foreign"):
        raise ValueError(f"Unknown control fixture: {case}")

    def git(_repo: Path, args: list[str]) -> bytes:
        assert args[:2] in (["cat-file", "-s"], ["cat-file", "blob"])
        raw = blobs[args[2]]
        return str(len(raw)).encode() if args[1] == "-s" else raw

    env = {
        "BASE_SHA": "a" * 40, "HEAD_SHA": "b" * 40,
        "TARGET_REPOSITORY": "example/project",
        "HEAD_REPOSITORY": "example/fork" if case == "foreign" else "example/project",
    }
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.dict(sys.modules, {"promotion_control": control}),
        mock.patch.object(sys, "path", sys.path.copy()),
        mock.patch.object(control, "_tree", side_effect=[before, entries]),
        mock.patch.object(control, "_git", git),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        if case == "legacy-controls" or case in ("review", "self-authorizing") and prepared:
            exec(compile(code, "<prepared-review-admission>", "exec"), {})
        else:
            try:
                exec(compile(code, "<prepared-review-admission>", "exec"), {})
            except (RuntimeError, control.ControlPreservationError):
                return
            raise AssertionError(f"Unsafe control transaction was admitted: {case}")
