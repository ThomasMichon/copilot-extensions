"""Run existing release-consumer recipes against one immutable promotion candidate."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

STATE = Path(".github") / "release-pipeline-state.json"
SCHEMA = "copilot-extensions.release-pipeline-state"
SHA = re.compile(r"[0-9a-f]{40}")
PLUGIN = re.compile(r"[a-z][a-z0-9-]{0,63}")
STANDALONE = {"dispatch": Path("services") / "dispatch", "index": Path("services") / "index"}
FORBIDDEN_AUTHORITY = (
    "GH_TOKEN", "GITHUB_TOKEN", "APERTURE_RELEASE_TOKEN", "PROMOTION_MERGE_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL",
)


class CandidateValidationError(RuntimeError):
    pass


def _environment() -> dict[str, str]:
    selectors = {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
    env = {
        key: value for key, value in os.environ.items()
        if key.upper() not in selectors and not key.upper().startswith("GIT_CONFIG")
    }
    env.update(
        GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0",
    )
    return env


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
        capture_output=True, text=True, timeout=30, env=_environment(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise CandidateValidationError(
            f"candidate Git inspection failed ({arguments[0]}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _regular(root: Path, relative: Path) -> Path:
    target = root / relative
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise CandidateValidationError("candidate consumer path must not be a symlink")
    if not target.is_file():
        raise CandidateValidationError(f"required candidate consumer file is absent: {relative}")
    return target


def validate(root: Path, candidate: str, dev: str, main_before: str) -> dict[str, str]:
    if any(not isinstance(value, str) or not SHA.fullmatch(value)
           for value in (candidate, dev, main_before)):
        raise CandidateValidationError("candidate, dev and main identities must be immutable SHAs")
    if root.is_symlink() or not root.is_dir():
        raise CandidateValidationError("candidate checkout must be an existing regular directory")
    root = root.resolve()
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise CandidateValidationError("candidate path is not its Git checkout root")
    if _git(root, "rev-parse", "HEAD") != candidate:
        raise CandidateValidationError("checked-out candidate does not match the trusted input")
    if _git(root, "show", "-s", "--format=%P", "HEAD").split() != [main_before]:
        raise CandidateValidationError("candidate does not have the exact main parent")
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise CandidateValidationError("candidate tracked inputs were modified")
    try:
        state = json.loads(_regular(root, STATE).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CandidateValidationError("candidate composition state is unreadable") from error
    promotion = state.get("last_promotion") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict) or state.get("schema") != SCHEMA
        or type(state.get("version")) is not int or state["version"] != 1
        or not isinstance(promotion, dict)
        or promotion.get("dev_head") != dev or promotion.get("main_before") != main_before
    ):
        raise CandidateValidationError("candidate composition does not match the trusted inputs")
    return {
        "candidate_sha": candidate, "dev_sha": dev, "main_before": main_before,
        "tree": _git(root, "rev-parse", "HEAD^{tree}"),
    }


def matrix(root: Path) -> dict[str, list[dict[str, str | int]]]:
    rows: list[dict[str, str | int]] = []
    plugins = root / "plugins"
    if plugins.is_symlink() or not plugins.is_dir():
        raise CandidateValidationError("candidate plugin directory is unavailable")
    for plugin in sorted(plugins.iterdir()):
        if plugin.is_symlink() or (plugin / "tests").is_symlink():
            raise CandidateValidationError("candidate plugin test root must not be a symlink")
        if not (plugin / "tests").exists():
            continue
        if not PLUGIN.fullmatch(plugin.name) or not (plugin / "tests").is_dir():
            raise CandidateValidationError("invalid candidate plugin test root")
        if not any((plugin / "tests").glob("test_*.py")):
            continue
        _regular(root, plugin.relative_to(root) / "plugin.json")
        rows.append({
            "os": "ubuntu-latest", "component": f"plugin:{plugin.name}",
            "minutes": 12 if plugin.name in ("agent-dispatch", "agent-index", "agent-worktrees") else 8,
        })
    if not rows or len(rows) > 64 or not any(
        row["component"] == "plugin:agent-worktrees" for row in rows
    ):
        raise CandidateValidationError("candidate plugin consumer inventory is incomplete")
    for component, platforms in (
        ("manager", ("ubuntu-latest",)),
        ("dispatch", ("ubuntu-latest",)),
        ("index", ("ubuntu-latest", "windows-latest")),
    ):
        relative = Path("worktree-manager") if component == "manager" else STANDALONE[component]
        _regular(root, relative / "pyproject.toml")
        rows.extend({
            "os": platform, "component": component, "minutes": 5 if component == "index" else 6,
        } for platform in platforms)
    return {"include": rows}


def recipes(root: Path, component: str) -> list[tuple[list[str], Path]]:
    plugin_runner = _regular(root, Path("tools") / "run-plugin-tests.py")
    if component.startswith("plugin:"):
        name = component.removeprefix("plugin:")
        if not PLUGIN.fullmatch(name):
            raise CandidateValidationError("invalid plugin consumer")
        _regular(root, Path("plugins") / name / "plugin.json")
        if name == "agent-worktrees":
            return [
                ([sys.executable, str(plugin_runner), name, flag], root)
                for flag in ("--collect-only", "--guards")
            ]
        return [([sys.executable, str(plugin_runner), name], root)]
    if component in STANDALONE:
        runner = _regular(root, Path("tools") / "run-standalone-tests.py")
        argv = [
            sys.executable, str(runner), str(STANDALONE[component]),
            "--prepare", "--admission-wait", "60",
        ]
        if component == "dispatch":
            argv += ["--timeout", "180"]
        else:
            argv += ["--smoke"]
        return [(argv, root)]
    if component == "manager":
        _regular(root, Path("worktree-manager") / "pyproject.toml")
        return [(["uv", "run", "--extra", "dev", "pytest", "-q"], root / "worktree-manager")]
    raise CandidateValidationError("unknown materialized consumer recipe")


def run(root: Path, candidate: str, dev: str, main_before: str, component: str) -> dict[str, str]:
    identity = validate(root, candidate, dev, main_before)
    if any(os.environ.get(name) for name in FORBIDDEN_AUTHORITY):
        raise CandidateValidationError("candidate tests must not receive release or API authority")
    root = root.resolve()
    expected = matrix(root)["include"]
    if not any(row["component"] == component for row in expected):
        raise CandidateValidationError("requested consumer is absent from the candidate inventory")
    commands = recipes(root, component)
    if not commands:
        raise CandidateValidationError("required candidate consumer has no executable recipe")
    for argv, directory in commands:
        try:
            minutes = next(row["minutes"] for row in expected if row["component"] == component)
            result = subprocess.run(
                argv, cwd=directory, check=False, timeout=int(minutes) * 60,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            validate(root, candidate, dev, main_before)
            raise CandidateValidationError(f"candidate consumer could not complete: {component}") from error
        validate(root, candidate, dev, main_before)
        if result.returncode:
            raise CandidateValidationError(
                f"candidate consumer failed: {component} (exit {result.returncode})"
            )
    return {**identity, "component": component, "outcome": "success"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--dev-sha", required=True)
    parser.add_argument("--main-before", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--matrix", action="store_true")
    mode.add_argument("--component")
    args = parser.parse_args(argv)
    try:
        validate(args.root, args.candidate_sha, args.dev_sha, args.main_before)
        result = matrix(args.root) if args.matrix else run(
            args.root, args.candidate_sha, args.dev_sha, args.main_before, args.component,
        )
    except CandidateValidationError as error:
        print(f"materialized-candidate: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
