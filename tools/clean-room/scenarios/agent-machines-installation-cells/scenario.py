from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if os.name != "nt":
    import pwd

ROOT = Path(
    os.environ.get("CR_PARTNER_PATH") or os.environ["CR_HARNESS_MOUNT"]
).resolve()
RESULTS = Path(os.environ["CR_REPORT"]).resolve().parent
WORK = RESULTS / "agent-machines-installation-cells-state"
STATE = WORK / "state.json"
CONTEXT_TOOL = ROOT / "libs" / "installation-context" / "installation_context.py"
CONTEXT_TOOL_PS = ROOT / "libs" / "installation-context" / "installation-context.ps1"
GENERATOR = ROOT / "libs" / "payload-invocation" / "generate.py"
PLUGIN_SOURCE = ROOT / "plugins" / "agent-machines"


def profile_home() -> Path:
    if os.name == "nt":
        return Path(os.environ["USERPROFILE"]).resolve()
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


PROFILE = profile_home()
DURABLE = PROFILE / ".copilot-extensions"
LEGACY = PROFILE / ".agent-machines"
POLICY = DURABLE / "installation-mode.json"
if os.environ.get("CR_UV_INDEX"):
    os.environ["UV_DEFAULT_INDEX"] = os.environ["CR_UV_INDEX"]


def run(
    arguments: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {arguments!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def context(*arguments: str) -> dict[str, object]:
    if os.name == "nt":
        converted = [arguments[0]]
        for value in arguments[1:]:
            if value.startswith("--"):
                converted.append(
                    "-" + "".join(part.capitalize() for part in value[2:].split("-"))
                )
            else:
                converted.append(value)
        result = run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(CONTEXT_TOOL_PS),
                *converted,
            ]
        )
    else:
        result = run([sys.executable, str(CONTEXT_TOOL), *arguments])
    return json.loads(result.stdout)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def read_state() -> dict[str, object]:
    return json.loads(STATE.read_text(encoding="utf-8"))


def save_state(value: dict[str, object]) -> None:
    write_json(STATE, value)


def plugin_version(payload: Path) -> str:
    return str(json.loads((payload / "plugin.json").read_text(encoding="utf-8"))["version"])


def next_dev_version(version: str) -> str:
    match = re.fullmatch(r"(\d+\.\d+\.\d+-dev)(\d+)", version)
    if not match:
        raise RuntimeError(f"scenario requires a development version, got {version}")
    return f"{match.group(1)}{int(match.group(2)) + 1}"


def copy_payload(name: str) -> Path:
    target = WORK / name
    shutil.copytree(
        PLUGIN_SOURCE,
        target,
        ignore=shutil.ignore_patterns(
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "*.pyc",
            "*.egg-info",
            ".venv",
            "build",
            "dist",
        ),
    )
    return target


def source_descriptor(name: str) -> dict[str, str]:
    return {
        "source": "github",
        "repo": f"example-org/{name}-marketplace",
    }


def stamp(
    payload: Path,
    key: str,
    descriptor: dict[str, str],
    *,
    expected_namespace_generation: int,
    expected_install_generation: int,
) -> dict[str, object]:
    return context(
        "stamp",
        "--source-json",
        json.dumps(descriptor, separators=(",", ":")),
        "--marketplace-key",
        key,
        "--plugin-id",
        "agent-machines",
        "--payload-root",
        str(payload),
        "--payload-version",
        plugin_version(payload),
        "--payload-origin",
        "explicit",
        "--expected-namespace-generation",
        str(expected_namespace_generation),
        "--expected-install-generation",
        str(expected_install_generation),
        "--durable-home",
        str(DURABLE),
    )


def activate(
    install: Path,
    marketplace_id: str,
    namespace_generation: int,
    install_generation: int,
    activation_generation: int,
) -> dict[str, object]:
    return context(
        "activation-cas",
        "--context",
        str(install),
        "--expected-marketplace-id",
        marketplace_id,
        "--expected-plugin-id",
        "agent-machines",
        "--expected-namespace-generation",
        str(namespace_generation),
        "--expected-install-generation",
        str(install_generation),
        "--expected-activation-generation",
        str(activation_generation),
        "--activation-mode",
        "namespaced",
        "--activation-state",
        "active",
        "--legacy-disposition",
        "absent",
        "--legacy-probe-json",
        '{"declared":true,"result":"absent","checkedAt":"2026-01-01T00:00:00Z"}',
        "--legacy-root",
        str(LEGACY),
        "--durable-home",
        str(DURABLE),
    )


