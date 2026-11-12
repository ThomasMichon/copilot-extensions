"""Driver setup/status contract (agent-ssh setup / agent-ssh status)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_ssh import __main__ as cli
from agent_ssh import driver as d

DRIVER = d.Driver("example-provider", "example")


def _result(state="ready", requests=None, **extra):
    doc = {
        "schema": "agent-ssh.driver-result",
        "version": 1,
        "state": state,
        "detail": f"{state} detail",
        "auth_requests": requests or [],
    }
    doc.update(extra)
    return doc


def _auth(key="login", command=None):
    return {
        "key": key,
        "driver": DRIVER.label,
        "machine": "workstation",
        "reason": "provider sign-in required",
        "command": command or ["example-cli", "login"],
    }


def _write_module(root: Path, *, setup=True, status=True, module="example") -> Path:
    mod_dir = root / "transports" / module
    (mod_dir / "scripts").mkdir(parents=True)
    for name in ("setup.sh", "setup.ps1", "status.sh", "status.ps1"):
        (mod_dir / "scripts" / name).write_text("", encoding="utf-8")
    lines = [
        f"module: {module}",
        "kind: transport",
        "produces: {ssh_profile: true, keyed_by: machine-name}",
        "inbound_ports: none",
        "capabilities:",
        f"  setup: {str(setup).lower()}",
        f"  status: {str(status).lower()}",
        "entrypoints:",
        "  setup: {posix: scripts/setup.sh, windows: scripts/setup.ps1}",
        "  status: {posix: scripts/status.sh, windows: scripts/status.ps1}",
    ]
    path = mod_dir / "module.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_machines(root: Path, driver_block="      driver: {plugin: example-provider, module: example}\n") -> Path:
    path = root / "machines.yaml"
    path.write_text(
        "machines:\n"
        "  workstation:\n"
        "    hostname: workstation\n"
        "    ssh:\n"
        f"{driver_block}"
        "  laptop:\n"
        "    hostname: laptop\n",
        encoding="utf-8",
    )
    return path


class FakeRunner:
    """Scripted entrypoint runs; records argv and kwargs."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        doc, code = self.outputs.pop(0)
        stdout = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=b"")


# --- declaration ------------------------------------------------------------


def test_parse_driver_valid_and_absent():
    assert d.parse_driver("w", {"ssh": {"driver": {"plugin": "example-provider", "module": "example"}}}) == DRIVER
    assert d.parse_driver("w", {"hostname": "w"}) is None
    assert d.parse_driver("w", {"ssh": {"ready": True}}) is None


@pytest.mark.parametrize(
    "raw",
    [
        "example",
        {"plugin": "example-provider"},
        {"plugin": "", "module": "example"},
        {"plugin": "Example", "module": "example"},
        {"plugin": "example-provider", "module": "../x"},
        {"plugin": "example-provider", "module": "example", "extra": 1},
        {"plugin": 1, "module": "example"},
    ],
)
def test_parse_driver_rejects_invalid(raw):
    with pytest.raises(d.DriverError):
        d.parse_driver("w", {"ssh": {"driver": raw}})


# --- local machine selection -----------------------------------------------


MACHINES = {
    "workstation": {"hostname": "workstation.example.com"},
    "laptop": {},
    "twin": {"hostname": "twin"},
    "twin-copy": {"hostname": "twin"},
}


def test_select_local_machine_matches_hostname_and_key_fallback():
    assert d.select_local_machine(MACHINES, hostname="workstation") == "workstation"
    assert d.select_local_machine(MACHINES, hostname="LAPTOP") == "laptop"


def test_select_local_machine_none_and_ambiguous():
    with pytest.raises(d.DriverError, match="no machines.yaml entry"):
        d.select_local_machine(MACHINES, hostname="elsewhere")
    with pytest.raises(d.DriverError, match="multiple"):
        d.select_local_machine(MACHINES, hostname="twin")


def test_select_local_machine_override():
    assert d.select_local_machine(MACHINES, "twin", hostname="elsewhere") == "twin"
    with pytest.raises(d.DriverError):
        d.select_local_machine(MACHINES, "missing")


# --- module discovery -------------------------------------------------------


