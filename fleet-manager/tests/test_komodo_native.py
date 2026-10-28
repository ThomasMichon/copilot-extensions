"""Real contained subprocess protocol controls, not official-image deployment proof."""

from __future__ import annotations

from dataclasses import replace
import inspect
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any

import pytest

from fleet_manager.komodo_declaration import KomodoDeclaration
from fleet_manager.komodo_native import KomodoNative, KomodoTooling
from fleet_manager.komodo_profile import DATA_NAMES, SECRET_NAMES, KomodoMaterialization, render_profile
from fleet_manager.planning import PlanError
from test_komodo_declaration import declaration

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux host substrate contract")


def _docker_protocol(args: list[str], directory: str) -> tuple[int, str]:
    import json
    import os
    from pathlib import Path
    import re
    import time

    root = Path(directory)
    with (root / "calls.jsonl").open("a") as out:
        out.write(json.dumps({"argv": args, "environment": dict(os.environ)}) + "\n")
    if (root / "delay").exists():
        time.sleep(10)
    if (root / "error").exists():
        return 9, "sensitive-native-output-must-not-be-reported"
    args = args[4:]
    if args[0] == "info":
        return 0, json.dumps({"id": "daemon-a", "os": "linux", "architecture": "aarch64"})
    if args[:2] == ["compose", "version"]:
        version = root / "compose-version"
        return 0, version.read_text() if version.exists() else "2.39.4"
    if args[:2] == ["image", "inspect"]:
        overrides = root / "image-digests.json"
        digests = json.loads(overrides.read_text()) if overrides.exists() else {}
        return 0, json.dumps({
            "id": "sha256:" + args[-1].split("sha256:")[1],
            "os": "linux", "architecture": "arm64", "digests": digests.get(args[-1], [args[-1]]),
            "environment": [], "entrypoint": [], "command": [], "user": "",
        })
    if args[:2] == ["network", "ls"]:
        network = json.loads((root / "network.json").read_text()) if (root / "network.json").exists() else None
        extra = root / "extra-networks.json"
        networks = ([network] if network else []) + (json.loads(extra.read_text()) if extra.exists() else [])
        pattern = args[args.index("--filter") + 1].removeprefix("name=")
        return 0, "\n".join(item["Id"] for item in networks if re.search(pattern, item["Name"]))
    if args[:2] == ["network", "inspect"]:
        network = json.loads((root / "network.json").read_text())
        live = json.loads((root / "live.json").read_text()) if (root / "live.json").exists() else {}
        network["Containers"].update({
            item["Id"]: {"Name": name} for name, item in live.items() if item["State"]["Running"]
        })
        return 0, json.dumps([network])
    if args[:2] == ["container", "ls"]:
        if (root / "stateful").exists():
            live = json.loads((root / "live.json").read_text()) if (root / "live.json").exists() else {}
            identity = next((arg[3:] for arg in args if arg.startswith("id=")), None)
            return 0, "\n".join(item["Id"] for item in live.values()
                                if identity is None or item["Id"] == identity)
        return 0, "\n".join(f"{number:064x}" for number in (1, 2, 3))
    if args[:2] == ["container", "inspect"]:
        if (root / "stateful").exists():
            return 0, json.dumps(list(json.loads((root / "live.json").read_text()).values()))
        return 0, json.dumps([{
            "Id": f"{number:064x}", "Config": {"Labels": {
                "com.docker.compose.service": name,
                "com.docker.compose.project": "example-manager",
                "com.docker.compose.oneoff": "False",
            }}, "HostConfig": {}, "Mounts": [], "State": {"Status": "running"},
        } for number, name in enumerate(("core", "mongo", "periphery"), 1)])
    if args[:2] in (["container", "stop"], ["container", "rm"]):
        live = json.loads((root / "live.json").read_text())
        identities = args[4:] if args[1] == "stop" else args[2:]
        if not identities or not set(identities).issubset({item["Id"] for item in live.values()}):
            return 8, "unowned-container-effect"
        with (root / "retire-effects").open("a") as out:
            out.write(json.dumps(args) + "\n")
        for name, item in list(live.items()):
            if item["Id"] in identities:
                if args[1] == "stop":
                    item["State"] = {"Status": "exited", "Running": False}
                else:
                    del live[name]
        (root / "live.json").write_text(json.dumps(live))
        return (7, "lost-retirement-reply") if (root / ("lost-" + args[1])).exists() else (0, "")
    if args[0] == "compose" and "up" in args:
        with (root / "effects").open("a") as out:
            out.write("one\n")
        if (root / "stateful").exists():
            import hashlib

            profile = json.loads(Path(args[args.index("--file") + 1]).read_text())
            failure = json.loads((root / "fail-create.json").read_text()) if (root / "fail-create.json").exists() else {}
            if failure.get("fail") and failure.get("before_network"):
                (root / "live.json").write_text("{}")
                return 7, "settled-before-network-creation"
            network_config = profile["networks"]["default"]
            if not (root / "network.json").exists():
                if network_config.get("external"):
                    return 8, "external-network-missing"
                (root / "network.json").write_text(json.dumps({
                    "Id": hashlib.sha256((profile["name"] + "-network").encode()).hexdigest(),
                    "Name": network_config["name"], "Driver": "bridge", "Scope": "local",
                    "Internal": False, "Attachable": False, "Ingress": False, "ConfigOnly": False,
                    "ConfigFrom": {"Network": ""}, "Options": {}, "IPAM": {"Driver": "default"},
                    "Labels": {**network_config["labels"], "com.docker.compose.project": profile["name"],
                               "com.docker.compose.network": "default"}, "Containers": {},
                }))
            network = json.loads((root / "network.json").read_text())
            previous = json.loads((root / "live.json").read_text()) if (root / "live.json").exists() else {}
            live = {}
            sequence = len((root / "effects").read_text().splitlines())
            for name, service in profile["services"].items():
                labels = {
                    **service["labels"], "com.docker.compose.service": name,
                    "com.docker.compose.project": profile["name"],
                    "com.docker.compose.oneoff": "False",
                }
                old = previous.get(name)
                if old and old["Config"]["Labels"] == labels and old["State"]["Running"]:
                    live[name] = old
                    continue
                mounts = [{
                    "Source": item["source"], "Destination": item["target"],
                    "Type": "bind", "RW": not item["read_only"],
                } for item in service["volumes"]]
                mounts.extend({
                    "Source": profile["secrets"][secret]["file"],
                    "Destination": "/run/secrets/" + secret, "Type": "bind", "RW": False,
                } for secret in service.get("secrets", []))
                live[name] = {
                    "Id": hashlib.sha256(f"{name}-{sequence}".encode()).hexdigest(),
                    "Image": "sha256:" + service["image"].split("sha256:")[1],
                    "Config": {"Labels": labels, "User": "", "Entrypoint": [],
                               "Cmd": [argument.replace("$$", "$")
                                       for argument in service.get("command", [])],
                               "Env": [key + "=" + value for key, value in service["environment"].items()]},
                    "HostConfig": {
                        "Privileged": False, "CapAdd": None, "PidMode": "",
                        "NetworkMode": profile["name"] + "_default",
                        "Memory": service["mem_limit"], "NanoCpus": round(float(service["cpus"]) * 10**9),
                        "PortBindings": {"9120/tcp": [
                            {"HostIp": "127.0.0.1", "HostPort": service["ports"][0]["published"]},
                        ]} if name == "core" else {},
                    }, "Mounts": mounts, "State": {
                        "Status": "running", "Running": True, "Paused": False,
                        "Restarting": False, "Dead": False,
                        "Pid": 1000 + sequence * 10 + ("core", "mongo", "periphery").index(name),
                        "StartedAt": "fixture-start-" + str(sequence),
                    },
                    "NetworkSettings": {"Networks": {
                        network["Name"]: {"NetworkID": network["Id"]},
                    }},
                }
            if (root / "corrupt-fixed").exists():
                live["core"]["Config"]["Env"].append("UNSELECTED_SETTING=unexpected")
            if failure.get("fail"):
                live.update({name: previous[name] for name in failure.get("retain", [])})
                live = {name: item for name, item in live.items() if name in failure["services"]}
                if failure.get("unstarted"):
                    for item in live.values():
                        item["State"] = {"Status": "created", "Running": False}
                        item["NetworkSettings"]["Networks"][network["Name"]]["NetworkID"] = ""
            for name in ("core", "periphery"):
                if name in live and live[name]["State"]["Running"]:
                    key_root = Path(profile["services"][name]["volumes"][0]["source"])
                    key = key_root / (name + ".pub")
                    if not key.exists():
                        key.write_text("fixture-public-key-" + name)
            (root / "live.json").write_text(json.dumps(live))
            if failure.get("fail"):
                return 7, "settled-container-creation-failure"
            if (root / "lost-reply").exists():
                return 7, "lost-reply-after-native-create"
        return 0, ""
    return 8, "unsupported-protocol-command"


