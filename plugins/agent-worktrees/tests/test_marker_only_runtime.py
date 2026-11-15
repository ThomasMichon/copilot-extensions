"""Tests for the junction-free, marker-only runtime resolution (#1106).

Every runtime-resolution flow resolves the active slot python via the
`current-version` marker -> versions/<ver>, never through a `.venv` link. These
tests cover the shared resolver helpers' presence + deployment, and that no
runtime-resolution flow still reads a `.venv` link.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_SCRIPTS = _PLUGIN / "scripts"
_BIN = _PLUGIN / "bin"


def test_resolver_helpers_exist():
    assert (_SCRIPTS / "resolve-runtime.sh").is_file()
    assert (_SCRIPTS / "resolve-runtime.ps1").is_file()


def test_resolver_helpers_are_marker_only():
    for name in ("resolve-runtime.sh", "resolve-runtime.ps1"):
        text = (_SCRIPTS / name).read_text(encoding="utf-8")
        assert "current-version" in text, f"{name} must resolve via the marker"
        assert "versions" in text


def _native_resolver(root: Path, host: str, *, probe: str | None = None):
    executable = shutil.which(host)
    if host == "bash" and os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
        executable = str(candidate) if candidate and candidate.is_file() else None
    if not executable:
        pytest.skip(f"{host} unavailable")
    if host == "bash":
        def posix(path):
            value = path.as_posix()
            return "/" + value[0].lower() + value[2:] if os.name == "nt" else value

        script = (
            f"set -eu; AGENT_RT_ROOT='{posix(root)}'; "
            f". '{posix(_SCRIPTS / 'resolve-runtime.sh')}'; "
        )
        script += (
            f'"$AW_PY" -I -B -c \'{probe}\'' if probe else 'printf "%s" "$AW_PY"'
        )
        argv = [executable, "-c", script]
    else:
        literal_root = str(root).replace("'", "''")
        resolver = str(_SCRIPTS / "resolve-runtime.ps1").replace("'", "''")
        script = f"$env:AGENT_RT_ROOT='{literal_root}'; . '{resolver}'; "
        script += (
            "& $AwPy -I -B -c '" + probe.replace("'", "''") + "'; exit $LASTEXITCODE"
            if probe else "[Console]::Out.Write($AwPy)"
        )
        argv = [executable, "-NoProfile", "-Command", script]
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("host", ["bash", "pwsh"])
@pytest.mark.parametrize("layout", ["nested", "flat"])
def test_native_resolver_executes_real_uv_environment(tmp_path, host, layout):
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv unavailable")
    root = tmp_path / "runtime"
    version = "1.2.3+source." + "a" * 40
    slot = root / "versions" / version
    environment = slot / ".venv" if layout == "nested" else slot
    result = subprocess.run(
        [uv, "venv", "--python", sys.executable, "--no-python-downloads", str(environment)],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert (environment / "pyvenv.cfg").is_file()
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = subprocess.run(
        [str(python), "-I", "-B", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert site.returncode == 0, site.stderr
    (Path(site.stdout.strip()) / "consumer_probe.py").write_text("VALUE = 42\n", encoding="utf-8")
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": version, "completed_at": "2026-11-04T00:00:00Z", "pid": 1,
    }), encoding="utf-8")
    (root / "current-version").write_text(version + "\n", encoding="utf-8")
    probe = (
        'import consumer_probe,json,sys; '
        'print(json.dumps([sys.prefix,sys.base_prefix,consumer_probe.VALUE]))'
    )
    result = _native_resolver(root, host, probe=probe)
    assert result.returncode == 0, result.stderr
    prefix, base, value = json.loads(result.stdout)
    assert Path(prefix) == environment and Path(base) != environment and value == 42


@pytest.mark.parametrize("layout", ["nested", "flat"])
@pytest.mark.parametrize("context_case", ["legacy", "valid", "invalid"])
@pytest.mark.parametrize("native_shell", ["bash", "pwsh", "powershell.exe"])
def test_session_end_uses_real_slot_without_path_python(tmp_path, layout, context_case, native_shell):
    from agent_worktrees.config import detect_platform

    if (native_shell == "bash") == (os.name == "nt"):
        pytest.skip("platform-native hook shell")
    uv = shutil.which("uv")
    shell = shutil.which(native_shell)
    if not uv or not shell:
        pytest.skip("uv and native hook shell required")
    tmp_path = tmp_path.parent / hashlib.sha256(os.fsencode(tmp_path.name)).hexdigest()[:8] / "caf\u00e9"
    tmp_path.mkdir(parents=True)
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0}
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in ("deregister-session.sh", "deregister-session.ps1", "hook_runtime.py",
                 "resolve-runtime.sh", "resolve-runtime.ps1", "managed-session.ps1",
                 "managed-session.sh", "managed_session.py", "execution_platform.py",
                 "guarded_anchor_map.py", "registry_root.py", "hook_policy_process.py",
                 "hook_process_identity.py", "managed_project_identity.py", "remote_identity.py"):
        shutil.copyfile(_SCRIPTS / name, scripts / name)
    shutil.copytree(_SCRIPTS / "installation-context", scripts / "installation-context")
    shutil.copyfile(_PLUGIN / "plugin.json", payload / "plugin.json")
    (scripts / "hook_client.py").write_text(
        "import consumer_probe,json,sys\n"
        "print(json.dumps({'prefix':sys.prefix,'event':sys.argv[1],"
        "'payload':json.load(sys.stdin),'cache':consumer_probe.__cached__,"
        "'value':consumer_probe.VALUE,'no_write':sys.dont_write_bytecode,'utf8':sys.flags.utf8_mode,"
        "'stdinEncoding':sys.stdin.encoding}))\n", encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    root = home / ".agent-worktrees"
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "AGENT_HOME": str(home),
           "COPILOT_PLUGIN_ROOT": str(payload), "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252",
           "LOCALAPPDATA": str(home / "os-cache"), "XDG_CACHE_HOME": str(home / "os-cache")}
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_WORKTREES_PAYLOAD_ROOT",
                "AGENT_RT_ROOT", "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"):
        env.pop(key, None)
    if context_case != "legacy":
        common = ["-Action", "stamp", "-SourceJson",
                  json.dumps({"source": "github", "repo": "example/marketplace"}),
                  "-MarketplaceKey", "fixture", "-PluginId", "agent-worktrees",
                  "-PayloadRoot", str(payload), "-PayloadVersion", "1.0.0",
                  "-PayloadOrigin", "explicit", "-ExpectedNamespaceGeneration", "0",
                  "-ExpectedInstallGeneration", "0", "-DurableHome", str(tmp_path / "durable")]
        if os.name == "nt":
            stamp = [shell, "-NoProfile", "-File",
                     str(scripts / "installation-context/installation-context.ps1"), *common]
        else:
            options = ["stamp"]
            names = {"-Action": None, "-SourceJson": "--source-json",
                     "-MarketplaceKey": "--marketplace-key", "-PluginId": "--plugin-id",
                     "-PayloadRoot": "--payload-root", "-PayloadVersion": "--payload-version",
                     "-PayloadOrigin": "--payload-origin",
                     "-ExpectedNamespaceGeneration": "--expected-namespace-generation",
                     "-ExpectedInstallGeneration": "--expected-install-generation",
                     "-DurableHome": "--durable-home"}
            for name, value in zip(common[::2], common[1::2]):
                if names[name]:
                    options.extend([names[name], value])
            stamp = [shell, str(scripts / "installation-context/installation-context.sh"), *options]
        result = subprocess.run(
            stamp, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30, **flags,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        context = json.loads(result.stdout)
        root = Path(context["pluginRoot"])
        receipt = Path(context["installReceipt"])
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(receipt)
        if context_case == "invalid":
            data = json.loads(receipt.read_text(encoding="utf-8"))
            data["pluginId"] = "foreign-plugin"
            receipt.write_text(json.dumps(data), encoding="utf-8")
    version = "1.2.3+source." + "a" * 40
    slot = root / "versions" / version
    environment = slot / ".venv" if layout == "nested" else slot
    result = subprocess.run(
        [uv, "venv", "--python", sys.executable, "--no-python-downloads", str(environment)],
        capture_output=True, text=True, timeout=30, **flags,
    )
    assert result.returncode == 0, result.stderr
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    control = subprocess.run(
        [str(python), "-X", "utf8=0", "-c",
         "import json,sys; print(json.dumps([sys.flags.utf8_mode,sys.stdin.encoding]))"],
        env=env, input="", capture_output=True, text=True, timeout=30, **flags,
    )
    assert control.returncode == 0, control.stderr
    assert json.loads(control.stdout) == [0, "cp1252"]
    result = subprocess.run(
        [str(python), "-I", "-B", "-X", "utf8", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
        capture_output=True, text=True, encoding="utf-8", timeout=30, **flags,
    )
    assert result.returncode == 0, result.stderr
    site = Path(result.stdout.strip())
    (site / "consumer_probe.py").write_text("VALUE=42\n", encoding="utf-8")
    probe_hash = hashlib.sha256((site / "consumer_probe.py").read_bytes()).digest()
    if context_case != "legacy":
        owner = site / "agent_procutil"
        shutil.copytree(
            _PLUGIN.parents[1] / "libs/agent-procutil/src/agent_procutil",
            owner, ignore=shutil.ignore_patterns("__pycache__"),
        )
        metadata = site / "consumer_probe-1.0.dist-info"
        metadata.mkdir()
        (metadata / "RECORD").write_text(
            "consumer_probe.py,sha256="
            + base64.urlsafe_b64encode(probe_hash).decode().rstrip("=")
            + "," + str((site / "consumer_probe.py").stat().st_size) + "\n",
            encoding="utf-8",
        )
    site_loaded = tmp_path / "site-loaded"
    (site / "sitecustomize.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(site_loaded)) + ").touch()\n",
        encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": version, "completed_at": "2026-11-04T00:00:00Z", "pid": 1,
        "payload_hash": probe_hash.hex(),
    }), encoding="utf-8")
    (root / "current-version").write_text(version + "\n", encoding="utf-8")
    workspace = tmp_path / "caf\u00e9"
    for folder in ("objects", "refs"):
        (workspace / ".git" / folder).mkdir(parents=True)
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / "projects.yaml").write_text(json.dumps({
        "projects": {"example": {}},
    }))
    (root / "repos.yaml").write_text(json.dumps({
        "repos": {"example": {detect_platform(): str(workspace)}},
    }))
    legacy = home / ".agent-worktrees/bin"
    legacy.mkdir(parents=True, exist_ok=True)
    for suffix in ("sh", "ps1"):
        (legacy / ("deregister-session." + suffix)).write_text("exit 91\n", encoding="utf-8")
    if os.name == "nt":
        env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    else:
        tools = tmp_path / "tools"
        tools.mkdir()
        for name in ("bash", "dirname", "basename", "awk", "tr", "cat", "sort", "find",
                     "date", "readlink", "realpath", "cut", "stat", "od", "grep", "sed",
                     "head", "tail", "wc", "uname", "sha256sum", "shasum", "openssl",
                     "mktemp", "rm", "sleep", "id"):
            executable = shutil.which(name)
            if not executable and name in {"realpath", "sha256sum", "shasum", "openssl"}:
                continue
            assert executable, name
            (tools / name).symlink_to(executable)
        env["PATH"] = str(tools)
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    entry = hooks["hooks"]["sessionEnd"][0]
    command = ([shell, "-NoProfile", "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::GetEncoding(437); "
                "$OutputEncoding=[Text.Encoding]::GetEncoding(437); " + entry["powershell"]] if os.name == "nt"
               else [shell, "-c", entry["bash"]])
    event = {"sessionId": "fixture-session", "cwd": str(tmp_path / "caf\u00e9"), "reason": "user_exit"}
    result = subprocess.run(
        command, input=json.dumps(event, ensure_ascii=False), env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=30, **flags,
    )
    assert result.returncode == 0, result.stderr
    if context_case == "invalid":
        assert not result.stdout
        assert not result.stderr
        assert not site_loaded.exists()
    else:
        assert result.stdout, result.stderr
        observed = json.loads(result.stdout)
        assert Path(observed["prefix"]) == environment
        assert observed["event"] == "sessionEnd" and observed["payload"] == event
        assert observed["value"] == 42
        assert observed["utf8"] == 1 and observed["stdinEncoding"].lower() == "utf-8"
        cached = Path(observed["cache"].removeprefix("\\\\?\\"))
        assert observed["no_write"] is True
        assert not cached.exists()
        assert not (root / "cache/python").exists()
        if context_case == "valid":
            repeated = subprocess.run(
                command, input=json.dumps(event, ensure_ascii=False), env=env,
                capture_output=True, text=True, encoding="utf-8", timeout=30, **flags,
            )
            assert repeated.returncode == 0, repeated.stderr
            assert json.loads(repeated.stdout) == observed
            assert not cached.exists()
        assert not list(slot.rglob("*.pyc"))


@pytest.mark.parametrize("host", ["bash", "pwsh"])
def test_native_resolver_missing_current_does_not_abort_fallback(tmp_path, host):
    _make_slot(tmp_path, "1.2.3")
    root = tmp_path / ".agent-worktrees"
    (root / "current-version").write_text("9.9.9\n", encoding="utf-8")
    (root / "last-known-good").write_text("1.2.3\n", encoding="utf-8")
    result = _native_resolver(root, host)
    assert result.returncode == 0, result.stderr
    assert result.stdout.replace("\\", "/").endswith("/versions/1.2.3/bin/python")


@pytest.mark.parametrize("host", ["bash", "pwsh"])
def test_native_resolver_refuses_redirected_project_environment(tmp_path, host):
    _make_slot(tmp_path, "1.2.3")
    root = tmp_path / ".agent-worktrees"
    environment = root / "versions/1.2.3/.venv"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    if os.name == "nt":
        pwsh = shutil.which("pwsh")
        if not pwsh:
            pytest.skip("PowerShell unavailable for unprivileged junction fixture")
        link = str(environment).replace("'", "''")
        target = str(foreign).replace("'", "''")
        result = subprocess.run(
            [pwsh, "-NoProfile", "-Command",
             f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' | Out-Null"],
            capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
    else:
        environment.symlink_to(foreign, target_is_directory=True)
    (root / "current-version").write_text("1.2.3\n", encoding="utf-8")
    result = _native_resolver(root, host)
    assert result.returncode == 0, result.stderr
    assert not result.stdout


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is unavailable")
def test_powershell_resolver_exports_payload_invocation_contract(tmp_path):
    home = tmp_path / "home"
    runtime = tmp_path / "cell-runtime"
    slot_python = runtime / "versions" / "1.2.3" / "Scripts" / "python.exe"
    slot_python.parent.mkdir(parents=True)
    slot_python.touch()
    (slot_python.parents[1] / ".install-complete.json").write_text(
        json.dumps({
            "version": "1.2.3",
            "completed_at": "2026-08-27T00:00:00Z",
            "pid": 1,
        }),
        encoding="utf-8",
    )
    (runtime / "current-version").write_text("1.2.3\n", encoding="utf-8")
    resolver = _SCRIPTS / "resolve-runtime.ps1"
    home_literal = str(home).replace("'", "''")
    runtime_literal = str(runtime).replace("'", "''")
    resolver_literal = str(resolver).replace("'", "''")
    script = (
        f"$env:USERPROFILE = '{home_literal}'; "
        f"$env:AGENT_RT_ROOT = '{runtime_literal}'; "
        f". '{resolver_literal}'; "
        "[pscustomobject]@{ AwPy = $AwPy; AgentRtPy = $AgentRtPy } "
        "| ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    resolved = json.loads(result.stdout)
    assert Path(resolved["AwPy"]) == slot_python
    assert resolved["AgentRtPy"] == resolved["AwPy"]


@pytest.mark.parametrize("installer", ["install.ps1", "install.sh"])
def test_installers_deploy_the_resolver(installer: str):
    text = (_SCRIPTS / installer).read_text(encoding="utf-8")
    assert "resolve-runtime.ps1" in text
    assert "resolve-runtime.sh" in text


def test_installer_py_deploys_the_resolver():
    text = (_PLUGIN / "src" / "agent_worktrees" / "installer.py").read_text("utf-8")
    assert "resolve-runtime.ps1" in text
    assert "resolve-runtime.sh" in text


def _hook_scripts() -> list[Path]:
    names = [
        "session-machine", "session-conduct", "register-session",
        "deregister-session", "project-hooks", "provision-check",
        "anchor-hygiene-check", "bootstrap-check",
    ]
    out: list[Path] = []
    for n in names:
        for ext in (".sh", ".ps1"):
            p = _SCRIPTS / f"{n}{ext}"
            if p.is_file():
                out.append(p)
    return out


def test_hooks_resolve_via_marker_not_venv():
    """No hook resolves a python interpreter through a `.venv` link -- each
    sources the canonical marker resolver instead (#1106)."""
    for hook in _hook_scripts():
        text = hook.read_text(encoding="utf-8")
        assert not re.search(r"\.venv[/\\](bin|Scripts)", text), (
            f"{hook.name} still resolves python through a .venv link"
        )
        if hook.stem in {"bootstrap-check", "provision-check"}:
            assert "resolve-runtime" not in text
        else:
            assert "resolve-runtime" in text, f"{hook.name} must source the resolver"


def test_binstubs_are_marker_only():
    """The launcher binstubs resolve via the marker with no `.venv` fallback."""
    for name in ("agent-worktrees", "agent-worktrees.ps1", "agent-worktrees.cmd",
                 "launch-session.sh", "launch-session.ps1", "launch-session.cmd"):
        p = _BIN / name
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        if name.endswith(".cmd"):
            assert name.replace(".cmd", ".ps1") in text
            assert "powershell" in text
            continue
        assert "current-version" in text, f"{name} must resolve via the marker"
        assert not re.search(r"\.venv[/\\](bin|Scripts)", text), (
            f"{name} still resolves python through a .venv link"
        )


# -- last-known-good fallback (#742) ------------------------------------------

def test_resolvers_reference_last_known_good():
    """Both resolvers implement the tier-2 last-known-good fallback."""
    for name in ("resolve-runtime.sh", "resolve-runtime.ps1"):
        text = (_SCRIPTS / name).read_text(encoding="utf-8")
        assert "last-known-good" in text, (
            f"{name} must prefer last-known-good over a newest-slot guess (#742)"
        )


@pytest.mark.parametrize("installer", ["install.sh", "install.ps1"])
def test_installer_records_last_known_good(installer: str):
    """The installer stamps last-known-good when it activates a slot (#742)."""
    text = (_SCRIPTS / installer).read_text(encoding="utf-8")
    assert "last-known-good" in text, (
        f"{installer} must record last-known-good on activate (#742)"
    )


def _make_slot(root: Path, version: str) -> None:
    runtime = root / ".agent-worktrees"
    slot = runtime / "versions" / version
    runtime_bin = runtime / "bin"
    runtime_bin.mkdir(parents=True, exist_ok=True)
    for name in ("resolve-runtime.sh", "resolve-runtime.ps1"):
        shutil.copy2(_SCRIPTS / name, runtime_bin / name)
    (slot / "bin").mkdir(parents=True, exist_ok=True)
    (slot / ".install-complete.json").write_text(
        json.dumps({
            "version": version,
            "completed_at": "2026-08-27T00:00:00Z",
            "pid": 1,
        }),
        encoding="utf-8",
    )
    py = slot / "bin" / "python"
    py.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    py.chmod(0o755)


def _resolve(home: Path) -> str:
    """Source resolve-runtime.sh under a fabricated HOME; return AW_PY."""
    import subprocess

    resolver = _SCRIPTS / "resolve-runtime.sh"
    script = f'. "{resolver}"; printf "%s" "$AW_PY"'
    out = subprocess.run(
        ["sh", "-c", script],
        capture_output=True, text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
    )
    assert out.returncode == 0, (
        f"resolver exited {out.returncode}: {out.stderr.strip()}"
    )
    return out.stdout.strip()


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_resolver_rejects_incomplete_marker_slot(tmp_path):
    _make_slot(tmp_path, "1.2.3")
    runtime = tmp_path / ".agent-worktrees"
    (runtime / "versions" / "1.2.3" / ".install-complete.json").unlink()
    (runtime / "current-version").write_text("1.2.3\n", encoding="utf-8")

    assert _resolve(tmp_path) == ""


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
@pytest.mark.parametrize(
    "marker",
    [
        "{not-json",
        '{"version": "wrong", "completed_at": "x", "pid": 1}',
        '{"version": "1.2.3", "version": "1.2.3", "completed_at": "x", "pid": 1}',
        '{"\\u0076ersion": "wrong", "version": "1.2.3", "completed_at": "x", "pid": 1}',
        '{"version": "1.2.3", "completed_at": "x", "pid": 01}',
    ],
)
def test_resolver_rejects_invalid_marker_json(tmp_path, marker):
    _make_slot(tmp_path, "1.2.3")
    runtime = tmp_path / ".agent-worktrees"
    (runtime / "versions" / "1.2.3" / ".install-complete.json").write_text(
        marker, encoding="utf-8"
    )
    (runtime / "current-version").write_text("1.2.3\n", encoding="utf-8")

    assert _resolve(tmp_path) == ""


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_posix_resolver_exports_payload_invocation_contract(tmp_path):
    _make_slot(tmp_path, "1.2.3")
    runtime = tmp_path / ".agent-worktrees"
    (runtime / "current-version").write_text("1.2.3\n", encoding="utf-8")
    resolver = _SCRIPTS / "resolve-runtime.sh"
    script = (
        f'. "{resolver}"; '
        'printf "%s\\n%s\\n" "$AW_PY" "$AGENT_RT_PY"'
    )
    result = subprocess.run(
        ["sh", "-c", script],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    aw_py, agent_rt_py = result.stdout.splitlines()
    assert Path(aw_py).name == "python"
    assert agent_rt_py == aw_py


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_posix_resolver_honors_explicit_runtime_root(tmp_path):
    cell = tmp_path / "cell-runtime"
    slot = cell / "versions" / "1.2.3"
    (slot / "bin").mkdir(parents=True)
    (slot / ".install-complete.json").write_text(
        json.dumps(
            {
                "version": "1.2.3",
                "completed_at": "2026-08-27T00:00:00Z",
                "pid": 1,
            }
        ),
        encoding="utf-8",
    )
    python = slot / "bin" / "python"
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    (cell / "current-version").write_text("1.2.3\n", encoding="utf-8")
    resolver = _SCRIPTS / "resolve-runtime.sh"
    script = f'. "{resolver}"; printf "%s" "$AW_PY"'
    result = subprocess.run(
        ["sh", "-c", script],
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path / "home"),
            "AGENT_RT_ROOT": str(cell),
            "PATH": "/usr/bin:/bin",
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(python)


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_resolver_tier1_prefers_current_version_marker(tmp_path):
    aw = tmp_path / ".agent-worktrees"
    _make_slot(tmp_path, "0.1.0")
    _make_slot(tmp_path, "0.2.0")
    (aw).mkdir(parents=True, exist_ok=True)
    (aw / "current-version").write_text("0.1.0\n", encoding="utf-8")
    assert _resolve(tmp_path).endswith("versions/0.1.0/bin/python")


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_resolver_tier2_prefers_last_known_good_over_newest(tmp_path):
    # No marker; last-known-good names the older slot. It must win over the
    # newest-slot guess -- the core #742 behavior.
    aw = tmp_path / ".agent-worktrees"
    _make_slot(tmp_path, "0.1.0")
    _make_slot(tmp_path, "0.2.0")
    aw.mkdir(parents=True, exist_ok=True)
    (aw / "last-known-good").write_text("0.1.0\n", encoding="utf-8")
    assert _resolve(tmp_path).endswith("versions/0.1.0/bin/python")


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_resolver_stale_marker_falls_to_last_known_good(tmp_path):
    # Marker names a slot that is gone; last-known-good (an installed slot) is
    # preferred over guessing the newest slot.
    aw = tmp_path / ".agent-worktrees"
    _make_slot(tmp_path, "0.1.0")
    _make_slot(tmp_path, "0.2.0")
    aw.mkdir(parents=True, exist_ok=True)
    (aw / "current-version").write_text("9.9.9\n", encoding="utf-8")
    (aw / "last-known-good").write_text("0.1.0\n", encoding="utf-8")
    assert _resolve(tmp_path).endswith("versions/0.1.0/bin/python")


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_resolver_tier3_newest_on_true_first_run(tmp_path):
    # No marker and no last-known-good -> newest installed slot.
    _make_slot(tmp_path, "0.1.0-dev9")
    _make_slot(tmp_path, "0.1.0-dev10")
    assert _resolve(tmp_path).endswith("versions/0.1.0-dev10/bin/python")
    _make_slot(tmp_path, "0.1.0")
    assert _resolve(tmp_path).endswith("versions/0.1.0/bin/python")


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh binstub")
def test_global_binstub_tier3_prefers_dev10_over_dev9(tmp_path):
    import os
    import subprocess

    runtime = tmp_path / ".agent-worktrees"
    runtime_bin = runtime / "bin"
    runtime_bin.mkdir(parents=True)
    shutil.copy2(_SCRIPTS / "resolve-runtime.sh", runtime_bin / "resolve-runtime.sh")
    for version in ("1.5.3-dev9", "1.5.3-dev10"):
        slot = runtime / "versions" / version
        command = slot / "bin" / "python"
        command.parent.mkdir(parents=True)
        command.write_text(
            f"#!/bin/sh\nprintf '%s' '{version}'\n", encoding="utf-8"
        )
        command.chmod(0o755)
        (slot / ".install-complete.json").write_text(
            json.dumps({
                "version": version,
                "completed_at": "2026-08-27T00:00:00Z",
                "pid": 1,
            }),
            encoding="utf-8",
        )
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)

    result = subprocess.run(
        ["sh", str(_BIN / "agent-worktrees"), "status"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "1.5.3-dev10"

    slot = runtime / "versions" / "1.5.3"
    command = slot / "bin" / "python"
    command.parent.mkdir(parents=True)
    command.write_text("#!/bin/sh\nprintf '%s' '1.5.3'\n", encoding="utf-8")
    command.chmod(0o755)
    (slot / ".install-complete.json").write_text(
        json.dumps({
            "version": "1.5.3",
            "completed_at": "2026-08-27T00:00:00Z",
            "pid": 1,
        }),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["sh", str(_BIN / "agent-worktrees"), "status"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "1.5.3"


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh binstub")
def test_posix_binstub_sanitizes_inherited_python_env(tmp_path):
    """#2411: a stray, mismatched-architecture PYTHONHOME (or any of the same
    class of inherited Python runtime variable) in the CALLER's ambient
    environment must not survive into the dispatched interpreter -- it can
    crash a resolved runtime-slot python that honors it."""
    import os
    import subprocess

    runtime = tmp_path / ".agent-worktrees"
    runtime_bin = runtime / "bin"
    runtime_bin.mkdir(parents=True)
    shutil.copy2(_SCRIPTS / "resolve-runtime.sh", runtime_bin / "resolve-runtime.sh")
    slot = runtime / "versions" / "1.5.3"
    command = slot / "bin" / "python"
    command.parent.mkdir(parents=True)
    command.write_text(
        "#!/bin/sh\n"
        "printf 'PYTHONHOME=%s\\n' \"${PYTHONHOME:-<unset>}\"\n"
        "printf 'PYTHONPATH=%s\\n' \"${PYTHONPATH:-<unset>}\"\n"
        "printf 'UV_INTERNAL__PYTHONHOME=%s\\n' \"${UV_INTERNAL__PYTHONHOME:-<unset>}\"\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    (slot / ".install-complete.json").write_text(
        json.dumps({
            "version": "1.5.3",
            "completed_at": "2026-08-27T00:00:00Z",
            "pid": 1,
        }),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PYTHONHOME"] = "/some/mismatched-architecture/python"
    env["PYTHONPATH"] = "/some/stray/site-packages"
    env["UV_INTERNAL__PYTHONHOME"] = "/some/uv-managed/python"

    result = subprocess.run(
        ["sh", str(_BIN / "agent-worktrees"), "status"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "PYTHONHOME=<unset>" in result.stdout
    assert "PYTHONPATH=<unset>" in result.stdout
    assert "UV_INTERNAL__PYTHONHOME=<unset>" in result.stdout


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is unavailable")
def test_windows_binstub_sanitizes_inherited_python_env():
    """#2411, Windows binstub parity: extracts and runs ONLY the sanitization
    prologue from agent-worktrees.ps1 (the binstub's real dispatch paths all
    end in `exit`, making a full end-to-end invocation unsuitable for
    asserting on post-run environment state) and proves it clears every
    inherited Python runtime variable this class of bug can involve."""
    import subprocess

    text = (_BIN / "agent-worktrees.ps1").read_text(encoding="utf-8")
    marker = "# Resolve the runtime slot python SOLELY"
    prologue_end = text.index(marker)
    prologue = text[:prologue_end]
    assert "Remove-Item" in prologue and "PYTHONHOME" in prologue, (
        "sanitization prologue not found before the resolver comment -- "
        "update this test's extraction marker if agent-worktrees.ps1 moved it"
    )
    script = (
        "$env:PYTHONHOME = 'C:\\mismatched\\architecture\\python'; "
        "$env:PYTHONPATH = 'C:\\stray\\site-packages'; "
        "$env:PYTHONEXECUTABLE = 'C:\\stray\\python.exe'; "
        "$env:VIRTUAL_ENV = 'C:\\stray\\venv'; "
        "$env:UV_INTERNAL__PYTHONHOME = 'C:\\uv\\managed\\python'; "
        "$env:__PYVENV_LAUNCHER__ = 'C:\\stray\\python.exe'; "
        f"{prologue}\n"
        "[pscustomobject]@{ "
        "PYTHONHOME = $env:PYTHONHOME; "
        "PYTHONPATH = $env:PYTHONPATH; "
        "PYTHONEXECUTABLE = $env:PYTHONEXECUTABLE; "
        "VIRTUAL_ENV = $env:VIRTUAL_ENV; "
        "UV_INTERNAL__PYTHONHOME = $env:UV_INTERNAL__PYTHONHOME; "
        "__PYVENV_LAUNCHER__ = $env:__PYVENV_LAUNCHER__ "
        "} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    resolved = json.loads(result.stdout.strip().splitlines()[-1])
    for name in (
        "PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE",
        "VIRTUAL_ENV", "UV_INTERNAL__PYTHONHOME", "__PYVENV_LAUNCHER__",
    ):
        assert resolved[name] is None, f"{name} was not sanitized"


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_resolver_empty_when_nothing_installed(tmp_path):
    assert _resolve(tmp_path) == ""
