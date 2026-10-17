#!/usr/bin/env python3
"""Build and validate the native-hook-composition clean-room fixture.

Proves whether the host itself composes ``additionalContext`` from every
independent ``sessionStart`` hook (native multi-hook composition)
instead of retaining only the last hook's output. Two plugins that share NO
code, wrapper, authority, or adoption configuration each emit one distinct
high-entropy canary directly from a plain ``sessionStart`` command hook.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path

MARKETPLACE_NAME = "native-hook-composition"
PLUGIN_NAMES = ("synthetic-hook-alpha", "synthetic-hook-beta")
CANARY_PATTERN = re.compile(r"CRHOOK_CANARY_[A-Z]+_[0-9a-f]{48}")
SESSION_LINE_PATTERN = re.compile(r"^\[i\] Connected: session=([\w-]+)", re.MULTILINE)
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
TOOL_PATTERNS = (
    re.compile(r"\btool[_ -]?call\b", re.IGNORECASE),
    re.compile(
        r"^\s*(?:[>\u25cf\u25b6]\s*)?(?:bash|powershell|view|read_file|glob|grep|rg)\b",
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


def _plugin(root: Path, name: str, token: str, markers_dir: Path) -> None:
    root.mkdir(parents=True)
    _write_json(
        root / "plugin.json",
        {
            "name": name,
            "description": (
                "Synthetic independent plugin with a plain sessionStart hook."
            ),
            "version": "1.0.0",
            "runtimeScope": "none",
            "hooks": "hooks.json",
        },
    )
    payload = json.dumps({"additionalContext": token}, separators=(",", ":"))
    scripts = root / "scripts"
    scripts.mkdir()
    # Independent, model-invisible proof that THIS hook's process actually ran,
    # decoupled from whether its additionalContext reached the model. Written
    # from stdin's sessionId so a marker can be tied to the exact session that
    # produced it, never inferred from stdout content.
    marker_dir = markers_dir / name
    emit_py = f"""#!/usr/bin/env python3
import json, pathlib, sys, time

session_id = "unknown"
try:
    raw = sys.stdin.read()
    value = json.loads(raw) if raw.strip() else {{}}
    if isinstance(value, dict) and isinstance(value.get("sessionId"), str) \\
            and value["sessionId"]:
        session_id = value["sessionId"]
except Exception:
    pass
try:
    marker_dir = pathlib.Path({str(marker_dir)!r})
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / (session_id + ".invoked")).write_text(
        str(time.time()), encoding="utf-8"
    )
except OSError:
    pass
