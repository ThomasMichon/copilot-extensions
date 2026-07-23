from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_ssh import explore  # noqa: E402


def _probe_output(repos_json: str = "", related: str = "") -> str:
    """Build a synthetic probe stdout with the delimited sections."""
    repos = repos_json or "{}"
    return (
        "===AGENT_SSH_PROBE:os===\n"
        "Linux box 6.1.0 x86_64\n"
        "===AGENT_SSH_PROBE:tools===\n"
        "agent-worktrees\t/home/u/.local/bin/agent-worktrees\tagent-worktrees 1.5.3\n"
        "agent-bridge\t/home/u/.local/bin/agent-bridge\t\n"
        "agent-dispatch\t\t\n"
        "===AGENT_SSH_PROBE:repos===\n"
        f"{repos}\n"
        "===AGENT_SSH_PROBE:related===\n"
        f"{related}"
        "===AGENT_SSH_PROBE:end===\n"
    )


def _relfile(path: str, body: str) -> str:
    """Frame a related.yaml body the way the probe emits it."""
    return f">>>RELFILE:{path}\n{body}\n>>>RELEND\n"


RELATED_ALPHA = _relfile(
    "/home/u/src/alpha",
    "primary: alpha\n"
    "related:\n"
    "  gamma:\n"
    "    role: dependency\n"
    "    summary: Local inference engine used by the voice services.\n"
    "  alpha:\n"
    "    role: tooling\n"
    "    summary: The control-plane catalog repo.\n",
)


REPOS_JSON = """{
  "version": 1,
  "repos": [
    {"name": "alpha", "class": "worktree", "agent": true, "paths": {"linux": "/home/u/src/alpha"}},
    {"name": "beta", "class": "reference", "agent": false, "paths": {"linux": "/home/u/src/beta"}},
    {"name": "gamma", "class": "singleton", "agent": true, "paths": {"linux": "/home/u/src/gamma"}}
  ]
}"""


def test_section_extraction():
    raw = _probe_output()
    assert explore._section(raw, "os") == "Linux box 6.1.0 x86_64"
    assert "agent-worktrees" in explore._section(raw, "tools")
    assert explore._section(raw, "repos") == "{}"


def test_parse_probe_runtimes():
    parsed = explore.parse_probe(_probe_output())
    by_name = {r.name: r for r in parsed["runtimes"]}
    assert by_name["agent-worktrees"].installed is True
    assert by_name["agent-worktrees"].path.endswith("agent-worktrees")
    assert by_name["agent-worktrees"].version == "agent-worktrees 1.5.3"
    # installed but no version reported
    assert by_name["agent-bridge"].installed is True
    assert by_name["agent-bridge"].version == ""
    # not installed
    assert by_name["agent-dispatch"].installed is False


def test_parse_probe_repos():
    parsed = explore.parse_probe(_probe_output(REPOS_JSON))
    names = [r["name"] for r in parsed["repos"]]
    assert names == ["alpha", "beta", "gamma"]


def test_parse_probe_bad_repos_json_is_safe():
    parsed = explore.parse_probe(_probe_output("not json{"))
    assert parsed["repos"] == []


def test_derive_agents_only_agent_backing():
    parsed = explore.parse_probe(_probe_output(REPOS_JSON))
    agents = explore.derive_agents("boxwsl", parsed["repos"])
    names = [a.name for a in agents]
    # only agent:true repos (alpha, gamma) -- beta (agent:false) excluded
    assert names == ["alpha@boxwsl", "gamma@boxwsl"]
    assert agents[0].repo == "alpha"
    assert agents[0].repo_class == "worktree"
    assert agents[0].path == "/home/u/src/alpha"


def test_derive_agents_skips_unnamed_or_pathless():
    repos = [
        {"name": "", "agent": True, "paths": {"linux": "/x"}},
        {"name": "noflag", "paths": {"linux": "/y"}},
        {"name": "ok", "agent": True, "paths": {}},
    ]
    agents = explore.derive_agents("t", repos)
    # unnamed skipped; no-agent-flag skipped; "ok" kept even with empty paths
    assert [a.name for a in agents] == ["ok@t"]
    assert agents[0].path == ""


def test_explore_unreachable(monkeypatch):
    class _Proc:
        returncode = 255
        stdout = ""
        stderr = "ssh: connect to host x port 22: Connection refused\n"

    monkeypatch.setattr(explore, "_ssh_probe", lambda target, timeout: _Proc())
    result = explore.explore("deadhost")
    assert result.reachable is False
    assert "Connection refused" in result.error
    assert result.derived_agents == []


