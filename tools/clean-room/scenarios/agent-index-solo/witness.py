"""Assertions and shipped management calls for the released Index solo witness."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(
    Path(os.environ["CR_LIB"]).parent if os.environ.get("CR_LIB")
    else Path(__file__).resolve().parents[2] / "lib"
))
from materialized_provenance import verify as verify_candidate  # noqa: E402


def read_object(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    # capture() prefixes logs with the invoked command; scan only line starts.
    objects = []
    for offset, line in enumerate(text.splitlines(keepends=True)):
        if line.lstrip().startswith("{"):
            data, _ = json.JSONDecoder().raw_decode(
                "".join(text.splitlines(keepends=True)[offset:]).lstrip()
            )
            if isinstance(data, dict):
                objects.append(data)
    if objects:
        return objects[-1]
    raise ValueError(f"no JSON object in {path}")


def run(*args: str) -> dict:
    result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=300)
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    result.check_returncode()
    return json.loads(result.stdout)


def context(payload: Path, *args: str) -> dict:
    return run("bash", str(payload / "scripts/installation-context/installation-context.sh"), *args)


def reports_version(text: str, version: str) -> bool:
    normalized = version.replace("-dev", ".dev")
    return any(line.strip() in {version, normalized, f"agent-index {version}", f"agent-index {normalized}"}
               for line in text.splitlines())


def assert_dormant(home: Path, root: Path | None, *, stamped: bool = False) -> None:
    if stamped:
        legacy = home / ".agent-index"
        assert set(p.name for p in legacy.iterdir()) <= {
            "payload-dir", "stamped-version", ".publication.lock", "logs",
        }, "stamp created more than metadata"
        logs = legacy / "logs"
        if logs.exists():
            assert set(p.name for p in logs.iterdir()) <= {"boot-trace.jsonl"}
            trace = logs / "boot-trace.jsonl"
            for line in trace.read_text().splitlines() if trace.is_file() else []:
                record = json.loads(line)
                assert record["event"] == "boot_trace" and record["source"] == "resolver", record
    else:
        assert not (home / ".agent-index").exists(), "legacy runtime was created"
        assert not (home / ".local/bin/agent-index").exists(), "global Index binstub was created"
    if root:
        for name in ("current-version", "deploy-manifest.json", "run/service-identity.json", "run/zdd/active.json"):
            assert not (root / name).exists(), f"unexpected runtime artifact: {name}"
    assert_no_engine(home)


def assert_no_engine(home: Path) -> None:
    for name in (".agent-index/engine", ".cache/huggingface", ".config/systemd/user/agent-index.service",
                 ".config/systemd/user/agent-index-engine.service"):
        assert not (home / name).exists(), f"unexpected engine/model/scheduler artifact: {name}"


def adopt(release: Path, payload: Path, key: str, version: str) -> None:
    home = Path.home()
    durable = home / ".copilot-extensions"
    stamped = context(
        payload, "stamp", "--source-json",
        json.dumps({"source": "directory", "path": str(release)}),
        "--marketplace-key", key, "--plugin-id", "agent-index",
        "--payload-root", str(payload), "--payload-version", version,
        "--payload-origin", "explicit", "--expected-namespace-generation", "0",
        "--expected-install-generation", "0", "--durable-home", str(durable),
    )
    receipt = str(stamped["installReceipt"])
    marketplace = str(stamped["marketplaceId"])
    common = ("--context", receipt, "--expected-marketplace-id", marketplace,
              "--expected-plugin-id", "agent-index", "--durable-home", str(durable))
    status = context(payload, "status", *common, "--payload-root", str(payload),
                     "--plugin-id", "agent-index", "--legacy-root", str(home / ".agent-index"))
    assert status["actualMode"] == "legacy", "default policy must not activate a cell"
    root = Path(receipt).parent
    assert_dormant(home, root, stamped=True)
    assert not (root / "installation-activation.json").exists()
    durable.mkdir(exist_ok=True)
    (durable / "installation-mode.json").write_text(json.dumps({
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True},
    }) + "\n", encoding="utf-8")
    # The legacy metadata stamp is present but inert, never declared absent.
    probe = {"declared": True, "result": "present",
             "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    active = context(
        payload, "activation-cas", *common,
        "--expected-namespace-generation", str(stamped["namespaceGeneration"]),
        "--expected-install-generation", str(stamped["generation"]),
        "--expected-activation-generation", "0",
        "--activation-mode", "namespaced", "--activation-state", "active",
        "--legacy-disposition", "retained-inert", "--legacy-probe-json", json.dumps(probe),
        "--legacy-root", str(home / ".agent-index"),
    )
    assert active["status"] == "ready", active
    validated = context(payload, "validate", *common, "--expected-payload-root", str(payload))
    (home / "cell-witness.json").write_text(json.dumps({
        "context": receipt, "marketplaceId": marketplace, "pluginRoot": validated["pluginRoot"],
    }) + "\n", encoding="utf-8")


def interpreter(root: Path) -> Path:
    manifest = read_object(root / "deploy-manifest.json")
    python = Path(manifest["runtime"]["interpreter"])
    assert root.is_absolute() and python.is_absolute(), "runtime paths must be absolute"
    assert ".." not in root.parts and ".." not in python.parts, "runtime path traversal"
    assert stat.S_ISDIR(root.lstat().st_mode), "runtime root is not an unlinked directory"
    resolved_root = root.resolve(strict=True)
    assert python.is_relative_to(root / "versions"), "interpreter is outside runtime versions"
    relative = python.relative_to(root / "versions")
    assert len(relative.parts) == 3 and relative.parts[1:] in {
        ("bin", "python"), ("Scripts", "python.exe"),
    }, "interpreter must name a version slot's venv python"
    expected = resolved_root
    directory = root
    for component in ("versions", *relative.parts[:-1]):
        directory /= component
        expected /= component
        assert stat.S_ISDIR(directory.lstat().st_mode), "runtime directory link or non-directory"
        assert directory.resolve(strict=True) == expected, "runtime directory identity escaped"
    # A genuine venv may link only its final python executable to the base runtime.
    assert python.is_file(), "runtime interpreter is missing or not a file"
    return python


def main(args: list[str]) -> None:
    action, *values = args
    if action == "field":
        value = read_object(Path(values[0]))
        for key in values[1].split("."):
            value = value[key]
        print(value)
    elif action == "interpreter":
        print(interpreter(Path(values[0])))
    elif action == "state":
        data = read_object(Path(values[0]))
        assert data["state"] == values[1], data
        if values[2]:
            assert data["runtime"]["state"] == values[2], data
        assert data["running"] is False, data
    elif action == "role":
        data = read_object(Path(values[0]))
        assert data["role"] == values[1], data
    elif action == "version":
        assert reports_version(Path(values[0]).read_text(), values[1])
    elif action == "candidate":
        print(verify_candidate(Path(values[0]), values[1]))
    elif action == "dormant":
        assert_dormant(Path(values[0]), Path(values[1]) if values[1] else None,
                       stamped=len(values) > 2 and values[2] == "stamped")
    elif action == "stamped":
        home, payload = map(Path, values)
        assert_dormant(home, None, stamped=True)
        root = home / ".agent-index"
        assert (root / "payload-dir").read_text().strip() == str(payload)
        assert (root / "stamped-version").read_text().strip() == read_object(payload / "plugin.json")["version"]
        assert os.access(home / ".local/bin/agent-index", os.X_OK)
        print("metadata and callable binstub only; no venv/deployment/service/engine/autostart")
    elif action == "provenance":
        release, payload = map(Path, values[:2])
        version = read_object(payload / "plugin.json")["version"]
        if values[2]:
            assert version == values[2], version
        for name in ("plugin.json", "scripts/install.sh", "scripts/install.ps1",
                     "scripts/installer-engine.sh", "scripts/installer-engine.ps1"):
            installed = (payload / name).read_bytes()
            assert installed == (release / "plugins/agent-index" / name).read_bytes(), name
            if "installer-engine." in name:
                assert installed == (release / "libs/installer-engine" / Path(name).name).read_bytes(), name
            print(name, hashlib.sha256(installed).hexdigest())
    elif action == "payload":
        payload = Path(values[0])
        settings = read_object(Path.home() / ".copilot/settings.json")
        enabled = [name for name, active in settings["enabledPlugins"].items() if active]
        assert len(enabled) == 1 and enabled[0].startswith("agent-index@"), enabled
        assert not (payload / "mcp/agent-index.yaml").exists()
        assert not (payload / "agents/agent-index.agent.md").exists()
        assert (payload / "scripts/emit-scope-binding.sh").is_file()
        assert (payload / "scripts/emit-scope-binding.ps1").is_file()
    elif action == "adopt":
        adopt(Path(values[0]), Path(values[1]), values[2], values[3])
    elif action == "partial":
        payload, receipt, marketplace, version, root = values
        runtime = version + "+host"
        validated = context(Path(payload), "validate", "--context", receipt,
                            "--expected-marketplace-id", marketplace,
                            "--expected-plugin-id", "agent-index",
                            "--expected-payload-root", payload,
                            "--durable-home", str(Path.home() / ".copilot-extensions"))
        snapshot = Path(validated["snapshotsRoot"]) / version
        # An owned slot requires a real immutable payload snapshot first. This
        # fixture publishes genuine provenance, never runtime completion.
        shutil.copytree(payload, snapshot)
        context(Path(payload), "snapshot-stamp", "--context", receipt,
                "--expected-marketplace-id", marketplace, "--expected-plugin-id", "agent-index",
                "--expected-namespace-generation", str(validated["namespaceGeneration"]),
                "--expected-install-generation", str(validated["generation"]),
                "--snapshot-id", version,
                "--durable-home", str(Path.home() / ".copilot-extensions"))
        context(Path(payload), "slot-provision", "--context", receipt,
                "--expected-marketplace-id", marketplace, "--expected-plugin-id", "agent-index",
                "--expected-payload-root", payload, "--expected-payload-version", version,
                "--snapshot-id", version, "--runtime-version", runtime,
                "--durable-home", str(Path.home() / ".copilot-extensions"))
        slot = Path(root) / "versions" / runtime
        subprocess.run(["uv", "venv", "--allow-existing", str(slot)], check=True, timeout=120)
        python = slot / "bin/python"
        probe = subprocess.run([str(python), "-I", "-c", "import agent_index"], capture_output=True, text=True, timeout=15)
        assert probe.returncode != 0 and "No module named" in probe.stderr, probe
        assert not (slot / ".install-complete.json").exists()
        assert not (Path(root) / "current-version").exists()
        print("owned genuine incomplete venv; import fails; no fabricated completion or selection")
    elif action == "runtime":
        root, version = Path(values[0]), values[1]
        python = interpreter(root)
        slot = python.parent.parent
        assert (slot / "pyvenv.cfg").is_file()
        assert (slot / ".install-complete.json").is_file()
        result = subprocess.run([str(python), "-I", "-c",
                                 "import sys,agent_index; print(sys.prefix); print(agent_index.__file__)"],
                                check=True, capture_output=True, text=True, timeout=30)
        prefix, origin = result.stdout.strip().splitlines()
        assert Path(prefix).resolve() == slot.resolve()
        assert Path(origin).resolve().is_relative_to(slot.resolve())
        result = subprocess.run([str(python), "-I", "-m", "agent_index", "--version"],
                                check=True, capture_output=True, text=True, timeout=30)
        assert reports_version(result.stdout, version), result.stdout
        print(result.stdout, prefix, origin)
    elif action == "health":
        root, marketplace, version = Path(values[0]), values[1], values[2] + "+host"
        identity = read_object(root / "run/service-identity.json")
        active = read_object(root / "run/zdd/active.json")["active"]
        port = int(active["port"])
        for route in ("health", "status"):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/{route}", timeout=5) as response:
                data = json.load(response)
                assert response.status == 200
            assert data["version"] == version and data["installationId"] == f"{marketplace}/agent-index", data
            assert identity["runtimeVersion"] == version
            assert identity["installationId"] == data["installationId"]
            assert isinstance(data["pid"], int) and data["instanceToken"], data
            if route == "health":
                assert data["status"] == "ok" and data["promoted"] is True and data["passive"] is False, data
            os.kill(int(data["pid"]), 0)
            print(route, json.dumps(data))
        instances = list((root / "run/instances").glob("*.json"))
        live = [read_object(p) for p in instances if read_object(p).get("state") == "active"]
        assert len(live) == 1, live
        assert live[0]["pid"] == data["pid"] and live[0]["instanceToken"] == data["instanceToken"], live
    elif action == "mcp":
        subprocess.run([values[0], "-I", "-c",
                        "import importlib.util; "
                        "assert importlib.util.find_spec('mcp') is None; print('MCP SDK absent')"],
                       check=True, timeout=30)
        result = subprocess.run([values[0], "-I", "-m", "agent_index", "mcp"],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "[stdio-mcp] extra" in result.stderr, result.stderr
        print("managed host refuses optional stdio MCP without installation")
    elif action == "engine-absent":
        assert_no_engine(Path(values[0]))
        subprocess.run([values[1], "-I", "-c",
                        "import importlib.util; assert importlib.util.find_spec('torch') is None; print('torch absent')"],
                       check=True, timeout=30)
    else:
        raise ValueError(f"unknown assertion: {action}")


if __name__ == "__main__":
    main(sys.argv[1:])