@pytest.fixture
def native_rig(tmp_path: Path, monkeypatch: Any) -> Any:
    root = Path(__file__).absolute().parents[2]
    monkeypatch.syspath_prepend(str(root / "libs" / "agent-procutil" / "src"))
    from agent_procutil.owned_process import run_caller_owned

    directory = tmp_path / "docker-client"
    directory.mkdir(mode=0o700)
    client_config = tmp_path / "client-config"
    client_config.mkdir(mode=0o700)
    (client_config / "config.json").write_bytes(b"{}")
    (client_config / "config.json").chmod(0o600)
    paths = {}
    for name in DATA_NAMES:
        paths[name] = tmp_path / name
        paths[name].mkdir(mode=0o700)
    for name in SECRET_NAMES:
        paths[name] = tmp_path / name
        paths[name].write_bytes((name + "-fixture-material").encode())
        paths[name].chmod(0o600)
    bound = socket.socket(socket.AF_UNIX)
    socket_path = tmp_path / "docker.sock"
    bound.bind(str(socket_path))
    material = KomodoMaterialization(docker_socket=socket_path, **paths)
    selected = KomodoDeclaration.from_dict(declaration())
    script = tmp_path / "docker-fixture.py"
    script.write_text(
        "import sys\n" + inspect.getsource(_docker_protocol)
        + "\ncode, output = _docker_protocol(sys.argv[1:], "
        + repr(str(directory)) + ")\nprint(output, end='')\nraise SystemExit(code)\n")
    executable = Path(sys.executable).resolve()

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return run_caller_owned([str(executable), str(script), *argv[1:]], **kwargs)

    tooling = KomodoTooling(executable, client_config, "daemon-a", run)
    try:
        yield {"native": KomodoNative(tooling), "tooling": tooling, "material": material,
               "selected": selected, "root": tmp_path, "directory": directory}
    finally:
        bound.close()


