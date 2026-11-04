import json

import pytest
import yaml
from configuration_layers import ConfigurationError

from agent_mcp import config, configuration


def setup(tmp_path, monkeypatch, binding="bound"):
    base, knowledge, local, machine = (tmp_path / part for part in ("base", "knowledge", "local", "machine"))
    for root in (base, knowledge, local, machine):
        root.mkdir()
    selected = tmp_path / "selected.mcp.yaml"
    selected.write_text("server:\n  type: http\n  url: https://example.invalid/mcp\ntimeout: 10\n", encoding="utf-8")
    context = {
        "repository_id": "repo-qualified", "execution_machine": "logical",
        "installation_cell": "cell", "binding": binding, "base_root": str(base),
    }
    if binding == "bound":
        context["knowledge_root"] = str(knowledge)
    report = {
        "schema": "agent-worktrees.configuration", "version": 1, "context": context,
        "repository_machine_root": str(local),
    }
    monkeypatch.setattr(configuration, "owner_report", lambda root: report)
    monkeypatch.setattr(config, "OVERRIDES_DIR", machine)
    return selected, base, knowledge, local, machine, report


def write(root, values):
    path = root / ".copilot-extensions" / "agent-mcp" / "bridges" / "selected.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(values), encoding="utf-8")


def test_native_mcp_loader_all_tiers_and_protected_source(tmp_path, monkeypatch):
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch)
    write(base, {"timeout": 20, "retries": 2})
    write(knowledge, {"timeout": 30, "retries": 3})
    (machine / "selected.yaml").write_text("timeout: 40\nheaders:\n  X-Native: retained\n", encoding="utf-8")
    write(local, {"timeout": 50})
    result = config.load_config(str(selected))
    assert (result.timeout, result.retries) == (50, 3)
    assert result.server.url == "https://example.invalid/mcp"
    assert result.headers == {"X-Native": "retained"}
    assert result.source_path == selected
    assert result.configuration_provenance["/timeout"]["installation_cell"] == "cell"
    assert result.configuration_provenance["/timeout"]["source"]["root"] == str(local)


def test_native_kn_then_machine_precedence(tmp_path, monkeypatch):
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch)
    write(knowledge, {"timeout": 30})
    assert config.load_config(str(selected)).timeout == 30
    (machine / "selected.json").write_text(json.dumps({"timeout": 40}), encoding="utf-8")
    assert config.load_config(str(selected)).timeout == 40


def test_native_unbound_default_and_explicit_path(tmp_path, monkeypatch):
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch, "unbound")
    write(knowledge, {"timeout": 999})
    assert config.load_config(str(selected)).timeout == 10
    report["context"]["binding"] = "unresolved"
    with pytest.raises(ConfigurationError, match="unresolved"):
        config.load_config(str(selected))


@pytest.mark.parametrize("protected", ["auth", "server", "headers", "routing", "decorators", "tools"])
def test_portable_timing_cannot_change_native_protected_schema(tmp_path, monkeypatch, protected):
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch)
    write(knowledge, {protected: {}})
    with pytest.raises(ValueError, match="cannot change"):
        config.load_config(str(selected))


def test_native_environment_idle_selector_preserved(tmp_path, monkeypatch):
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_MCP_BRIDGE_IDLE_TIMEOUT", "17")
    result = config.load_config(str(selected))
    assert result.idle_timeout == 17
    assert "/idle_timeout" not in result.configuration_provenance
    assert result.configuration_selectors == {"idle_timeout": "AGENT_MCP_BRIDGE_IDLE_TIMEOUT"}
    write(knowledge, {"idle_timeout": 18})
    assert config.load_config(str(selected)).idle_timeout == 18


def test_actual_absent_owner_preserves_unbound_explicit_bridge_without_bootstrap(tmp_path, monkeypatch):
    import configuration_context

    for name in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "OVERRIDES_DIR", tmp_path / "absent-overrides")
    selected = tmp_path / "standalone.yaml"
    selected.write_text("server:\n  url: https://example.invalid/mcp\n", encoding="utf-8")

    def no_process(*args, **kwargs):
        pytest.fail("native config loading must not install or launch an optional owner")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    result = config.load_config(str(selected))
    assert (result.timeout, result.retries) == (30.0, 1)
    assert sorted(item.name for item in tmp_path.iterdir()) == ["standalone.yaml"]


