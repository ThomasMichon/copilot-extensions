from __future__ import annotations

import base64
import copy
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_procutil import no_window_flags
from agent_ssh import fragment_registry, gateway_profile, ssh_profile
from jsonschema import Draft7Validator


def _key(number: int = 1) -> str:
    wire = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes([number]) * 32
    return "ssh-ed25519 " + base64.b64encode(wire).decode()


def _config(root: Path) -> dict:
    return {
        "schema": "agent-ssh.gateway-profile", "version": 1,
        "gateway": "chosen-gateway",
        "identity_file": str(root / "existing approved identity"),
        "machines": [
            {"name": "build-machine", "user": "builder", "reverse_port": 23107, "host_key": _key()},
            {"name": "portable", "user": "owner", "reverse_port": 23491, "host_key": _key(2)},
        ],
    }


def test_compiles_strict_routes_with_independent_names_pins_and_ports(tmp_path):
    config = _config(tmp_path)
    snapshot, registry, files = gateway_profile.profile_sources(config, tmp_path / "sources")
    assert registry["transport"] == "gateway"
    assert [row["name"] for row in registry["machines"]] == ["build-machine", "portable"]
    for actual, declared in zip(registry["machines"], config["machines"]):
        assert actual["hostname"] == "127.0.0.1"
        assert actual["port"] == declared["reverse_port"]
        assert actual["user"] == declared["user"]
        assert actual["identity_file"] == '"' + config["identity_file"] + '"'
        assert actual["options"]["ProxyJump"] == "chosen-gateway"
        assert actual["options"]["HostKeyAlias"] == declared["name"]
        assert actual["options"]["StrictHostKeyChecking"] == "yes"
        assert actual["options"]["GlobalKnownHostsFile"] == "none"
        assert actual["options"]["UpdateHostKeys"] == "no"
        assert actual["options"]["ForwardAgent"] == "no"
        assert actual["options"]["PasswordAuthentication"] == "no"
        assert actual["options"]["BatchMode"] == "yes"
    assert registry["machines"][0]["options"]["UserKnownHostsFile"] == f'"{snapshot / "known_hosts"}"'
    assert files["known_hosts"] == f"build-machine {_key()}\nportable {_key(2)}\n"
    assert json.loads(files["registry.json"]) == registry
    module = json.loads(files["module.json"])
    assert module["module"] == "gateway"
    contract = Path(__file__).resolve().parents[1] / "contract" / "module.schema.json"
    Draft7Validator(json.loads(contract.read_text())).validate(module)
    assert not snapshot.exists()


@pytest.mark.parametrize("count", [0, 1, 7, 32, 256])
def test_arbitrary_declared_machine_sets_not_a_fixed_pilot(tmp_path, count):
    config = _config(tmp_path)
    config["machines"] = [
        {"name": f"machine-{index}", "user": "owner", "reverse_port": 24000 + index,
         "host_key": _key(index % 255)}
        for index in range(count)
    ]
    _, registry, files = gateway_profile.profile_sources(config, tmp_path / "sources")
    assert len(registry["machines"]) == count
    assert len(files["known_hosts"].splitlines()) == count


def test_selection_is_immutable_and_order_independent(tmp_path):
    config = _config(tmp_path)
    original = copy.deepcopy(config)
    first = gateway_profile.profile_sources(config, tmp_path)
    assert config == original
    config["machines"].reverse()
    assert gateway_profile.profile_sources(config, tmp_path) == first
    config["machines"][0]["host_key"] = _key(3)
    assert gateway_profile.profile_sources(config, tmp_path)[0] != first[0]


def test_retained_sources_cannot_depend_on_the_callers_working_directory(tmp_path):
    with pytest.raises(ValueError, match="must be absolute"):
        gateway_profile.profile_sources(_config(tmp_path), Path("relative"))