def invocation(payload: Path, install: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(PROFILE),
            "USERPROFILE": str(PROFILE),
            "COPILOT_EXTENSIONS_CONTEXT": str(install),
            "COPILOT_PLUGIN_ROOT": str(payload),
        }
    )
    if os.name == "nt":
        return run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(payload / "bin" / "agent-machines.ps1"),
                "--version",
            ],
            env=environment,
            check=False,
        )
    return run(
        ["bash", str(payload / "bin" / "agent-machines"), "--version"],
        env=environment,
        check=False,
    )


def cell_provision(
    payload: Path,
    install: Path,
    marketplace_id: str,
    *,
    environment_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update({"HOME": str(PROFILE), "USERPROFILE": str(PROFILE)})
    if environment_overrides:
        environment.update(environment_overrides)
    if os.name == "nt":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "init.ps1"),
            "-Action",
            "cell-provision",
            "-Context",
            str(install),
            "-ExpectedMarketplaceId",
            marketplace_id,
            "-DurableHome",
            str(DURABLE),
        ]
    else:
        command = [
            "bash",
            str(payload / "scripts" / "init.sh"),
            "cell-provision",
            "--context",
            str(install),
            "--expected-marketplace-id",
            marketplace_id,
            "--durable-home",
            str(DURABLE),
        ]
    return run(command, env=environment, check=False)


def bootstrap(payload: Path, install: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(PROFILE),
            "USERPROFILE": str(PROFILE),
            "COPILOT_EXTENSIONS_CONTEXT": str(install),
            "COPILOT_PLUGIN_ROOT": str(payload),
        }
    )
    if os.name == "nt":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "bootstrap-check.ps1"),
        ]
    else:
        command = ["bash", str(payload / "scripts" / "bootstrap-check.sh")]
    return run(command, env=environment, check=False)


def validate_install(install: Path, marketplace_id: str, payload: Path) -> dict[str, object]:
    return context(
        "validate",
        "--context",
        str(install),
        "--expected-marketplace-id",
        marketplace_id,
        "--expected-plugin-id",
        "agent-machines",
        "--expected-payload-root",
        str(payload),
        "--durable-home",
        str(DURABLE),
    )


def runtime_version(plugin_root: Path) -> str:
    return (plugin_root / "current-version").read_text(encoding="utf-8").strip()


def assert_no_legacy_runtime() -> None:
    if LEGACY.exists() or LEGACY.is_symlink():
        raise RuntimeError(f"legacy runtime root was created: {LEGACY}")
    local_bin = PROFILE / ".local" / "bin"
    for name in ("agent-machines", "agent-machines.cmd", "agent-machines.ps1"):
        if (local_bin / name).exists():
            raise RuntimeError(f"legacy global command was created: {local_bin / name}")