def test_native_unready_legacy_owner_preserves_bridge_defaults(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    import configuration_context

    home = tmp_path / "home"
    scripts = home / ".agent-mcp" / "scripts"
    scripts.mkdir(parents=True)
    for source in (Path(configuration.__file__).parents[2] / "scripts").glob("versioned_runtime*.py"):
        shutil.copy2(source, scripts / source.name)
    peer = home / ".agent-worktrees"
    fake_python = peer / "versions" / "1.0.0" / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("not a completed runtime", encoding="utf-8")
    (peer / "current-version").write_text("1.0.0", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in ("AGENT_MCP_HOME", "AGENT_RT_ROOT", "COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "OVERRIDES_DIR", tmp_path / "absent-overrides")
    selected = tmp_path / "bridge.yaml"
    selected.write_text("server:\n  url: https://example.invalid/mcp\n", encoding="utf-8")

    def no_process(*args, **kwargs):
        pytest.fail("an unready optional owner must not bootstrap")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    assert config.load_config(str(selected)).timeout == 30.0
    assert (scripts / "versioned_runtime.py").is_file()
    assert configuration._legacy_report(tmp_path) is None


@pytest.mark.parametrize("implicit", [False, True])
@pytest.mark.parametrize("matching_cell", [False, True])
def test_real_loader_checks_delivered_cell_against_actual_owner(tmp_path, monkeypatch, implicit, matching_cell):
    real_report = configuration.owner_report
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    write(knowledge, {"timeout": 31})
    cell = tmp_path / "marketplaces" / "current-cell"
    own_root = cell / "plugins" / "agent-mcp"
    own_root.mkdir(parents=True)
    report["context"]["installation_cell"] = str(cell if matching_cell else tmp_path / "foreign-cell")
    calls = []

    def validate(owner, root, raw):
        calls.append((owner, root, raw))
        assert root == own_root
        assert raw == str(own_root / "install.json")
        return {"cellRoot": str(cell), "pluginRoot": str(own_root)}

    monkeypatch.setattr(configuration._peer_launch, "validate_owner", validate)
    monkeypatch.setenv("AGENT_RT_ROOT", str(own_root))
    if implicit:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    else:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own_root / "install.json"))
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    monkeypatch.chdir(base)
    if matching_cell:
        assert config.load_config(str(selected)).timeout == 31
    else:
        with pytest.raises(ConfigurationError, match="another installation cell"):
            config.load_config(str(selected))
    assert calls


@pytest.mark.parametrize("code,diagnostic", [
    (2, "unrecognized arguments: --configuration"),
    (2, "unknown option: --configuration"),
    (1, "binding refused"), (3, "binding unresolved"), (126, "owner refused"),
    (2, "unrecognized arguments: --unrelated"), (0, "malformed"),
])
def test_optional_older_peer_preserves_native_bridge_or_fails_real_refusal(tmp_path, monkeypatch, code, diagnostic):
    import subprocess
    real_report = configuration.owner_report
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    cell = tmp_path / "marketplaces" / "current-cell"
    own = cell / "plugins" / "agent-mcp"
    (cell / "plugins" / "agent-worktrees").mkdir(parents=True)
    monkeypatch.setenv("AGENT_RT_ROOT", str(own))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own / "install.json"))
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    monkeypatch.setattr(configuration._peer_launch, "validate_owner", lambda *args:
                        {"cellRoot": str(cell), "pluginRoot": str(own)})
    monkeypatch.setattr(configuration._peer_launch, "launch_prefix", lambda *args: ["qualified-peer"])
    calls = []

    def run(argv, **kwargs):
        assert argv == ["qualified-peer", "state-root", "--configuration", "--json"]
        calls.append(argv)
        return subprocess.CompletedProcess(argv, code, b"{bad-json", diagnostic.encode())

    monkeypatch.setattr(subprocess, "run", run)
    if code == 2 and diagnostic.endswith("--configuration"):
        assert config.load_config(str(selected)).timeout == 10
    else:
        with pytest.raises(ConfigurationError):
            config.load_config(str(selected))
    assert len(calls) == 1