def _repo_with_directory_marketplace(tmp_path: Path, *, manifest=True, enabled=True) -> Path:
    repo = tmp_path / "repo"
    (repo / ".github" / "copilot").mkdir(parents=True)
    (repo / ".github" / "copilot" / "settings.json").write_text(
        json.dumps({"extraKnownMarketplaces": {
            "local": {"source": {"source": "directory", "path": "./plugins-src"}},
            "remote": {"source": {"source": "github", "repo": "example/remote"}},
        }, "enabledPlugins": {"example-provider@local": enabled}}),
        encoding="utf-8",
    )
    market = repo / "plugins-src"
    if manifest:
        (market / ".claude-plugin").mkdir(parents=True)
        (market / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"plugins": [{"name": "example-provider", "source": "./providers/example"}]}),
            encoding="utf-8",
        )
        _write_module(market / "providers" / "example")
    else:
        _write_module(market / "example-provider")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _copilot(home: Path, *, trusted=(), installed=(), user_enabled=None) -> Path:
    """Write Copilot config: trusted folders, installed inventory and user activation."""
    copilot = home / ".copilot"
    copilot.mkdir(parents=True, exist_ok=True)
    inventory = []
    for market, enabled in installed:
        _write_module(copilot / "installed-plugins" / market / "example-provider")
        inventory.append({"name": "example-provider", "marketplace": market, "enabled": enabled})
    (copilot / "config.json").write_text(
        "// managed by copilot\n"
        + json.dumps({"trustedFolders": [str(f) for f in trusted], "installedPlugins": inventory}),
        encoding="utf-8",
    )
    if user_enabled is not None:
        (copilot / "settings.json").write_text(json.dumps({"enabledPlugins": user_enabled}), encoding="utf-8")
    return home


@pytest.mark.parametrize("manifest", [True, False])
def test_find_module_directory_marketplace(tmp_path, manifest):
    repo = _repo_with_directory_marketplace(tmp_path, manifest=manifest)
    home = _copilot(tmp_path / "home", trusted=[repo])
    found = d.find_module(DRIVER, repo, home=home, payload_root=lambda: None)
    sub = "providers/example" if manifest else "example-provider"
    assert found == (repo / "plugins-src" / sub / "transports" / "example" / "module.yaml").resolve()


def test_directory_marketplace_wins_over_installed_copy(tmp_path):
    repo = _repo_with_directory_marketplace(tmp_path)
    home = _copilot(tmp_path / "home", trusted=[tmp_path], installed=[("local", True)])
    found = d.find_module(DRIVER, repo, home=home, payload_root=lambda: None)
    assert "plugins-src" in str(found)


def test_untrusted_checkout_cannot_override_installed_provider(tmp_path):
    repo = _repo_with_directory_marketplace(tmp_path)
    home = _copilot(tmp_path / "home", trusted=[tmp_path / "elsewhere"], installed=[("local", True)])
    installed = home / ".copilot" / "installed-plugins" / "local" / "example-provider"
    found = d.find_module(DRIVER, repo, home=home, payload_root=lambda: None)
    assert found == (installed / "transports" / "example" / "module.yaml").resolve()


def test_untrusted_checkout_provider_is_not_discovered(tmp_path):
    repo = _repo_with_directory_marketplace(tmp_path)
    with pytest.raises(d.DriverError, match="not a trusted Copilot folder"):
        d.find_module(DRIVER, repo, home=tmp_path / "home", payload_root=lambda: None)


def test_disabled_repository_provider_is_not_discovered(tmp_path):
    repo = _repo_with_directory_marketplace(tmp_path, enabled=False)
    home = _copilot(tmp_path / "home", trusted=[repo])
    with pytest.raises(d.DriverError, match="disabled plugin.*example-provider@local"):
        d.find_module(DRIVER, repo, home=home, payload_root=lambda: None)