def test_fixed_profile_uses_only_official_pinned_images_and_borrowed_secret_files(native_rig: Any) -> None:
    material, selected = native_rig["material"], native_rig["selected"]
    raw = render_profile(selected, material, owner="d" * 64)
    profile = json.loads(raw)
    for value in material.credential_bytes().values():
        assert value not in raw
    assert set(profile) == {"name", "services", "secrets", "networks"}
    assert set(profile["services"]) == {"core", "mongo", "periphery"}
    assert "volumes" not in profile
    assert profile["networks"]["default"] == {
        "name": "example-manager_default", "driver": "bridge", "internal": False,
        "attachable": False, "labels": {"io.fleet-manager.owner": "d" * 64},
    }
    for name, service in profile["services"].items():
        assert service["image"] == selected.to_dict()["images"][name]["image"]
        assert service["mem_limit"] == 512 * 1024**2
        assert service["cpus"] == "0.500"
        assert "build" not in service and "env_file" not in service
        assert all(mount["bind"]["create_host_path"] is False for mount in service["volumes"])
    assert profile["services"]["core"]["ports"] == [{
        "host_ip": "127.0.0.1", "protocol": "tcp", "published": "19120", "target": 9120,
    }]
    assert "ports" not in profile["services"]["mongo"]
    assert "ports" not in profile["services"]["periphery"]
    mounts = profile["services"]["periphery"]["volumes"]
    assert next(item for item in mounts if item["target"] == "/var/run/docker.sock")["source"] == str(
        material.docker_socket)