sys.stdout.write({payload!r})
"""
    (scripts / "emit.py").write_text(emit_py, encoding="utf-8")
    bash = (
        'root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-}}"; '
        'python3 "$root/scripts/emit.py"'
    )
    powershell = (
        "$root = $env:COPILOT_PLUGIN_ROOT; "
        "if (-not $root) { $root = $env:PLUGIN_ROOT }; "
        "& python3 (Join-Path $root 'scripts/emit.py')"
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
                        "timeoutSec": 15,
                    }
                ]
            },
        },
    )


def _settings(enable: bool) -> dict[str, object]:
    return {
        "enabledPlugins": {
            f"{name}@{MARKETPLACE_NAME}": enable for name in PLUGIN_NAMES
        }
    }


def prepare(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    marketplace = root / "marketplace"
    plugins = marketplace / "plugins"
    markers_dir = root / "hook-invocations"
    tokens: dict[str, str] = {}
    for name in PLUGIN_NAMES:
        role = name.rsplit("-", 1)[-1].upper()
        token = f"CRHOOK_CANARY_{role}_{secrets.token_hex(24)}"
        tokens[name] = token
        _plugin(plugins / name, name, token, markers_dir)
    _write_json(
        marketplace / ".github" / "plugin" / "marketplace.json",
        {
            "name": MARKETPLACE_NAME,
            "owner": {"name": "Clean Room", "email": "noreply@example.invalid"},
            "metadata": {
                "description": (
                    "Synthetic marketplace for the native-hook-composition"
                    " witness."
                ),
                "version": "1.0.0",
            },
            "plugins": [
                {
                    "name": name,
                    "description": (
                        "Synthetic independent plugin with a plain"
                        " sessionStart hook."
                    ),
                    "version": "1.0.0",
                    "source": f"plugins/{name}",
                }
                for name in tokens
            ],
        },
    )
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    _write_json(repo / ".github" / "copilot" / "settings.json", _settings(True))
    _write_json(root / "expected.json", {"tokens": sorted(tokens.values())})
    copilot = Path.home() / ".copilot"
    copilot.mkdir(parents=True, exist_ok=True)
    _write_json(copilot / "config.json", {"trustedFolders": [str(repo)]})
    _write_json(copilot / "settings.json", {"enabledPlugins": {}})
    (root / "acp-cwd").write_text(str(repo) + "\n", encoding="utf-8")


def activate(root: Path) -> None:
    del root
    settings_path = Path.home() / ".copilot" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["enabledPlugins"] = _settings(False)["enabledPlugins"]
    _write_json(settings_path, settings)


def _payloads(root: Path) -> Path:
    live = root / "marketplace" / "plugins"
    if (live / PLUGIN_NAMES[0]).is_dir():
        return live
    return Path.home() / ".copilot" / "installed-plugins" / MARKETPLACE_NAME


def tier_p(root: Path) -> None:
    payloads = _payloads(root)
    expected = json.loads((root / "expected.json").read_text(encoding="utf-8"))
    tokens = expected["tokens"]
    markers_dir = root / "hook-invocations"
    tier_p_session = "tier-p-marker-proof"
    observed: list[str] = []
    for name in PLUGIN_NAMES:
        plugin_root = payloads / name
        hooks = json.loads(
            (plugin_root / "hooks.json").read_text(encoding="utf-8")
        )
        command = hooks["hooks"]["sessionStart"][0]["bash"]
        env = os.environ.copy()
        env["COPILOT_PLUGIN_ROOT"] = str(plugin_root)
        result = subprocess.run(
            ["bash", "-c", command],
            input=json.dumps(
                {"sessionId": tier_p_session, "cwd": str(root / "repo")}
            ),
            text=True,
            capture_output=True,
            env=env,
            check=True,
            timeout=15,
        )
        value = json.loads(result.stdout)
        if set(value) != {"additionalContext"}:
            raise AssertionError(f"{name}: unexpected hook output shape")
        observed.append(value["additionalContext"])
        marker = markers_dir / name / f"{tier_p_session}.invoked"
        if not marker.is_file():
            raise AssertionError(
                f"{name}: hook ran but wrote no independent invocation marker"
            )
    if sorted(observed) != sorted(tokens):
        raise AssertionError(
            "Tier-P: hook fixtures do not emit the expected canary pair"
        )
    _write_json(
        root / "tier-p-evidence.json",
        {"verdict": "PASS", "hash_pairs": sorted(_hash(t) for t in tokens)},
    )


def verify_tier_p(root: Path) -> None:
    evidence = json.loads(
        (root / "tier-p-evidence.json").read_text(encoding="utf-8")
    )
    if evidence.get("verdict") != "PASS":
        raise SystemExit(1)


def summarize_transcript(
    transcript: str, expected_tokens: list[str]
) -> dict[str, object]:
    clean = ANSI_PATTERN.sub("", transcript)
    observed = CANARY_PATTERN.findall(clean)
    expected_set = set(expected_tokens)
    counts = {_hash(token): observed.count(token) for token in expected_tokens}
    invented = [token for token in observed if token not in expected_set]
    strict = json.dumps({"tokens": sorted(expected_tokens)}, separators=(",", ":"))
    strict_count = sum(line.strip() == strict for line in clean.splitlines())
    tool_markers = sum(len(pattern.findall(clean)) for pattern in TOOL_PATTERNS)
    return {
        "observed_token_hashes": sorted({_hash(t) for t in observed}),
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


def _hook_invocation_evidence(root: Path, expected_session_count: int) -> dict[str, object]:
    """Independent, model-invisible proof that BOTH hooks actually ran for
    every real eval session -- decoupled entirely from whether either hook's
    additionalContext reached the model. Compares the set of session UUIDs
    each plugin's hook independently recorded a marker for (excluding the
    Tier-P precondition's synthetic session id). The transcript's own
    ``session=<short-id>`` banner is a DISPLAY id, not the real session UUID
    passed to sessionStart hooks, so per-run attribution by that id is not
    reliable; set equality between the two plugins is.
    """
    markers_dir = root / "hook-invocations"
    sentinel = "tier-p-marker-proof"
    sessions_by_plugin: dict[str, set[str]] = {}
    for name in PLUGIN_NAMES:
        plugin_dir = markers_dir / name
        sessions_by_plugin[name] = {
            marker.stem
            for marker in plugin_dir.glob("*.invoked")
            if marker.stem != sentinel
        } if plugin_dir.is_dir() else set()
    alpha_sessions = sessions_by_plugin[PLUGIN_NAMES[0]]
    beta_sessions = sessions_by_plugin[PLUGIN_NAMES[1]]
    sets_equal = alpha_sessions == beta_sessions
    session_count_matches_runs = len(alpha_sessions) == expected_session_count
    return {
        "alpha_marker_sessions": sorted(alpha_sessions),
        "beta_marker_sessions": sorted(beta_sessions),
        "sets_equal": sets_equal,
        "expected_session_count": expected_session_count,
        "session_count_matches_runs": session_count_matches_runs,
        "all_hooks_invoked_every_run": sets_equal and session_count_matches_runs,
    }


def post_check(root: Path, results: Path) -> None:
    expected = json.loads((root / "expected.json").read_text(encoding="utf-8"))
    tokens = expected["tokens"]
    eval_dir = results / "eval"
    transcripts = sorted(eval_dir.glob("run-*/transcript.txt"))
    if not transcripts and (eval_dir / "transcript.txt").is_file():
        transcripts = [eval_dir / "transcript.txt"]
    runs: list[dict[str, object]] = []
    for index, transcript in enumerate(transcripts, start=1):
        raw = transcript.read_text(encoding="utf-8", errors="replace")
        summary = summarize_transcript(raw, tokens)
        summary["run"] = index
        summary["transcript"] = str(transcript)
        summary["display_session"] = (
            SESSION_LINE_PATTERN.search(raw).group(1)
            if SESSION_LINE_PATTERN.search(raw)
            else None
        )
        runs.append(summary)
    hook_invocation = _hook_invocation_evidence(root, len(transcripts))
    verdict = "PASS" if runs and all(run["pass"] for run in runs) else "FAIL"
    single_token_every_run = bool(runs) and all(
        sum(1 for c in run["occurrence_counts"].values() if c == 1) == 1
        and sum(run["occurrence_counts"].values()) == 1
        for run in runs
    )
    hook_invocation_incomplete = not hook_invocation["all_hooks_invoked_every_run"]
    evidence = {
        "schema": "copilot-extensions.native-hook-composition-evidence",
        "version": 1,
        "verdict": verdict,
        "single_token_every_run": single_token_every_run,
        "hook_invocation_incomplete": hook_invocation_incomplete,
        "hook_invocation": hook_invocation,
        "runs": runs,
    }
    _write_json(results / "eval" / "context-evidence.json", evidence)
    if verdict != "PASS":
        raise SystemExit(1)


def failure_class(results: Path) -> None:
    evidence_path = results / "eval" / "context-evidence.json"
    if not evidence_path.is_file():
        print("scenario-transport-gap")
        return
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if evidence.get("hook_invocation_incomplete"):
        print("hook-invocation-incomplete")
        return
    if evidence.get("single_token_every_run"):
        print("context-composition-incomplete")
        return
    runs = evidence.get("runs", [])
    if any(run.get("tool_marker_count", 0) for run in runs):
        print("literal-mode-violation")
        return
    print("context-composition-regression")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("--root", required=True, type=Path)

    p_activate = sub.add_parser("activate")
    p_activate.add_argument("--root", required=True, type=Path)

    p_tier_p = sub.add_parser("tier-p")
    p_tier_p.add_argument("--root", required=True, type=Path)

    p_verify = sub.add_parser("verify-tier-p")
    p_verify.add_argument("--root", required=True, type=Path)

    p_post = sub.add_parser("post-check")
    p_post.add_argument("--root", required=True, type=Path)
    p_post.add_argument("--results", required=True, type=Path)

    p_fail = sub.add_parser("failure-class")
    p_fail.add_argument("--results", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.root)
    elif args.command == "activate":
        activate(args.root)
    elif args.command == "tier-p":
        tier_p(args.root)
    elif args.command == "verify-tier-p":
        verify_tier_p(args.root)
    elif args.command == "post-check":
        post_check(args.root, args.results)
    elif args.command == "failure-class":
        failure_class(args.results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
