"""Run existing release-consumer recipes against one immutable promotion candidate."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from materialized_candidate_inventory import (  # noqa: E402
    PLUGIN,
    STANDALONE,
    CandidateValidationError,
    fleet_partitions,
    matrix as inventory_matrix,
    python_suites,
    regular as _regular,
    standalone_roster,
)

STATE = Path(".github") / "release-pipeline-state.json"
SCHEMA = "copilot-extensions.release-pipeline-state"
SHA = re.compile(r"[0-9a-f]{40}")
FORBIDDEN_AUTHORITY = (
    "GH_TOKEN", "GITHUB_TOKEN", "APERTURE_RELEASE_TOKEN", "PROMOTION_MERGE_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL",
)


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


def matrix(root: Path, dev_root: Path, dev: str) -> dict[str, list[dict[str, str | int]]]:
    if dev_root.is_symlink() or not dev_root.is_dir():
        raise CandidateValidationError("validated development checkout is unavailable")
    dev_root = dev_root.resolve()
    if (
        not SHA.fullmatch(dev)
        or Path(_git(dev_root, "rev-parse", "--show-toplevel")).resolve() != dev_root
        or _git(dev_root, "rev-parse", "HEAD") != dev
        or _git(dev_root, "status", "--porcelain", "--untracked-files=no")
    ):
        raise CandidateValidationError("development inventory is not the validated immutable input")
    return inventory_matrix(root, python_suites(dev_root), standalone_roster(dev_root))


def recipes(
    root: Path, component: str, *, bootstrap_root: Path | None = None,
) -> list[tuple[list[str], Path]]:
    plugin_runner = _regular(root, Path("tools") / "run-plugin-tests.py")
    if component == "validator-bootstrap":
        if bootstrap_root is None or not bootstrap_root.is_dir() or any(bootstrap_root.iterdir()):
            raise CandidateValidationError("validator bootstrap requires a fresh isolated workspace")
        target = bootstrap_root / "validator"
        interpreter = target / "Scripts" / "python.exe" if os.name == "nt" else target / "bin" / "python"
        adapter = _regular(root, Path("tools") / "seasoned_uv.py")
        requirements = _regular(root, Path("tools") / "artifact_validation_requirements.txt")
        return [
            (["uv", "venv", "--python", sys.executable, str(target)], root),
            ([sys.executable, str(adapter), "--registry-only", "--uv", "uv", "--",
              "--python", str(interpreter), "--no-config", "--only-binary", ":all:",
              "--require-hashes", "--index-url",
              os.environ.get("ARTIFACT_VALIDATION_INDEX_URL") or "https://pypi.org/simple",
              "-r", str(requirements)], root),
            ([str(interpreter), "-I", "-B", "-c",
              "import packaging; print(packaging.__version__)"], root),
        ]
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
    fleet = component.startswith("fleet-manager:")
    standalone = "fleet-manager" if fleet else component
    if standalone in STANDALONE and (standalone != "fleet-manager" or fleet):
        if fleet and component.removeprefix("fleet-manager:") not in fleet_partitions(root):
            raise CandidateValidationError("unknown canonical Fleet partition")
        runner = _regular(root, Path("tools") / "run-standalone-tests.py")
        argv = [
            sys.executable, str(runner), STANDALONE[standalone].as_posix(),
            "--prepare", "--admission-wait", "60",
        ]
        if component == "dispatch":
            argv += ["--timeout", "180"]
        elif component == "index":
            argv += ["--smoke"]
        elif component == "chat":
            argv += ["--timeout", "120"]
        else:
            argv += ["--timeout", "120"]
            return [
                ([sys.executable, str(_regular(root, Path("tools/seasoned_uv.py"))),
                  "--uv", "uv", "--", "--system", "pytest"], root),
                ([sys.executable, "-m", "pytest", "-q",
                  str(_regular(root, Path("tools/test_fleet_ci_scope.py")))], root),
                (argv, root),
            ]
        return [(argv, root)]
    if component == "manager":
        _regular(root, Path("worktree-manager") / "pyproject.toml")
        return [(["uv", "run", "--extra", "dev", "pytest", "-q"], root / "worktree-manager")]
    raise CandidateValidationError("unknown materialized consumer recipe")


def run(
    root: Path, candidate: str, dev: str, main_before: str, component: str, *, dev_root: Path,
) -> dict[str, str]:
    identity = validate(root, candidate, dev, main_before)
    if any(os.environ.get(name) for name in FORBIDDEN_AUTHORITY):
        raise CandidateValidationError("candidate tests must not receive release or API authority")
    root = root.resolve()
    expected = matrix(root, dev_root, dev)["include"]
    if not any(row["component"] == component for row in expected):
        raise CandidateValidationError("requested consumer is absent from the candidate inventory")
    workspace = (
        tempfile.TemporaryDirectory(prefix="materialized-validator-bootstrap-")
        if component == "validator-bootstrap" else nullcontext(None)
    )
    with workspace as temporary:
        commands = (
            recipes(root, component, bootstrap_root=Path(temporary))
            if temporary else recipes(root, component)
        )
        env = _environment()
        if temporary:
            env["UV_CACHE_DIR"] = str(Path(temporary) / "uv-cache")
        fleet = component.startswith("fleet-manager:")
        if fleet:
            env["PYTEST_ADDOPTS"] = '-m "not client_distribution_full and not observation_retention_full"'
        if not commands:
            raise CandidateValidationError("required candidate consumer has no executable recipe")
        for index, (argv, directory) in enumerate(commands):
            command_env = env
            if fleet and index == len(commands) - 1:
                selector = fleet_partitions(root)[component.removeprefix("fleet-manager:")]
                command_env = {**env, "PYTEST_ADDOPTS": env["PYTEST_ADDOPTS"] + f' -k "{selector}"'}
            try:
                minutes = next(row["minutes"] for row in expected if row["component"] == component)
                result = subprocess.run(
                    argv, cwd=directory, check=False, timeout=int(minutes) * 60, env=command_env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                validate(root, candidate, dev, main_before)
                matrix(root, dev_root, dev)
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
    parser.add_argument("--dev-root", type=Path, required=True)
    parser.add_argument("--main-before", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--matrix", action="store_true")
    mode.add_argument("--component")
    args = parser.parse_args(argv)
    try:
        validate(args.root, args.candidate_sha, args.dev_sha, args.main_before)
        result = matrix(args.root, args.dev_root, args.dev_sha) if args.matrix else run(
            args.root, args.candidate_sha, args.dev_sha, args.main_before, args.component,
            dev_root=args.dev_root,
        )
    except CandidateValidationError as error:
        print(f"materialized-candidate: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
