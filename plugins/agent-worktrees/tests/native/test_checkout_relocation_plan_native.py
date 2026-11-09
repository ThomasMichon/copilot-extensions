"""Actual native CLI refusal with poison detection, not an absence producer."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import pytest
from agent_procutil.owned_process import run_caller_owned

from agent_worktrees import config as cfg, git_ops


def _snapshot(root):
    return {
        str(path.relative_to(root)): (
            path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.timeout(120)
@pytest.mark.skipif(os.name != "nt", reason="native Windows read-only CLI contract")
def test_actual_plan_refuses_unknown_without_backend_or_state_effects(tmp_path, record_property):
    home = tmp_path / "home"
    home.mkdir()
    anchor = home / "anchor"
    anchor.mkdir()
    git_ops.git("init", "--template=", "-b", "main", cwd=anchor)
    (anchor / "tracked.txt").write_bytes(b"original checkout content\n")
    git_ops.git("add", ".", cwd=anchor)
    git_ops.git(
        "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-m", "fixture", cwd=anchor,
    )
    identity = "fixture-win-20261105-100000-abcd"
    checkout = home / "old" / identity
    git_ops.create_worktree(str(anchor), str(checkout), f"worktree/{identity}", "HEAD")
    (checkout / "untracked.bin").write_bytes(b"preserve even before data qualification")
    target_parent = home / "new"
    target_parent.mkdir()
    project = home / ".sample"
    records = project / "worktrees"
    records.mkdir(parents=True)
    (project / "config.yaml").write_text(json.dumps({
        "repo_name": "sample", "machine": cfg.detect_machine(), "srcroot": str(home),
        "repos": {"sample": {
            "anchor": str(anchor), "worktree_root": str(home / "old"),
            "remote": "origin", "default_branch": "main",
        }},
    }), encoding="utf-8")
    (records / f"{identity}.yaml").write_text(json.dumps({
        "worktree_id": identity, "branch": f"worktree/{identity}",
        "worktree_path": str(checkout), "repo": "sample", "machine": cfg.detect_machine(),
        "platform": cfg.detect_platform(), "creation_nonce": "original-nonce",
        "status": "completed", "sessions": [],
    }), encoding="utf-8")
    poison = tmp_path / "poison"
    poison.mkdir()
    marker = poison / "forbidden-invocation.json"
    (poison / "sitecustomize.py").write_text(
        "import json, os, pathlib, socket, subprocess\n"
        "_popen = subprocess.Popen\n"
        "def forbidden(argv):\n"
        "    pathlib.Path(os.environ['PLAN_POISON_MARKER']).write_text(json.dumps(argv))\n"
        "    raise OSError('forbidden effectful backend invocation')\n"
        "def popen(argv, *args, **kwargs):\n"
        "    words = [str(x).lower() for x in argv] if isinstance(argv, (list, tuple)) else [str(argv).lower()]\n"
        "    if any('psmux' in x or 'tmux' in x for x in words): forbidden(words)\n"
        "    return _popen(argv, *args, **kwargs)\n"
        "subprocess.Popen = popen\n"
        "socket.socket.connect = lambda *args, **kwargs: forbidden(['control-connect'])\n",
        encoding="utf-8",
    )
    before = _snapshot(home)
    env = dict(
        os.environ, AGENT_HOME=str(home), USERPROFILE=str(home), HOME=str(home),
        PLAN_POISON_MARKER=str(marker), PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=os.pathsep.join([str(poison), os.environ.get("PYTHONPATH", "")]),
    )
    result = run_caller_owned(
        [sys.executable, "-m", "agent_worktrees", "--project", "sample", "move",
         identity, "--to", str(target_parent / identity), "--json"],
        cwd=home, env=env, timeout=60,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert not marker.exists(), "plan attempted an effectful backend; no absence proof is provided"
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["complete"] is False
    assert "qualified passive native owner evidence" in report["error"]
    assert "Traceback" not in result.stderr
    assert _snapshot(home) == before
    digest = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
    record_property("native_cli_returncode", result.returncode)
    record_property("unchanged_state_and_file_identity_sha256", digest)
    record_property("effectful_backend_called", False)


def test_recovery_fixture_keeps_strict_routing_and_original_bytes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import checkout_cross_volume_fixture as fixture_owner

    ledger = tmp_path / "original.yaml"
    ledger.write_bytes(b"original allocation evidence")
    journal = fixture_owner.state.journal_path(ledger)
    journal.write_bytes(b"original relocation evidence")
    fixture = SimpleNamespace(
        record=SimpleNamespace(yaml_path=ledger, worktree_id="original"),
        destination=tmp_path / "original", config=object(),
    )
    calls = []

    def preflight(*args):
        calls.append(("preflight", args))
        return {"recovery": True, "apply": False}

    def recover(*args):
        calls.append(("move", args))
        assert ledger.read_bytes() == b"original allocation evidence"
        assert journal.read_bytes() == b"original relocation evidence"
        return {"phase": "complete", "stage": "original-stage"}

    monkeypatch.setattr(fixture_owner.relocation, "_action_plan", preflight)
    monkeypatch.setattr(fixture_owner.relocation, "move", recover)
    monkeypatch.setattr(
        fixture_owner.relocation, "plan",
        lambda *_args: pytest.fail("strict recovery fixture entered passive planning"),
    )
    result = fixture_owner._recover(fixture, {"stage": "original-stage"})
    assert result == {"phase": "complete", "stage": "original-stage"}
    expected = ("original", str(fixture.destination), fixture.config)
    assert calls == [("preflight", expected), ("move", expected)]