def stage_1() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    fixtures = WORK / "eligibility"
    for plugin, with_runtime in (
        ("agent-unrelated", True),
        ("context-handoff", False),
    ):
        root = fixtures / plugin
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        for name in (
            "init.sh",
            "init.ps1",
            "resolve-runtime.sh",
            "resolve-runtime.ps1",
            "invoke.sh",
            "invoke.ps1",
        ):
            (scripts / name).write_text("# fixture\n", encoding="utf-8")
        if with_runtime:
            (root / "pyproject.toml").write_text(
                f'[project]\nname = "{plugin}"\nversion = "1.0.0"\n',
                encoding="utf-8",
            )
        write_json(
            root / "plugin.json",
            {
                "name": plugin,
                "version": "1.0.0",
                "runtimeScope": "machine-gated",
                "tools": ["synthetic"],
                "services": ["synthetic"],
            },
        )
        write_json(
            root / "payload-invocation.json",
            {
                "schema": "copilot-extensions.payload-invocation",
                "version": 2,
                "command": plugin,
                "module": "synthetic_runtime",
                "legacyRuntimeRoot": f".{plugin}",
                "installationContext": "required",
                "noSelfProvisionEnv": "SYNTHETIC_NO_SELFPROVISION",
                "purpose": "Exercise a synthetic runtime",
                "installer": "init",
                "payloadRootEnv": "SYNTHETIC_PAYLOAD_ROOT",
                "payloadDispatcher": {
                    "posix": "scripts/invoke.sh",
                    "windows": "scripts/invoke.ps1",
                },
            },
        )
        result = run(
            [sys.executable, str(GENERATOR), str(root / "payload-invocation.json")],
            check=False,
        )
        if result.returncode == 0:
            raise RuntimeError(f"ineligible identity adopted installation context: {plugin}")
    print("eligibility-negative=pass")

    payload = copy_payload("payload-lock")
    stamped = stamp(
        payload,
        "lock",
        source_descriptor("lock"),
        expected_namespace_generation=0,
        expected_install_generation=0,
    )
    install = Path(str(stamped["installReceipt"]))
    witness = WORK / "cell-provision-lock-events.txt"
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(PROFILE),
            "USERPROFILE": str(PROFILE),
            "AGENT_MACHINES_CELL_PROVISION_LOCK_SMOKE": str(witness),
            "AGENT_MACHINES_CELL_PROVISION_LOCK_SMOKE_SLEEP": "1",
            "AGENT_MACHINES_CELL_PROVISION_LOCK_SMOKE_MILLISECONDS": "1000",
        }
    )
    if os.name == "nt":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "init.ps1"),
            "-Action",
            "cell-provision",
            "-Context",
            str(install),
            "-ExpectedMarketplaceId",
            str(stamped["marketplaceId"]),
            "-DurableHome",
            str(DURABLE),
        ]
    else:
        command = [
            "bash",
            str(payload / "scripts" / "init.sh"),
            "cell-provision",
            "--context",
            str(install),
            "--expected-marketplace-id",
            str(stamped["marketplaceId"]),
            "--durable-home",
            str(DURABLE),
        ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: run(command, env=environment, check=False),
                range(2),
            )
        )
    if any(result.returncode != 0 for result in results):
        raise RuntimeError(
            "cell-provision lock witness failed\n"
            + "\n".join(result.stderr for result in results)
        )
    events = witness.read_text(encoding="utf-8").splitlines()
    if (
        len(events) != 4
        or events[0].split()[0] != "start"
        or events[1].split() != ["end", events[0].split()[1]]
        or events[2].split()[0] != "start"
        or events[3].split() != ["end", events[2].split()[1]]
    ):
        raise RuntimeError(f"cell-provision transactions overlapped: {events}")
    print("cell-provision-lock=pass")