@pytest.mark.parametrize(("field", "value"), [
    ("schema", "other"), ("version", True), ("version", 2),
    ("gateway", "-oProxyCommand=command"), ("gateway", "Host *"), ("gateway", "chosen\nHost bad"),
    ("identity_file", "relative"), ("identity_file", 'bad"path'), ("identity_file", "%h"),
    ("machines", {}), ("machines", [None]), ("machines", [None] * 257),
])
def test_invalid_top_level_fields_fail(tmp_path, field, value):
    config = _config(tmp_path)
    config[field] = value
    with pytest.raises(ValueError):
        gateway_profile.validate_config(config)


@pytest.mark.parametrize(("field", "value"), [
    ("name", "chosen-gateway"), ("name", "CHOSEN-GATEWAY"), ("name", "*"),
    ("name", "one two"), ("name", "host\nInclude other"), ("user", "-root"),
    ("user", "user\nProxyCommand command"), ("reverse_port", 0), ("reverse_port", 65536),
    ("reverse_port", True), ("reverse_port", "23107"),
    ("host_key", "ssh-rsa AAAA"), ("host_key", "ssh-ed25519 !!!!"),
    ("host_key", "ssh-ed25519 YQ=="), ("host_key", "ssh-ed25519 YQ==\nother"),
])
def test_invalid_peer_fields_fail(tmp_path, field, value):
    config = _config(tmp_path)
    config["machines"][0][field] = value
    with pytest.raises(ValueError):
        gateway_profile.validate_config(config)


@pytest.mark.parametrize("change", ["alias", "port", "extra", "missing"])
def test_collisions_and_unknown_or_missing_fields_fail(tmp_path, change):
    config = _config(tmp_path)
    if change == "alias":
        config["machines"][1]["name"] = "BUILD-MACHINE"
    elif change == "port":
        config["machines"][1]["reverse_port"] = config["machines"][0]["reverse_port"]
    elif change == "extra":
        config["machines"][0]["command"] = "not allowed"
    else:
        del config["machines"][0]["host_key"]
    with pytest.raises(ValueError):
        gateway_profile.validate_config(config)


def test_duplicate_oversized_and_malformed_documents_fail(tmp_path):
    source = tmp_path / "input.json"
    for content in ['{"schema":"first","schema":"second"}', "[]", '{"unfinished":',
                    " " * (gateway_profile.MAX_INPUT_BYTES + 1), "[" * 2000]:
        source.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError):
            gateway_profile.load_config(source)


@pytest.fixture
def emitted(tmp_path):
    if not shutil.which("ssh"):
        pytest.skip("native OpenSSH is required for actual fragment publication")
    config = _config(tmp_path)
    identity = Path(config["identity_file"])
    identity.write_text("fixture placeholder; the emitter must not read private key bytes\n")
    source = tmp_path / "selected.json"
    source.write_text(json.dumps(config), encoding="utf-8")
    ssh_dir = tmp_path / "ssh"
    managed = ssh_dir / "config.d"
    managed.mkdir(parents=True)
    root_config = ssh_dir / "config"
    root_config.write_text("Host chosen-gateway\n    HostName gateway.invalid\n    User gatewayuser\n")
    foreign = managed / "50-agent-ssh-unrelated.conf"
    foreign_config = {"transport": "unrelated", "machines": [
        {"name": "unrelated", "hostname": "unrelated.invalid"},
    ]}
    foreign_module = {"module": "unrelated"}
    registry_path, module_path = tmp_path / "unrelated.json", tmp_path / "unrelated-module.json"
    registry_path.write_text(json.dumps(foreign_config))
    module_path.write_text(json.dumps(foreign_module))
    foreign.write_text(ssh_profile.render_fragment(
        foreign_config, foreign_module, registry_path=registry_path, module_path=module_path,
    ))
    foreign_before = foreign.read_bytes()
    fragment = gateway_profile.emit_profile(source, config_d=managed, ssh_config=root_config)
    return SimpleNamespace(
        source=source, config=config, managed=managed, root_config=root_config,
        fragment=fragment, foreign=foreign, foreign_before=foreign_before, identity=identity,
        sources=ssh_dir / gateway_profile._SOURCE_DIR,
    )


