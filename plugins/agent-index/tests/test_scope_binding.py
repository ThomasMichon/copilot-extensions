from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]


def _repo(path: Path, config: str | None) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if config is not None:
        target = path / ".agent-index" / "config.yaml"
        target.parent.mkdir(parents=True)
        target.write_text(config, encoding="utf-8")
    return path


def _run(shell: str, repo: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("AGENT_INDEX_CONFIG_DATA_B64", None)
    env.pop("AGENT_INDEX_REPO", None)
    if shell == "bash":
        if os.name == "nt" or shutil.which("bash") is None:
            pytest.skip("POSIX scope-binding test")
        command = ["bash", str(PLUGIN / "scripts" / "emit-scope-binding.sh")]
    else:
        pwsh = shutil.which("pwsh")
        if not pwsh:
            pytest.skip("PowerShell scope-binding test")
        command = [
            pwsh,
            "-NoProfile",
            "-File",
            str(PLUGIN / "scripts" / "emit-scope-binding.ps1"),
        ]
    return subprocess.run(
        command,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_scope_binding_emits_only_valid_effective_sources(
    tmp_path: Path, shell: str
) -> None:
    repo = _repo(
        tmp_path,
        "indexers:\n"
        "  - machine: host-a\n"
        "corpus:\n"
        "  sources:\n"
        "    - name: git:example\n"
        "      repo: example/repo\n"
        "      trust_domain: example\n",
    )

    result = _run(shell, repo)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    context = payload["additionalContext"]
    assert "example/repo (source `git:example`) [example]" in context
    assert "commands[id=agent-index].argv" in context


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize(
    "config",
    [None, "indexers: [\n", "indexers:\n  - machine: host-a\n"],
    ids=["missing", "malformed", "no-sources"],
)
def test_scope_binding_is_empty_without_active_scopes(
    tmp_path: Path, shell: str, config: str | None
) -> None:
    repo = _repo(tmp_path, config)

    result = _run(shell, repo)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