def stage_2() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    if DURABLE.exists():
        shutil.rmtree(DURABLE)
    if LEGACY.exists() or LEGACY.is_symlink():
        raise RuntimeError("clean-room profile already has a legacy Agent Machines root")
    payload_a = copy_payload("payload-a-v1")
    payload_b = copy_payload("payload-b-v1")
    descriptor_a = source_descriptor("alpha")
    descriptor_b = source_descriptor("beta")
    stamped_a = stamp(
        payload_a,
        "alpha",
        descriptor_a,
        expected_namespace_generation=0,
        expected_install_generation=0,
    )
    stamped_b = stamp(
        payload_b,
        "beta",
        descriptor_b,
        expected_namespace_generation=0,
        expected_install_generation=0,
    )
    write_json(
        POLICY,
        {
            "schema": "copilot-extensions.installation-mode",
            "version": 1,
            "installationMode": {"enabled": True},
        },
    )
    cells: dict[str, dict[str, object]] = {}
    for name, payload, descriptor, stamped in (
        ("a", payload_a, descriptor_a, stamped_a),
        ("b", payload_b, descriptor_b, stamped_b),
    ):
        install = Path(str(stamped["installReceipt"]))
        marketplace_id = str(stamped["marketplaceId"])
        activated = activate(
            install,
            marketplace_id,
            int(stamped["namespaceGeneration"]),
            int(stamped["generation"]),
            0,
        )
        if name == "a":
            failed_snapshot = cell_provision(
                payload,
                install,
                marketplace_id,
                environment_overrides={
                    "AGENT_MACHINES_CELL_SNAPSHOT_FAIL_BEFORE_STAMP": "1"
                },
            )
            if failed_snapshot.returncode == 0:
                raise RuntimeError("injected snapshot publication failure succeeded")
            failed_snapshot_root = (
                install.parent / "snapshots" / plugin_version(payload)
            )
            if failed_snapshot_root.exists():
                raise RuntimeError(
                    "failed snapshot publication left a visible final directory"
                )
            if list((install.parent / "snapshots").glob(".agent-machines-snapshot-*")):
                raise RuntimeError("failed snapshot publication leaked owned staging")
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: invocation(payload, install), range(2)))
        for result in results:
            if result.returncode != 0 or plugin_version(payload) not in result.stdout:
                raise RuntimeError(
                    f"cell {name} concurrent invocation failed ({result.returncode})\n"
                    f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
                )
        validated = validate_install(install, marketplace_id, payload)
        plugin_root = Path(str(validated["pluginRoot"]))
        version = plugin_version(payload)
        if runtime_version(plugin_root) != version:
            raise RuntimeError(f"cell {name} did not select {version}")
        completion = plugin_root / "versions" / version / ".runtime-slot-completion.json"
        if not completion.is_file():
            raise RuntimeError(f"cell {name} has no immutable completion receipt")
        manifest = json.loads(
            (plugin_root / "deploy-manifest.json").read_text(encoding="utf-8")
        )
        if manifest.get("installation", {}).get("marketplaceId") != marketplace_id:
            raise RuntimeError(f"cell {name} deploy manifest lost installation identity")
        if manifest.get("source", {}).get("version") != version:
            raise RuntimeError(f"cell {name} deploy manifest reports the wrong version")
        if Path(str(manifest.get("source", {}).get("path"))).resolve() != payload:
            raise RuntimeError(f"cell {name} deploy manifest reports the wrong payload")
        if manifest.get("runtime", {}).get("version") != version:
            raise RuntimeError(f"cell {name} deploy manifest selected the wrong runtime")
        if Path(str(manifest.get("runtime", {}).get("path"))).resolve() != (
            plugin_root / "versions" / version
        ):
            raise RuntimeError(f"cell {name} deploy manifest reports the wrong runtime")
        cells[name] = {
            "payload_v1": str(payload),
            "descriptor": descriptor,
            "key": "alpha" if name == "a" else "beta",
            "install": str(install),
            "marketplace_id": marketplace_id,
            "plugin_root": str(plugin_root),
            "namespace_generation": int(stamped["namespaceGeneration"]),
            "install_generation": int(stamped["generation"]),
            "activation_generation": int(activated["activationGeneration"]),
            "version_v1": version,
        }
    if cells["a"]["plugin_root"] == cells["b"]["plugin_root"]:
        raise RuntimeError("independent sources resolved to one plugin root")
    assert_no_legacy_runtime()
    save_state({"cells": cells})
    print("dual-cell-install-use=pass")


