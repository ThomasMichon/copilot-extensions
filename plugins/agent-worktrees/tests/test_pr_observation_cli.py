"""The explicit-target status alias shares the central snapshot operation."""

import argparse

from agent_worktrees import pr_cli, pr_state_cli


def parse(*argv):
    parser = argparse.ArgumentParser()
    pr_state_cli.add_parsers(parser.add_subparsers(dest="verb"))
    return parser.parse_args(argv)


def test_explicit_status_alias_uses_existing_target_resolution(monkeypatch):
    seen = []
    monkeypatch.setattr(pr_cli, "cmd_pr_watch_dispatch", lambda argv: seen.append(argv) or 0)
    args = parse("pr-status", "--repo", "Project/repository", "--pr", "7", "--json")
    assert pr_state_cli.cmd_pr_status(args) == 0
    assert seen == [["snapshot", "Project/repository", "7", "--json"]]


def test_explicit_status_needs_both_target_coordinates(monkeypatch):
    monkeypatch.setattr(pr_cli, "cmd_pr_watch_dispatch", lambda argv: (_ for _ in ()).throw(AssertionError()))
    assert pr_state_cli.cmd_pr_status(parse("pr-status", "--repo", "Project/repository", "--json")) != 0
    assert pr_state_cli.cmd_pr_status(parse("pr-status", "--pr", "7", "--json")) != 0


def test_explicit_threads_uses_on_demand_body_read_not_a_stored_snapshot(monkeypatch):
    seen = []
    monkeypatch.setattr(pr_cli, "cmd_pr_watch_dispatch", lambda argv: seen.append(argv) or 0)
    args = parse("pr-status", "--repo", "Project/repository", "--pr", "7", "--threads", "--json")
    assert pr_state_cli.cmd_pr_status(args) == 0
    assert seen[0][0] == "threads"


def test_explicit_status_cannot_mutate_or_mix_another_worktrees_state():
    for options in (["--resolve-threads"], ["--all"], ["--no-live"], ["some-worktree"]):
        args = parse("pr-status", "--repo", "Project/repository", "--pr", "7", *options)
        assert pr_state_cli.cmd_pr_status(args) != 0


def test_configured_observation_is_parsed_through_real_pr_config():
    from agent_worktrees.config import _parse_pr
    parsed = _parse_pr({
        "provider": "azure-devops",
        "observation": {"requirements": {"minimum_approvals": 2},
                        "guidance": "Keep approvals persistent."},
    })
    assert parsed.observation.minimum_approvals == 2
    assert parsed.observation.guidance == "Keep approvals persistent."


def test_legacy_set_pr_selector_remains_its_original_mutation_field():
    args = parse("set-pr", "--pr", "7", "--number", "7", "--branch", "feature")
    assert args.pr == 7 and args.number == 7 and args.branch == "feature"


def test_central_watch_routes_bounded_remediation_before_polling(monkeypatch):
    from agent_worktrees import pr_remediation_cli
    seen = []
    monkeypatch.setattr(pr_remediation_cli, "dispatch", lambda argv: seen.append(argv) or 17)
    monkeypatch.setattr(pr_cli, "require_claimant_worktree",
                        lambda *args: (_ for _ in ()).throw(AssertionError("not a wait")))
    argv = ["reply", "Project/repository", "7", "--help"]
    assert pr_cli.cmd_pr_watch_dispatch(argv) == 17
    assert seen == [argv]