@pytest.mark.parametrize(
    "inventory_enabled, user_enabled",
    [(False, None), (True, {"example-provider@market-a": False}), (None, None)],
)
def test_disabled_or_uninventoried_installed_copy_is_not_executed(tmp_path, inventory_enabled, user_enabled):
    cwd = tmp_path / "plain"
    cwd.mkdir()
    installed = [] if inventory_enabled is None else [("market-a", inventory_enabled)]
    home = _copilot(tmp_path / "home", installed=installed, user_enabled=user_enabled)
    if inventory_enabled is None:
        _write_module(home / ".copilot" / "installed-plugins" / "market-a" / "example-provider")
    with pytest.raises(d.DriverError, match="disabled"):
        d.find_module(DRIVER, cwd, home=home, payload_root=lambda: None)


def test_user_activation_enables_inventory_disabled_copy(tmp_path):
    cwd = tmp_path / "plain"
    cwd.mkdir()
    home = _copilot(
        tmp_path / "home", installed=[("market-a", False)], user_enabled={"example-provider@market-a": True},
    )
    assert "market-a" in str(d.find_module(DRIVER, cwd, home=home, payload_root=lambda: None))


def test_find_module_installed_plugins_and_ambiguity(tmp_path):
    cwd = tmp_path / "plain"
    cwd.mkdir()
    home = _copilot(tmp_path / "home", installed=[("market-a", True)])
    installed = home / ".copilot" / "installed-plugins"
    found = d.find_module(DRIVER, cwd, home=home, payload_root=lambda: None)
    assert found == (installed / "market-a" / "example-provider" / "transports" / "example" / "module.yaml").resolve()
    home = _copilot(tmp_path / "home2", installed=[("market-a", True), ("market-b", True)])
    with pytest.raises(d.DriverError, match="ambiguous") as err:
        d.find_module(DRIVER, cwd, home=home, payload_root=lambda: None)
    assert "market-a" in str(err.value) and "market-b" in str(err.value)


def test_find_module_agent_ssh_payload(tmp_path):
    payload = tmp_path / "payload"
    _write_module(payload, module="example")
    found = d.find_module(
        d.Driver("agent-ssh", "example"), tmp_path, home=tmp_path / "home", payload_root=lambda: payload,
    )
    assert found.parent.name == "example"


def test_find_module_missing(tmp_path):
    with pytest.raises(d.DriverError, match="not found"):
        d.find_module(DRIVER, tmp_path, home=tmp_path / "home", payload_root=lambda: None)


def test_entrypoint_must_stay_inside_module(tmp_path):
    path = _write_module(tmp_path)
    module = {"entrypoints": {"setup": {"posix": "../../escape.sh", "windows": "../../escape.ps1"}}}
    with pytest.raises(d.DriverError, match="inside"):
        d.entrypoint_script(path, module, "setup", windows=False)


# --- result parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    "state,code", [("ready", 0), ("planned", 0), ("applied", 0), ("failed", 1)],
)
def test_parse_result_valid_states(state, code):
    result = d.parse_result(json.dumps(_result(state)), code, driver=DRIVER, machine="workstation")
    assert result["state"] == state
    assert d.exit_code(result) == code


def test_parse_result_operator_action():
    doc = _result("operator-action-required", [_auth()])
    result = d.parse_result(json.dumps(doc), 2, driver=DRIVER, machine="workstation")
    assert result["auth_requests"] == [_auth()]
    assert d.exit_code(result) == 2


def test_parse_result_auth_requests_optional_when_empty():
    doc = _result("ready")
    del doc["auth_requests"]
    assert d.parse_result(json.dumps(doc), 0, driver=DRIVER, machine="workstation")["state"] == "ready"