def stage_3() -> None:
    state = read_state()
    cells = state["cells"]
    cell_a = cells["a"]
    cell_b = cells["b"]
    payload_v2 = copy_payload("payload-a-v2")
    old_version = str(cell_a["version_v1"])
    new_version = next_dev_version(old_version)
    plugin_json = json.loads((payload_v2 / "plugin.json").read_text(encoding="utf-8"))
    plugin_json["version"] = new_version
    write_json(payload_v2 / "plugin.json", plugin_json)
    pyproject = (payload_v2 / "pyproject.toml").read_text(encoding="utf-8")
    (payload_v2 / "pyproject.toml").write_text(
        re.sub(
            r'(?m)^version = "[^"]+"$',
            f'version = "{new_version}"',
            pyproject,
            count=1,
        ),
        encoding="utf-8",
    )
    init_path = payload_v2 / "src" / "agent_machines" / "__init__.py"
    init_path.write_text(
        init_path.read_text(encoding="utf-8").replace(old_version, new_version),
        encoding="utf-8",
    )
    install = Path(str(cell_a["install"]))
    stamped = stamp(
        payload_v2,
        str(cell_a["key"]),
        dict(cell_a["descriptor"]),
        expected_namespace_generation=int(cell_a["namespace_generation"]),
        expected_install_generation=int(cell_a["install_generation"]),
    )
    activated = activate(
        install,
        str(cell_a["marketplace_id"]),
        int(stamped["namespaceGeneration"]),
        int(stamped["generation"]),
        int(cell_a["activation_generation"]),
    )
    result = invocation(payload_v2, install)
    if result.returncode != 0 or new_version not in result.stdout:
        raise RuntimeError(
            f"updated cell invocation failed ({result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    if runtime_version(Path(str(cell_a["plugin_root"]))) != new_version:
        raise RuntimeError("updated cell did not select its new slot")
    if runtime_version(Path(str(cell_b["plugin_root"]))) != str(cell_b["version_v1"]):
        raise RuntimeError("peer cell changed during update")
    manifest = json.loads(
        (
            Path(str(cell_a["plugin_root"])) / "deploy-manifest.json"
        ).read_text(encoding="utf-8")
    )
    if manifest.get("source", {}).get("version") != new_version:
        raise RuntimeError("updated cell manifest did not select its new version")
    if Path(str(manifest.get("source", {}).get("path"))).resolve() != payload_v2:
        raise RuntimeError("updated cell manifest did not select its new payload")
    if manifest.get("runtime", {}).get("version") != new_version:
        raise RuntimeError("updated cell manifest did not select its new runtime")
    if Path(str(manifest.get("runtime", {}).get("path"))).resolve() != (
        Path(str(cell_a["plugin_root"])) / "versions" / new_version
    ):
        raise RuntimeError("updated cell manifest did not select its new runtime")
    cell_a.update(
        {
            "payload_v2": str(payload_v2),
            "version_v2": new_version,
            "namespace_generation": int(stamped["namespaceGeneration"]),
            "install_generation": int(stamped["generation"]),
            "activation_generation": int(activated["activationGeneration"]),
        }
    )
    save_state(state)
    assert_no_legacy_runtime()
    print("isolated-update=pass")


def stage_4() -> None:
    state = read_state()
    cells = state["cells"]
    cell_a = cells["a"]
    cell_b = cells["b"]
    payload_v1 = Path(str(cell_a["payload_v1"]))
    install = Path(str(cell_a["install"]))
    if os.name == "nt":
        arguments = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload_v1 / "scripts" / "init.ps1"),
            "-Action",
            "slot-cutover",
            "-Context",
            str(install),
            "-ExpectedMarketplaceId",
            str(cell_a["marketplace_id"]),
            "-ExpectedNamespaceGeneration",
            str(cell_a["namespace_generation"]),
            "-ExpectedInstallGeneration",
            str(cell_a["install_generation"]),
            "-ExpectedCurrentVersion",
            str(cell_a["version_v2"]),
            "-DurableHome",
            str(DURABLE),
        ]
    else:
        arguments = [
            "bash",
            str(payload_v1 / "scripts" / "init.sh"),
            "slot-cutover",
            "--context",
            str(install),
            "--expected-marketplace-id",
            str(cell_a["marketplace_id"]),
            "--expected-namespace-generation",
            str(cell_a["namespace_generation"]),
            "--expected-install-generation",
            str(cell_a["install_generation"]),
            "--expected-current-version",
            str(cell_a["version_v2"]),
            "--durable-home",
            str(DURABLE),
        ]
    environment = os.environ.copy()
    environment.update({"HOME": str(PROFILE), "USERPROFILE": str(PROFILE)})
    result = run(arguments, env=environment, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"rollback failed ({result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    if runtime_version(Path(str(cell_a["plugin_root"]))) != str(cell_a["version_v1"]):
        raise RuntimeError("rollback did not select the historical owned slot")
    if runtime_version(Path(str(cell_b["plugin_root"]))) != str(cell_b["version_v1"]):
        raise RuntimeError("peer cell changed during rollback")
    manifest = json.loads(
        (
            Path(str(cell_a["plugin_root"])) / "deploy-manifest.json"
        ).read_text(encoding="utf-8")
    )
    if manifest.get("source", {}).get("version") != str(cell_a["version_v2"]):
        raise RuntimeError("rollback manifest lost the current payload provenance")
    if Path(str(manifest.get("source", {}).get("path"))).resolve() != Path(
        str(cell_a["payload_v2"])
    ):
        raise RuntimeError("rollback manifest lost the current payload path")
    if manifest.get("runtime", {}).get("version") != str(cell_a["version_v1"]):
        raise RuntimeError("rollback manifest did not select the historical version")
    if Path(str(manifest.get("runtime", {}).get("path"))).resolve() != (
        Path(str(cell_a["plugin_root"])) / "versions" / str(cell_a["version_v1"])
    ):
        raise RuntimeError("rollback manifest did not select the historical runtime")
    if Path(
        str(manifest.get("runtime", {}).get("selectedBy", {}).get("path"))
    ).resolve() != payload_v1:
        raise RuntimeError("rollback manifest lost the selecting payload")
    invoked = invocation(Path(str(cell_a["payload_v2"])), install)
    if invoked.returncode != 0 or str(cell_a["version_v1"]) not in invoked.stdout:
        raise RuntimeError("payload invocation did not follow the rolled-back slot")
    bootstrapped = bootstrap(Path(str(cell_a["payload_v2"])), install)
    if bootstrapped.returncode != 0:
        raise RuntimeError(
            f"post-rollback bootstrap failed ({bootstrapped.returncode})\n"
            f"stdout:\n{bootstrapped.stdout}\nstderr:\n{bootstrapped.stderr}"
        )
    if "reconciling in background" in bootstrapped.stdout + bootstrapped.stderr:
        raise RuntimeError("post-rollback bootstrap tried to reverse explicit rollback")
    if runtime_version(Path(str(cell_a["plugin_root"]))) != str(cell_a["version_v1"]):
        raise RuntimeError("post-rollback bootstrap reversed explicit rollback")
    assert_no_legacy_runtime()
    print("isolated-rollback=pass")


def expect_blocked(label: str, payload: Path, install: Path) -> None:
    result = invocation(payload, install)
    if result.returncode != 126:
        raise RuntimeError(
            f"{label} did not fail closed with exit 126: {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def stage_5() -> None:
    state = read_state()
    cell_a = state["cells"]["a"]
    payload = Path(str(cell_a["payload_v2"]))
    install = Path(str(cell_a["install"]))
    plugin_root = Path(str(cell_a["plugin_root"]))
    original_policy = POLICY.read_bytes()

    POLICY.write_text("{\n", encoding="utf-8")
    expect_blocked("malformed policy", payload, install)
    POLICY.write_bytes(original_policy)

    maintenance = plugin_root / "maintenance"
    maintenance.write_text("maintenance\n", encoding="utf-8")
    expect_blocked("maintenance", payload, install)
    maintenance.unlink()

    requested_payload = copy_payload("payload-requested")
    requested_stamp = stamp(
        requested_payload,
        "requested",
        source_descriptor("requested"),
        expected_namespace_generation=0,
        expected_install_generation=0,
    )
    requested_install = Path(str(requested_stamp["installReceipt"]))
    expect_blocked("requested-only", requested_payload, requested_install)
    requested_validated = validate_install(
        requested_install,
        str(requested_stamp["marketplaceId"]),
        requested_payload,
    )

    foreign_activation = Path(str(requested_validated["pluginRoot"])) / "installation-activation.json"
    write_json(
        foreign_activation,
        {
            "schema": "copilot-extensions.installation-activation",
            "version": 1,
            "marketplaceId": requested_stamp["marketplaceId"],
            "pluginId": "agent-machines",
            "mode": "namespaced",
            "state": "active",
            "environment": {
                "platform": "posix" if os.name == "nt" else "windows",
                "homeRealPath": str(PROFILE),
                "wslDistro": None,
            },
            "context": str(requested_install),
            "namespaceGeneration": requested_stamp["namespaceGeneration"],
            "installGeneration": requested_stamp["generation"],
            "generation": 1,
            "legacy": {
                "disposition": "absent",
                "probe": {
                    "declared": True,
                    "result": "absent",
                    "checkedAt": "2026-01-01T00:00:00Z",
                },
            },
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
    )
    expect_blocked("foreign activation", requested_payload, requested_install)
    foreign_activation.unlink()

    LEGACY.mkdir()
    write_json(
        LEGACY / ".installation-ownership.json",
        {
            "schema": "copilot-extensions.legacy-installation-ownership",
            "version": 1,
            "marketplaceId": requested_stamp["marketplaceId"],
            "pluginId": "agent-machines",
            "activation": {
                "path": str(
                    Path(str(requested_validated["pluginRoot"]))
                    / "missing-activation.json"
                ),
                "generation": 1,
            },
            "environment": {
                "platform": "windows" if os.name == "nt" else "posix",
                "homeRealPath": str(PROFILE),
                "wslDistro": os.environ.get("WSL_DISTRO_NAME") or None,
            },
            "transferredAt": "2026-01-01T00:00:00Z",
        },
    )
    expect_blocked("orphaned transfer", requested_payload, requested_install)
    shutil.rmtree(LEGACY)

    assert_no_legacy_runtime()
    print("blocked-states=pass")


STAGES = {
    1: stage_1,
    2: stage_2,
    3: stage_3,
    4: stage_4,
    5: stage_5,
}


def main() -> int:
    stage = int(sys.argv[1])
    STAGES[stage]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
