"""Shell-first repair must fetch before touching any installed Python runtime."""

from __future__ import annotations

import ast
import json
import os
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import project_command_templates

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]


@pytest.fixture
def update_entry_case(tmp_path: Path, monkeypatch, request):
    if os.name == "nt" and "native_bash" in request.fixturenames:
        pytest.skip("POSIX fake-executable lane; native Windows has its own executable coverage")
    home = tmp_path / "home"
    home.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    downloaded = tmp_path / "download.sh"
    downloaded.write_text(
        '#!/bin/sh\nprintf "%s\\0" "$@" > "$BOOT_ARGS"\npwd > "$BOOT_CWD"\n'
        'printf "%s" "$AGENT_WORKTREES_UPDATE_CONTEXT" > "$BOOT_CONTEXT"\nexit "$BOOT_RC"\n'
    )
    curl = tools / "curl"
    curl.write_text(
        '#!/bin/sh\nprintf "%s\\0" "$@" > "$CURL_ARGS"\n'
        '[ "$CURL_RC" = 0 ] || exit "$CURL_RC"\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = -o ]; then cp "$DOWNLOAD_SCRIPT" "$2"; exit $?; fi\n'
        "  shift\ndone\nexit 99\n"
    )
    curl.chmod(0o755)
    python = tools / "python"
    python.write_text('#!/bin/sh\nprintf "%s\\0" "$@" > "$PYTHON_ARGS"\nexit 19\n')
    python.chmod(0o755)
    if os.name == "nt":
        shutil.copy2(python, tools / "python3")
    else:
        (tools / "python3").symlink_to(python)
    manager = tools / "worktree-manager"
    manager.write_text('#!/bin/sh\nprintf touched > "$MANAGER_CALLED"\nexit 71\n')
    manager.chmod(0o755)
    git = tools / "git"
    git.write_text('#!/bin/sh\nprintf unexpected-git > "$GIT_CALLED"\nexit 89\n')
    git.chmod(0o755)
    resolver = home / ".agent-worktrees/bin/resolve-runtime.sh"
    resolver.parent.mkdir(parents=True)
    resolver.write_text('printf resolved > "$RESOLVED"\nAW_PY="$FAKE_PY"\n')
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "DOWNLOAD_SCRIPT": str(downloaded),
        "FAKE_PY": str(python),
        "BOOT_ARGS": str(tmp_path / "bootstrap.args"),
        "BOOT_CWD": str(tmp_path / "cwd"),
        "BOOT_CONTEXT": str(tmp_path / "context"),
        "CURL_ARGS": str(tmp_path / "curl.args"),
        "PYTHON_ARGS": str(tmp_path / "python.args"),
        "RESOLVED": str(tmp_path / "resolved"),
        "MANAGER_CALLED": str(tmp_path / "manager-called"),
        "GIT_CALLED": str(tmp_path / "git-called"),
        "BOOT_RC": "23",
        "CURL_RC": "0",
        "TMPDIR": str(tmp_path),
        "AGENT_SCRATCH_ROOT": str(tmp_path),
        "AGENT_WORKTREES_NO_SELFPROVISION": "1",
        "PYTHONHOME": "/unavailable-python-home",
        "PYTHONPATH": "/unavailable-python-path",
        "VIRTUAL_ENV": "/unavailable-venv",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key in ("WORKTREE_MANAGER_ROOT", "AGENT_WORKTREES_CONTROL_PLANE_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR", raising=False)
    if os.name == "nt":
        monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(home / ".worktree-manager"))
    return tmp_path, home, resolver


def _arguments(path: Path) -> list[str]:
    values = path.read_bytes().decode().split("\0")
    assert values[-1] == ""
    return values[:-1]


@pytest.mark.parametrize("front", ["global", "payload", "project", "installed-project"])
@pytest.mark.parametrize("runtime", ["missing", "broken"])
def test_update_entry_fetches_before_python(update_entry_case, front, runtime, native_bash):
    tmp, _home, resolver = update_entry_case
    if runtime == "missing":
        resolver.unlink()
    flags = ["--force", "--skip-modules", "module with spaces", "", "*", "--no-anchor-sync", ""]
    if front == "payload":
        command = [native_bash, str(PLUGIN / "bin/payload/agent-worktrees")]
    elif front in ("project", "installed-project"):
        user_bin = None
        if front == "installed-project":
            user_bin = _home / ".local/bin"
            user_bin.mkdir(parents=True)
            for name in (
                "agent-worktrees", "agent-worktrees-update-entry.sh",
                "agent-worktrees-update-entry.ps1",
            ):
                shutil.copy2(PLUGIN / "bin" / name, user_bin / name)
        rendered = project_command_templates.render(
            "sample", PLUGIN, windows=False, user_bin=user_bin,
        )
        project = tmp / "sample"
        project.write_text(rendered["sample"])
        command = [native_bash, str(project)]
    else:
        command = [native_bash, str(PLUGIN / "bin/agent-worktrees"), "--project", "sample"]
    if front == "payload":
        command += ["--project", "sample"]
    result = subprocess.run([*command, "update", *flags], capture_output=True, timeout=15)
    assert result.returncode == 23, result.stderr.decode()
    assert _arguments(tmp / "bootstrap.args") == [
        "--repair-update",
        "https://github.com/ThomasMichon/copilot-extensions.git",
        "main",
        "update",
        "--project",
        "sample",
        *flags,
    ]
    assert not (tmp / "resolved").exists()
    assert not (tmp / "python.args").exists()
    assert not (tmp / "manager-called").exists()
    assert not list(tmp.glob("agent-update-*"))