@pytest.mark.parametrize(
    "stdout,code",
    [
        (b"not json", 0),
        (json.dumps(["list"]).encode(), 0),
        (json.dumps(_result(schema="other")).encode(), 0),
        (json.dumps(_result(version=2)).encode(), 0),
        (json.dumps(_result(version=True)).encode(), 0),
        (json.dumps(_result(state="unknown")).encode(), 0),
        (json.dumps(_result(state=[])).encode(), 0),
        (json.dumps(_result(state={})).encode(), 0),
        (json.dumps(_result(extra=1)).encode(), 0),
        (json.dumps(_result("ready")).encode(), 2),
        (json.dumps(_result("operator-action-required")).encode(), 2),
        (json.dumps(_result("ready", [_auth()])).encode(), 0),
        (json.dumps(_result("operator-action-required", [_auth(command="example-cli login")])).encode(), 2),
        (json.dumps(_result("operator-action-required", [_auth(command=["example-cli", 1])])).encode(), 2),
        (json.dumps(_result("operator-action-required", [_auth(command=["example-cli", "a\x00b"])])).encode(), 2),
        (json.dumps(_result("operator-action-required", [{**_auth(), "machine": "laptop"}])).encode(), 2),
        (json.dumps(_result("operator-action-required", [{**_auth(), "driver": "other:example"}])).encode(), 2),
        (b"{" + b" " * (d.MAX_OUTPUT_BYTES + 1) + b"}", 0),
    ],
)
def test_parse_result_malformed_is_failed(stdout, code):
    result = d.parse_result(stdout, code, driver=DRIVER, machine="workstation")
    assert result["state"] == "failed"
    assert result["auth_requests"] == []
    assert d.exit_code(result) == 1


# --- end-to-end through run() -----------------------------------------------


@pytest.fixture
def topology(tmp_path):
    machines = _write_machines(tmp_path)
    module = _write_module(tmp_path / "provider")
    return machines, module


def _request(topology, **kw):
    machines, module = topology
    return d.Request(machines=machines, module=module, cwd=machines.parent, **kw)


def test_setup_plan_invocation_is_headless_and_noninteractive(topology):
    runner = FakeRunner((_result("planned"), 0))
    result = d.run(_request(topology, verb="setup"), runner=runner, hostname="workstation")
    assert result["state"] == "planned"
    argv, kwargs = runner.calls[0]
    machines = str(topology[0].resolve())
    assert argv[-5:] == ["--machine", "workstation", "--machines", machines, "--json"]
    assert "--apply" not in argv and "--interactive" not in argv
    assert kwargs["timeout"] == d.ENTRYPOINT_TIMEOUT["setup"]


def test_entrypoint_runs_from_module_directory(topology):
    runner = FakeRunner((_result("planned"), 0))
    d.run(_request(topology, verb="setup"), runner=runner, hostname="workstation")
    assert Path(runner.calls[0][1]["cwd"]) == topology[1].parent