def test_real_emitter_coexists_and_retains_sources_after_input_removal(emitted):
    body = emitted.fragment.read_text()
    assert "ProxyJump chosen-gateway" in body
    assert "Host build-machine" in body and "Host portable" in body
    assert "fixture placeholder" not in "".join(
        file.read_text() for file in emitted.sources.rglob("*") if file.is_file()
    )
    emitted.source.unlink()
    report = fragment_registry.scan_fragment_registry(emitted.managed)
    assert report.permits_probe("build-machine")
    assert report.permits_probe("portable")
    assert emitted.foreign.read_bytes() == emitted.foreign_before
    assert emitted.root_config.read_text().count(ssh_profile._include_line(emitted.managed)) == 1
    assert emitted.identity.read_text().startswith("fixture placeholder")


def test_native_openssh_resolves_the_real_published_profile(emitted):
    result = subprocess.run(
        [shutil.which("ssh"), "-G", "-F", str(emitted.root_config), "build-machine"],
        capture_output=True, text=True, timeout=15, check=False,
        creationflags=no_window_flags(),
    )
    assert result.returncode == 0, result.stderr
    options = dict(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)
    assert options["hostname"] == "127.0.0.1"
    assert options["user"] == "builder"
    assert options["port"] == "23107"
    assert options["proxyjump"] == "chosen-gateway"
    assert options["hostkeyalias"] == "build-machine"
    assert options["stricthostkeychecking"] in ("yes", "true")
    assert options["globalknownhostsfile"] == "none"
    assert "existing approved identity" in options["identityfile"]


def test_repeat_reuses_identical_sources_and_preserves_other_transport(emitted):
    before = {file: (file.read_bytes(), file.stat().st_mtime_ns)
              for file in emitted.sources.rglob("*") if file.is_file() and file.name != "gateway.lock"}
    gateway_profile.emit_profile(
        emitted.source, config_d=emitted.managed, ssh_config=emitted.root_config,
    )
    assert all((file.read_bytes(), file.stat().st_mtime_ns) == value for file, value in before.items())
    assert len(list(emitted.sources.glob("gateway-*"))) == 1
    assert emitted.root_config.read_text().count(ssh_profile._include_line(emitted.managed)) == 1


@pytest.mark.parametrize("change", [
    "bad-input", "missing-key", "changed-retained-pin", "foreign-source", "identity-is-config",
])
def test_refusal_preserves_the_current_profile_and_unrelated_state(emitted, change):
    before = emitted.fragment.read_bytes()
    if change == "bad-input":
        emitted.source.write_text('{"invalid": true}')
    elif change == "missing-key":
        emitted.identity.unlink()
    elif change == "changed-retained-pin":
        next(emitted.sources.glob("gateway-*/known_hosts")).write_text("foreign bytes\n")
    elif change == "identity-is-config":
        config = dict(emitted.config, identity_file=str(emitted.root_config))
        emitted.source.write_text(json.dumps(config))
    else:
        next(emitted.sources.glob("gateway-*")).joinpath("foreign-file").write_text("preserve\n")
    with pytest.raises(ValueError):
        gateway_profile.emit_profile(
            emitted.source, config_d=emitted.managed, ssh_config=emitted.root_config,
        )
    assert emitted.fragment.read_bytes() == before
    assert emitted.foreign.read_bytes() == emitted.foreign_before


def test_cli_registration_and_truthful_output(emitted, capsys):
    from agent_ssh.__main__ import main

    code = main([
        "gateway-profile", str(emitted.source), "--config-d", str(emitted.managed),
        "--ssh-config", str(emitted.root_config), "--json",
    ])
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "transport": "gateway", "state": "configured", "fragment": str(emitted.fragment),
        "connection_attempted": False, "enrollment_performed": False,
    }
    emitted.source.write_text('{"invalid":true}')
    assert main(["gateway-profile", str(emitted.source), "--json"]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "[FAIL]" in output.err
