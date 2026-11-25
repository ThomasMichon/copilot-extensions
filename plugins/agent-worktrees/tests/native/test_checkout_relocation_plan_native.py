"""Actual native CLI refusal with poison detection, not an absence producer."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Callable, cast
import pytest
from agent_procutil.owned_process import run_caller_owned

from agent_worktrees import config as cfg, git_ops
from agent_worktrees import checkout_relocation as relocation, checkout_relocation_state as state


def _snapshot(root: Path) -> dict[str, tuple[int, int, int, str]]:
    return {
        str(path.relative_to(root)): (
            path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.timeout(120)
@pytest.mark.skipif(os.name != "nt", reason="native Windows read-only CLI contract")
@pytest.mark.parametrize(
    ("nonce", "expected_error"),
    [
        ("original-nonce", "qualified passive native owner evidence"),
        (None, "qualified passive native owner evidence"),
        ("", "completed ordinary sessionless"),
        (123, "qualified passive native owner evidence"),
    ],
    ids=["nonce-backed", "absent-birth-metadata", "empty-nonce", "existing-numeric-parser"],
)
def test_actual_plan_refuses_unknown_without_backend_or_state_effects(
    tmp_path: Path, record_property: Callable[[str, object], None],
    nonce: str | int | None, expected_error: str,
) -> None:
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
    retained: dict[str, object] = {
        "worktree_id": identity, "branch": f"worktree/{identity}",
        "worktree_path": str(checkout), "repo": "sample", "machine": cfg.detect_machine(),
        "platform": cast(Callable[[], str], getattr(cfg, "detect_platform"))(),
        "status": "completed", "sessions": [],
    }
    if nonce is not None:
        retained["creation_nonce"] = nonce
    (records / f"{identity}.yaml").write_text(json.dumps(retained), encoding="utf-8")
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
    assert expected_error in report["error"]
    assert "Traceback" not in result.stderr
    assert _snapshot(home) == before
    digest = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
    record_property("native_cli_returncode", result.returncode)
    record_property("unchanged_state_and_file_identity_sha256", digest)
    record_property("effectful_backend_called", False)


def test_recovery_fixture_keeps_strict_routing_and_original_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import checkout_cross_volume_fixture as fixture_owner

    ledger = tmp_path / "original.yaml"
    ledger.write_bytes(b"original allocation evidence")
    journal = state.journal_path(ledger)
    journal.write_bytes(b"original relocation evidence")
    fixture = SimpleNamespace(
        record=SimpleNamespace(yaml_path=ledger, worktree_id="original"),
        destination=tmp_path / "original", config=object(),
    )
    calls: list[tuple[str, tuple[object, ...]]] = []

    def preflight(*args: object) -> dict[str, bool]:
        calls.append(("preflight", args))
        return {"recovery": True, "apply": False}

    def recover(*args: object) -> dict[str, str]:
        calls.append(("move", args))
        assert ledger.read_bytes() == b"original allocation evidence"
        assert journal.read_bytes() == b"original relocation evidence"
        return {"phase": "complete", "stage": "original-stage"}

    monkeypatch.setattr(relocation, "_action_plan", preflight)
    monkeypatch.setattr(relocation, "move", recover)
    monkeypatch.setattr(
        relocation, "plan",
        lambda *_args: pytest.fail("strict recovery fixture entered passive planning"),
    )
    replay = cast(Callable[..., dict[str, str]], fixture_owner._recover)
    result = replay(fixture, {"stage": "original-stage"})
    assert result == {"phase": "complete", "stage": "original-stage"}
    expected = ("original", str(fixture.destination), fixture.config)
    assert calls == [("preflight", expected), ("move", expected)]