def test_bounded_runner_enforces_byte_budgets(monkeypatch):
    from agent_procutil import owned_process

    seen = {}

    def fake(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(owned_process, "run_caller_owned", fake)
    d.bounded_runner(["x"], timeout=5, cwd=".")
    assert seen["max_stdout_bytes"] == d.MAX_OUTPUT_BYTES
    assert seen["max_stderr_bytes"] == d.MAX_STDERR_BYTES
    assert seen["binary"] is True and seen["cwd"] == "."


def test_output_overflow_is_failed(topology):
    from agent_procutil.owned_process import OutputLimitExceeded

    def _overflow(argv, **kwargs):
        raise OutputLimitExceeded("stdout", d.MAX_OUTPUT_BYTES)

    result = d.run(_request(topology, verb="setup"), runner=_overflow, hostname="workstation")
    assert result["state"] == "failed" and d.exit_code(result) == 1


def test_owned_runner_runtime_failure_is_failed(topology):
    def _native_failure(argv, **kwargs):
        raise RuntimeError("execution failed")

    result = d.run(_request(topology, verb="setup"), runner=_native_failure, hostname="workstation")
    assert result["state"] == "failed" and "execution failed" in result["detail"]


def test_setup_apply_passes_flag(topology):
    runner = FakeRunner((_result("applied"), 0))
    d.run(_request(topology, verb="setup", apply=True), runner=runner, hostname="workstation")
    assert "--apply" in runner.calls[0][0]


def test_noninteractive_never_runs_auth_commands(topology):
    runner = FakeRunner((_result("operator-action-required", [_auth()]), 2))

    def _never(command):
        raise AssertionError("non-interactive mode must not run auth commands")

    result = d.run(_request(topology, verb="setup", apply=True), runner=runner,
                   auth_runner=_never, hostname="workstation")
    assert result["state"] == "operator-action-required"
    assert d.exit_code(result) == 2
    assert len(runner.calls) == 1


def test_interactive_runs_each_auth_key_once_then_reruns_setup(topology):
    requests = [_auth("login"), _auth("login"), _auth("enroll", ["example-cli", "enroll"])]
    runner = FakeRunner(
        (_result("operator-action-required", requests), 2),
        (_result("applied"), 0),
    )
    ran = []
    result = d.run(
        _request(topology, verb="setup", apply=True, interactive=True), runner=runner,
        auth_runner=lambda command: ran.append(command) or 0, hostname="workstation",
        console=lambda: True,
    )
    assert ran == [["example-cli", "login"], ["example-cli", "enroll"]]
    assert len(runner.calls) == 2
    assert all("--interactive" in call[0] and "--apply" in call[0] for call in runner.calls)
    assert result["state"] == "applied"


def test_interactive_still_required_after_rerun_exits_2(topology):
    pending = (_result("operator-action-required", [_auth()]), 2)
    runner = FakeRunner(pending, pending)
    result = d.run(
        _request(topology, verb="setup", interactive=True), runner=runner,
        auth_runner=lambda command: 0, hostname="workstation", console=lambda: True,
    )
    assert len(runner.calls) == 2
    assert d.exit_code(result) == 2


def test_interactive_requires_console(topology):
    runner = FakeRunner()
    result = d.run(_request(topology, verb="setup", interactive=True), runner=runner,
                   hostname="workstation", console=lambda: False)
    assert result["state"] == "failed" and "console" in result["detail"]
    assert runner.calls == []


def test_status_uses_status_entrypoint(topology):
    runner = FakeRunner((_result("ready"), 0))
    d.run(_request(topology, verb="status"), runner=runner, hostname="workstation")
    assert "status." in " ".join(runner.calls[0][0])


def test_status_falls_back_to_setup_plan(tmp_path):
    machines = _write_machines(tmp_path)
    module = _write_module(tmp_path / "provider", status=False)
    runner = FakeRunner((_result("planned"), 0))
    req = d.Request(verb="status", machines=machines, module=module, cwd=tmp_path)
    d.run(req, runner=runner, hostname="workstation")
    argv = runner.calls[0][0]
    assert "setup." in " ".join(argv) and "--apply" not in argv and "--interactive" not in argv


def test_no_setup_capability_is_ready(tmp_path):
    machines = _write_machines(tmp_path)
    module = _write_module(tmp_path / "provider", setup=False, status=False)
    runner = FakeRunner()
    req = d.Request(verb="setup", machines=machines, module=module, cwd=tmp_path)
    result = d.run(req, runner=runner, hostname="workstation")
    assert result["state"] == "ready" and "no driver setup" in result["detail"]
    assert runner.calls == []


def test_undeclared_driver_is_ready(tmp_path):
    machines = _write_machines(tmp_path, driver_block="      ready: true\n")
    result = d.run(d.Request(verb="setup", machines=machines, cwd=tmp_path), runner=FakeRunner(),
                   hostname="workstation")
    assert result["state"] == "ready" and "no driver setup" in result["detail"]


def test_invalid_driver_declaration_fails(tmp_path):
    machines = _write_machines(tmp_path, driver_block="      driver: {plugin: example-provider}\n")
    result = d.run(d.Request(verb="setup", machines=machines, cwd=tmp_path), runner=FakeRunner(),
                   hostname="workstation")
    assert result["state"] == "failed"


def test_entrypoint_launch_error_is_failed(topology):
    def _boom(argv, **kwargs):
        raise OSError("missing interpreter")

    result = d.run(_request(topology, verb="setup"), runner=_boom, hostname="workstation")
    assert result["state"] == "failed"


def test_script_argv_mapping(tmp_path):
    assert d.script_argv(tmp_path / "s.ps1", ["-a"], windows=True)[1:] == [
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tmp_path / "s.ps1"), "-a",
    ]
    assert d.script_argv(tmp_path / "s.cmd", ["login"], windows=True)[1:] == [
        "/d", "/c", str(tmp_path / "s.cmd"), "login",
    ]
    assert d.script_argv(tmp_path / "s.py", windows=False)[1] == str(tmp_path / "s.py")
    assert d.script_argv(tmp_path / "s.sh", windows=False) == [str(tmp_path / "s.sh")]


