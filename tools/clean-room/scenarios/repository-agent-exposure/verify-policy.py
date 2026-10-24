"""Credential-free assertions against the candidate's installed CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    if not __debug__:
        raise SystemExit("verification requires assertions (run without -O)")
    command, root = Path(sys.argv[1]), Path(sys.argv[2])
    root.mkdir(parents=True)
    initialized = subprocess.run(
        ["git", "init", "--quiet", str(root)], capture_output=True, text=True,
        check=False, timeout=30,
    )
    assert initialized.returncode == 0, initialized.stderr
    config = root / ".copilot-extensions/agent-worktrees/config.yaml"
    config.parent.mkdir(parents=True)
    # Review-only instructions are data, not a hosting exception.
    (root / "AGENTS.md").write_text("# External code review only\n", encoding="utf-8")

    def invoke(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(command), *args], cwd=root, capture_output=True, text=True,
            check=False, timeout=30,
        )

    def check(operation: str = "launch") -> subprocess.CompletedProcess[str]:
        return invoke("agent-policy", "check", "--path", str(root),
                      "--operation", operation, "--json")

    empty = check()
    assert empty.returncode == 0, empty.stderr or empty.stdout
    policy = json.loads(empty.stdout)
    assert type(policy["version"]) is int and policy["version"] == 1, policy
    assert policy["allowed"] is True and policy["expose_agent"] is None, policy
    assert Path(policy["repository"]) == root, policy
    assert policy["sources"] == [] and policy["denied_by"] == [], policy

    config.write_text("expose_agent: false\n", encoding="utf-8")
    for operation in ("launch", "resume", "adopt", "review"):
        result = check(operation)
        assert result.returncode != 0, (operation, result.stdout)
        refusal = json.loads(result.stdout)
        assert refusal["code"] == "repository-agent-exposure-denied", refusal
        assert str(config) in refusal["error"], refusal
    before = config.read_bytes()
    projects = Path.home() / ".agent-worktrees/projects.yaml"
    registrations = projects.read_bytes() if projects.exists() else None
    result = invoke("register", "denied-fixture")
    assert result.returncode != 0, result.stdout
    assert "expose_agent" in result.stderr + result.stdout, result
    assert (projects.read_bytes() if projects.exists() else None) == registrations
    assert config.read_bytes() == before

    def git(*args: str) -> None:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            check=False, timeout=30,
        )
        assert result.returncode == 0, result.stderr or result.stdout

    git("add", "AGENTS.md", ".copilot-extensions")
    git("-c", "user.name=Policy Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Add source-only policy fixture")
    branch = subprocess.run(
        ["git", "-C", str(root), "branch", "--show-current"],
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert branch.returncode == 0 and branch.stdout.strip(), branch
    remote = root.parent / "source-only-remote.git"
    git("clone", "--quiet", "--bare", str(root), str(remote))
    registered = invoke(
        "repos", "add", "denied-source", str(root), "--class", "worktree",
        "--no-agent", "--default-branch", branch.stdout.strip(), "--remote", str(remote),
    )
    assert registered.returncode == 0, registered.stderr or registered.stdout
    owner_check = invoke(
        "--project", "denied-source", "agent-policy", "check", "--project-root", "--json",
    )
    assert owner_check.returncode != 0, owner_check.stdout
    assert json.loads(owner_check.stdout)["code"] == "repository-agent-exposure-denied"
    assert (projects.read_bytes() if projects.exists() else None) == registrations
    created = invoke(
        "--project", "denied-source", "create", "--json", "--no-owner", "--no-pair",
    )
    assert created.returncode == 0, created.stderr or created.stdout
    allocation = json.loads(created.stdout)
    assert allocation["worktree"] and "launch" not in allocation, allocation
    exact_check = invoke(
        "--project", "denied-source", "agent-policy", "check",
        "--worktree-id", allocation["worktree"]["id"], "--json",
    )
    assert exact_check.returncode != 0, exact_check.stdout
    assert json.loads(exact_check.stdout)["code"] == "repository-agent-exposure-denied"
    assert (projects.read_bytes() if projects.exists() else None) == registrations
    assert config.read_bytes() == before

    config.write_text("expose_agent: true\n", encoding="utf-8")
    result = check()
    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout)["expose_agent"] is True
    config.write_text('expose_agent: "false"\n', encoding="utf-8")
    result = check()
    assert result.returncode != 0, result.stdout
    refusal = json.loads(result.stdout)
    assert refusal["code"] == "repository-agent-exposure-invalid", refusal
    assert str(config) in refusal["error"], refusal
    print(
        "Fresh installed policy, review-label denial, source-only allocation "
        "and adoption-state preservation passed."
    )


if __name__ == "__main__":
    main()
