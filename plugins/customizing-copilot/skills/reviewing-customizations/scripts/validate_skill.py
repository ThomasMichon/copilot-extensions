#!/usr/bin/env python3
"""Check SKILL.md loading with the installed Copilot CLI, without parsing YAML."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unicodedata


def _issue(category: str, code: str, message: str) -> dict:
    return {"category": category, "code": code, "message": message}


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _temporary_workspace(paths: list[Path]) -> tempfile.TemporaryDirectory:
    # Windows' usual temp directory is inside USERPROFILE. Never place the
    # staged project there, where user or ancestor repository config may leak in.
    forbidden = [Path.home().resolve()]
    for key in ("HOME", "USERPROFILE", "COPILOT_HOME"):
        if os.environ.get(key):
            forbidden.append(Path(os.environ[key]).resolve())
    for path in [Path.cwd(), *paths]:
        path = path.resolve()
        for ancestor in (path, *path.parents):
            if (ancestor / ".git").exists():
                forbidden.append(ancestor.resolve())
                break
    parents = []
    if os.name == "nt":
        if os.environ.get("SystemRoot"):
            parents.append(Path(os.environ["SystemRoot"]) / "Temp")
    else:
        parents.extend([Path("/tmp"), Path("/var/tmp")])
    parents.append(Path(tempfile.gettempdir()))
    failures = []
    for parent in dict.fromkeys(parents):
        parent = parent.resolve()
        if any(parent.is_relative_to(root) for root in forbidden):
            failures.append(f"{parent}: inside a user home or candidate repository")
            continue
        # HOME can have been overridden while TEMP still points inside the real
        # profile. Refuse ancestor discovery roots even in that split-home case.
        if any(
            (ancestor / marker).exists()
            for ancestor in (parent, *parent.parents)
            for marker in (".git", ".github", ".copilot", ".claude", ".agents")
        ):
            failures.append(f"{parent}: ancestor contains repository or user configuration")
            continue
        try:
            return tempfile.TemporaryDirectory(prefix="copilot-skill-check-", dir=parent)
        except OSError as error:
            failures.append(f"{parent}: {error}")
    raise OSError("No safe temporary workspace is available: " + "; ".join(failures))


def _environment(root: Path) -> dict[str, str]:
    # An allowlist also removes unknown future COPILOT_*, AGENT_*, auth, proxy,
    # NODE_OPTIONS and plugin/session variables rather than chasing their names.
    launcher_keys = {
        "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
        "OS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432",
        "NUMBER_OF_PROCESSORS", "LANG", "LC_ALL", "LC_CTYPE",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in launcher_keys}
    home = root / "home"
    directories = {
        "HOME": home,
        "USERPROFILE": home,
        "COPILOT_HOME": home / ".copilot",
        "APPDATA": home / "AppData" / "Roaming",
        "LOCALAPPDATA": home / "AppData" / "Local",
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_DATA_HOME": home / ".local" / "share",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_STATE_HOME": home / ".local" / "state",
        "XDG_RUNTIME_DIR": home / ".run",
        "GH_CONFIG_DIR": home / ".config" / "gh",
        "TEMP": root / "tmp",
        "TMP": root / "tmp",
        "TMPDIR": root / "tmp",
    }
    for key, directory in directories.items():
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    drive, home_path = os.path.splitdrive(str(home))
    env.update(
        HOMEDRIVE=drive,
        HOMEPATH=home_path,
        COPILOT_AUTO_UPDATE="false",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=str(home / ".gitconfig"),
        GIT_TERMINAL_PROMPT="0",
        GH_PROMPT_DISABLED="1",
        NO_COLOR="1",
        TERM="dumb",
    )
    return env


def _run(command: list[str], cwd: Path, env: dict[str, str], timeout: float):
    return subprocess.run(
        command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="strict",
        timeout=timeout, check=False, shell=False,
    )


def _metadata_issues(loaded: dict) -> list[dict]:
    issues = []
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", loaded["name"]):
        issues.append(_issue("policy", "name-kebab-case", "Resolved name must be lower-kebab-case."))
    description = loaded["description"]
    if not description.strip():
        issues.append(_issue("policy", "description-empty", "Resolved description must not be blank."))
    if "<" in description or ">" in description:
        issues.append(_issue("policy", "description-angle-brackets", "Resolved description must not contain < or >."))
    # Newlines and tabs are legitimate results of YAML block scalars.
    if any(unicodedata.category(char) in {"Cc", "Cs"} and char not in "\t\r\n" for char in description):
        issues.append(_issue("policy", "description-controls", "Resolved description contains control characters or unpaired surrogates."))
    units = len(description.encode("utf-16-le", errors="surrogatepass")) // 2
    if units > 1024:
        issues.append(_issue("policy", "description-budget", f"Resolved description uses {units} UTF-16 units; the authoring limit is 1024."))
    return issues


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _read_inventory(output: str) -> list[dict]:
    inventory = json.loads(output)
    if not isinstance(inventory, list):
        raise ValueError("Expected a JSON array from copilot skill list --json.")
    for entry in inventory:
        if (
            not isinstance(entry, dict)
            or any(not isinstance(entry.get(key), str) for key in ("name", "description", "source", "path"))
            or type(entry.get("enabled")) is not bool
            or not Path(entry["path"]).is_absolute()
        ):
            raise ValueError("Unknown skill-list record schema; expected name/description/source/path strings, an absolute directory path, and enabled boolean.")
    return inventory


def _finish(report: dict) -> dict:
    states = {candidate["status"] for candidate in report["candidates"]}
    if report["issues"]:
        states.add(report["failure_status"])
    report["status"] = "blocked" if "blocked" in states else "invalid" if "invalid" in states else "valid"
    del report["failure_status"]
    return report


def _load(command, project, env, timeout, report, candidates, expected_description, runtime_only):
    version = _run([*command, "--version"], project, env, timeout)
    report["cli"]["version_output"] = version.stdout
    report["cli"]["version_stderr"] = version.stderr
    first_line = version.stdout.splitlines()[0] if version.stdout else ""
    match = re.fullmatch(r"GitHub Copilot CLI (\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\.?", first_line)
    if version.returncode != 0 or version.stderr.strip() or not match:
        report["issues"].append(_issue("runtime", "cli-version", f"Cannot establish Copilot CLI version (exit {version.returncode}); see version_output/version_stderr."))
        return
    report["cli"]["version"] = match.group(1)
    result = _run([*command, "skill", "list", "--json"], project, env, timeout)
    report["cli"]["list_exit_code"] = result.returncode
    report["cli"]["list_stderr"] = result.stderr
    if result.returncode != 0:
        report["issues"].append(_issue("runtime", "cli-exit", f"Copilot skill list exited {result.returncode}; see list_stderr."))
        return
    inventory = _read_inventory(result.stdout)
    by_path = {}
    expected_paths = {_path_key(item["staged_path"]) for item in candidates}
    for entry in inventory:
        key = _path_key(entry["path"])
        if key in by_path:
            raise ValueError(f"Duplicate skill-list path: {entry['path']}")
        by_path[key] = entry
        if key not in expected_paths and entry["source"] != "builtin":
            raise ValueError(f"Unexpected non-builtin skill outside the staged candidates: {entry['path']}")
    for candidate in candidates:
        entry = by_path.get(_path_key(candidate["staged_path"]))
        candidate["runtime_accepted"] = False
        candidate["status"] = "invalid"
        if entry is None:
            candidate["issues"].append(_issue("runtime", "candidate-not-loaded", "Exact staged candidate directory was omitted. The file may be invalid or shadowed by a duplicate name; rerun it alone to distinguish."))
            continue
        candidate["loaded"] = entry
        if entry["source"] != "project" or entry["enabled"] is not True:
            candidate["issues"].append(_issue("runtime", "candidate-not-enabled-project", "The staged candidate must load enabled with project source."))
            continue
        candidate["runtime_accepted"] = True
        if not runtime_only:
            candidate["issues"].extend(_metadata_issues(entry))
        if expected_description is not None and entry["description"] != expected_description:
            candidate["issues"].append(_issue("expectation", "description-mismatch", f"Resolved description did not exactly match {expected_description!r}."))
        candidate["status"] = "invalid" if candidate["issues"] else "valid"
    if result.stderr.strip():
        report["failure_status"] = "invalid"
        report["issues"].append(_issue("runtime", "cli-diagnostics", "Copilot emitted diagnostics on stderr; see list_stderr. A zero exit alone is not acceptance."))


def validate(
    paths,
    *,
    copilot: str | os.PathLike | None = None,
    copilot_args=(),
    expected_description: str | None = None,
    timeout: float = 60.0,
    runtime_only: bool = False,
) -> dict:
    """Return schema_version=1 evidence; no CLI installation, YAML parser or model call.

    Status is valid, invalid (exit 1), or blocked (exit 2). Runtime acceptance
    and authoring policy are separate. Missing paths can mean invalid bytes OR a name
    collision; rerun a missing candidate alone to distinguish those cases.
    timeout applies to each subprocess. copilot_args are trusted launcher-prefix
    arguments, e.g. an absolute installed index.js path passed to node.
    """
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    paths = list(paths)
    report = {
        "schema_version": 1,
        "scope": "file-loading-only",
        "policy": "runtime-only" if runtime_only else "authoring-policy",
        "status": "blocked",
        "cli": {
            "executable": None, "arguments": [], "version": None,
            "version_output": None, "version_stderr": None,
            "list_exit_code": None, "list_stderr": None,
        },
        "issues": [],
        "candidates": [],
        "failure_status": "blocked",
    }
    if not paths or (expected_description is not None and len(paths) != 1):
        report["issues"].append(_issue("input", "candidate-count", "Supply at least one candidate; --expect-description requires exactly one."))
        return _finish(report)
    if not math.isfinite(timeout) or timeout <= 0:
        report["issues"].append(_issue("input", "timeout", "Timeout must be finite and greater than zero."))
        return _finish(report)
    candidates = report["candidates"]
    for value in paths:
        path = Path(os.path.abspath(value))
        if path.is_dir():
            path /= "SKILL.md"
        candidate = {
            "path": str(path), "sha256": None, "staged_path": None,
            "status": "blocked", "runtime_accepted": None, "loaded": None, "issues": [],
        }
        candidates.append(candidate)
        if path.name != "SKILL.md" or not path.is_file() or not path.parent.name:
            candidate["status"] = "invalid"
            candidate["issues"].append(_issue("input", "skill-file", "Expected an existing SKILL.md file or a directory containing it."))
    eligible = [candidate for candidate in candidates if not candidate["issues"]]
    if not eligible:
        return _finish(report)
    executable = shutil.which(os.fspath(copilot) if copilot is not None else "copilot")
    if executable is None:
        report["issues"].append(_issue("runtime", "cli-unavailable", "Copilot CLI was not found. Supply --copilot PATH (and --copilot-arg for a trusted launcher prefix). Nothing was installed."))
        return _finish(report)
    executable = str(Path(executable).absolute())
    report["cli"]["executable"] = executable
    report["cli"]["arguments"] = [os.fspath(arg) for arg in copilot_args]
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        report["issues"].append(_issue("runtime", "shell-launcher", "Use copilot.exe, or an explicit node executable plus --copilot-arg=<installed index.js>; batch launchers cannot guarantee literal argument handling."))
        return _finish(report)
    try:
        with _temporary_workspace([Path(item["path"]) for item in candidates]) as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            env = _environment(root)
            command = [
                executable, *report["cli"]["arguments"],
                "--no-auto-update", "--config-dir", env["COPILOT_HOME"],
            ]
            try:
                for index, candidate in enumerate(eligible):
                    original = Path(candidate["path"])
                    directory = project / ".github" / "skills" / f"candidate-{index:04d}" / original.parent.name
                    directory.mkdir(parents=True)
                    candidate["staged_path"] = str(directory)
                    # Stream every byte, including oversized files, to let the
                    # actual runtime enforce its own limit. Never truncate.
                    staged = directory / "SKILL.md"
                    shutil.copyfile(original, staged)
                    candidate["sha256"] = _digest(staged)
                _load(command, project, env, timeout, report, eligible, expected_description, runtime_only)
            finally:
                _check_integrity(eligible)
    except subprocess.TimeoutExpired:
        report["failure_status"] = "blocked"
        report["issues"].append(_issue("runtime", "cli-timeout", f"Copilot CLI exceeded the {timeout:g}s subprocess timeout."))
    except (OSError, UnicodeError, ValueError) as error:
        report["failure_status"] = "blocked"
        report["issues"].append(_issue("runtime", "conformance-blocked", str(error)))
    return _finish(report)


def _check_integrity(candidates: list[dict]) -> None:
    for candidate in candidates:
        if candidate["sha256"] is None:
            continue
        try:
            changed = _digest(Path(candidate["path"])) != candidate["sha256"]
        except OSError as error:
            message = f"Cannot re-read the original after CLI execution: {error}"
        else:
            if not changed:
                continue
            message = "Original SKILL.md changed during validation; rerun against stable bytes."
        candidate["status"] = "blocked"
        candidate["runtime_accepted"] = None
        candidate["issues"].append(_issue("integrity", "original-changed", message))


def exit_code(report: dict) -> int:
    return {"valid": 0, "invalid": 1, "blocked": 2}[report["status"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="Skill directories or SKILL.md files.")
    parser.add_argument("--copilot", help="Installed executable (default: shutil.which('copilot')).")
    parser.add_argument("--copilot-arg", action="append", default=[], help="Trusted launcher-prefix argument; repeat for node <installed index.js>.")
    parser.add_argument("--expect-description", help="Exact resolved description; requires one candidate.")
    parser.add_argument("--timeout", type=float, default=60.0, help="Seconds per CLI subprocess (default: 60).")
    parser.add_argument("--runtime-only", action="store_true", help="Skip authoring metadata hygiene, not runtime or expectation checks.")
    parser.add_argument("--json", action="store_true", help="Print only the structured report to stdout.")
    args = parser.parse_args(argv)
    report = validate(
        args.paths, copilot=args.copilot, copilot_args=args.copilot_arg,
        expected_description=args.expect_description, timeout=args.timeout,
        runtime_only=args.runtime_only,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        print(f"{report['status'].upper()}: file-loading-only; Copilot CLI {report['cli']['version'] or 'unavailable'}; {report['policy']}")
        for candidate in report["candidates"]:
            print(f"{candidate['status'].upper()}: {json.dumps(candidate['path'])}")
            if candidate["loaded"] is not None:
                print("  name: " + json.dumps(candidate["loaded"]["name"], ensure_ascii=True))
                print("  description: " + json.dumps(candidate["loaded"]["description"], ensure_ascii=True))
            for issue in candidate["issues"]:
                print(f"  [{issue['category']}/{issue['code']}] {issue['message']}")
        for issue in report["issues"]:
            print(f"[{issue['category']}/{issue['code']}] {issue['message']}")
        for key in ("version_stderr", "list_stderr"):
            if report["cli"][key]:
                print(f"{key}: {json.dumps(report['cli'][key], ensure_ascii=True)}")
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