@pytest.mark.parametrize("arg", ["workstation&echo.INJECTED>marker.txt&rem", "a|b", "%PATH%", 'a"b', "x^y", "(x)"])
def test_batch_refuses_shell_metacharacters(tmp_path, arg):
    with pytest.raises(d.DriverError, match="metacharacters"):
        d.script_argv(tmp_path / "s.cmd", ["--machine", arg], windows=True)


def test_batch_entrypoints_are_refused_on_windows(tmp_path):
    path = _write_module(tmp_path)
    (path.parent / "scripts" / "setup.cmd").write_text("", encoding="utf-8")
    module = {"entrypoints": {"setup": {"posix": "scripts/setup.sh", "windows": "scripts/setup.cmd"}}}
    with pytest.raises(d.DriverError, match="batch"):
        d.entrypoint_script(path, module, "setup", windows=True)


def test_auth_command_runs_from_module_dir_with_stdout_on_stderr(monkeypatch, tmp_path):
    calls = []
    tool = str(tmp_path / "bin" / "example-cli")
    monkeypatch.setattr(d, "_search_path", lambda name, windows: tool)
    monkeypatch.setattr(d, "_stderr_target", lambda: "STDERR")

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    assert d.run_auth_command(["example-cli", "login"], tmp_path, runner=runner, windows=False) == 0
    assert calls == [([tool, "login"], {"check": False, "cwd": str(tmp_path), "stdout": "STDERR"})]


def test_auth_command_batch_with_metacharacters_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(d, "_search_path", lambda name, windows: str(tmp_path / "example-cli.cmd"))

    def runner(argv, **kwargs):
        raise AssertionError("must not run")

    assert d.run_auth_command(["example-cli", "a&b"], tmp_path, runner=runner, windows=True) == 126


def test_auth_executable_never_resolves_from_caller_checkout(monkeypatch, tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "example-cli").write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "example-cli").write_text("", encoding="utf-8")
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", str(bin_dir)]))
    module_dir = tmp_path / "module"
    assert d.resolve_auth_executable("example-cli", module_dir, windows=False) == str(bin_dir / "example-cli")


def test_relative_auth_helper_resolves_inside_module_dir(tmp_path):
    module_dir = tmp_path / "module"
    assert d.resolve_auth_executable("scripts/login.py", module_dir, windows=False) == str(
        (module_dir / "scripts" / "login.py").resolve()
    )
    with pytest.raises(d.DriverError, match="inside the module"):
        d.resolve_auth_executable("../outside.py", module_dir, windows=False)


def test_missing_auth_executable_exits_127(monkeypatch, tmp_path):
    monkeypatch.setattr(d, "_search_path", lambda name, windows: None)
    assert d.run_auth_command(["example-cli"], tmp_path, runner=None, windows=False) == 127


def test_human_output_escapes_terminal_controls():
    osc52 = "\x1b]52;c;ZXZpbA==\x07"
    result = d.make_result("ready", f"no SSH driver declared for machine 'w{osc52}'")
    text = d.render_human(result, "status")
    assert "\x1b" not in text and "\x07" not in text and "\\x1b" in text


def test_display_command_uses_platform_quoting():
    assert d.display_command(["example-cli", "--scope", "a&b"], windows=False) == "example-cli --scope 'a&b'"
    assert d.display_command(["example-cli", "a b"], windows=True) == 'example-cli "a b"'


def test_git_toplevel_ignores_inherited_git_selectors(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere" / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "elsewhere"))
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=str(tmp_path) + "\n")

    monkeypatch.setattr(d.subprocess, "run", fake_run)
    assert d._git_toplevel(tmp_path) == tmp_path
    assert not any(key.upper().startswith("GIT_") for key in seen["env"])


def test_cli_json_output_and_exit_code(topology, monkeypatch, capsys):
    pending = _result("operator-action-required", [_auth()])
    monkeypatch.setattr(d, "run", lambda req: pending)
    machines, module = topology
    rc = cli.main(["setup", "--machines", str(machines), "--module", str(module), "--json"])
    assert rc == 2
    assert json.loads(capsys.readouterr().out) == pending