@pytest.mark.parametrize("version", [
    "2.39.4", "v2.39.4", "5.1.4", "v5.1.4", "5.1.4+vendor.1", "5.0.0", "5.10.12+vendor-build.001",
])
def test_native_prerequisites_use_actual_selected_daemon_images_and_scrub_ambient_controls(
    native_rig: Any, monkeypatch: Any, version: str,
) -> None:
    monkeypatch.setenv("DOCKER_HOST", "tcp://wrong-owner.invalid")
    monkeypatch.setenv("DOCKER_CONTEXT", "wrong-context")
    monkeypatch.setenv("DOCKER_AUTH_CONFIG", "private-ambient-material")
    monkeypatch.setenv("PATH", "/untrusted-client-tools")
    rig = native_rig
    (rig["directory"] / "compose-version").write_text(version)
    images = rig["native"].prerequisites(rig["selected"], rig["material"], deadline=time.monotonic() + 10)
    assert {name: image.identity for name, image in images.items()} == {
        "core": "sha256:" + "a" * 64, "mongo": "sha256:" + "c" * 64,
        "periphery": "sha256:" + "b" * 64,
    }
    calls = [json.loads(line) for line in (rig["directory"] / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 5
    assert all(call["argv"][:4] == [
        "--config", str(rig["tooling"].client_config), "--host", "unix://" + str(rig["material"].docker_socket),
    ] for call in calls)
    assert all(not {"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_AUTH_CONFIG"} & call["environment"].keys()
               for call in calls)
    assert all(call["environment"]["PATH"] == os.defpath for call in calls)
    assert not (rig["directory"] / "effects").exists()
    other = KomodoNative(replace(rig["tooling"], daemon_id="other-daemon"))
    with pytest.raises(PlanError, match="physical host"):
        other.prerequisites(rig["selected"], rig["material"], deadline=time.monotonic() + 10)


@pytest.mark.parametrize("version", [
    "1.29.2", "3.0.0", "4.1.0", "6.0.0", "5.1", "5.1.4-rc.1", "5.1.4\n6.0.0", "unavailable",
    "5.01.4", "5.1.04", "5.1.4+.", "5.1.4+vendor..1", "5.1.4+.vendor", "5.1.4+vendor.",
])
def test_native_prerequisites_refuse_unqualified_compose_before_image_or_effect(
    native_rig: Any, version: str,
) -> None:
    rig = native_rig
    (rig["directory"] / "compose-version").write_text(version)
    with pytest.raises(PlanError, match="admitted Compose"):
        rig["native"].prerequisites(rig["selected"], rig["material"], deadline=time.monotonic() + 10)
    calls = [json.loads(line) for line in (rig["directory"] / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 2 and calls[-1]["argv"][4:] == ["compose", "version", "--short"]
    assert not (rig["directory"] / "effects").exists()


def test_fixed_native_effect_executes_real_owned_child_without_build_pull_or_cleanup(native_rig: Any) -> None:
    rig = native_rig
    profile = rig["root"] / "compose.json"
    raw = render_profile(rig["selected"], rig["material"], owner="d" * 64)
    profile.write_bytes(raw)
    profile.chmod(0o600)
    rig["native"]._apply(rig["selected"], rig["material"], profile=profile, expected=raw,
                         deadline=time.monotonic() + 10)
    assert (rig["directory"] / "effects").read_text() == "one\n"
    arguments = json.loads((rig["directory"] / "calls.jsonl").read_text())["argv"]
    assert arguments[-3:] == ["core", "mongo", "periphery"]
    assert arguments[arguments.index("--pull") + 1] == "never"
    assert "--no-build" in arguments and "--wait" in arguments
    assert not {"down", "rm", "--remove-orphans", "--volumes", "--build"} & set(arguments)
    containers = rig["native"].inspect(rig["selected"], rig["material"], deadline=time.monotonic() + 10)
    assert set(containers) == {"core", "mongo", "periphery"}
    profile.write_bytes(b"changed")
    with pytest.raises(PlanError, match="changed before"):
        rig["native"]._apply(rig["selected"], rig["material"], profile=profile, expected=raw,
                             deadline=time.monotonic() + 10)
    assert (rig["directory"] / "effects").read_text() == "one\n"


def test_native_failure_never_reports_captured_content_or_replays_effect(native_rig: Any) -> None:
    rig = native_rig
    (rig["directory"] / "error").touch()
    with pytest.raises(PlanError) as caught:
        rig["native"].inspect(rig["selected"], rig["material"], deadline=time.monotonic() + 10)
    assert "sensitive-native-output" not in str(caught.value)
    assert "fixture-material" not in str(caught.value)
    assert len((rig["directory"] / "calls.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("change", ["shared-directory", "cli-plugins", ".docker", "contexts"])
def test_native_client_configuration_cannot_supply_extra_executable_state(native_rig: Any, change: str) -> None:
    rig = native_rig
    directory = rig["tooling"].client_config
    if change == "shared-directory":
        directory.chmod(0o755)
    else:
        (directory / change).mkdir(mode=0o700)
    with pytest.raises(PlanError, match="client configuration"):
        replace(rig["tooling"])
    with pytest.raises(PlanError, match="client configuration"):
        rig["native"].prerequisites(rig["selected"], rig["material"], deadline=time.monotonic() + 10)
    assert not (rig["directory"] / "calls.jsonl").exists()


def test_native_client_directory_identity_cannot_be_replaced_after_selection(native_rig: Any) -> None:
    rig = native_rig
    directory = rig["tooling"].client_config
    directory.rename(directory.with_suffix(".retained"))
    directory.mkdir(mode=0o700)
    (directory / "config.json").write_bytes(b"{}")
    (directory / "config.json").chmod(0o600)
    fresh = replace(rig["tooling"])
    assert fresh.client_identity != rig["tooling"].client_identity
    with pytest.raises(PlanError, match="client configuration"):
        rig["native"].prerequisites(rig["selected"], rig["material"], deadline=time.monotonic() + 10)
    assert not (rig["directory"] / "calls.jsonl").exists()


@pytest.mark.parametrize("repository", [
    "mongo", "library/mongo", "docker.io/mongo", "docker.io/library/mongo",
    "index.docker.io/library/mongo", "foreign.invalid/library/mongo", "docker.io/other/mongo",
    "ghcr.io/library/mongo", "mongo-other",
])
@pytest.mark.parametrize("matching_digest", [True, False])
def test_native_mongo_digest_accepts_only_equivalent_official_repository(
    native_rig: Any, repository: str, matching_digest: bool,
) -> None:
    rig = native_rig
    image = rig["selected"].to_dict()["images"]["mongo"]["image"]
    actual = repository + "@sha256:" + ("c" if matching_digest else "d") * 64
    (rig["directory"] / "image-digests.json").write_text(json.dumps({image: [actual]}))
    arguments = (rig["selected"], rig["material"])
    if matching_digest and repository in {
        "mongo", "library/mongo", "docker.io/mongo", "docker.io/library/mongo",
        "index.docker.io/library/mongo",
    }:
        images = rig["native"].selected_images(*arguments, deadline=time.monotonic() + 10)
        assert images["mongo"].identity == "sha256:" + "c" * 64
    else:
        with pytest.raises(PlanError, match="immutable image"):
            rig["native"].selected_images(*arguments, deadline=time.monotonic() + 10)
    assert not (rig["directory"] / "effects").exists()


@pytest.mark.parametrize("service", ["core", "periphery"])
def test_native_other_official_repositories_do_not_gain_familiar_name_aliases(
    native_rig: Any, service: str,
) -> None:
    rig = native_rig
    image = rig["selected"].to_dict()["images"][service]["image"]
    (rig["directory"] / "image-digests.json").write_text(json.dumps({
        image: [image.removeprefix("ghcr.io/")],
    }))
    with pytest.raises(PlanError, match="immutable image"):
        rig["native"].selected_images(rig["selected"], rig["material"], deadline=time.monotonic() + 10)


@pytest.mark.parametrize("scenario", ["substring-only", "exact-and-substring", "duplicate-exact"])
def test_native_network_filter_matches_complete_name_only(native_rig: Any, scenario: str) -> None:
    rig = native_rig
    name = rig["selected"].to_dict()["project"] + "_default"
    expected = {"Id": "a" * 64, "Name": name, "Containers": {}}
    if scenario != "substring-only":
        (rig["directory"] / "network.json").write_text(json.dumps(expected))
    extras = [{"Id": "b" * 64, "Name": "other-" + name, "Containers": {}},
              {"Id": "c" * 64, "Name": name + "-other", "Containers": {}}]
    if scenario == "duplicate-exact":
        extras.append({"Id": "d" * 64, "Name": name, "Containers": {}})
    (rig["directory"] / "extra-networks.json").write_text(json.dumps(extras))
    if scenario == "duplicate-exact":
        with pytest.raises(PlanError, match="ambiguous"):
            rig["native"].network(rig["selected"], rig["material"], deadline=time.monotonic() + 10)
    else:
        result = rig["native"].network(rig["selected"], rig["material"], deadline=time.monotonic() + 10)
        assert result == (None if scenario == "substring-only" else expected)
    assert not (rig["directory"] / "effects").exists()


def test_unconfirmed_cleanup_latches_native_owner_before_another_process(native_rig: Any) -> None:
    rig = native_rig
    calls = []

    def unsettled(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "[]", "")

    native = KomodoNative(replace(rig["tooling"], run=unsettled))
    for _ in range(2):
        with pytest.raises(PlanError, match="settlement|custody"):
            native.inspect(rig["selected"], rig["material"], deadline=time.monotonic() + 10)
    assert len(calls) == 1 and native.native_failure is not None


def test_materialization_refuses_unsafe_or_changed_credential_custody(native_rig: Any) -> None:
    material = native_rig["material"]
    for name in ("admin_password", "database_password", "jwt_secret"):
        path = getattr(material, name)
        original = path.read_bytes()
        path.chmod(0o644)
        with pytest.raises(PlanError, match="private"):
            material.credential_bytes()
        path.chmod(0o600)
        path.write_bytes(b"short")
        with pytest.raises(PlanError, match="unsupported material"):
            material.credential_bytes()
        path.write_bytes(original)
    with pytest.raises(PlanError, match="alias"):
        replace(material, admin_password=material.database_password)
    with pytest.raises(PlanError, match="alias|disjoint"):
        replace(material, mongo_config=material.mongo_data)
