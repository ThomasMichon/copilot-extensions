from __future__ import annotations

from pathlib import Path

import pytest

from repository_policy import (
    AgentHostingDenied,
    RepositoryPolicyError,
    load_agent_policy,
    require_agent_hosting,
)

CANONICAL = ".copilot-extensions/agent-worktrees/config.yaml"
LEGACY = ".agent-worktrees/config.yaml"
OLDEST = ".agent-worktrees.yaml"


def write(root: Path, text: str, relative: str = CANONICAL) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


@pytest.mark.parametrize("relative", [CANONICAL, LEGACY, OLDEST])
@pytest.mark.parametrize("value", ["true", "false"])
def test_boolean_policy_and_provenance(tmp_path: Path, relative: str, value: str) -> None:
    target = write(tmp_path, f"expose_agent: {value}\n", relative)
    policy = load_agent_policy(tmp_path)
    assert policy.expose_agent is (value == "true")
    assert policy.sources == (target,)
    assert policy.denied_by == ((target,) if value == "false" else ())
    if value == "false":
        with pytest.raises(AgentHostingDenied, match="external harness"):
            require_agent_hosting(tmp_path, operation="resume")
    else:
        assert require_agent_hosting(tmp_path) == policy


@pytest.mark.parametrize("text", ["", "# empty\n", "pr:\n  required: true\n"])
def test_absent_key_does_not_grant_permission(tmp_path: Path, text: str) -> None:
    write(tmp_path, text)
    policy = require_agent_hosting(tmp_path)
    assert policy.expose_agent is None
    assert "allowed" not in policy.to_dict()
    assert not policy.denied_by


