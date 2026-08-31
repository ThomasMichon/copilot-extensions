#!/usr/bin/env python3
"""Build and validate the synthetic context-injection clean-room fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

AUTHORITY = "context-injection@copilot-extensions"
ADOPTION_CONFIG = """\
schema: copilot-extensions.context-injection
version: 1
authority: context-injection@copilot-extensions
engine:
  schema: copilot-extensions.context-injection-engine
  version: 2
"""
CONTRIBUTOR_SCHEMA = "copilot-extensions.session-context-contributors"
CANARY_PATTERN = re.compile(r"CRCTX_CANARY_[A-Z]+_[A-Z]+_[0-9a-f]{48}")
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
TOOL_PATTERNS = (
    re.compile(r"\btool[_ -]?call\b", re.IGNORECASE),
    re.compile(
        r"^\s*(?:[>●▶]\s*)?(?:bash|powershell|view|read_file|glob|grep|rg)\b",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"\bfunctions\.(?:powershell|view|rg|glob|web_fetch)\b",
        re.IGNORECASE,
    ),
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _plugin_manifest(name: str, description: str) -> dict[str, object]:
    return {
        "name": name,
        "description": description,
        "version": "1.0.0",
        "runtimeScope": "none",
        "hooks": "hooks.json",
        "sessionContext": "session-context.json",
    }


def _producer(root: Path, name: str, token: str) -> None:
    root.mkdir(parents=True)
    _write_json(
        root / "plugin.json",
        _plugin_manifest(name, "Synthetic authority-aware context producer."),
    )
    producer = f"{name}@copilot-extensions/main"
    bash = (
        'root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-}}"; '
        'authority="$(dirname "$root")/context-injection"; '
        f'python3 "$authority/scripts/aggregate_context.py" --producer "{producer}"'
    )
    powershell = (
        "$root = $env:COPILOT_PLUGIN_ROOT; "
        "if (-not $root) { $root = $env:PLUGIN_ROOT }; "
        "$authority = Join-Path (Split-Path -Parent $root) 'context-injection'; "
        f"& python3 (Join-Path $authority 'scripts/aggregate_context.py') "
        f"--producer '{producer}'"
    )
    _write_json(
        root / "hooks.json",
        {
            "version": 1,
            "hooks": {
                "sessionStart": [
                    {
                        "type": "command",
                        "bash": bash,
                        "powershell": powershell,
                        "timeoutSec": 30,
                    }
                ]
            },
        },
    )
    _write_json(
        root / "session-context.json",
        {
            "schema": CONTRIBUTOR_SCHEMA,
            "version": 1,
            "complete": True,
            "sessionStart": {
                "sideEffects": "none",
                "context": "authority-aware",
            },
            "contributors": [
                {
                    "id": "main",
                    "pure": True,
                    "order": 100,
                    "timeoutSeconds": 5,
                    "maxBytes": 512,
                    "bash": ["scripts/emit.sh"],
                    "powershell": ["scripts/emit.ps1"],
                }
            ],
        },
    )
    scripts = root / "scripts"
    scripts.mkdir()
    payload = json.dumps({"additionalContext": token}, separators=(",", ":"))
    (scripts / "emit.sh").write_text(
        "#!/usr/bin/env bash\nprintf '%s' " + json.dumps(payload) + "\n",
        encoding="utf-8",
    )
    (scripts / "emit.ps1").write_text(
        f"[Console]::Out.Write('{payload}')\n",
        encoding="utf-8",
    )


def _side_effect(root: Path) -> None:
    root.mkdir(parents=True)
    name = "synthetic-side-effect"
    _write_json(
        root / "plugin.json",
        _plugin_manifest(name, "Synthetic idempotent side-effect-only hook."),
    )
    bash = (
        'root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-}}"; '
        'python3 "$root/scripts/apply.py"'
    )
    powershell = (
        "$root = $env:COPILOT_PLUGIN_ROOT; "
        "if (-not $root) { $root = $env:PLUGIN_ROOT }; "
        "& python3 (Join-Path $root 'scripts/apply.py')"
    )
    _write_json(
        root / "hooks.json",
        {
            "version": 1,
            "hooks": {
                "sessionStart": [
                    {
                        "type": "command",
                        "bash": bash,
                        "powershell": powershell,
                        "timeoutSec": 30,
                    }
                ]
            },
        },
    )
    _write_json(
        root / "session-context.json",
        {
            "schema": CONTRIBUTOR_SCHEMA,
            "version": 1,
            "complete": True,
            "sessionStart": {
                "sideEffects": "restart-safe-idempotent",
                "context": "none",
            },
            "contributors": [],
        },
    )
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "apply.py").write_text(
        """#!/usr/bin/env python3