def test_unqualified_delivery_cannot_be_admitted_by_optional_absence(tmp_path, monkeypatch):
    real_report = configuration.owner_report
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_RT_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    monkeypatch.setattr(configuration, "_legacy_report", lambda root: None)
    monkeypatch.chdir(base)
    with pytest.raises(ConfigurationError, match="matching qualified owner"):
        config.load_config(str(selected))


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("delivered", [False, True])
def test_slot_retained_resolver_qualifies_legacy_owner(tmp_path, monkeypatch, nested, delivered):
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    real_report = configuration.owner_report
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    home = tmp_path / "home"
    own = home / ".agent-mcp"
    slot = own / "versions" / "1.0.0"
    prefix = slot / ".venv" if nested else slot
    module = prefix / "lib" / "agent_mcp" / "configuration.py"
    module.parent.mkdir(parents=True)
    module.touch()
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    scripts = Path(configuration.__file__).parents[2] / "scripts"
    for source in scripts.glob("versioned_runtime*.py"):
        shutil.copy2(source, bootstrap / source.name)
    runtime = configuration._peer_launch._load_primitive(
        bootstrap / "versioned_runtime.py", "_test_mcp_retained_runtime",
    )
    peer = home / ".agent-worktrees"
    python = peer / "versions" / "1.0.0" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    runtime.mark_complete(peer, "1.0.0")
    (peer / "current-version").write_text("1.0.0", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(configuration, "__file__", str(module))
    monkeypatch.chdir(base)
    for key in ("AGENT_MCP_HOME", "AGENT_RT_ROOT", "COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(key, raising=False)
    if delivered:
        monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    monkeypatch.setenv("AGENT_WORKTREES_ROOT", "must-not-select-this")
    monkeypatch.setenv("GH_TOKEN", "must-not-reach-peer")
    calls = []

    def run(argv, **kwargs):
        assert argv == [
            str(python), "-I", "-X", "utf8", "-m", "agent_worktrees",
            "state-root", "--configuration", "--json",
        ]
        assert kwargs["cwd"] == base
        assert not set(kwargs["env"]) & {
            "AGENT_CONFIGURATION_CONTEXT", "AGENT_RT_ROOT", "AGENT_WORKTREES_ROOT", "GH_TOKEN",
        }
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(report).encode(), b"")

    monkeypatch.setattr(subprocess, "run", run)
    write(knowledge, {"timeout": 37})
    assert not (own / "scripts").exists()
    assert config.load_config(str(selected)).timeout == 37
    assert len(calls) == 1

    if delivered:
        changed = json.loads(json.dumps(report))
        changed["context"]["repository_id"] = "another-repository"
        monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(changed))
        with pytest.raises(ConfigurationError, match="matching qualified owner"):
            config.load_config(str(selected))

    monkeypatch.setattr(configuration, "__file__", str(tmp_path / "unrelated" / "configuration.py"))
    assert configuration._legacy_report(base) is None
    monkeypatch.setattr(configuration, "__file__", str(module))
    runtime.marker_path(peer, "1.0.0").unlink()
    assert configuration._legacy_report(base) is None
    runtime.mark_complete(peer, "1.0.0")
    (peer / ".installation-ownership.json").write_text("{}", encoding="utf-8")
    with pytest.raises(configuration._peer_launch.ContextRefused, match="installation-cell ownership"):
        config.load_config(str(selected))


def test_fresh_slot_loader_reads_qualified_peer_in_real_subprocess(tmp_path):
    import os
    import shutil
    import subprocess
    import venv
    from pathlib import Path

    import configuration_context
    import configuration_layers

    home = tmp_path / "home"
    base = tmp_path / "repository"
    knowledge = tmp_path / "knowledge"
    base.mkdir()
    knowledge.mkdir()
    write(knowledge, {"timeout": 43})
    selected = tmp_path / "selected.yaml"
    selected.write_text("server:\n  url: https://example.invalid/mcp\n", encoding="utf-8")
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "qualified-repository", "execution_machine": "logical",
            "installation_cell": "legacy", "binding": "bound",
            "base_root": str(base), "knowledge_root": str(knowledge),
        },
    }

    def environment(root):
        prefix = root / "versions" / "1.0.0"
        venv.EnvBuilder(with_pip=False).create(prefix)
        python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        result = subprocess.run(
            [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return prefix, python, Path(result.stdout.strip())

    own = home / ".agent-mcp"
    slot, python, packages = environment(own)
    _, peer_python, peer_packages = environment(home / ".agent-worktrees")
    import agent_mcp

    for package in (agent_mcp, yaml, configuration_context, configuration_layers):
        source = Path(package.__file__).parent
        shutil.copytree(source, packages / source.name, ignore=shutil.ignore_patterns("__pycache__"))
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    scripts = Path(configuration.__file__).parents[2] / "scripts"
    for source in scripts.glob("versioned_runtime*.py"):
        shutil.copy2(source, bootstrap / source.name)
    runtime = configuration._peer_launch._load_primitive(
        bootstrap / "versioned_runtime.py", "_test_mcp_subprocess_runtime",
    )
    peer = home / ".agent-worktrees"
    runtime.mark_complete(peer, "1.0.0")
    (peer / "current-version").write_text("1.0.0", encoding="utf-8")
    peer_module = peer_packages / "agent_worktrees"
    peer_module.mkdir()
    (peer_module / "__init__.py").touch()
    (peer_module / "__main__.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "assert sys.argv[1:] == ['state-root', '--configuration', '--json']\n"
        "assert not set(os.environ) & {'AGENT_CONFIGURATION_CONTEXT', 'AGENT_RT_ROOT', 'GH_TOKEN'}\n"
        "print(Path(__file__).with_name('report.json').read_text(encoding='utf-8'))\n",
        encoding="utf-8",
    )
    (peer_module / "report.json").write_text(json.dumps(report), encoding="utf-8")
    inherited = configuration._peer_launch.legacy_peer_environment(dict(os.environ))
    inherited.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "AGENT_CONFIGURATION_CONTEXT": json.dumps(report),
    })
    assert peer_python.is_file()
    assert not (own / "scripts").exists()
    result = subprocess.run(
        [str(python), "-I", "-c",
         "import sys; from agent_mcp.config import load_config; print(load_config(sys.argv[1]).timeout)",
         str(selected)],
        env=inherited, cwd=base, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stderr
    assert float(result.stdout.strip()) == 43