@pytest.mark.parametrize(
    "args",
    [
        ["status"],
        ["--project", "sample", "status"],
        ["update", "--no-manager"],
        ["update", "--auth-report"],
        ["update", "--phase", "preflight", "--plugin", "agent-example"],
        ["update", "--phase=runtimes", "--plugin=agent-example"],
        ["update", "--plugin", "agent-example"],
        ["update", "--request-status", "synthetic-request"],
        ["update", "--request-status=synthetic-request"],
        ["update", "--recovery-status"],
        ["update", "--recover-after-restart"],
        ["update", "--recover-for-repair"],
        ["update", "--help"],
        ["unknown", "update"],
    ],
)
def test_update_entry_leaves_other_calls_unchanged(update_entry_case, native_bash, args):
    tmp, _home, _resolver = update_entry_case
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), *args],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 19, result.stderr.decode()
    assert (tmp / "resolved").exists()
    assert not (tmp / "curl.args").exists()


@pytest.mark.parametrize("front", ["global", "installed-project"])
@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("flags", [
    ["--phase", "preflight", "--plugin", "agent-example"],
    ["--phase=runtimes", "--plugin=agent-example"],
    ["--plugin", "agent-example"],
    ["--inventory", "--json"],
    ["--target", "agent-example"],
    ["--target=agent-example"],
])
def test_selected_update_operation_refuses_implicit_posix_bootstrap(
    update_entry_case, monkeypatch, native_bash, front, ready, flags,
):
    tmp, home, resolver = update_entry_case
    monkeypatch.delenv("AGENT_WORKTREES_NO_SELFPROVISION")
    installer = home / ".copilot/installed-plugins/fixture/agent-worktrees/scripts/install.sh"
    installer.parent.mkdir(parents=True)
    installer.write_text(
        '#!/bin/sh\nprintf unexpected > "$MANAGER_CALLED"\nexit 99\n',
    )
    if not ready:
        resolver.unlink()
    if front == "installed-project":
        user_bin = home / ".local/bin"
        user_bin.mkdir(parents=True)
        for name in (
            "agent-worktrees", "agent-worktrees-update-entry.sh",
            "agent-worktrees-update-entry.ps1",
        ):
            shutil.copy2(PLUGIN / "bin" / name, user_bin / name)
        rendered = project_command_templates.render(
            "sample", PLUGIN, windows=False, user_bin=user_bin,
        )
        entry = tmp / "sample"
        entry.write_text(rendered["sample"])
        command = [native_bash, str(entry)]
    else:
        command = [native_bash, str(PLUGIN / "bin/agent-worktrees"), "--project", "sample"]
    result = subprocess.run(
        [*command, "update", *flags], capture_output=True, timeout=15,
    )
    assert result.returncode == (19 if ready else 127), result.stderr.decode()
    if ready:
        assert _arguments(tmp / "python.args") == [
            "-m", "agent_worktrees", "--project", "sample", "update", *flags,
        ]
    else:
        assert not (tmp / "python.args").exists()
    assert not (tmp / "curl.args").exists()
    assert not (tmp / "manager-called").exists()
    assert not (home / ".agent-worktrees/.provision-status").exists()


def test_update_entry_preserves_download_failure(update_entry_case, monkeypatch, native_bash):
    tmp, _home, _resolver = update_entry_case
    monkeypatch.setenv("CURL_RC", "22")
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "update"],
        timeout=15,
    )
    # The optional raw download failed, then the same-source Git attempt failed.
    assert result.returncode == 89
    assert not (tmp / "bootstrap.args").exists()
    assert not (tmp / "resolved").exists()
    assert not list(tmp.glob("agent-update-*"))


