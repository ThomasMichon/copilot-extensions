"""Pre-merge syntax validation of core infrastructure files.

Validates PowerShell, Bash, and Python files that, if broken, could
prevent the repository from bootstrapping.  The paths to validate are
configured per-repo via ``validate_paths`` in config.yaml.
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import git_ops, output

# Legacy hardcoded paths — used as default when no config is provided.
# New deployments should set validate_paths in config.yaml instead.
_LEGACY_CORE_PATHS: list[str] = [
    "tools/setup/",
    "tools/worktree/",
    "tools/vault/",
    ".github/skills/log-session/",
    ".github/skills/recap/",
    ".github/skills/services/",
]

# Backward-compat alias
CORE_PATHS = _LEGACY_CORE_PATHS


@dataclass
class ValidationFailure:
    """A single file validation failure."""
    file: str
    check_type: str
    errors: str


def _check_powershell(full_path: Path) -> ValidationFailure | None:
    """Validate PowerShell syntax using pwsh -c with Parser."""
    try:
        result = subprocess.run(
            [
                "pwsh.exe" if platform.system() == "Windows" else "pwsh",
                "-NoProfile", "-Command",
                f"""
                $errors = $null
                [void][System.Management.Automation.Language.Parser]::ParseFile(
                    '{full_path}', [ref]$null, [ref]$errors
                )
                if ($errors.Count -gt 0) {{
                    $errors | ForEach-Object {{ "Line $($_.Extent.StartLineNumber): $($_.Message)" }}
                    exit 1
                }}
                """,
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return ValidationFailure(
                file=str(full_path),
                check_type="PowerShell syntax",
                errors=result.stdout.strip() or result.stderr.strip(),
            )
    except FileNotFoundError:
        return None  # pwsh not available, skip
    return None


def _check_bash(full_path: Path) -> ValidationFailure | None:
    """Validate Bash syntax using bash -n."""
    bash_cmd = None

    if platform.system() == "Windows":
        # Use native bash on Windows (Git Bash, etc.) -- avoid WSL calls
        # that can hang when WSL is unavailable or unresponsive.
        bash_cmd = "bash"
    else:
        bash_cmd = "bash"

    if bash_cmd:
        try:
            result = subprocess.run(
                [bash_cmd, "-n", str(full_path)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                return ValidationFailure(
                    file=str(full_path),
                    check_type="Bash syntax",
                    errors=result.stderr.strip(),
                )
        except FileNotFoundError:
            pass  # bash not available, skip

    return None


def _check_python(full_path: Path) -> ValidationFailure | None:
    """Validate Python syntax using py_compile, then optionally ruff."""
    try:
        result = subprocess.run(
            ["python", "-m", "py_compile", str(full_path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return ValidationFailure(
                file=str(full_path),
                check_type="Python syntax",
                errors=result.stderr.strip(),
            )
    except FileNotFoundError:
        pass  # python not available

    # Best-effort ruff check (warn-only — don't block on missing ruff)
    try:
        result = subprocess.run(
            ["ruff", "check", "--no-fix", "--force-exclude", str(full_path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0 and result.stdout.strip():
            return ValidationFailure(
                file=str(full_path),
                check_type="ruff",
                errors=result.stdout.strip(),
            )
    except FileNotFoundError:
        pass  # ruff not available
    return None


_VALIDATORS: dict[str, type] = {
    ".ps1": _check_powershell,  # type: ignore[dict-item]
    ".sh": _check_bash,  # type: ignore[dict-item]
    ".py": _check_python,  # type: ignore[dict-item]
}


def get_changed_core_files(
    worktree_path: str,
    default_branch: str = "origin/master",
    validate_paths: list[str] | None = None,
) -> list[str]:
    """Get files changed in core paths relative to the default branch.

    Args:
        worktree_path: Path to the worktree.
        default_branch: Branch to diff against.
        validate_paths: Repo-relative path prefixes to check.
            Falls back to ``_LEGACY_CORE_PATHS`` if None.
    """
    core_paths = validate_paths if validate_paths is not None else _LEGACY_CORE_PATHS
    if not core_paths:
        return []

    try:
        result = git_ops.git(
            "diff", "--name-only", f"{default_branch}...HEAD",
            cwd=worktree_path, check=False,
        )
        if result.returncode != 0:
            result = git_ops.git(
                "diff", "--name-only", default_branch,
                cwd=worktree_path, check=False,
            )
    except Exception:
        return []

    changed = [f for f in result.stdout.splitlines() if f.strip()]
    core_files: list[str] = []
    for f in changed:
        for cp in core_paths:
            if f.startswith(cp):
                core_files.append(f)
                break

    return core_files


def validate_files(
    worktree_path: str,
    files: list[str] | None = None,
    *,
    default_branch: str = "origin/master",
    dry_run: bool = False,
    validate_paths: list[str] | None = None,
) -> list[ValidationFailure]:
    """Validate core infrastructure files.

    Args:
        worktree_path: Path to the worktree.
        files: Explicit files to check. If None, auto-detect from diff.
        default_branch: Branch to diff against.
        dry_run: If True, list files without validating.
        validate_paths: Repo-relative path prefixes to check.
            Falls back to ``_LEGACY_CORE_PATHS`` if None.

    Returns:
        List of failures (empty = all passed).
    """
    if files is None:
        files = get_changed_core_files(
            worktree_path, default_branch, validate_paths=validate_paths,
        )

    if not files:
        output.ok("No core infrastructure files changed — validation skipped.")
        return []

    print()
    print(f"🔍 Validating {len(files)} core infrastructure file(s)...")
    print()

    if dry_run:
        for f in files:
            print(f"  Would validate: {f}")
        print()
        output.ok("Dry run complete — no validation performed.")
        return []

    failures: list[ValidationFailure] = []

    for rel_path in files:
        wt = Path(worktree_path)
        full_path = wt / rel_path.replace("/", os.sep) if platform.system() == "Windows" else wt / rel_path

        if not full_path.exists():
            continue  # deleted file, fine

        ext = full_path.suffix.lower()
        validator = _VALIDATORS.get(ext)

        if validator:
            failure = validator(full_path)  # type: ignore[operator]
            if failure:
                failure.file = rel_path
                failures.append(failure)
                output.err(f"{rel_path} — {failure.check_type} error")
                for line in failure.errors.splitlines()[:3]:
                    print(f"       {line}")
            else:
                output.ok(rel_path)
        else:
            print(f"  ─ {rel_path} — no validator for {ext}")

    print()
    if failures:
        output.err(f"Validation FAILED — {len(failures)} file(s) have errors")
    else:
        output.ok(f"All {len(files)} core file(s) passed validation.")

    return failures


# Needed for os.sep reference
import os
