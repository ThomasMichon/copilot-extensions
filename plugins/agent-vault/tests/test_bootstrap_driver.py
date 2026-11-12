"""Native bootstrap routing, opt-outs and skew without a real installer."""

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from agent_procutil import no_window_kwargs

from .test_bootstrap_check_reconcile_always_on import (
    _BASH, _clean_env, _make_fake_install, _make_fake_plugin,
)


def native_bootstrap(plugin, project, native_directory):
    script = plugin / "scripts" / ("bootstrap-check.ps1" if os.name == "nt" else "bootstrap-check.sh")
    if os.name != "nt":
        if _BASH is None:
            pytest.skip("native Bash is unavailable")
        return [_BASH, str(script)]
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    return [
        str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
        f"Set-Location -LiteralPath {quote(project)}; "
        f"[IO.Directory]::SetCurrentDirectory({quote(native_directory)}); "
        f"& {quote(script)}; exit $LASTEXITCODE",
    ]


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell interpreter floor")
@pytest.mark.parametrize("kind,minimum", [("wheel", 11), ("source", 10)])
def test_native_profile_readiness_selects_the_required_python_floor(tmp_path, kind, minimum):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    plugin = _make_fake_plugin(tmp_path, "agent-vault")
    installation = _make_fake_install(home, "agent-vault")
    (installation / "deploy-manifest.json").write_text(json.dumps({
        "source": {"version": "1.0.1"},
        "runtime_version": "1.0.1.profile." + "a" * 16 if kind == "wheel" else "1.0.1",
    }), encoding="utf-8")
    if kind == "source":
        (plugin / "runtime-source.json").write_text("{}", encoding="utf-8")
    (plugin / "scripts" / "versioned_runtime_native.ps1").write_text(
        "function Resolve-NativeBootstrapPython { param([int]$MinimumMinor)\n"
        " [IO.File]::WriteAllText($env:BOOTSTRAP_TEST_FLOOR, [string]$MinimumMinor)\n"
        " return 'fixture-python'\n}\n"
        "function Invoke-Hidden { param([string]$FilePath, [string[]]$ArgList)\n"
        " $global:LASTEXITCODE=0\n}\n",
        encoding="utf-8",
    )
    floor = tmp_path / "floor.txt"
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home), "BOOTSTRAP_TEST_FLOOR": str(floor),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    result = subprocess.run(
        native_bootstrap(plugin, project, home), env=env, cwd=project,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0 and json.loads(result.stdout) == {}, result.stderr
    assert floor.read_text() == str(minimum)
    assert not (project / "handoff").exists()


@pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="explicit isolated bootstrap performance measurement",
)
@pytest.mark.parametrize("phase", ["cold", "current-metadata"])
def test_native_bootstrap_performance_samples(tmp_path, capsys, phase):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    plugin = _make_fake_plugin(tmp_path, "agent-vault")
    if phase == "current-metadata":
        installation = _make_fake_install(home, "agent-vault")
        (installation / "deploy-manifest.json").write_text(
            '{"source":{"version":"1.0.1"}}', encoding="utf-8",
        )
        (installation / "current-version").write_text("1.0.1", encoding="utf-8")
        python = installation / "versions" / "1.0.1" / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        python.parent.mkdir(parents=True)
        python.write_text("current interpreter metadata fixture\n", encoding="utf-8")
        python.chmod(0o755)
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    samples, errors = [], []
    parent_cpu = time.process_time()
    try:
        for _ in range(2):
            started = time.perf_counter()
            result = subprocess.run(
                native_bootstrap(plugin, project, home), env=env, cwd=project,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding="utf-8", timeout=15, **no_window_kwargs(),
            )
            samples.append(time.perf_counter() - started)
            assert result.returncode == 0 and json.loads(result.stdout) == {}, result.stderr
            assert (project / "handoff").exists() is (phase == "cold")
    except BaseException as error:
        errors.append(type(error).__name__)
        raise
    finally:
        ordered = sorted(samples)
        report = {
            "phase": phase, "platform": sys.platform,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "source_sha": os.environ.get("GITHUB_SHA"),
            "vault_installations": 1, "configuration_contexts": 1,
            "expected": 2, "completed": len(samples), "samples_seconds": samples,
            "status": "measured" if len(samples) == 2 and not errors else "incomplete",
            "errors": errors, "quantile_method": "nearest-rank",
            "insufficient_tail_samples": True,
            "parent_cpu_seconds": time.process_time() - parent_cpu,
            "cpu_coverage": "test-parent-only", "child_cpu_seconds": None,
            "spawn_count": None, "spawn_coverage": "separate native work-count control",
            "cache_conditions": "fresh shell per sample; existing Python; OS caches uncontrolled",
        }
        for name, quantile in (("p50", .5), ("p95", .95), ("p99", .99), ("max", 1)):
            report[name + "_seconds"] = ordered[math.ceil(quantile * len(ordered)) - 1] if ordered else None
        with capsys.disabled():
            print("vault-bootstrap-measurement=" + json.dumps(report, sort_keys=True), flush=True)


