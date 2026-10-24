"""Revision-bound setup with a real Git source and native script consumer."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from agent_procutil import no_window_kwargs
import pytest

from agent_worktrees import repository_setup as setup

URL = "https://example.invalid/team/sample.git"
pytestmark = pytest.mark.timeout(180)


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True,
        **no_window_kwargs(),
    ).stdout.strip()


def commit(path: Path) -> None:
    git(path, "add", ".")
    git(path, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture", "--no-verify")


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "source with spaces"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "core.autocrlf", "false")
    git(path, "remote", "add", "origin", URL)
    (path / ".agent-worktrees").mkdir()
    (path / setup.DECLARATION).write_text(json.dumps({
        "version": 1, "entrypoints": {
            "windows": {"runtime": "powershell", "script": "setup.ps1"},
            "posix": {"runtime": "bash", "script": "setup.sh"},
        },
    }), encoding="utf-8")
    (path / "setup.ps1").write_text("param([string]$RequestFile)\n", encoding="utf-8")
    (path / "setup.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    commit(path)
    return path


@pytest.mark.parametrize("url", [
    "", "sample", "http://example.invalid/a", "ssh://example.invalid/a",
    "https://name:secret@example.invalid/a", "https://name@example.invalid/a",
    "https://example.invalid/a?", "https://example.invalid/a#",
    "https://example.invalid/a/../b", "https://example.invalid/",
    "https://example.invalid:broken/a", "https://example.invalid/a\n",
    "https://bad%host/a", "https://example.invalid/a\\b",
])
def test_rejects_unsupported_urls(url: str) -> None:
    with pytest.raises(setup.SetupError):
        setup.https_url(url)


def test_url_identity_is_canonical() -> None:
    assert setup.https_url("https://EXAMPLE.invalid:443/team/sample.git/") == (
        "https://example.invalid/team/sample"
    )
    assert setup.https_url("https://example.invalid:8443/a") == "https://example.invalid:8443/a"


def test_inspect_is_read_only_and_revision_bound(source: Path) -> None:
    before = git(source, "status", "--porcelain")
    selected = setup.inspect(source, URL)
    assert selected.source.commit == git(source, "rev-parse", "HEAD")
    assert selected.source.url == "https://example.invalid/team/sample"
    selected.verify()
    assert git(source, "status", "--porcelain") == before == ""
    (source / "setup.ps1").write_text("changed", encoding="utf-8")
    with pytest.raises(setup.SetupError, match="source changes"):
        selected.verify()


@pytest.mark.parametrize("kind", ["foreign", "untracked", "modified", "linked", "nested"])
def test_preserves_unqualified_source(source: Path, kind: str) -> None:
    selected = source
    if kind == "foreign":
        git(source, "remote", "set-url", "origin", "https://example.invalid/other/sample")
    elif kind == "untracked":
        (source / "precious.txt").write_text("preserve", encoding="utf-8")
    elif kind == "modified":
        (source / "setup.ps1").write_text("preserve", encoding="utf-8")
    elif kind == "linked":
        selected = source.parent / "linked"
        git(source, "worktree", "add", "-q", "-b", "other", str(selected))
    else:
        selected = source / ".agent-worktrees"
    before = git(source, "status", "--porcelain")
    with pytest.raises(setup.SetupError):
        setup.inspect(selected, URL)
    assert git(source, "status", "--porcelain") == before


@pytest.mark.parametrize("declaration", [
    '{"version":1,"version":1,"entrypoints":{}}',
    '{"version":true,"entrypoints":{}}',
    '{"version":2,"entrypoints":{}}',
    '{"version":1,"entrypoints":{},"extra":1}',
    '{"version":1,"entrypoints":{"other":{}}}',
    '{"version":1,"entrypoints":{"windows":{"runtime":"cmd","script":"setup.ps1"}}}',
    '{"version":1,"entrypoints":{"windows":{"runtime":"powershell","script":"../outside"}}}',
    '{"version":1,"entrypoints":{"windows":{"runtime":"powershell","script":"C:/outside"}}}',
    '{"version":1,"entrypoints":{"windows":{"runtime":"powershell","script":"missing"}}}',
    "[]", "not json", " " * (setup.LIMIT + 1),
])
def test_rejects_malformed_tracked_declarations(source: Path, declaration: str) -> None:
    (source / setup.DECLARATION).write_text(declaration, encoding="utf-8")
    commit(source)
    with pytest.raises(setup.SetupError):
        setup.inspect(source, URL)


def test_never_executes_without_trust(source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected = setup.inspect(source, URL)
    monkeypatch.setattr(setup, "_call", lambda *_: pytest.fail("untrusted execution"))
    assert setup.run(selected, apply=True, trusted=False).status == "refused"


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_cannot_hide_modified_entry_in_index_flags(source: Path, flag: str) -> None:
    git(source, "update-index", flag, "setup.ps1")
    (source / "setup.ps1").write_text("hidden modification", encoding="utf-8")
    with pytest.raises(setup.SetupError, match="index hides"):
        setup.inspect(source, URL)


def consumer(source: Path, body: str) -> setup.Setup:
    """Native Windows coverage; POSIX uses the equivalent argv/file consumer."""
    if sys.platform == "win32":
        if not (shutil.which("pwsh") or shutil.which("powershell")):
            pytest.skip("Native PowerShell unavailable")
        script = source / "setup.ps1"
        script.write_text(
            "param([Parameter(Mandatory=$true)][string]$RequestFile)\n"
            "$ErrorActionPreference = 'Stop'\n"
            "$r = Get-Content -LiteralPath $RequestFile -Raw | ConvertFrom-Json\n"
            "$state = Join-Path (Split-Path $r.repository.path -Parent) 'state.txt'\n"
            "$log = Join-Path (Split-Path $r.repository.path -Parent) 'calls.txt'\n"
            "Add-Content -LiteralPath $log -Value $r.mode\n" + body,
            encoding="utf-8",
        )
    else:
        pytest.skip("Windows script case; POSIX native consumer covered separately")
    commit(source)
    return setup.inspect(source, URL)


def test_real_consumer_check_apply_final_check(source: Path) -> None:
    selected = consumer(source, """