def test_explore_reachable(monkeypatch):
    class _Proc:
        returncode = 0
        stdout = _probe_output(REPOS_JSON)
        stderr = ""

    monkeypatch.setattr(explore, "_ssh_probe", lambda target, timeout: _Proc())
    result = explore.explore("livehost")
    assert result.reachable is True
    assert result.os.startswith("Linux")
    assert len(result.repos) == 3
    assert [a.name for a in result.derived_agents] == ["alpha@livehost", "gamma@livehost"]
    # to_dict is JSON-serializable
    import json

    json.dumps(result.to_dict())


def test_explore_ssh_missing(monkeypatch):
    def _boom(target, timeout):
        raise FileNotFoundError("ssh")

    monkeypatch.setattr(explore, "_ssh_probe", _boom)
    result = explore.explore("x")
    assert result.reachable is False
    assert "ssh not found" in result.error


def test_format_report_reachable():
    # Build a result directly to format.
    parsed = explore.parse_probe(_probe_output(REPOS_JSON))
    res = explore.ExploreResult(
        target="livehost",
        reachable=True,
        os=parsed["os"],
        runtimes=parsed["runtimes"],
        repos=parsed["repos"],
        derived_agents=explore.derive_agents("livehost", parsed["repos"]),
    )
    text = explore.format_report(res)
    assert "livehost" in text
    assert "alpha@livehost" in text
    assert "agent-worktrees" in text
    assert "not installed" in text  # agent-dispatch


def test_format_report_unreachable():
    res = explore.ExploreResult(target="deadhost", reachable=False, error="Connection refused")
    text = explore.format_report(res)
    assert "unreachable" in text
    assert "Connection refused" in text


def test_parse_related_union_first_nonempty_wins():
    extra = _relfile(
        "/home/u/src/gamma",
        "primary: gamma\n"
        "related:\n"
        "  gamma:\n"
        "    role: overridden\n"
        "    summary: should not replace the first non-empty purpose\n",
    )
    purposes = explore.parse_related(RELATED_ALPHA + extra)
    assert purposes["gamma"] == {
        "role": "dependency",
        "summary": "Local inference engine used by the voice services.",
    }
    assert purposes["alpha"]["role"] == "tooling"


def test_parse_related_empty_is_safe():
    assert explore.parse_related("") == {}
    assert explore.parse_related("   \n") == {}


def test_parse_related_bad_yaml_is_skipped():
    bad = ">>>RELFILE:/x\n: : not: valid: yaml:\n\t- broken\n>>>RELEND\n"
    assert explore.parse_related(bad) == {}


def test_parse_probe_surfaces_purposes():
    parsed = explore.parse_probe(_probe_output(REPOS_JSON, RELATED_ALPHA))
    assert parsed["purposes"]["gamma"]["role"] == "dependency"


def test_derive_agents_carries_purpose():
    parsed = explore.parse_probe(_probe_output(REPOS_JSON, RELATED_ALPHA))
    agents = explore.derive_agents("boxwsl", parsed["repos"], parsed["purposes"])
    by_name = {a.repo: a for a in agents}
    # gamma is agent:true and described -> purpose attached
    assert by_name["gamma"].role == "dependency"
    assert "inference engine" in by_name["gamma"].summary
    # alpha is agent:true but self-described only as tooling in the catalog
    assert by_name["alpha"].role == "tooling"


def test_explore_reachable_annotates_purpose(monkeypatch):
    class _Proc:
        returncode = 0
        stdout = _probe_output(REPOS_JSON, RELATED_ALPHA)
        stderr = ""

    monkeypatch.setattr(explore, "_ssh_probe", lambda target, timeout: _Proc())
    result = explore.explore("livehost")
    by_name = {r["name"]: r for r in result.repos}
    assert by_name["gamma"]["purpose"]["role"] == "dependency"
    # beta is not described anywhere -> no purpose key
    assert "purpose" not in by_name["beta"]
    # derived agents carry it too
    dgamma = next(a for a in result.derived_agents if a.repo == "gamma")
    assert "inference engine" in dgamma.summary
    import json

    json.dumps(result.to_dict())


def test_format_report_shows_purpose():
    parsed = explore.parse_probe(_probe_output(REPOS_JSON, RELATED_ALPHA))
    repos = parsed["repos"]
    for entry in repos:
        p = parsed["purposes"].get(entry.get("name", ""))
        if p:
            entry["purpose"] = p
    res = explore.ExploreResult(
        target="livehost",
        reachable=True,
        os=parsed["os"],
        runtimes=parsed["runtimes"],
        repos=repos,
        derived_agents=explore.derive_agents("livehost", repos, parsed["purposes"]),
    )
    text = explore.format_report(res)
    assert "purpose:" in text
    assert "[dependency]" in text
    assert "inference engine" in text