import hashlib
import json
import os
import pathlib
import sys

try:
    value = json.load(sys.stdin)
    session_id = value["sessionId"]
    cwd = str(pathlib.Path(value["cwd"]).resolve(strict=True))
    if not isinstance(session_id, str) or not session_id:
        raise ValueError
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    cwd_hash = hashlib.sha256(os.path.normcase(cwd).encode()).hexdigest()[:16]
    marker = pathlib.Path.home() / "context-injection-eval" / "side-effects"
    (marker / f"{session_hash}-{cwd_hash}").mkdir(parents=True, exist_ok=True)
except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
    pass
print("{}", end="")
""",
        encoding="utf-8",
    )


def _settings(root: Path, variant: str) -> dict[str, object]:
    enabled = {
        AUTHORITY: True,
        "synthetic-side-effect@copilot-extensions": True,
    }
    for current in ("A", "B"):
        for role in ("alpha", "beta"):
            enabled[f"synthetic-{current.lower()}-{role}@copilot-extensions"] = (
                current == variant
            )
    return {"enabledPlugins": enabled}


def prepare(root: Path, source: Path, variant: str) -> None:
    variant = variant.upper()
    if variant not in {"A", "B"}:
        raise ValueError("CR_CONTEXT_VARIANT must be A or B")
    authority_source = source / "plugins" / "context-injection"
    if not (authority_source / "scripts" / "aggregate_context.py").is_file():
        raise FileNotFoundError(
            "mounted source checkout has no context-injection prototype"
        )
    if root.exists():
        shutil.rmtree(root)
    marketplace = root / "marketplace"
    plugins = marketplace / "plugins"
    shutil.copytree(authority_source, plugins / "context-injection")
    entries = [
        {
            "name": "context-injection",
            "description": "Unpublished context aggregation authority.",
            "version": json.loads(
                (authority_source / "plugin.json").read_text(encoding="utf-8")
            )["version"],
            "source": "plugins/context-injection",
        }
    ]
    expected: dict[str, list[str]] = {}
    for current in ("A", "B"):
        tokens: list[str] = []
        for role in ("alpha", "beta"):
            name = f"synthetic-{current.lower()}-{role}"
            token = (
                f"CRCTX_CANARY_{current}_{role.upper()}_{secrets.token_hex(24)}"
            )
            tokens.append(token)
            _producer(plugins / name, name, token)
            entries.append(
                {
                    "name": name,
                    "description": "Synthetic authority-aware context producer.",
                    "version": "1.0.0",
                    "source": f"plugins/{name}",
                }
            )
        expected[current] = sorted(tokens)
    _side_effect(plugins / "synthetic-side-effect")
    entries.append(
        {
            "name": "synthetic-side-effect",
            "description": "Synthetic idempotent side-effect-only hook.",
            "version": "1.0.0",
            "source": "plugins/synthetic-side-effect",
        }
    )
    for role in ("alpha", "beta"):
        shutil.copytree(
            plugins / f"synthetic-{variant.lower()}-{role}",
            plugins / f"selected-{role}",
        )
    _write_json(
        marketplace / ".github" / "plugin" / "marketplace.json",
        {
            "name": "copilot-extensions",
            "owner": {"name": "Clean Room", "email": "noreply@example.invalid"},
            "metadata": {
                "description": "Synthetic local context-injection marketplace.",
                "version": "1.0.0",
            },
            "plugins": entries,
        },
    )
    repos = root / "repos"
    for current in ("A", "B"):
        repo = repos / current.lower()
        (repo / ".git").mkdir(parents=True)
        _write_json(repo / ".github" / "copilot" / "settings.json", _settings(root, current))
        config = repo / ".context-injection" / "config.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(ADOPTION_CONFIG, encoding="utf-8")
        _write_json(
            root / f"expected-{current.lower()}.json",
            {
                "variant": current,
                "tokens": expected[current],
                "contributors": [
                    f"synthetic-{current.lower()}-alpha@copilot-extensions/main",
                    f"synthetic-{current.lower()}-beta@copilot-extensions/main",
                ],
            },
        )
    copilot = Path.home() / ".copilot"
    copilot.mkdir(parents=True, exist_ok=True)
    _write_json(
        copilot / "config.json",
        {"trustedFolders": [str(repos / "a"), str(repos / "b")]},
    )
    _write_json(copilot / "settings.json", {"enabledPlugins": {}})
    (root / "selected-variant").write_text(variant + "\n", encoding="utf-8")
    (root / "acp-cwd").write_text(
        str(repos / variant.lower()) + "\n", encoding="utf-8"
    )


def activate(root: Path) -> None:
    settings_path = Path.home() / ".copilot" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    marketplace = json.loads(
        (
            root
            / "marketplace"
            / ".github"
            / "plugin"
            / "marketplace.json"
        ).read_text(encoding="utf-8")
    )
    settings["enabledPlugins"] = {
        entry["name"] + "@copilot-extensions": False
        for entry in marketplace["plugins"]
    }
    _write_json(settings_path, settings)


def _payloads(root: Path) -> Path:
    live = root / "marketplace" / "plugins"
    if (live / "context-injection").is_dir():
        return live
    return Path.home() / ".copilot" / "installed-plugins" / "copilot-extensions"


def _invoke(
    root: Path,
    variant: str,
    session_id: str,
    caller: str,
) -> tuple[str, str]:
    installed = _payloads(root)
    authority = installed / "context-injection"
    producer = ""
    if caller != "authority":
        producer = (
            f"synthetic-{variant.lower()}-{caller}@copilot-extensions/main"
        )
        caller_root = installed / f"synthetic-{variant.lower()}-{caller}"
    else:
        caller_root = authority
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "invoke-host",
        "--engine",
        str(authority / "scripts" / "aggregate_context.py"),
    ]
    if producer:
        command.extend(["--producer", producer])
    command.append("--acp")
    for name in (
        "context-injection",
        f"synthetic-{variant.lower()}-alpha",
        f"synthetic-{variant.lower()}-beta",
        "synthetic-side-effect",
    ):
        command.extend(["--plugin-dir", str(installed / name)])
    repo = root / "repos" / variant.lower()
    environment = os.environ.copy()
    environment["COPILOT_PLUGIN_ROOT"] = str(caller_root)
    environment["COPILOT_CONTEXT_INJECTION_CACHE_DIR"] = str(
        root / "tier-p-cache"
    )
    result = subprocess.run(
        command,
        input=json.dumps(
            {"cwd": str(repo), "source": "new", "sessionId": session_id}
        ),
        text=True,
        capture_output=True,
        env=environment,
        check=True,
        timeout=35,
    )
    return result.stdout, result.stderr


def _expected(root: Path, variant: str) -> dict[str, object]:
    return json.loads(
        (root / f"expected-{variant.lower()}.json").read_text(encoding="utf-8")
    )


def _case_evidence(
    root: Path,
    variant: str,
    session_id: str,
    mode: str,
    outputs: list[tuple[str, str]],
) -> dict[str, object]:
    expected = _expected(root, variant)
    tokens = expected["tokens"]
    assert isinstance(tokens, list)
    parsed = [json.loads(stdout) for stdout, _ in outputs]
    nonempty = [value for value in parsed if value]
    if len(nonempty) != 1 or set(nonempty[0]) != {"additionalContext"}:
        raise AssertionError(f"{mode}: expected exactly one authority output")
    context = nonempty[0]["additionalContext"]
    if not isinstance(context, str):
        raise AssertionError(f"{mode}: authority output is not text")
    observed = CANARY_PATTERN.findall(context)
    counts = {_hash(token): observed.count(token) for token in tokens}
    if any(count != 1 for count in counts.values()) or set(observed) != set(tokens):
        raise AssertionError(f"{mode}: canary set is incomplete")
    if mode == "concurrent":
        if any(json.loads(outputs[index][0]) != {} for index in (0, 1)):
            raise AssertionError("concurrent: producer emitted context")
    else:
        expected_empty = len(outputs) - 1
        if sum(value == {} for value in parsed) != expected_empty:
            raise AssertionError(f"{mode}: producer emitted context")
    repo = root / "repos" / variant.lower()
    return {
        "mode": mode,
        "variant": variant,
        "expected_contributors": len(tokens),
        "observed_tokens": sorted(counts),
        "occurrence_counts": counts,
        "session_identity_hash": _hash(session_id),
        "cwd_identity_hash": _hash(os.path.normcase(str(repo.resolve()))),
        "nonempty_outputs": len(nonempty),
    }


def tier_p(root: Path) -> None:
    required = [
        "context-injection",
        "synthetic-a-alpha",
        "synthetic-a-beta",
        "synthetic-b-alpha",
        "synthetic-b-beta",
        "synthetic-side-effect",
        "selected-alpha",
        "selected-beta",
    ]
    missing = [name for name in required if not (_payloads(root) / name).is_dir()]
    if missing:
        raise FileNotFoundError(f"installed payloads missing: {', '.join(missing)}")
    cases: list[dict[str, object]] = []
    authority_first = [
        _invoke(root, "A", "tier-p-a-authority-first", caller)
        for caller in ("authority", "alpha", "beta")
    ]
    cases.append(
        _case_evidence(
            root,
            "A",
            "tier-p-a-authority-first",
            "authority-first",
            authority_first,
        )
    )
    producer_first = [
        _invoke(root, "A", "tier-p-a-producer-first", caller)
        for caller in ("alpha", "beta", "authority")
    ]
    cases.append(
        _case_evidence(
            root,
            "A",
            "tier-p-a-producer-first",
            "producer-first",
            producer_first,
        )
    )
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(
                _invoke,
                root,
                "B",
                "tier-p-b-concurrent",
                caller,
            )
            for caller in ("alpha", "beta", "authority")
        ]
        concurrent = [future.result() for future in futures]
    cases.append(
        _case_evidence(
            root,
            "B",
            "tier-p-b-concurrent",
            "concurrent",
            concurrent,
        )
    )
    cwd_hashes = {case["cwd_identity_hash"] for case in cases}
    a_sessions = {
        case["session_identity_hash"]
        for case in cases
        if case["variant"] == "A"
    }
    token_sets = {
        tuple(case["observed_tokens"])
        for case in cases
        if case["mode"] in {"authority-first", "concurrent"}
    }
    if len(cwd_hashes) != 2 or len(a_sessions) != 2 or len(token_sets) != 2:
        raise AssertionError("Tier-P identity separation was not established")
    evidence = {
        "schema": "copilot-extensions.context-injection-evidence",
        "version": 1,
        "tier": "P",
        "verdict": "PASS",
        "cases": cases,
        "summary": {
            "expected_contributors_per_cwd": 2,
            "cwd_identity_count": len(cwd_hashes),
            "repo_a_session_identity_count": len(a_sessions),
            "distinct_canary_sets": len(token_sets),
            "staged_payload_count": 4,
        },
    }
    _write_json(root / "tier-p-evidence.json", evidence)
    _write_json(Path("/home/operator/out") / "tier-p-context-evidence.json", evidence)


def verify_tier_p(root: Path) -> None:
    evidence = json.loads(
        (root / "tier-p-evidence.json").read_text(encoding="utf-8")
    )
    if evidence.get("tier") != "P" or evidence.get("verdict") != "PASS":
        raise SystemExit(1)
    summary = evidence.get("summary")
    if not isinstance(summary, dict) or summary != {
        "expected_contributors_per_cwd": 2,
        "cwd_identity_count": 2,
        "repo_a_session_identity_count": 2,
        "distinct_canary_sets": 2,
        "staged_payload_count": 4,
    }:
        raise SystemExit(1)


def invoke_host(engine: Path, producer: str) -> int:
    command = [sys.executable, str(engine)]
    if producer:
        command.extend(["--producer", producer])
    result = subprocess.run(
        command,
        input=sys.stdin.buffer.read(),
        capture_output=True,
        env=os.environ.copy(),
        check=False,
    )
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


def summarize_transcript(
    transcript: str,
    expected_tokens: list[str],
) -> dict[str, object]:
    clean = ANSI_PATTERN.sub("", transcript)
    observed = CANARY_PATTERN.findall(clean)
    expected_set = set(expected_tokens)
    counts = {_hash(token): observed.count(token) for token in expected_tokens}
    invented = [token for token in observed if token not in expected_set]
    strict = json.dumps(
        {"tokens": sorted(expected_tokens)},
        separators=(",", ":"),
    )
    strict_count = sum(line.strip() == strict for line in clean.splitlines())
    tool_markers = sum(
        len(pattern.findall(clean))
        for pattern in TOOL_PATTERNS
    )
    return {
        "observed_tokens": sorted({_hash(token) for token in observed}),
        "occurrence_counts": counts,
        "invented_token_count": len(invented),
        "strict_response_count": strict_count,
        "tool_marker_count": tool_markers,
        "pass": (
            all(count == 1 for count in counts.values())
            and len(observed) == len(expected_tokens)
            and not invented
            and strict_count == 1
            and tool_markers == 0
        ),
    }


def post_check(root: Path, results: Path) -> None:
    variant = (root / "selected-variant").read_text(encoding="utf-8").strip()
    expected = _expected(root, variant)
    tokens = expected["tokens"]
    contributors = expected["contributors"]
    if not isinstance(tokens, list) or not isinstance(contributors, list):
        raise TypeError("invalid expected evidence")
    eval_dir = results / "eval"
    transcripts = sorted(eval_dir.glob("run-*/transcript.txt"))
    if not transcripts and (eval_dir / "transcript.txt").is_file():
        transcripts = [eval_dir / "transcript.txt"]
    runs: list[dict[str, object]] = []
    for index, transcript in enumerate(transcripts, start=1):
        summary = summarize_transcript(
            transcript.read_text(encoding="utf-8", errors="replace"),
            tokens,
        )
        runs.append({"run": index, **summary})
    expected_cwd_hash = _hash(
        os.path.normcase(str((root / "repos" / variant.lower()).resolve()))
    )
    markers = sorted((root / "side-effects").glob("*")) if (
        root / "side-effects"
    ).is_dir() else []
    marker_pattern = re.compile(r"^([0-9a-f]{16})-([0-9a-f]{16})$")
    session_hashes: set[str] = set()
    cwd_hashes: set[str] = set()
    malformed_markers = 0
    for marker in markers:
        match = marker_pattern.fullmatch(marker.name)
        if match is None:
            malformed_markers += 1
            continue
        session_hashes.add(match.group(1))
        cwd_hashes.add(match.group(2))
    side_effect_ok = (
        len(session_hashes) == len(transcripts)
        and cwd_hashes == {expected_cwd_hash}
        and malformed_markers == 0
    )
    verdict = (
        "PASS"
        if transcripts and all(run["pass"] for run in runs) and side_effect_ok
        else "FAIL"
    )
    evidence = {
        "schema": "copilot-extensions.context-injection-evidence",
        "version": 1,
        "tier": "E",
        "variant": variant,
        "verdict": verdict,
        "expected_contributors": len(contributors),
        "expected_token_hashes": sorted(_hash(token) for token in tokens),
        "runs": runs,
        "session_identity_hashes": sorted(session_hashes),
        "cwd_identity_hashes": sorted(cwd_hashes),
        "side_effect": {
            "declaration": "restart-safe-idempotent/context:none",
            "marker_count": len(markers),
            "malformed_marker_count": malformed_markers,
            "pass": side_effect_ok,
        },
    }
    _write_json(eval_dir / "context-evidence.json", evidence)
    if verdict != "PASS":
        raise SystemExit(1)


def failure_class(results: Path) -> str:
    evidence = json.loads(
        (results / "eval" / "context-evidence.json").read_text(encoding="utf-8")
    )
    runs = evidence.get("runs")
    if isinstance(runs, list) and runs and all(
        isinstance(run, dict)
        and isinstance(run.get("occurrence_counts"), dict)
        and all(count == 1 for count in run["occurrence_counts"].values())
        and run.get("invented_token_count") == 0
        and run.get("tool_marker_count") == 0
        for run in runs
    ):
        return "literal-response-contract"
    return "scenario-transport-gap"


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--root", type=Path, required=True)
    prepare_parser.add_argument("--source", type=Path, required=True)
    prepare_parser.add_argument("--variant", required=True)
    for command in ("activate", "tier-p", "verify-tier-p"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--root", type=Path, required=True)
    post_parser = subparsers.add_parser("post-check")
    post_parser.add_argument("--root", type=Path, required=True)
    post_parser.add_argument("--results", type=Path, required=True)
    failure_parser = subparsers.add_parser("failure-class")
    failure_parser.add_argument("--results", type=Path, required=True)
    host_parser = subparsers.add_parser("invoke-host")
    host_parser.add_argument("--engine", type=Path, required=True)
    host_parser.add_argument("--producer", default="")
    host_parser.add_argument("--acp", action="store_true", required=True)
    host_parser.add_argument("--plugin-dir", action="append", default=[])
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.root, args.source, args.variant)
    elif args.command == "activate":
        activate(args.root)
    elif args.command == "tier-p":
        tier_p(args.root)
    elif args.command == "verify-tier-p":
        verify_tier_p(args.root)
    elif args.command == "post-check":
        post_check(args.root, args.results)
    elif args.command == "failure-class":
        print(failure_class(args.results))
    elif args.command == "invoke-host":
        return invoke_host(args.engine, args.producer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