if ($r.mode -eq 'apply') { Set-Content -LiteralPath $state -Value 'done' }
$s = if (Test-Path -LiteralPath $state) { 'ready' } else { 'needed' }
@{version=1;status=$s;message='Repository owner checked'} |
    ConvertTo-Json -Compress | Set-Content -LiteralPath $r.result_file -Encoding utf8
""")
    outcome = setup.run(selected, apply=True, trusted=True)
    assert outcome.status == "ready"
    assert (source.parent / "calls.txt").read_text().splitlines() == ["check", "apply", "check"]


@pytest.mark.parametrize("status", ["waiting", "refused", "failed", "ready"])
def test_real_consumer_terminal_check_never_applies(source: Path, status: str) -> None:
    selected = consumer(source, f"""
[IO.File]::WriteAllText($r.result_file,
    '{{"version":1,"status":"{status}","message":"Owner status"}}')
""")
    assert setup.run(selected, apply=True, trusted=True).status == status
    assert (source.parent / "calls.txt").read_text().splitlines() == ["check"]


@pytest.mark.parametrize("result", [
    '{"version":1,"status":"ready","status":"ready","message":"duplicate"}',
    '{"version":1,"status":"unknown","message":"unknown"}',
    '{"version":1,"status":"ready","message":""}',
    '{"version":1,"status":"ready","message":"ok","extra":true}',
    '{"version":true,"status":"ready","message":"ok"}',
    "[]", "bad", "x" * (setup.LIMIT + 1),
])
def test_real_consumer_cannot_fake_success(source: Path, result: str) -> None:
    literal = result.replace("'", "''")
    selected = consumer(source, f"[IO.File]::WriteAllText($r.result_file, '{literal}')\n")
    with pytest.raises(setup.SetupError):
        setup.run(selected, apply=True, trusted=True)


def test_real_consumer_nonzero_overrides_ready(source: Path) -> None:
    selected = consumer(source, """
[IO.File]::WriteAllText($r.result_file, '{"version":1,"status":"ready","message":"not proof"}')
exit 7
""")
    assert setup.run(selected, apply=True, trusted=True).status == "failed"


def test_real_consumer_source_drift_requires_new_trust(source: Path) -> None:
    selected = consumer(source, """
Add-Content -LiteralPath (Join-Path $r.repository.path 'setup.ps1') -Value '# changed'
[IO.File]::WriteAllText($r.result_file, '{"version":1,"status":"ready","message":"not proof"}')
""")
    with pytest.raises(setup.SetupError, match="source changes"):
        setup.run(selected, apply=True, trusted=True)


def test_real_consumer_missing_result_is_unknown_not_success(source: Path) -> None:
    selected = consumer(source, "exit 0\n")
    with pytest.raises(setup.SetupError, match="no result"):
        setup.run(selected, apply=True, trusted=True)


def test_real_consumer_check_only_does_not_apply(source: Path) -> None:
    selected = consumer(source, """
[IO.File]::WriteAllText($r.result_file, '{"version":1,"status":"needed","message":"Owner needs setup"}')
""")
    assert setup.run(selected, apply=False, trusted=True).status == "needed"
    assert (source.parent / "calls.txt").read_text().splitlines() == ["check"]


def test_real_consumer_interrupted_apply_is_not_replayed(source: Path) -> None:
    selected = consumer(source, """
if ($r.mode -eq 'apply') { exit 9 }
[IO.File]::WriteAllText($r.result_file, '{"version":1,"status":"needed","message":"Check first"}')
""")
    assert setup.run(selected, apply=True, trusted=True).status == "failed"
    assert setup.run(selected, apply=False, trusted=True).status == "needed"
    assert (source.parent / "calls.txt").read_text().splitlines() == ["check", "apply", "check"]


@pytest.mark.skipif(sys.platform == "win32", reason="Native POSIX subprocess contract")
def test_real_posix_consumer_check_apply_final_check(source: Path) -> None:
    body = """
import json, pathlib, sys
assert sys.argv[1] == '--request-file'
r = json.loads(pathlib.Path(sys.argv[2]).read_text())
root = pathlib.Path(r['repository']['path'])
assert pathlib.Path.cwd() == root
state = root.parent / 'state.txt'
with (root.parent / 'calls.txt').open('a') as log:
    log.write(r['mode'] + '\\n')
if r['mode'] == 'apply':
    state.write_text('done')
pathlib.Path(r['result_file']).write_text(json.dumps({
    'version': 1, 'status': 'ready' if state.exists() else 'needed', 'message': 'Owner checked'
}))
"""
    (source / "setup.sh").write_text(
        f"#!/usr/bin/env bash\nexec {shlex.quote(sys.executable)} -c {shlex.quote(body)} \"$@\"\n",
        encoding="utf-8",
    )
    commit(source)
    assert setup.run(setup.inspect(source, URL), apply=True, trusted=True).status == "ready"
    assert (source.parent / "calls.txt").read_text().splitlines() == ["check", "apply", "check"]