def test_absent_file_reads_no_machine_settings(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    write(home, "expose_agent: false\n", ".agent-worktrees/projects.yaml")
    monkeypatch.setenv("HOME", str(home))
    repo = tmp_path / "source"
    repo.mkdir()
    policy = require_agent_hosting(repo)
    assert policy.expose_agent is None
    assert policy.sources == ()
    assert sorted(repo.iterdir()) == []


def test_canonical_wins_over_legacy_aliases(tmp_path: Path) -> None:
    target = write(tmp_path, "expose_agent: true\n")
    write(tmp_path, "expose_agent: false\n", LEGACY)
    write(tmp_path, "expose_agent: invalid\n", OLDEST)
    assert require_agent_hosting(tmp_path).sources == (target,)


def test_overlay_can_add_but_not_weaken_repository_denial(tmp_path: Path) -> None:
    base = write(tmp_path, "expose_agent: false\n")
    overlay = write(
        tmp_path, "expose_agent: true\n",
        ".copilot-extensions/agent-worktrees/marketplaces/example/config.yaml",
    )
    policy = load_agent_policy(tmp_path, marketplace_id="example")
    assert policy.expose_agent is False
    assert policy.sources == (base, overlay)
    assert policy.denied_by == (base,)
    write(tmp_path, "expose_agent: true\n")
    overlay.write_text("expose_agent: false\n", encoding="utf-8")
    assert load_agent_policy(tmp_path, marketplace_id="example").denied_by == (overlay,)


def test_other_marketplace_is_not_consulted(tmp_path: Path) -> None:
    write(
        tmp_path, "expose_agent: invalid\n",
        ".copilot-extensions/agent-worktrees/marketplaces/other/config.yaml",
    )
    assert require_agent_hosting(tmp_path, marketplace_id="example").expose_agent is None


@pytest.mark.parametrize("identity", ["../other", ".", "..", "/absolute", "a/b", "a\\b", 1])
def test_invalid_marketplace_identity_refuses(tmp_path: Path, identity) -> None:
    with pytest.raises(RepositoryPolicyError, match="marketplace"):
        require_agent_hosting(tmp_path, marketplace_id=identity)


@pytest.mark.parametrize("text", [
    'expose_agent: "false"\n',
    "expose_agent: null\n",
    "expose_agent: 0\n",
    "expose_agent: []\n",
    "expose_agent: {}\n",
    "expose_agent: [\n",
    "- expose_agent: false\n",
    "expose_agent: false\nexpose_agent: true\n",
])
def test_malformed_policy_refuses_with_source(tmp_path: Path, text: str) -> None:
    write(tmp_path, text)
    with pytest.raises(RepositoryPolicyError, match="config.yaml"):
        require_agent_hosting(tmp_path)


def test_unreadable_required_policy_is_not_absent(tmp_path: Path, monkeypatch) -> None:
    target = write(tmp_path, "expose_agent: false\n")
    original = Path.read_text

    def read(path: Path, *args, **kwargs):
        if path == target:
            raise PermissionError("synthetic access denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(RepositoryPolicyError, match="synthetic access denied"):
        require_agent_hosting(tmp_path)


def test_inspection_error_is_not_absent(tmp_path: Path, monkeypatch) -> None:
    original = Path.lstat

    def inspect(path: Path):
        if path == tmp_path / CANONICAL:
            raise PermissionError("synthetic inspection denied")
        return original(path)

    monkeypatch.setattr(Path, "lstat", inspect)
    with pytest.raises(RepositoryPolicyError, match="inspection denied"):
        require_agent_hosting(tmp_path)


def test_actual_nested_checkout_and_changed_policy_are_fresh(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    write(tmp_path, "expose_agent: false\n")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / ".git").write_text("gitdir: unused-for-policy\n", encoding="utf-8")
    directory = nested / "src"
    directory.mkdir()
    target = write(nested, "expose_agent: true\n")
    assert require_agent_hosting(directory).repository == nested
    target.write_text("expose_agent: false\n", encoding="utf-8")
    with pytest.raises(AgentHostingDenied, match="nested"):
        require_agent_hosting(directory)


def test_invalid_workspace_is_not_permission(tmp_path: Path) -> None:
    with pytest.raises(RepositoryPolicyError, match="workspace"):
        require_agent_hosting(tmp_path / "missing")
    target = tmp_path / "file"
    target.write_text("not a directory", encoding="utf-8")
    with pytest.raises(RepositoryPolicyError, match="not a directory"):
        require_agent_hosting(target)


def test_refusal_has_no_writes_and_no_purpose_escape(tmp_path: Path) -> None:
    write(tmp_path, "expose_agent: false\n")
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    for operation in ("launch", "resume", "adopt", "review"):
        with pytest.raises(AgentHostingDenied):
            require_agent_hosting(tmp_path, operation=operation)
    after = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    assert before == after
    assert load_agent_policy(tmp_path).to_dict()["version"] == 1


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.parametrize("text,allowed", [
    (None, True),
    ("expose_agent: true\n", True),
    ("expose_agent: false\n", False),
    ('expose_agent: "false"\n', False),
    ("expose_agent: false\nexpose_agent: true\n", False),
])
def test_fresh_guest_reader_uses_canonical_policy_without_installed_packages(
    tmp_path: Path, text: str | None, allowed: bool,
) -> None:
    import json
    import os
    import subprocess
    import sys

    from repository_policy.guest_probe import build_guest_policy_probe

    (tmp_path / ".git").mkdir()
    if text is not None:
        write(tmp_path, text)
    nested = tmp_path / "source"
    nested.mkdir()
    reader_temp = tmp_path / "reader-temp"
    reader_temp.mkdir()
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    script, payload = build_guest_policy_probe()
    environment = dict(os.environ)
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.update(TMP=str(reader_temp), TEMP=str(reader_temp), TMPDIR=str(reader_temp))
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(nested)],
        input=payload, capture_output=True, timeout=10, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.stderr == b"", result.stderr
    answer = json.loads(result.stdout)
    assert result.returncode == (0 if allowed else 3), answer
    if allowed:
        assert answer["allowed"] is True
        assert answer["repository"] == str(tmp_path)
        assert answer["sources"] == ([str(tmp_path / CANONICAL)] if text is not None else [])
    else:
        assert answer["code"] in {
            "repository-agent-exposure-denied", "repository-agent-exposure-invalid",
        }
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
def test_fresh_guest_reader_cannot_downgrade_explicit_installation_context(tmp_path: Path) -> None:
    import json
    import os
    import subprocess
    import sys

    from repository_policy.guest_probe import build_guest_policy_probe

    script, payload = build_guest_policy_probe()
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(tmp_path)],
        input=payload, capture_output=True, timeout=10,
        env=dict(os.environ, COPILOT_EXTENSIONS_CONTEXT="unvalidated-context"),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 3
    answer = json.loads(result.stdout)
    assert answer["code"] == "repository-agent-exposure-invalid"
    assert "context" in answer["error"]
