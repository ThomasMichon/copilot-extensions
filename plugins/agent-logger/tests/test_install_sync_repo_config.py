"""Regression coverage for the scheduled-sync repo-config discovery wiring
(the ``config_repo:`` -> ``agent-worktrees repos find`` -> ``.agent-logger.yaml``
lookup added alongside schema v3's ``sync.local_path``) in both installers.

Without this wiring, the scheduled sync (a systemd service on POSIX, a
Scheduled Task on Windows) runs with no useful working directory of its own,
so it would never discover a repo's schema v3 ``sync.local_path`` declaration
on its own -- confirmed live (see the sync.local_path activation fix this
file accompanies). These tests exercise the actual generation code with a
stubbed ``agent-worktrees repos find``, rather than only checking static
installer text markers.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
_INSTALL_SH = PLUGIN / "scripts" / "install.sh"
_INSTALL_PS1 = PLUGIN / "scripts" / "install.ps1"


def _resolve_bash() -> str | None:
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.is_file():
        return str(git_bash)
    path = os.environ.get("PATH")
    if not path:
        return None
    filtered = os.pathsep.join(
        part
        for part in path.split(os.pathsep)
        if "windowsapps" not in part.lower()
        and part.rstrip("\\").lower() != r"c:\windows\system32"
    )
    return shutil.which("bash", path=filtered)


_BASH = _resolve_bash()


def _extract_sh_function(name: str) -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    start = text.index(f"{name}()")
    end = text.index("\n}\n", start)
    return text[start : end + 2]


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _venv_python_path(root: Path) -> Path:
    """The platform-specific interpreter path inside a venv rooted at
    ``root`` -- mirrors ``tools/run-plugin-tests.py``'s own
    ``_venv_python()`` exactly (``Scripts/python.exe`` on Windows,
    ``bin/python`` elsewhere -- NOT ``bin/python3``, which the installer's
    own venv-health check (``_venv_healthy()``) never guarantees), since
    the two must agree on where a venv's interpreter lives for the
    ``.test-venvs`` fallback below to find it."""
    if sys.platform == "win32":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def _resolve_test_venv_root() -> Path:
    """Return a venv root this environment actually has, preferring the
    facility's own managed test-runner venv location
    (``.test-venvs/<sys.platform>/agent-logger``, see
    ``tools/run-plugin-tests.py``'s ``VENV_ROOT``) over a raw dev-local
    ``.venv`` -- the standard CI/test-runner path materializes ONLY the
    former, so relying on ``.venv`` alone would silently skip these tests
    (via the trailing ``pytest.skip``) under that runner, giving a false
    sense of coverage. Falls back to ``.venv`` for a plain
    ``uv run pytest`` dev invocation, which doesn't populate
    ``.test-venvs`` at all.
    """
    managed = PLUGIN.parents[1] / ".test-venvs" / sys.platform / "agent-logger"
    for candidate in (managed, PLUGIN / ".venv"):
        python_path = _venv_python_path(candidate)
        if python_path.is_file() or python_path.is_symlink():
            return candidate
    pytest.skip(
        "no usable venv found (.test-venvs/<platform>/agent-logger or .venv)"
    )
    raise AssertionError("unreachable")  # pytest.skip always raises


def _real_venv_with_python() -> Path:
    """The plugin's OWN usable dev/CI venv (which already has pyyaml
    installed, a hard dependency of this whole plugin) -- so write_units'
    real YAML-based config_repo parsing runs against a genuine interpreter
    with the right packages on its path. See
    :func:`_resolve_test_venv_root` for which venv root this resolves to.

    Returned directly (not copied/symlinked into a fake structure): Python's
    venv site-packages association is resolved via ``pyvenv.cfg`` sitting
    next to the executable's OWN invocation path, not by following symlinks
    to their final target -- a lone ``bin/python3`` symlink planted in an
    otherwise-empty fake directory (no sibling ``pyvenv.cfg``) silently
    loses that association and falls back to the base interpreter's
    site-packages, missing pyyaml, even though ``sys.executable`` resolves
    correctly. Using the real venv wholesale sidesteps this entirely.
    """
    return _resolve_test_venv_root()


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_write_units_skips_untrusted_config_repo(tmp_path: Path) -> None:
    """`agent-worktrees repos find` also resolves `reference`-class
    registrations, which are not guaranteed to be a git checkout at all --
    a valid, non-symlinked ``.agent-logger.yaml`` present in the discovered
    directory must NOT be wired into the service unless that directory
    passes the same registered-project + default-branch trust decision
    normal (CWD-based) discovery applies. No
    ``AGENT_LOGGER_TRUST_REPO_CONFIG`` override and no real git remotes
    here, so the directory is untrusted and the Environment= line must be
    omitted entirely, even though nothing else about the discovery would
    otherwise reject it."""
    install_dir = tmp_path / "install"
    unit_dir = tmp_path / "units"
    install_dir.mkdir()
    unit_dir.mkdir()
    (install_dir / "config.yaml").write_text("config_repo: demo\n", encoding="utf-8")

    repo_dir = tmp_path / "demo-repo"
    repo_dir.mkdir()
    (repo_dir / ".agent-logger.yaml").write_text("schema_version: 3\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "agent-worktrees",
        "#!/bin/sh\n"
        'if [ "$1" = "repos" ] && [ "$2" = "find" ] && [ "$3" = "demo" ]; then\n'
        f'  echo "{repo_dir}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        f'INSTALL_DIR="{install_dir}"\n'
        f'UNIT_DIR="{unit_dir}"\n'
        f'VENV="{_real_venv_with_python()}"\n'
        'TIMER_NAME="agent-logger-sync"\n'
        'chg() { :; }\n'
        + _extract_sh_function("write_units")
        + "\nwrite_units\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
    env.pop("AGENT_LOGGER_TRUST_REPO_CONFIG", None)
    subprocess.run(
        [_BASH, str(harness)], capture_output=True, text=True, env=env, timeout=20, check=True
    )

    unit_text = (unit_dir / "agent-logger-sync.service").read_text(encoding="utf-8")
    assert "AGENT_LOGGER_REPO_CONFIG" not in unit_text


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
@pytest.mark.parametrize("repo_dir_name", ["demo-repo", "demo repo with spaces"])
def test_write_units_sets_repo_config_env_when_config_repo_adopted(
    tmp_path: Path, repo_dir_name: str
) -> None:
    """A machine-local config_repo: <name> plus an adopted registry match
    must produce a correctly-quoted Environment= line in the generated
    systemd unit -- including when the discovered path contains whitespace
    (systemd splits an unquoted Environment= value on whitespace)."""
    install_dir = tmp_path / "install"
    unit_dir = tmp_path / "units"
    install_dir.mkdir()
    unit_dir.mkdir()
    (install_dir / "config.yaml").write_text("config_repo: demo\n", encoding="utf-8")

    repo_dir = tmp_path / repo_dir_name
    repo_dir.mkdir()
    (repo_dir / ".agent-logger.yaml").write_text("schema_version: 3\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "agent-worktrees",
        "#!/bin/sh\n"
        'if [ "$1" = "repos" ] && [ "$2" = "find" ] && [ "$3" = "demo" ]; then\n'
        f'  echo "{repo_dir}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        f'INSTALL_DIR="{install_dir}"\n'
        f'UNIT_DIR="{unit_dir}"\n'
        f'VENV="{_real_venv_with_python()}"\n'
        'TIMER_NAME="agent-logger-sync"\n'
        'chg() { :; }\n'
        + _extract_sh_function("write_units")
        + "\nwrite_units\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        # Bypasses the real registered-project/default-branch trust
        # decision for exactly this stub repo dir -- a plain tmp_path
        # directory has no git remotes at all, so without this the new
        # trust gate (see test_write_units_skips_untrusted_config_repo)
        # would correctly reject it and this test would no longer
        # observe the Environment= line it asserts on.
        "AGENT_LOGGER_TRUST_REPO_CONFIG": str(repo_dir),
    }
    subprocess.run(
        [_BASH, str(harness)], capture_output=True, text=True, env=env, timeout=20, check=True
    )

    unit_text = (unit_dir / "agent-logger-sync.service").read_text(encoding="utf-8")
    expected = f'Environment="AGENT_LOGGER_REPO_CONFIG={repo_dir}/.agent-logger.yaml"'
    assert expected in unit_text


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_write_units_generated_env_resolves_under_clean_environment(
    tmp_path: Path,
) -> None:
    """The generated unit's own Environment= lines, applied to a process
    with NONE of the installer's own AGENT_WORKTREES_REPOS_YAML context (a
    non-default registry location, in this test), must be sufficient for
    find_repo_config() to resolve the same file the installer validated --
    the actual failure mode a scheduled systemd/task run hits: it inherits
    only what install.sh/install.ps1 wrote into the unit, never the
    installer process's own environment. Uses a REAL git checkout with a
    registered remote (no AGENT_LOGGER_TRUST_REPO_CONFIG override anywhere)
    so trust is re-derived honestly from live git remotes + the propagated
    registry file, never bypassed."""
    from .conftest import init_git_repo

    install_dir = tmp_path / "install"
    unit_dir = tmp_path / "units"
    install_dir.mkdir()
    unit_dir.mkdir()
    (install_dir / "config.yaml").write_text("config_repo: demo\n", encoding="utf-8")

    repo_dir = tmp_path / "demo-repo"
    init_git_repo(repo_dir, remote="https://example.test/example-owner/demo.git", branch="main")
    (repo_dir / ".agent-logger.yaml").write_text("schema_version: 3\n", encoding="utf-8")

    registry = tmp_path / "custom-repos.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "repos": {
                    "demo": {
                        "remote": "https://example.test/example-owner/demo.git",
                        "default_branch": "main",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "agent-worktrees",
        "#!/bin/sh\n"
        'if [ "$1" = "repos" ] && [ "$2" = "find" ] && [ "$3" = "demo" ]; then\n'
        f'  echo "{repo_dir}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        f'INSTALL_DIR="{install_dir}"\n'
        f'UNIT_DIR="{unit_dir}"\n'
        f'VENV="{_real_venv_with_python()}"\n'
        'TIMER_NAME="agent-logger-sync"\n'
        'chg() { :; }\n'
        + _extract_sh_function("write_units")
        + "\nwrite_units\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    # The custom AGENT_WORKTREES_REPOS_YAML here is deliberately only the
    # INSTALLER process's context -- the whole point is to prove the
    # generated unit carries it forward on its own.
    install_env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "AGENT_WORKTREES_REPOS_YAML": str(registry),
    }
    subprocess.run(
        [_BASH, str(harness)],
        capture_output=True,
        text=True,
        env=install_env,
        timeout=20,
        check=True,
    )

    unit_text = (unit_dir / "agent-logger-sync.service").read_text(encoding="utf-8")
    repo_config_match = re.search(
        r'Environment="AGENT_LOGGER_REPO_CONFIG=(.*)"$', unit_text, re.MULTILINE
    )
    repos_yaml_match = re.search(
        r'Environment="AGENT_WORKTREES_REPOS_YAML=(.*)"$', unit_text, re.MULTILINE
    )
    assert repo_config_match is not None
    assert repos_yaml_match is not None
    assert repos_yaml_match.group(1) == str(registry)

    # A clean environment: no ambient AGENT_LOGGER_TRUST_REPO_CONFIG
    # anywhere, no default-location registry file, no inherited process
    # environment at all -- ONLY the unit's own AGENT_LOGGER_REPO_CONFIG
    # and the propagated AGENT_WORKTREES_REPOS_YAML.
    proc = subprocess.run(
        [
            str(_venv_python_path(_real_venv_with_python())),
            "-c",
            "from agent_logger.config import find_repo_config; print(find_repo_config())",
        ],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "AGENT_LOGGER_REPO_CONFIG": repo_config_match.group(1),
            "AGENT_WORKTREES_REPOS_YAML": repos_yaml_match.group(1),
        },
        cwd=str(tmp_path),
        timeout=20,
        check=True,
    )

    assert proc.stdout.strip() == str(repo_dir / ".agent-logger.yaml")


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_write_units_falls_through_symlinked_alias_to_next_candidate(
    tmp_path: Path,
) -> None:
    """If the highest-priority alias (.agent-logger.yaml) is a symlink, it
    must be skipped in favor of the next valid alias -- selecting it would
    embed a path find_repo_config() immediately rejects outright (it never
    falls through to a lower-priority alias for an explicit path), leaving
    the scheduled sync with no usable AGENT_LOGGER_REPO_CONFIG at all even
    though a perfectly good alias exists."""
    install_dir = tmp_path / "install"
    unit_dir = tmp_path / "units"
    install_dir.mkdir()
    unit_dir.mkdir()
    (install_dir / "config.yaml").write_text("config_repo: demo\n", encoding="utf-8")

    repo_dir = tmp_path / "demo-repo"
    repo_dir.mkdir()
    outside_target = tmp_path / "outside.yaml"
    outside_target.write_text("schema_version: 3\n", encoding="utf-8")
    (repo_dir / ".agent-logger.yaml").symlink_to(outside_target)
    (repo_dir / ".agent-logger.yml").write_text("schema_version: 3\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "agent-worktrees",
        "#!/bin/sh\n"
        'if [ "$1" = "repos" ] && [ "$2" = "find" ] && [ "$3" = "demo" ]; then\n'
        f'  echo "{repo_dir}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        f'INSTALL_DIR="{install_dir}"\n'
        f'UNIT_DIR="{unit_dir}"\n'
        f'VENV="{_real_venv_with_python()}"\n'
        'TIMER_NAME="agent-logger-sync"\n'
        'chg() { :; }\n'
        + _extract_sh_function("write_units")
        + "\nwrite_units\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        # Bypasses the real registered-project/default-branch trust
        # decision for exactly this stub repo dir -- a plain tmp_path
        # directory has no git remotes at all, so without this the new
        # trust gate (see test_write_units_skips_untrusted_config_repo)
        # would correctly reject it and this test would no longer
        # observe the Environment= line it asserts on.
        "AGENT_LOGGER_TRUST_REPO_CONFIG": str(repo_dir),
    }
    subprocess.run(
        [_BASH, str(harness)], capture_output=True, text=True, env=env, timeout=20, check=True
    )

    unit_text = (unit_dir / "agent-logger-sync.service").read_text(encoding="utf-8")
    expected = f'Environment="AGENT_LOGGER_REPO_CONFIG={repo_dir}/.agent-logger.yml"'
    assert expected in unit_text


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
@pytest.mark.parametrize(
    "config_yaml_body",
    [
        "config_repo: demo  # central config\n",
        'config_repo: "demo"  # central config\n',
        "config_repo: 'demo'\n",
    ],
)
def test_write_units_parses_config_repo_with_real_yaml_semantics(
    tmp_path: Path, config_yaml_body: str
) -> None:
    """A trailing '# comment' or a quoted scalar must not corrupt the
    extracted repo name -- a line-oriented sed/tr pass (the pre-fix
    implementation) mishandles both, silently yielding no match."""
    install_dir = tmp_path / "install"
    unit_dir = tmp_path / "units"
    install_dir.mkdir()
    unit_dir.mkdir()
    (install_dir / "config.yaml").write_text(config_yaml_body, encoding="utf-8")

    repo_dir = tmp_path / "demo-repo"
    repo_dir.mkdir()
    (repo_dir / ".agent-logger.yaml").write_text("schema_version: 3\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "agent-worktrees",
        "#!/bin/sh\n"
        'if [ "$1" = "repos" ] && [ "$2" = "find" ] && [ "$3" = "demo" ]; then\n'
        f'  echo "{repo_dir}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        f'INSTALL_DIR="{install_dir}"\n'
        f'UNIT_DIR="{unit_dir}"\n'
        f'VENV="{_real_venv_with_python()}"\n'
        'TIMER_NAME="agent-logger-sync"\n'
        'chg() { :; }\n'
        + _extract_sh_function("write_units")
        + "\nwrite_units\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        # Bypasses the real registered-project/default-branch trust
        # decision for exactly this stub repo dir -- a plain tmp_path
        # directory has no git remotes at all, so without this the new
        # trust gate (see test_write_units_skips_untrusted_config_repo)
        # would correctly reject it and this test would no longer
        # observe the Environment= line it asserts on.
        "AGENT_LOGGER_TRUST_REPO_CONFIG": str(repo_dir),
    }
    subprocess.run(
        [_BASH, str(harness)], capture_output=True, text=True, env=env, timeout=20, check=True
    )

    unit_text = (unit_dir / "agent-logger-sync.service").read_text(encoding="utf-8")
    expected = f'Environment="AGENT_LOGGER_REPO_CONFIG={repo_dir}/.agent-logger.yaml"'
    assert expected in unit_text


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_write_units_escapes_systemd_special_characters_in_path(tmp_path: Path) -> None:
    """A discovered path containing a systemd Environment= special
    character (\\, ", or the %-specifier escape) must be escaped, not just
    whitespace-quoted -- otherwise the generated unit is malformed and
    daemon-reload/the timer can fail instead of running."""
    install_dir = tmp_path / "install"
    unit_dir = tmp_path / "units"
    install_dir.mkdir()
    unit_dir.mkdir()
    (install_dir / "config.yaml").write_text("config_repo: demo\n", encoding="utf-8")

    repo_dir = tmp_path / 'demo"repo%with\\backslash'
    repo_dir.mkdir()
    (repo_dir / ".agent-logger.yaml").write_text("schema_version: 3\n", encoding="utf-8")

    # Written to a side file and `cat`, rather than embedded into the stub
    # script's shell text via plain string interpolation -- repo_dir
    # contains a literal '"' character, which would otherwise prematurely
    # close the stub's own quoted printf argument and corrupt the script.
    repo_dir_file = tmp_path / "repo-dir.txt"
    repo_dir_file.write_text(str(repo_dir), encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "agent-worktrees",
        "#!/bin/sh\n"
        'if [ "$1" = "repos" ] && [ "$2" = "find" ] && [ "$3" = "demo" ]; then\n'
        f'  cat "{repo_dir_file}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        f'INSTALL_DIR="{install_dir}"\n'
        f'UNIT_DIR="{unit_dir}"\n'
        f'VENV="{_real_venv_with_python()}"\n'
        'TIMER_NAME="agent-logger-sync"\n'
        'chg() { :; }\n'
        + _extract_sh_function("write_units")
        + "\nwrite_units\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        # Bypasses the real registered-project/default-branch trust
        # decision for exactly this stub repo dir -- a plain tmp_path
        # directory has no git remotes at all, so without this the new
        # trust gate (see test_write_units_skips_untrusted_config_repo)
        # would correctly reject it and this test would no longer
        # observe the Environment= line it asserts on.
        "AGENT_LOGGER_TRUST_REPO_CONFIG": str(repo_dir),
    }
    subprocess.run(
        [_BASH, str(harness)], capture_output=True, text=True, env=env, timeout=20, check=True
    )

    unit_text = (unit_dir / "agent-logger-sync.service").read_text(encoding="utf-8")
    escaped_path = str(repo_dir).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    expected = f'Environment="AGENT_LOGGER_REPO_CONFIG={escaped_path}/.agent-logger.yaml"'
    assert expected in unit_text


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_write_units_escapes_newline_in_path(tmp_path: Path) -> None:
    """A discovered path containing an embedded newline (POSIX permits one
    in a directory name, and `agent-worktrees repos find` output is copied
    verbatim into the unit here-doc) must be escaped to a literal '\\n'
    sequence, not passed through as a real line break -- otherwise the
    Environment= assignment splits across physical lines in the generated
    unit file, which can fail daemon-reload or be misread as a bogus
    additional directive."""
    install_dir = tmp_path / "install"
    unit_dir = tmp_path / "units"
    install_dir.mkdir()
    unit_dir.mkdir()
    (install_dir / "config.yaml").write_text("config_repo: demo\n", encoding="utf-8")

    repo_dir = tmp_path / "demo-repo-with\nnewline"
    repo_dir.mkdir()
    (repo_dir / ".agent-logger.yaml").write_text("schema_version: 3\n", encoding="utf-8")

    # Written to a side file, rather than embedded into the stub script's
    # shell text via plain string interpolation -- the raw embedded
    # newline would otherwise corrupt the generated stub script itself.
    repo_dir_file = tmp_path / "repo-dir.txt"
    repo_dir_file.write_text(str(repo_dir), encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "agent-worktrees",
        "#!/bin/sh\n"
        'if [ "$1" = "repos" ] && [ "$2" = "find" ] && [ "$3" = "demo" ]; then\n'
        f'  cat "{repo_dir_file}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        f'INSTALL_DIR="{install_dir}"\n'
        f'UNIT_DIR="{unit_dir}"\n'
        f'VENV="{_real_venv_with_python()}"\n'
        'TIMER_NAME="agent-logger-sync"\n'
        'chg() { :; }\n'
        + _extract_sh_function("write_units")
        + "\nwrite_units\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "AGENT_LOGGER_TRUST_REPO_CONFIG": str(repo_dir),
    }
    subprocess.run(
        [_BASH, str(harness)], capture_output=True, text=True, env=env, timeout=20, check=True
    )

    unit_lines = (
        (unit_dir / "agent-logger-sync.service").read_text(encoding="utf-8").splitlines()
    )
    env_lines = [
        line for line in unit_lines if line.startswith('Environment="AGENT_LOGGER_REPO_CONFIG=')
    ]
    assert len(env_lines) == 1
    escaped_path = str(repo_dir).replace("\n", "\\n")
    assert (
        env_lines[0]
        == f'Environment="AGENT_LOGGER_REPO_CONFIG={escaped_path}/.agent-logger.yaml"'
    )


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_write_units_omits_repo_config_env_without_config_repo(tmp_path: Path) -> None:
    """No config_repo declared (today's default) -- and no agent-worktrees
    lookup attempted at all -- is a silent no-op, not an error."""
    install_dir = tmp_path / "install"
    unit_dir = tmp_path / "units"
    install_dir.mkdir()
    unit_dir.mkdir()
    # No config.yaml at all.

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "agent-worktrees",
        "#!/bin/sh\necho 'should not be called' >&2\nexit 1\n",
    )

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        f'INSTALL_DIR="{install_dir}"\n'
        f'UNIT_DIR="{unit_dir}"\n'
        f'VENV="{_real_venv_with_python()}"\n'
        'TIMER_NAME="agent-logger-sync"\n'
        'chg() { :; }\n'
        + _extract_sh_function("write_units")
        + "\nwrite_units\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
    result = subprocess.run(
        [_BASH, str(harness)], capture_output=True, text=True, env=env, timeout=20, check=True
    )

    assert "should not be called" not in result.stderr
    unit_text = (unit_dir / "agent-logger-sync.service").read_text(encoding="utf-8")
    assert "AGENT_LOGGER_REPO_CONFIG" not in unit_text


def _mask_here_strings(text: str) -> str:
    """Replace the contents of PowerShell here-strings (``@'...'@`` /
    ``@"..."@``) with same-length filler containing no braces, so a naive
    brace-counting scan isn't confused by literal ``{``/``}`` characters
    inside a here-string body (e.g. a nested function definition embedded
    as launcher-script text)."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("@'", i) or text.startswith('@"', i):
            terminator = "'@" if text[i + 1] == "'" else '"@'
            end = text.find(terminator, i + 2)
            if end == -1:
                out.append(text[i:])
                break
            out.append(text[i : i + 2])
            out.append("".join("x" if c not in "\n" else "\n" for c in text[i + 2 : end]))
            out.append(terminator)
            i = end + 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _extract_ps1_function(name: str) -> str:
    """Extract one PowerShell function definition by counting braces on a
    here-string-masked copy of the file (see :func:`_mask_here_strings`),
    then slicing the ORIGINAL (unmasked) text at the matching positions --
    unlike a naive ``split("\\n}\\n")``, this is correct even when the
    function body embeds a here-string that itself contains ``}`` at column
    zero (``Write-SyncTaskLauncher``'s launcher-script template does)."""
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    masked = _mask_here_strings(install_ps1)
    start = masked.index(f"function {name}")
    open_brace = masked.index("{", start)
    depth = 0
    i = open_brace
    while i < len(masked):
        if masked[i] == "{":
            depth += 1
        elif masked[i] == "}":
            depth -= 1
            if depth == 0:
                return install_ps1[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting function {name}")


def _extract_ps1_functions(*names: str) -> str:
    return "\n\n".join(_extract_ps1_function(name) for name in names)


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_write_sync_task_launcher_sets_repo_config_when_config_repo_adopted(
    tmp_path: Path, shell: str
) -> None:
    exe = shutil.which(shell)
    if not exe:
        pytest.skip(f"{shell} is not installed")
    # See _resolve_test_venv_root's docstring: the plugin's OWN usable
    # dev/CI venv interpreter, not sys.executable, which can lack pyyaml
    # when invoked directly under uv.
    real_python = _venv_python_path(_resolve_test_venv_root())

    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "config.yaml").write_text("config_repo: demo\n", encoding="utf-8")
    repo_dir = tmp_path / "demo-repo"
    repo_dir.mkdir()
    (repo_dir / ".agent-logger.yaml").write_text("schema_version: 3\n", encoding="utf-8")
    task_launcher = install_dir / "bin" / "session-sync-task.ps1"

    repo_dir_ps = str(repo_dir).replace("\\", "\\\\")
    harness = tmp_path / f"harness-{shell.replace('.exe', '')}.ps1"
    harness.write_text(
        _extract_ps1_functions("Get-ConfigRepoRegistrationPath", "Write-SyncTaskLauncher")
        + f"""

function agent-worktrees {{
    if ($args[0] -eq 'repos' -and $args[1] -eq 'find' -and $args[2] -eq 'demo') {{
        Write-Output '{repo_dir_ps}'
        exit 0
    }}
    exit 1
}}
$InstallDir = '{install_dir}'.Replace('\\\\', '\\')
$TaskLauncher = '{task_launcher}'.Replace('\\\\', '\\')
$VenvPython = '{real_python}'.Replace('\\\\', '\\')
Write-SyncTaskLauncher
""",
        encoding="utf-8",
    )
    subprocess.run(
        [exe, "-NoProfile", "-File", str(harness)],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            # See test_write_units_sets_repo_config_env_when_config_repo_adopted:
            # bypasses the real registered-project/default-branch trust
            # decision for this stub repo dir, which has no real git remotes.
            "AGENT_LOGGER_TRUST_REPO_CONFIG": str(repo_dir),
        },
        timeout=20,
    )

    launcher_text = task_launcher.read_text(encoding="utf-8")
    assert "AGENT_LOGGER_REPO_CONFIG" in launcher_text
    assert str(repo_dir) in launcher_text


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_write_sync_task_launcher_skips_untrusted_config_repo(
    tmp_path: Path, shell: str
) -> None:
    """Windows analogue of test_write_units_skips_untrusted_config_repo:
    an adopted-but-untrusted discovered directory (no real git checkout,
    no AGENT_LOGGER_TRUST_REPO_CONFIG override) must not have its path
    embedded into the scheduled launcher even though a valid,
    non-reparse-point .agent-logger.yaml is present."""
    exe = shutil.which(shell)
    if not exe:
        pytest.skip(f"{shell} is not installed")
    real_python = _venv_python_path(_resolve_test_venv_root())

    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "config.yaml").write_text("config_repo: demo\n", encoding="utf-8")
    repo_dir = tmp_path / "demo-repo"
    repo_dir.mkdir()
    (repo_dir / ".agent-logger.yaml").write_text("schema_version: 3\n", encoding="utf-8")
    task_launcher = install_dir / "bin" / "session-sync-task.ps1"

    repo_dir_ps = str(repo_dir).replace("\\", "\\\\")
    harness = tmp_path / f"harness-untrusted-{shell.replace('.exe', '')}.ps1"
    harness.write_text(
        _extract_ps1_functions("Get-ConfigRepoRegistrationPath", "Write-SyncTaskLauncher")
        + f"""

function agent-worktrees {{
    if ($args[0] -eq 'repos' -and $args[1] -eq 'find' -and $args[2] -eq 'demo') {{
        Write-Output '{repo_dir_ps}'
        exit 0
    }}
    exit 1
}}
$InstallDir = '{install_dir}'.Replace('\\\\', '\\')
$TaskLauncher = '{task_launcher}'.Replace('\\\\', '\\')
$VenvPython = '{real_python}'.Replace('\\\\', '\\')
Write-SyncTaskLauncher
""",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.pop("AGENT_LOGGER_TRUST_REPO_CONFIG", None)
    subprocess.run(
        [exe, "-NoProfile", "-File", str(harness)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )

    launcher_text = task_launcher.read_text(encoding="utf-8")
    assert "AGENT_LOGGER_REPO_CONFIG" not in launcher_text