@pytest.mark.parametrize("phase", ["cold", "drift"])
@pytest.mark.parametrize("condition", [
    "ordinary", "no-self", "pinned", "killswitch", "cell", "missing-driver",
    "old-driver", "missing-environment", "driver-refused", "nested", "stale-opt-in", "unicode",
    "no-ack", "foreign-ack",
])
def test_bootstrap_routes_or_defers_without_inline_install(
    tmp_path, phase, condition,
):
    label = "-caf\u00e9-\u6d4b\u8bd5" if condition == "unicode" else ""
    home = tmp_path / ("home" + label)
    home.mkdir()
    project = tmp_path / ("project" + label)
    project.mkdir()
    plugin = _make_fake_plugin(tmp_path, "agent-vault")
    installation = home / ".agent-vault"
    if phase == "drift":
        _make_fake_install(home, "agent-vault")
        lock = installation / "reconcile.lock"
        lock.write_text("424242", encoding="utf-8")
        os.utime(lock, (1_000_000_000, 1_000_000_000))
        old_lock = (lock.read_bytes(), lock.stat().st_mtime_ns)
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
        "AGENT_VAULT_INSTALLATION_ID": "foreign-id",
        "AGENT_VAULT_PIPE": "foreign-pipe",
    })
    suffix = "ps1" if os.name == "nt" else "sh"
    driver = plugin / "libs" / "update-driver" / "scripts" / ("first-use." + suffix)
    if condition == "no-self":
        env["AGENT_VAULT_NO_SELFPROVISION"] = "1"
    elif condition == "pinned":
        env["AGENT_VAULT_PINNED_CONTEXT"] = "{}"
    elif condition == "killswitch":
        (home / "killswitch.json").write_text('{"active":true}', encoding="utf-8")
    elif condition == "cell":
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(home / "cell.json")
    elif condition == "missing-driver":
        driver.unlink()
    elif condition == "old-driver":
        driver.write_text("exit 99\n", encoding="utf-8")
    elif condition == "missing-environment":
        (plugin / "scripts" / ("runtime-environment." + suffix)).unlink()
    elif condition == "driver-refused":
        env["BOOTSTRAP_TEST_DRIVER_EXIT_CODE"] = "37"
    elif condition == "no-ack":
        env["BOOTSTRAP_TEST_NO_ACK"] = "1"
    elif condition == "foreign-ack":
        env["BOOTSTRAP_TEST_FOREIGN_ACK"] = "1"
    elif condition == "nested":
        env["UPDATE_DRIVER_WITNESS"] = "{}"
    elif condition == "stale-opt-in":
        config = project / ".copilot-extensions" / "config.yaml"
        config.parent.mkdir()
        config.write_text("background_reconcile_agent-vault: false\n", encoding="utf-8")
    result = subprocess.run(
        native_bootstrap(plugin, project, home),
        env=env, cwd=project, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    assert json.loads(result.stdout) == {}, result.stdout + result.stderr
    handoff = project / "handoff"
    if condition in {"ordinary", "driver-refused", "stale-opt-in", "unicode", "no-ack", "foreign-ack"}:
        assert handoff.read_text(encoding="utf-8-sig").splitlines() == [
            str(home / ".copilot-extensions" / "update-driver"),
            str(plugin), str(installation), str(project),
            "stamp" if phase == "cold" else "provision", "<absent>", "<absent>",
        ]
        refused = condition in {"driver-refused", "no-ack", "foreign-ack"}
        assert result.returncode == (3 if refused else 0), result.stderr
        assert ("supervision launched" in result.stderr) == (not refused)
        if condition == "unicode":
            acknowledgement = next(
                line.partition("driver launch acknowledgement: ")[2]
                for line in result.stderr.splitlines() if "driver launch acknowledgement: " in line
            )
            if os.name == "nt":
                assert json.loads(acknowledgement)["project"] == str(project)
            else:
                assert str(home) in acknowledgement
    else:
        assert not handoff.exists()
        refused = condition in {"missing-driver", "old-driver", "missing-environment", "nested"}
        assert result.returncode == (3 if refused else 0), result.stderr
    assert not (plugin / "scripts" / "inline-entered").exists()
    assert not (home / ".copilot-extensions" / "update-driver").exists()
    if phase == "drift":
        assert (lock.read_bytes(), lock.stat().st_mtime_ns) == old_lock
    else:
        assert not installation.exists()


def test_healthy_bootstrap_never_requires_driver_or_environment_helper(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    plugin = _make_fake_plugin(tmp_path, "agent-vault")
    installation = _make_fake_install(home, "agent-vault")
    manifest = installation / "deploy-manifest.json"
    manifest.write_text('{"source":{"version":"1.0.1"}}', encoding="utf-8")
    (installation / "current-version").write_text("1.0.1", encoding="utf-8")
    python = installation / "versions" / "1.0.1" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    python.parent.mkdir(parents=True)
    python.write_text("healthy interpreter fixture\n", encoding="utf-8")
    python.chmod(0o755)
    shutil.rmtree(plugin / "libs")
    for suffix in ("ps1", "sh"):
        (plugin / "scripts" / ("runtime-environment." + suffix)).unlink()
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    result = subprocess.run(
        native_bootstrap(plugin, project, home), env=env, cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0 and json.loads(result.stdout) == {}, result.stdout + result.stderr
    assert not result.stderr and not (project / "handoff").exists()
    assert {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before


def test_shell_profile_inspection_selects_supported_existing_python(tmp_path):
    bash = _BASH
    if not bash and os.name == "nt" and shutil.which("git"):
        candidate = Path(shutil.which("git")).parents[1] / "bin" / "bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    if not bash:
        pytest.skip("native Bash is unavailable")
    script = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap-check.sh"
    text = script.read_text(encoding="utf-8")
    start = text.index('    profile_python=""')
    end = text.index("    profile_code=0", start)
    probe = tmp_path / "qualified-python.sh"
    probe.write_text(
        "set -euo pipefail\nminimum_minor=11\n"
        "python3() { return 1; }\n"
        "python() { return 1; }\n"
        "python3.13() { return 1; }\n"
        "python3.12() { return 1; }\n"
        'python3.11() { "$PROFILE_TEST_PYTHON" "$@"; }\n'
        + text[start:end]
        + '[[ "$profile_python" == python3.11 ]]\n'
        + '"$profile_python" -I -S -B -c "import tomllib"\n',
    )
    env = os.environ.copy()
    env["PROFILE_TEST_PYTHON"] = Path(sys._base_executable).as_posix()
    result = subprocess.run(
        [bash, str(probe)], env=env, capture_output=True, text=True,
        timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("condition", ["current", "base-runtime", "malformed-desire", "profile-directory"])
def test_native_bootstrap_preserves_or_reconciles_selected_ssh_profile(tmp_path, condition):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    plugin = _make_fake_plugin(tmp_path, "agent-vault")
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for name in ("runtime_profile.py", "runtime_source_target.py", "versioned_runtime_source.py",
                 "versioned_runtime_native.ps1"):
        shutil.copyfile(scripts / name, plugin / "scripts" / name)
    (plugin / "pyproject.toml").write_text('[project]\nname="agent-vault"\nversion="1.0.1"\n')
    installation = _make_fake_install(home, "agent-vault")
    version = "1.0.1+base" if condition == "base-runtime" else "1.0.1+ssh"
    (installation / "deploy-manifest.json").write_text(json.dumps({
        "source": {"version": "1.0.1"}, "runtime_version": version,
        "runtime_profile": "base" if condition == "base-runtime" else "ssh",
    }))
    if condition == "profile-directory":
        (installation / "runtime-profile.json").mkdir()
    else:
        (installation / "runtime-profile.json").write_text(
            "{" if condition == "malformed-desire" else json.dumps({
                "schema": "agent-vault.runtime-profile", "version": 1, "profile": "ssh",
            }),
        )
    (installation / "current-version").write_text(version)
    python = installation / "versions" / version / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    python.parent.mkdir(parents=True)
    python.write_bytes(b"interpreter metadata fixture")
    python.chmod(0o755)
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    result = subprocess.run(
        native_bootstrap(plugin, project, home), env=env, cwd=project,
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert json.loads(result.stdout) == {}, result.stdout + result.stderr
    assert result.returncode == (3 if condition in {"malformed-desire", "profile-directory"} else 0), result.stderr
    assert (project / "handoff").exists() is (condition == "base-runtime")
    assert {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("profile", [None, "ssh"])
@pytest.mark.parametrize("condition", ["current", "older-source", "receipt-drift", "marker-drift"])
def test_native_bootstrap_uses_incoming_source_with_or_without_desire(
    tmp_path, monkeypatch, profile, condition,
):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    plugin = _make_fake_plugin(tmp_path, "agent-vault")
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for path in [scripts / "runtime_profile.py", scripts / "runtime_source_target.py",
                 *scripts.glob("versioned_runtime*.py")]:
        shutil.copyfile(path, plugin / "scripts" / path.name)
    (plugin / "plugin.json").write_text('{"name":"agent-vault","version":"1.0.1"}')
    (plugin / "pyproject.toml").write_text('[project]\nname="agent-vault"\nversion="1.0.1"\n')
    (plugin / "scripts" / "installer_engine.py").write_text(
        'raise AssertionError("bootstrap must not import the constructor")\n',
    )
    monkeypatch.syspath_prepend(str(scripts))
    import versioned_runtime_source as source

    digest = source.assembly_hash(plugin)
    identity = source.SourceIdentity(
        "agent-vault", "1.0.1", "a" * 40, "b" * 40, digest,
        source.runtime_version("1.0.1", "a" * 40, digest),
    )
    (plugin / source.SOURCE_FILE).write_text(json.dumps(identity.document()))
    installation = _make_fake_install(home, "agent-vault")
    if profile:
        (installation / "runtime-profile.json").write_text(json.dumps({
            "schema": "agent-vault.runtime-profile", "version": 1, "profile": profile,
        }))
    version = source.runtime_version("1.0.1+ssh", "a" * 40, digest) if profile else identity.runtime_version
    if condition == "older-source":
        version = source.runtime_version("1.0.1+ssh" if profile else "1.0.1", "c" * 40, digest)
    receipt = identity.document()
    if condition in {"older-source", "receipt-drift"}:
        receipt["source_tree"] = "d" * 40
    (installation / "deploy-manifest.json").write_text(json.dumps({
        "source": {"version": "1.0.1"}, "runtime_version": version,
        "runtime_profile": profile or "base", "runtime_source": receipt,
    }))
    (installation / "current-version").write_text("1.0.1" if condition == "marker-drift" else version)
    python = installation / "versions" / version / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    python.parent.mkdir(parents=True)
    python.write_bytes(b"interpreter metadata fixture")
    python.chmod(0o755)
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    result = subprocess.run(
        native_bootstrap(plugin, project, home), env=env, cwd=project,
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0 and json.loads(result.stdout) == {}, result.stdout + result.stderr
    assert (project / "handoff").exists() is (condition != "current")
    assert {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("failure", [
    "empty-owner", "wrong-owner", "malformed-deployment", "nonstring-version", "missing-version",
])
def test_invalid_bootstrap_metadata_refuses_visibly_without_driver_handoff(tmp_path, failure):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    plugin = _make_fake_plugin(tmp_path, "agent-vault")
    installation = _make_fake_install(home, "agent-vault")
    if failure in {"empty-owner", "wrong-owner"}:
        (plugin / "plugin.json").write_text(
            json.dumps({"name": "" if failure == "empty-owner" else "other-plugin"}),
            encoding="utf-8",
        )
    elif failure == "malformed-deployment":
        (installation / "deploy-manifest.json").write_text("{", encoding="utf-8")
    elif failure == "nonstring-version":
        (installation / "deploy-manifest.json").write_text('{"source":{"version":true}}', encoding="utf-8")
    else:
        (plugin / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    result = subprocess.run(
        native_bootstrap(plugin, project, home), env=env, cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 3 and json.loads(result.stdout) == {}, result.stdout + result.stderr
    assert "Runtime publication refused" in result.stderr and not (project / "handoff").exists()
    assert {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX metadata-reader boundary")
@pytest.mark.parametrize("killswitch", [False, True])
def test_bootstrap_without_metadata_python_remains_read_only(tmp_path, killswitch):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    plugin = _make_fake_plugin(tmp_path, "agent-vault")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "dirname"):
        executable = shutil.which(name)
        if executable is None:
            pytest.skip(f"native tool is unavailable: {name}")
        (tools / name).symlink_to(executable)
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home), "PATH": str(tools),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    if killswitch:
        (home / "killswitch.json").write_text('{"active":true}', encoding="utf-8")
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    result = subprocess.run(
        native_bootstrap(plugin, project, home), env=env, cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0 and json.loads(result.stdout) == {}, result.stdout + result.stderr
    assert not (project / "handoff").exists()
    assert {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
