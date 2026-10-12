"""External PR bodies preserve managed attribution policy without provider access."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from agent_worktrees import attribution_prepare_cli as cli
from agent_worktrees.providers import attribution

pytestmark = [
    pytest.mark.guard,
    pytest.mark.contract("agent_worktrees.attribution_prepare"),
]


def fixture(mode="codename", source="built-in", explicit=False):
    record = SimpleNamespace(worktree_id="private-tree", machine="private-machine",
                             codename="bolted-circuit", codename_source=source,
                             worktree_path="fixture-root", sessions=[])
    config = SimpleNamespace(default_repo=SimpleNamespace(pr=SimpleNamespace(
        source_attribution=mode, source_attribution_configured=explicit,
    )))
    return record, config


def test_external_body_uses_existing_root_identity_builder(monkeypatch):
    record, config = fixture()
    calls = []

    def marker(name, actual, settings, head):
        calls.append((name, actual, settings, head))
        return attribution.build_codename_marker(name, root="curious-toolbox", identity="opaque-token")

    monkeypatch.setattr(cli.root_chain, "build_codename_marker_with_root", marker)
    result = cli.prepare("Authored body\n<!-- agent-worktrees:source machine=stale -->",
                         record, config, head="head", tag="control-harness")
    assert calls == [("bolted-circuit", record, config, "head")]
    assert result["attributed"]
    assert "private-machine" not in str(result) and "private-tree" not in str(result)
    assert result["body"].count("agent-worktrees:source") == 1
    assert "Attribution: control-harness" in result["body"]
    assert cli.prepare(result["body"], record, config, head="head", tag="control-harness") == result


@pytest.mark.parametrize("mode,omit", [(False, False), ("codename", True)])
def test_optout_strips_stale_marker_and_does_not_emit_tag(mode, omit):
    record, config = fixture(mode)
    result = cli.prepare("Body\n<!-- agent-worktrees:source machine=old -->",
                         record, config, head="", omit=omit, tag="control-harness")
    assert result == {"version": 1, "body": "Body", "marker": "", "attributed": False}


@pytest.mark.parametrize("source,explicit", [("unknown", True), ("custom", False)])
def test_unreviewed_codename_is_never_downgraded_to_raw(source, explicit):
    record, config = fixture(source=source, explicit=explicit)
    with pytest.raises(ValueError, match="provenance"):
        cli.prepare("", record, config, head="")


@pytest.mark.parametrize("tag", ["private-machine", "private-tree", "line\nbreak", "<!--raw-->"])
def test_invalid_or_private_tag_is_refused(tag):
    record, config = fixture()
    with pytest.raises(ValueError, match="tag"):
        cli.prepare("", record, config, head="", tag=tag)


def test_explicit_raw_mode_preserves_existing_contract():
    record, config = fixture(True)
    record.sessions = [
        SimpleNamespace(session_id="fixture-live", ended_at=None),
        SimpleNamespace(session_id="fixture-ended", ended_at="past"),
    ]
    result = cli.prepare("", record, config, head="abc")
    assert attribution.parse_marker(result["marker"]) == {
        "worktree": "private-tree", "machine": "private-machine",
        "session": "fixture-live", "head": "abc",
    }
    record.sessions[0].ended_at = "past"
    assert attribution.parse_marker(cli.prepare("", record, config, head="abc")["marker"])[
        "session"
    ] == "fixture-ended"


def test_empty_tagged_body_has_no_leading_blank_lines(monkeypatch):
    record, config = fixture()
    monkeypatch.setattr(
        cli.root_chain, "build_codename_marker_with_root",
        lambda *args: attribution.build_codename_marker("bolted-circuit"),
    )
    assert cli.prepare("", record, config, head="", tag="control-harness")["body"].startswith(
        "Attribution: control-harness\n",
    )


def test_real_lazy_dispatch_exposes_provider_neutral_command(tmp_path):
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [sys.executable, "-c",
         "from agent_worktrees.__main__ import _dispatch_lazy;"
         "_dispatch_lazy('attribution-prepare',['attribution-prepare','--help'])"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "--body-file" in result.stdout and "--omit-source" in result.stdout
    assert "--tag" in result.stdout


def test_cli_error_is_explicit_and_tag_output_is_not_discarded(monkeypatch, capfd):
    import argparse

    record, config = fixture()
    config.repo_name = "fixture"
    monkeypatch.setattr(cli.cfg, "load_config", lambda path: config)
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda name: Path("fixture"))
    monkeypatch.setattr(cli.tracking, "find_worktree_id_by_cwd", lambda *args, **kwargs: "fixture")
    monkeypatch.setattr(cli.tracking, "load_record_by_id", lambda *args, **kwargs: None)
    args = argparse.Namespace(config=None, body_file=None, tag="control-harness",
                              omit_source=False, json=True)
    assert cli.cmd_attribution_prepare(args) == 1
    assert "managed source worktree" in capfd.readouterr().out
    monkeypatch.setattr(cli.tracking, "load_record_by_id", lambda *args, **kwargs: record)
    def git(*args, **kwargs):
        assert kwargs["cwd"] == str(Path(record.worktree_path).resolve())
        assert kwargs["isolated_repository"] is True
        return SimpleNamespace(stdout="head")

    monkeypatch.setattr(cli.git_ops, "git", git)
    monkeypatch.setattr(
        cli.root_chain, "build_codename_marker_with_root",
        lambda *args: attribution.build_codename_marker("bolted-circuit"),
    )
    args.json = False
    assert cli.cmd_attribution_prepare(args) == 0
    text = capfd.readouterr().out
    assert "Attribution: control-harness" in text and "codename=bolted-circuit" in text


def test_nested_repository_head_does_not_replace_source_head(monkeypatch, tmp_path, capfd):
    import argparse
    import subprocess

    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    heads = []
    for root, message in ((source, "source"), (nested, "nested")):
        subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.name=Fixture",
             "-c", "user.email=fixture@example.invalid", "commit", "--quiet",
             "--allow-empty", "-m", message],
            check=True, capture_output=True,
        )
        heads.append(subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                            text=True).strip())
    record, config = fixture(True)
    config.repo_name = "fixture"
    record.worktree_path = str(source)
    monkeypatch.chdir(nested)
    monkeypatch.setenv("GIT_DIR", str(nested / ".git"))
    monkeypatch.setattr(cli.cfg, "load_config", lambda path: config)
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda name: tmp_path)
    monkeypatch.setattr(cli.tracking, "find_worktree_id_by_cwd", lambda *args, **kwargs: "fixture")
    monkeypatch.setattr(cli.tracking, "load_record_by_id", lambda *args, **kwargs: record)
    args = argparse.Namespace(config=None, body_file=None, tag=None, omit_source=False, json=False)
    assert cli.cmd_attribution_prepare(args) == 0
    assert attribution.parse_marker(capfd.readouterr().out)["head"] == heads[0]
    assert heads[0] != heads[1]