def test_update_entry_transports_invocation_directory(update_entry_case, native_bash, monkeypatch):
    tmp, _home, _resolver = update_entry_case
    invocation = tmp / "invocation repo"
    invocation.mkdir()
    monkeypatch.delenv("AGENT_WORKTREES_UPDATE_CONTEXT", raising=False)
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "update"],
        cwd=invocation, capture_output=True, timeout=15,
    )
    assert result.returncode == 23
    assert (tmp / "context").read_text() == str(invocation)


def test_update_entry_preserves_source_ref(update_entry_case, native_bash):
    tmp, home, _resolver = update_entry_case
    root = home / ".worktree-manager"
    root.mkdir()
    (root / "config.toml").write_text(
        '[other]\nrepo = "ignored"\n[source]\n'
        'repo = "https://github.com/example/fork.git"\nref = "release/canary"\n'
    )
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "update"],
        timeout=15,
    )
    assert result.returncode == 23
    assert _arguments(tmp / "bootstrap.args")[:3] == [
        "--repair-update",
        "https://github.com/example/fork.git",
        "release/canary",
    ]
    assert _arguments(tmp / "curl.args")[1] == (
        "https://raw.githubusercontent.com/example/fork/release/canary/worktree-manager/bootstrap.sh"
    )


def test_update_entry_uses_registered_nondefault_root(update_entry_case, native_bash):
    tmp, home, _resolver = update_entry_case
    root = tmp / "registered-manager"
    root.mkdir()
    (root / "config.toml").write_text(
        '[source]\nrepo = "https://github.com/example/registered.git"\nref = "canary"\n',
    )
    registry = home / ".agent-worktrees/control-plane-providers.d"
    registry.mkdir()
    (registry / "worktree-manager.json").write_text(json.dumps({
        "schema_version": 1, "provider": "worktree-manager", "provider_root": str(root),
        "command": [str(home / ".local/bin/worktree-manager")],
    }))
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "update"],
        capture_output=True, timeout=15,
    )
    assert result.returncode == 23, result.stderr.decode()
    assert _arguments(tmp / "bootstrap.args")[:3] == [
        "--repair-update", "https://github.com/example/registered.git", "canary",
    ]
    assert not (home / ".worktree-manager").exists()


@pytest.mark.parametrize(
    "source",
    [
        'repo = "https://github.com/example/fork.git"\nref = "first"\nref = "second"',
        'repo = "unterminated',
        'repo = "unsupported\\qescape"',
        'repo = ""',
        '"repo" = "https://github.com/example/fork.git"',
    ],
)
def test_update_entry_refuses_unrepresentable_source(update_entry_case, native_bash, source):
    tmp, home, _resolver = update_entry_case
    root = home / ".worktree-manager"
    root.mkdir()
    config = root / "config.toml"
    data = "[source]\n" + source + "\n"
    config.write_text(data)
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "update"],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 126
    assert config.read_text() == data
    assert not (tmp / "curl.args").exists()
    assert not (tmp / "resolved").exists()


def test_update_entry_refuses_alternate_provider(update_entry_case, monkeypatch, native_bash):
    tmp, _home, _resolver = update_entry_case
    monkeypatch.setenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", "another-provider")
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "update"],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 126
    assert not (tmp / "curl.args").exists()


@pytest.mark.parametrize("name", [
    "worktree-manager", "worktree-manager.cmd", "worktree-manager.ps1",
])
def test_update_entry_fresh_installer_protects_foreign_commands(tmp_path, monkeypatch, name):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", raising=False)
    stub = home / ".local/bin" / name
    stub.parent.mkdir(parents=True)
    stub.write_bytes(b"foreign command")
    validate = runpy.run_path(str(REPO / "worktree-manager/scripts/update_entry.py"))[
        "validate_manager_ownership"
    ]
    with pytest.raises(RuntimeError, match="Foreign Manager command preserved"):
        validate(home / ".worktree-manager", REPO / "worktree-manager")
    assert stub.read_bytes() == b"foreign command"


