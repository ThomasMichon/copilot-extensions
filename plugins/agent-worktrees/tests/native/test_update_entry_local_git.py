"""Opt-in real Git/file-source acceptance; never enters a production installer."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
import zlib
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import project_command_templates

PLUGIN = Path(__file__).resolve().parents[2]
REPO = PLUGIN.parents[1]
EXIT = 73
REF = "native-bootstrap-fixture"
pytestmark = pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="opt-in real shell/Git acceptance requires --allow-explicit-tiers",
)

BOOTSTRAP_SH = b"""#!/bin/sh
git -C "$NATIVE_BOOTSTRAP_GIT_REPO" rev-parse --is-bare-repository > "$NATIVE_BOOTSTRAP_GIT_PROOF" || exit $?
printf '%s\\0' "$@" > "$NATIVE_BOOTSTRAP_ARGS"
printf '%s' "$0" > "$NATIVE_BOOTSTRAP_PATH"
exit 73
"""
BOOTSTRAP_PS1 = b"""$ErrorActionPreference = 'Stop'
$proof = & git -C $env:NATIVE_BOOTSTRAP_GIT_REPO rev-parse --is-bare-repository
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
[IO.File]::WriteAllText($env:NATIVE_BOOTSTRAP_GIT_PROOF, $proof.Trim())
$json = ConvertTo-Json -Compress -InputObject @($args)
[IO.File]::WriteAllText($env:NATIVE_BOOTSTRAP_ARGS, $json)
[IO.File]::WriteAllText($env:NATIVE_BOOTSTRAP_PATH, $PSCommandPath)
exit 73
"""


def _run(
    command: list[str] | str, *, env: dict[str, str], cwd: Path,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command, env=env, cwd=cwd, capture_output=True, timeout=35, **no_window_kwargs(),
    )


def _private_root(tmp: Path, shell: str | None, env: dict[str, str]) -> Path:
    if os.name != "nt":
        return Path(tempfile.mkdtemp(prefix="update-entry-local-git-", dir=tmp))
    assert shell is not None
    # Use the product's actual atomic TokenUser creator, not the elevated
    # default owner of Python/PowerShell's ordinary directory creation.
    creator = tmp / "create-private-root.ps1"
    helper = str(PLUGIN / "bin/agent-worktrees-update-entry.ps1").replace("'", "''")
    creator.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
        "$tokens = $null; $parseErrors = $null\n"
        f"$ast = [Management.Automation.Language.Parser]::ParseFile('{helper}', "
        "[ref]$tokens, [ref]$parseErrors)\n"
        "if ($parseErrors.Count) { throw ($parseErrors | Out-String) }\n"
        "$function = $ast.Find({ param($node)\n"
        "  $node -is [Management.Automation.Language.FunctionDefinitionAst] -and\n"
        "  $node.Name -eq 'New-UpdateScratchDirectory'\n"
        "}, $true)\n"
        "if (-not $function) { throw 'Product private scratch creator is missing.' }\n"
        "$body = $function.Body.GetScriptBlock()\n& $body\n",
        encoding="utf-8",
    )
    result = _run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(creator)],
        env={**env, "AGENT_SCRATCH_ROOT": str(tmp.parent)}, cwd=tmp,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    root = Path(result.stdout.decode().strip())
    assert root.is_dir() and root.parent == tmp.parent
    return root


def _object(repo: Path, kind: str, content: bytes) -> bytes:
    body = f"{kind} {len(content)}\0".encode("ascii") + content
    digest = hashlib.sha1(body, usedforsecurity=False).hexdigest()
    path = repo / "objects" / digest[:2] / digest[2:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zlib.compress(body))
    return bytes.fromhex(digest)


def _local_git_source(root: Path, git: str, env: dict[str, str]) -> Path:
    repo = root / "source repo.git"
    result = _run(
        [git, "init", "--quiet", "--bare", "--object-format=sha1", str(repo)],
        env=env, cwd=root,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    # Materialize deterministic fixture objects without git commit/commit-tree,
    # identity discovery, hooks, or changes to any source worktree.
    ps1 = _object(repo, "blob", BOOTSTRAP_PS1)
    sh = _object(repo, "blob", BOOTSTRAP_SH)
    subtree = _object(
        repo, "tree", b"100644 bootstrap.ps1\0" + ps1 + b"100644 bootstrap.sh\0" + sh,
    )
    tree = _object(repo, "tree", b"40000 worktree-manager\0" + subtree)
    commit = _object(
        repo, "commit",
        f"tree {tree.hex()}\n"
        "author Fixture <fixture@example.invalid> 1700000000 +0000\n"
        "committer Fixture <fixture@example.invalid> 1700000000 +0000\n"
        "\nharmless bootstrap fixture\n".encode("ascii"),
    )
    (repo / "refs/heads" / REF).write_text(commit.hex() + "\n", encoding="ascii")
    (repo / "HEAD").write_text(f"ref: refs/heads/{REF}\n", encoding="ascii")
    result = _run(
        [git, "--git-dir", str(repo), "cat-file", "-e", f"{REF}^{{commit}}"],
        env=env, cwd=root,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return repo


def _poison_old_runtime(home: Path, failing: bool) -> None:
    local_bin = home / ".local/bin"
    posix = '#!/bin/sh\nprintf old > "$NATIVE_OLD_INTERPRETER"\nexit 91\n'
    ps1 = "[IO.File]::WriteAllText($env:NATIVE_OLD_INTERPRETER, 'old')\nexit 91\n"
    cmd = '@echo off\r\n(> "%NATIVE_OLD_INTERPRETER%" echo old)\r\nexit /b 91\r\n'
    for name, content in (
        ("python", posix), ("python3", posix), ("python.ps1", ps1), ("python.cmd", cmd),
    ):
        path = local_bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o700)
    engine = home / ".agent-worktrees"
    engine.mkdir()
    (engine / "current-version").write_text("unavailable-old-runtime\n")
    if not failing:
        return
    (engine / "bin").mkdir()
    (engine / "bin/resolve-runtime.sh").write_text(
        'printf old > "$NATIVE_OLD_RESOLVER"\nAW_PY="$HOME/.local/bin/python"\n',
    )
    (engine / "bin/resolve-runtime.ps1").write_text(
        "[IO.File]::WriteAllText($env:NATIVE_OLD_RESOLVER, 'old')\n"
        "$AwPy = Join-Path $env:USERPROFILE '.local\\bin\\python.cmd'\n",
    )
    for name, content in (
        ("worktree-manager", '#!/bin/sh\nprintf old > "$NATIVE_OLD_MANAGER"\nexit 92\n'),
        ("worktree-manager.ps1",
         "[IO.File]::WriteAllText($env:NATIVE_OLD_MANAGER, 'old')\nexit 92\n"),
        ("worktree-manager.cmd",
         '@echo off\r\n(> "%NATIVE_OLD_MANAGER%" echo old)\r\nexit /b 92\r\n'),
    ):
        path = local_bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o700)


@pytest.mark.parametrize(
    "front", ["posix", "powershell", "cmd", "payload-powershell", "payload-cmd", "git-sh"],
)
@pytest.mark.parametrize("runtime", ["missing", "failing"])
def test_update_entry_native_local_git(
    tmp_path: Path, request: pytest.FixtureRequest, front: str, runtime: str,
) -> None:
    windows = os.name == "nt"
    if windows == (front == "posix"):
        pytest.skip("different native platform")
    shell = request.getfixturevalue("native_powershell" if windows else "native_bash")
    git = shutil.which("git")
    if not git:
        pytest.skip("real Git is required for native file-source acceptance")
    env = dict(os.environ)
    for name in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_PARAMETERS",
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE",
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", "AGENT_WORKTREES_UPDATE_CONTEXT",
        "PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "VIRTUAL_ENV",
    ):
        env.pop(name, None)
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_GLOBAL": str(tmp_path / "empty-gitconfig"),
        "GIT_TEMPLATE_DIR": str(tmp_path / "empty-git-template"),
        "GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0",
    })
    Path(env["GIT_CONFIG_GLOBAL"]).write_text("")
    Path(env["GIT_TEMPLATE_DIR"]).mkdir()
    root = _private_root(tmp_path, shell if windows else None, env)
    home = root / "profile with spaces"
    local_bin = home / ".local/bin"
    local_bin.mkdir(parents=True)
    scratch = (
        tmp_path.parent / f"s space-{uuid.uuid4().hex[:4]}"
        if windows else root / "scratch with spaces"
    )
    scratch.mkdir()
    registry = root / "empty-provider-registry"
    registry.mkdir()
    repo = _local_git_source(root, git, env)
    manager = home / ".worktree-manager"
    manager.mkdir()
    (manager / "current-version").write_text("missing-selected-manager\n")
    (manager / "config.toml").write_text(
        f"[source]\nrepo = {json.dumps(repo.as_uri())}\nref = {json.dumps(REF)}\n",
    )
    if front == "git-sh":
        manifest = json.loads(
            (REPO / "worktree-manager/references/control-plane-provider.json").read_text(
                encoding="utf-8",
            )
        )
        manifest["provider_root"] = str(manager)
        manifest["command"] = [str(local_bin / "worktree-manager.cmd")]
        (registry / "worktree-manager.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name in (
        "agent-worktrees", "agent-worktrees.cmd", "agent-worktrees.ps1",
        "agent-worktrees-update-entry.sh", "agent-worktrees-update-entry.ps1",
    ):
        shutil.copy2(PLUGIN / "bin" / name, local_bin / name)
    shutil.copy2(
        PLUGIN / "scripts/installation-context/json-query.awk",
        local_bin / "agent-worktrees-update-json.awk",
    )
    rendered = project_command_templates.render(
        "native-fixture", PLUGIN, windows=windows, user_bin=local_bin,
    )
    for name, content in rendered.items():
        (local_bin / name).write_text(content, encoding="utf-8")
        (local_bin / name).chmod(0o700)
    _poison_old_runtime(home, runtime == "failing")
    env.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "WORKTREE_MANAGER_ROOT": str(manager), "AGENT_SCRATCH_ROOT": str(scratch),
        "WORKTREE_MANAGER_BOOTSTRAP_REPO": repo.as_uri(),
        "WORKTREE_MANAGER_BOOTSTRAP_REF": REF,
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR": str(registry),
        "AGENT_WORKTREES_NO_SELFPROVISION": "1",
        "NATIVE_BOOTSTRAP_ARGS": str(root / "fresh.args"),
        "NATIVE_BOOTSTRAP_PATH": str(root / "fresh.path"),
        "NATIVE_BOOTSTRAP_GIT_REPO": str(repo),
        "NATIVE_BOOTSTRAP_GIT_PROOF": str(root / "fresh.git"),
        "NATIVE_OLD_INTERPRETER": str(root / "old-interpreter"),
        "NATIVE_OLD_RESOLVER": str(root / "old-resolver"),
        "NATIVE_OLD_MANAGER": str(root / "old-manager"),
        "PYTHONHOME": str(root / "missing-python-home"),
        "PYTHONPATH": str(root / "missing-python-packages"),
        "VIRTUAL_ENV": str(root / "missing-old-venv"),
        "GIT_TRACE": str(root / "real-git.trace"),
    })
    flags = ["update", "--force", "--skip-modules", "module with spaces", "--no-anchor-sync"]
    if windows:
        system = Path(os.environ["SystemRoot"]) / "System32"
        env["PATH"] = os.pathsep.join(map(str, (
            local_bin, Path(git).parent, Path(shell).parent, system,
        )))
        if front in ("powershell", "payload-powershell"):
            entry = (
                PLUGIN / "bin/payload/agent-worktrees.ps1"
                if front == "payload-powershell" else local_bin / "native-fixture.ps1"
            )
            command = [
                shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(entry), *(["--project", "native-fixture"] if front.startswith("payload-") else []),
                *flags,
            ]
        elif front in ("cmd", "payload-cmd"):
            cmd = os.environ.get("COMSPEC", str(system / "cmd.exe"))
            entry = (
                PLUGIN / "bin/payload/agent-worktrees.cmd"
                if front == "payload-cmd" else local_bin / "native-fixture.cmd"
            )
            tail = subprocess.list2cmdline([
                str(entry), *(["--project", "native-fixture"] if front.startswith("payload-") else []),
                *flags,
            ])
            command = f'{subprocess.list2cmdline([cmd])} /d /s /c "{tail}"'
        else:
            bash = request.getfixturevalue("native_bash")
            env["PATH"] += os.pathsep + os.environ["PATH"]
            converted = _run(
                [bash, "-c", 'cygpath -u -- "$1"', "_", str(local_bin / "agent-worktrees")],
                env=env, cwd=root,
            )
            assert converted.returncode == 0, converted.stderr.decode(errors="replace")
            command = [
                bash, converted.stdout.decode().strip(), "--project", "native-fixture", *flags,
            ]
    else:
        for name in ("git", "bash", "dirname", "awk", "date", "mktemp", "rm", "rmdir"):
            (local_bin / name).symlink_to(shutil.which(name))
        env["PATH"] = str(local_bin)
        command = [shell, str(local_bin / "native-fixture"), *flags]
    result = _run(command, env=env, cwd=root)
    assert result.returncode == EXIT, result.stderr.decode(errors="replace")
    assert (root / "fresh.git").read_text(encoding="utf-8").strip() == "true"
    if windows:
        args = json.loads((root / "fresh.args").read_text(encoding="utf-8"))
    else:
        args = (root / "fresh.args").read_bytes().decode().split("\0")[:-1]
    assert args == ["--bootstrap-update", "update", "--project",
                    "native-fixture", *flags[1:]]
    fetched_script = Path((root / "fresh.path").read_text(encoding="utf-8"))
    assert fetched_script.is_relative_to(scratch)
    assert fetched_script.name == ("bootstrap.ps1" if windows else "bootstrap.sh")
    assert repo.as_uri() in (root / "real-git.trace").read_text(encoding="utf-8")
    assert all(not (root / name).exists() for name in (
        "old-interpreter", "old-resolver", "old-manager",
    ))
    assert not list(scratch.iterdir())
    assert (manager / "current-version").read_text() == "missing-selected-manager\n"
    assert not (manager / "staging").exists()
