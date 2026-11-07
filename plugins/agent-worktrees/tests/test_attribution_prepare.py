"""External PR bodies preserve managed attribution policy without provider access."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from agent_worktrees import attribution_prepare_cli as cli
from agent_worktrees import tracking
from agent_worktrees.providers import attribution
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity

pytestmark = [
    pytest.mark.guard,
    pytest.mark.contract("agent_worktrees.attribution_prepare"),
]


def fixture(mode="codename", source="built-in", explicit=False):
    record = tracking.parse_record_data({
        "worktree_id": "private-tree", "branch": "worktree/private-tree", "repo": "fixture",
        "machine": "private-machine", "platform": "linux",
        "codename": "bolted-circuit", "codename_source": source,
        "worktree_path": "fixture-root", "sessions": [],
    }, path=Path("fixture-root.yaml"))
    config = SimpleNamespace(default_repo=SimpleNamespace(pr=SimpleNamespace(
        source_attribution=mode, source_attribution_configured=explicit,
    )))
    return record, config


@pytest.mark.parametrize("tag", ["physical-host", "custom-guest"])
def test_tuple_tag_privacy_covers_both_physical_and_execution_names(tag):
    record, config = fixture()
    record.machine, record.platform = "physical-host", "wsl"
    record.machine_identity = WorktreeMachineIdentity("physical-host", "wsl", "custom-guest")
    with pytest.raises(ValueError, match="private source"):
        cli.prepare("", record, config, head="", tag=tag)


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


@pytest.mark.parametrize("mode,visibility,privacy,raw", [
    ("codename", "public", "public", False),
    ("codename", "internal", "public", False),
    (True, "public", "public", False),
    (True, "internal", "internal", True),
])
def test_comment_origin_is_distinct_compact_and_public_safe(mode, visibility, privacy, raw):
    record, config = fixture(mode, explicit=True)
    result = cli.prepare_comment_origin(record, config, visibility=visibility)
    assert result == {
        "protocol": "agent-worktrees.comment-origin/v1", "privacy": privacy,
        "origin": {"codename": "bolted-circuit", **({"worktree": "private-tree"} if raw else {})},
        "effects": "none",
    }
    assert "agent-worktrees:source" not in str(result)
    assert "private-machine" not in str(result) and "session" not in str(result)
    if not raw:
        assert "private-tree" not in str(result)
    assert record.codename == "bolted-circuit"


def test_anonymous_comment_origin_never_invents_or_exposes_identity():
    record, config = fixture(False)
    record.codename = None
    for visibility in ("public", "internal"):
        result = cli.prepare_comment_origin(record, config, visibility=visibility)
        assert result["privacy"] == "anonymous" and result["origin"] is None
        assert "private-tree" not in str(result)


def test_raw_private_optin_does_not_authorize_custom_codename_on_public_comment():
    record, config = fixture(True, source="custom", explicit=True)
    with pytest.raises(ValueError, match="provenance"):
        cli.prepare_comment_origin(record, config)
    assert cli.prepare_comment_origin(record, config, visibility="internal")["origin"] == {
        "codename": "bolted-circuit", "worktree": "private-tree",
    }
    config.default_repo.pr.source_attribution = "codename"
    assert cli.prepare_comment_origin(record, config)["origin"] == {"codename": "bolted-circuit"}


@pytest.mark.parametrize("source,explicit", [("unknown", True), ("custom", False)])
def test_comment_origin_refuses_unqualified_codename_without_raw_fallback(source, explicit):
    record, config = fixture(source=source, explicit=explicit)
    with pytest.raises(ValueError, match="provenance"):
        cli.prepare_comment_origin(record, config)


def test_comment_origin_rejects_unsafe_raw_stamp_without_echoing_it():
    record, config = fixture(True)
    record.worktree_id = "private/path\ninjected"
    with pytest.raises(ValueError, match="stamp is not supported") as failure:
        cli.prepare_comment_origin(record, config, visibility="internal")
    assert "private/path" not in str(failure.value)
    assert cli.prepare_comment_origin(record, config)["origin"] == {"codename": "bolted-circuit"}


def test_real_comment_origin_handler_uses_owner_record_without_git_vault_or_provider(monkeypatch, capfd):
    import argparse
    import json

    record, config = fixture(True)
    config.repo_name = "fixture"
    monkeypatch.setattr(cli.cfg, "load_config", lambda path: config)
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda name: Path("fixture"))
    monkeypatch.setattr(cli.tracking, "find_worktree_id_by_cwd", lambda *args, **kwargs: "fixture")
    monkeypatch.setattr(cli.tracking, "load_record_by_id", lambda *args, **kwargs: record)

    def forbidden(*args, **kwargs):
        raise AssertionError("compact comment origin must not acquire head, lineage or identity keys")

    monkeypatch.setattr(cli.git_ops, "git", forbidden)
    monkeypatch.setattr(cli.root_chain, "build_codename_marker_with_root", forbidden)
    args = argparse.Namespace(config=None, body_file=None, tag=None, omit_source=False,
                              json=True, comment_origin=True, visibility=None)
    assert cli.cmd_attribution_prepare(args) == 0
    assert json.loads(capfd.readouterr().out)["origin"] == {"codename": "bolted-circuit"}
    for field, value in (("body_file", Path("not-read.md")), ("tag", "tag"), ("omit_source", True)):
        conflicting = argparse.Namespace(**{**vars(args), field: value})
        assert cli.cmd_attribution_prepare(conflicting) == 1
        assert "cannot compose" in capfd.readouterr().out
    args.comment_origin = False
    args.visibility = "public"
    assert cli.cmd_attribution_prepare(args) == 1
    assert "requires comment-origin" in capfd.readouterr().out


def test_real_lazy_dispatch_declares_comment_mode_and_rejects_abbreviated_flag(tmp_path):
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    script = (
        "from agent_worktrees.__main__ import _dispatch_lazy;"
        "raise SystemExit(_dispatch_lazy('attribution-prepare',"
        "['attribution-prepare',*__import__('sys').argv[1:]]))"
    )
    help_result = subprocess.run(
        [sys.executable, "-c", script, "--help"], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=20,
    )
    assert help_result.returncode == 0 and "--comment-origin" in help_result.stdout
    assert "--visibility" in help_result.stdout
    typo = subprocess.run(
        [sys.executable, "-c", script, "--comment-o"], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=20,
    )
    assert typo.returncode == 2 and "unrecognized arguments" in typo.stderr


def test_actual_comment_origin_composes_through_independent_profile_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    import argparse
    import json
    import os
    import subprocess
    import sys

    record, config = fixture(True)
    config.repo_name = "fixture"
    monkeypatch.setattr(cli.cfg, "load_config", lambda path: config)
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda name: Path("fixture"))
    monkeypatch.setattr(cli.tracking, "find_worktree_id_by_cwd", lambda *args, **kwargs: "fixture")
    monkeypatch.setattr(cli.tracking, "load_record_by_id", lambda *args, **kwargs: record)
    args = argparse.Namespace(config=None, body_file=None, tag=None, omit_source=False,
                              json=True, comment_origin=True, visibility=None)
    assert cli.cmd_attribution_prepare(args) == 0
    origin = json.loads(capfd.readouterr().out)
    repo = tmp_path / "target"
    (repo / ".github").mkdir(parents=True)
    (repo / ".github" / "ai-attribution.json").write_text(json.dumps({
        "version": 1, "rules": [{"header": "[Selected profile] "}],
    }))
    payload = {
        "draft": "Selected reply.", "source": origin,
        "context": {"domain": "forge", "visibility": "public", "activity": "reply", "required": True},
        "comment_id": "11111111-1111-4111-8111-111111111111",
        "write_id": "22222222-2222-4222-8222-222222222222",
    }
    script = Path(__file__).resolve().parents[2] / "ai-attribution" / "scripts" / "comment_preparation.py"
    env = {**os.environ, "HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home"),
           "XDG_CONFIG_HOME": str(tmp_path / "config"), "APPDATA": str(tmp_path / "config"),
           "COPILOT_CUSTOM_INSTRUCTIONS_DIRS": ""}
    result = subprocess.run(
        [sys.executable, str(script), "--repo", str(repo)], input=json.dumps(payload),
        env=env, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    prepared = json.loads(result.stdout)
    assert prepared["body"].startswith("[Selected profile] Selected reply.")
    assert prepared["identity"]["source"] == origin
    assert prepared["body"].count("<!-- agent-comment:v1") == 1
    assert "cn=bolted-circuit" in prepared["body"]
    assert "private-tree" not in prepared["body"] and "agent-worktrees:source" not in prepared["body"]
    assert prepared["effects"] == "none"