def test_update_entry_fresh_installer_accepts_owned_commands(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", raising=False)
    local_bin = home / ".local/bin"
    local_bin.mkdir(parents=True)
    for name in (
        "agent-worktrees", "agent-worktrees.cmd", "agent-worktrees.ps1",
        "agent-worktrees-update-entry.sh", "agent-worktrees-update-entry.ps1",
    ):
        shutil.copy2(PLUGIN / "bin" / name, local_bin / name)
    shutil.copy2(
        PLUGIN / "scripts/installation-context/json-query.awk",
        local_bin / "agent-worktrees-update-json.awk",
    )
    validate = runpy.run_path(str(REPO / "worktree-manager/scripts/update_entry.py"))[
        "validate_manager_ownership"
    ]
    validate(home / ".worktree-manager", REPO / "worktree-manager")


@pytest.mark.parametrize("git_rc", [0, 31])
def test_update_entry_runs_fresh_bootstrap_and_preserves_native_exit(
    update_entry_case, monkeypatch, native_bash, git_rc,
):
    tmp, home, _resolver = update_entry_case
    staging = home / ".worktree-manager/staging"
    (staging / ".git").mkdir(parents=True)
    (staging / "worktree-manager").mkdir()
    interpreter = tmp / "fresh-python"
    interpreter.write_text(
        '#!/bin/sh\nprintf "%s\\0" "$@" > "$FRESH_ARGS"\nexit 37\n',
    )
    interpreter.chmod(0o755)
    uv = tmp / "tools/uv"
    uv.write_text(f'#!/bin/sh\nprintf "%s\\n" "{interpreter}"\n')
    uv.chmod(0o755)
    git = tmp / "tools/git"
    git.write_text(f'#!/bin/sh\nprintf "%s\\0" "$@" >> "$GIT_ARGS"\nexit {git_rc}\n')
    git.chmod(0o755)
    monkeypatch.setenv("DOWNLOAD_SCRIPT", str(REPO / "worktree-manager/bootstrap.sh"))
    monkeypatch.setenv("FRESH_ARGS", str(tmp / "fresh.args"))
    monkeypatch.setenv("GIT_ARGS", str(tmp / "git.args"))
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "--project", "sample",
         "update", "--force", "--skip-modules", "a b"],
        capture_output=True, timeout=15,
    )
    assert result.returncode == (git_rc or 37), result.stderr.decode()
    if git_rc:
        assert not (tmp / "fresh.args").exists()
    else:
        forwarded = _arguments(tmp / "fresh.args")
        assert forwarded[:6] == ["-I", "-S", "-B", "-X", "utf8", "scripts/runtime_install.py"]
        assert forwarded[-8:] == [
            "--repair-update", "--", "update", "--project", "sample", "--force",
            "--skip-modules", "a b",
        ]
        assert "--root" in forwarded
    assert not (tmp / "resolved").exists()
    assert not (tmp / "python.args").exists()


@pytest.mark.parametrize("source_kind", ["default", "fork", "malformed", "case-sensitive"])
def test_update_entry_powershell_before_runtime(
    update_entry_case, native_powershell, source_kind,
):
    tmp, home, _resolver = update_entry_case
    repo, ref = "https://github.com/ThomasMichon/copilot-extensions.git", "main"
    if source_kind != "default":
        root = home / ".worktree-manager"
        root.mkdir()
        table = "SOURCE" if source_kind == "case-sensitive" else "source"
        configured = "https://github.com/example/fork.git"
        (root / "config.toml").write_text(
            f'[{table}]\nrepo = "{configured}"\nref = '
            + ('"unterminated' if source_kind == "malformed" else '"release/canary"\n'),
        )
        if source_kind == "fork":
            repo, ref = configured, "release/canary"
    downloaded = tmp / "download.ps1"
    downloaded.write_text(
        "$json = ConvertTo-Json -Compress -InputObject @($args)\n"
        "[IO.File]::WriteAllText($env:BOOT_ARGS, $json)\n"
        "exit 23\n"
    )
    harness = tmp / "harness.ps1"
    command = str(PLUGIN / "bin/agent-worktrees.ps1").replace("'", "''")
    source = str(downloaded).replace("'", "''")
    harness.write_text(
        f"function Invoke-WebRequest {{ param($Uri, $OutFile, [switch]$UseBasicParsing)\n"
        f"  [IO.File]::Copy('{source}', $OutFile)\n}}\n"
        f"& '{command}' --project sample update --force 'module with spaces'\nexit $LASTEXITCODE\n"
    )
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        capture_output=True,
        timeout=30,
        **no_window_kwargs(),
    )
    if source_kind == "malformed":
        assert result.returncode != 0
        assert not (tmp / "bootstrap.args").exists()
        assert not (tmp / "python.args").exists()
        return
    assert result.returncode == 23, result.stderr.decode()
    assert json.loads((tmp / "bootstrap.args").read_text()) == [
        "--repair-update",
        repo,
        ref,
        "update",
        "--project",
        "sample",
        "--force",
        "module with spaces",
    ]
    assert not (tmp / "resolved").exists()
    assert not (tmp / "python.args").exists()


def test_update_entry_helpers_are_in_publication_inventory():
    tree = ast.parse((PLUGIN / "scripts/runtime_install_publication.py").read_text())
    inventory = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "TOOLS" for target in node.targets)
    )
    expected = {"agent-worktrees-update-entry.sh", "agent-worktrees-update-entry.ps1"}
    assert expected <= set(inventory)
    for shell in ("sh", "ps1"):
        installer = (PLUGIN / "scripts" / f"install.{shell}").read_text()
        assert "agent-worktrees-update-entry.sh" in installer
