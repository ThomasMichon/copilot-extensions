"""Separate host-wrapper invocations clean up only attributable registrations."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RIG = Path(__file__).resolve().parents[1]
IDENTITY = "a" * 64
REPLACEMENT = "b" * 64
NAME = "cr-base-cleanup-fixture"


@pytest.fixture(params=["bash", "powershell"])
def cleanup_lab(request, tmp_path):
    executable = shutil.which("bash" if request.param == "bash" else "pwsh")
    if os.name == "nt" or not executable:
        pytest.skip("POSIX fake Docker and selected host shell required")
    scenario = tmp_path / "scenario"
    scenario.mkdir()
    (scenario / "scenario.sh").write_text("exit 0\n", encoding="utf-8")
    (scenario / "manifest.json").write_text(
        json.dumps({"tier": "P", "auth": {"copilot": "none"}}), encoding="utf-8",
    )
    results = tmp_path / "results"
    results.mkdir()
    state = tmp_path / "docker-state.json"
    state.write_text("{}", encoding="utf-8")
    calls = tmp_path / "docker-calls.jsonl"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys
a = sys.argv[1:]
state = pathlib.Path(os.environ["DOCKER_STATE"])
containers = json.loads(state.read_text())
with open(os.environ["DOCKER_CALLS"], "a") as stream:
    stream.write(json.dumps(a) + "\\n")
def resolve(ref):
    matches = [(key, name) for key, name in containers.items()
               if ref == name or key.startswith(ref)]
    if len(matches) != 1:
        print("Error: No such object" if not matches else "ambiguous container ID", file=sys.stderr)
        sys.exit(1)
    return matches[0]
if a[0] == "images":
    print("cached-image")
elif a[0] == "run":
    containers["a" * 64] = "cr-base-cleanup-fixture"
    state.write_text(json.dumps(containers))
    print("a" * 64)
elif a[0] == "ps":
    for key, name in containers.items():
        if any("name=" in part for part in a):
            if name == "cr-base-cleanup-fixture":
                print(key)
        else:
            print(key[:12])  # Docker's default ps -q output is abbreviated.
elif a[0] == "inspect":
    key, name = resolve(a[-1])
    fmt = a[a.index("-f") + 1]
    if fmt == "{{.Id}}":
        print(key)
    elif fmt == "{{.Name}}":
        print("/" + name)
    else:
        print(key + " true")
elif a[0] == "rm":
    key, _ = resolve(a[-1])
    del containers[key]
    state.write_text(json.dumps(containers))
    print(key)
elif a[0] == "exec":
    pass
else:
    print("unexpected fake Docker command", a, file=sys.stderr)
    sys.exit(99)
''',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = {
        **os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "DOCKER_STATE": str(state), "DOCKER_CALLS": str(calls),
        "CR_RESULTS_DIR": str(results),
        "AGENT_BRIDGE_PROVIDERS_DIR": str(tmp_path / "providers.d"),
        "CLEAN_ROOM_DOCKER_COMMAND_JSON": json.dumps([sys.executable, str(docker)]),
    }

    class Lab:
        registrations = tmp_path / "cleanroom.d"
        manifest = tmp_path / "providers.d" / "cleanroom.json"

        def provider(self, *args):
            return subprocess.run(
                [sys.executable, str(RIG / "bridge_register.py"), *args],
                env=env, capture_output=True, text=True, timeout=30,
            )

        def wrapper(self, mode, *, bounded=False):
            if request.param == "bash":
                args = [executable, str(RIG / "run.sh"), "--scenario", str(scenario),
                        "--name-suffix", "cleanup-fixture"]
                if bounded:
                    args += ["--lifetime-seconds", "30"]
                args += [mode]
            else:
                args = [executable, "-NoProfile", "-File", str(RIG / "run.ps1"),
                        "-Scenario", str(scenario), "-NameSuffix", "cleanup-fixture", "-Mode", mode]
                if bounded:
                    args += ["-LifetimeSeconds", "30"]
            return subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)

        def containers(self, value=None):
            if value is not None:
                state.write_text(json.dumps(value), encoding="utf-8")
            return json.loads(state.read_text(encoding="utf-8"))

        def register(self, style="id"):
            if style in ("id", "both"):
                result = self.wrapper("bridge-register", bounded=True)
                assert result.returncode == 0, result.stderr
            else:
                self.containers({IDENTITY: NAME})
            if style in ("legacy", "both"):
                result = self.provider("register", "--container", NAME)
                assert result.returncode == 0, result.stderr

        def records(self):
            return {p.name: p.read_bytes() for p in self.registrations.glob("*.json")}

        def calls(self):
            return [json.loads(line) for line in calls.read_text().splitlines()]

    return Lab()


@pytest.mark.parametrize("route", ["bridge-unregister", "down", "prune"])
@pytest.mark.parametrize("style", ["id", "legacy", "both"])
def test_fresh_cleanup_process_handles_id_and_legacy_registration_keys(cleanup_lab, route, style):
    cleanup_lab.register(style)
    result = cleanup_lab.wrapper(route)  # A genuinely new process, without launch globals.
    assert result.returncode == 0, result.stderr
    assert cleanup_lab.records() == {}
    assert not cleanup_lab.manifest.exists()
    assert cleanup_lab.provider("namespace-resolve", IDENTITY).returncode == 3
    assert cleanup_lab.containers() == ({IDENTITY: NAME} if route == "bridge-unregister" else {})
    if route == "prune":
        removes = [call for call in cleanup_lab.calls() if call[0] == "rm"]
        assert removes == [["rm", "-f", IDENTITY]]


@pytest.mark.parametrize("reference,expected", [
    (IDENTITY, IDENTITY), (IDENTITY[:12], IDENTITY[:12]),
    (IDENTITY[:8], IDENTITY[:8]), (NAME, IDENTITY[:12]),
])
def test_adapter_resolves_full_and_abbreviated_ids_before_record_lookup(cleanup_lab, reference, expected):
    cleanup_lab.register("both")
    result = cleanup_lab.provider("unregister", "--container", reference, "--container-id", expected)
    assert result.returncode == 0, result.stderr
    assert cleanup_lab.records() == {}
    assert not cleanup_lab.manifest.exists()
    assert cleanup_lab.containers() == {IDENTITY: NAME}


@pytest.mark.parametrize("style", ["id", "legacy"])
def test_absent_original_requires_an_attributable_key_not_an_assumed_name(cleanup_lab, style):
    cleanup_lab.register(style)
    before = cleanup_lab.records()
    cleanup_lab.containers({})
    result = cleanup_lab.wrapper("bridge-unregister")
    if style == "id":
        assert result.returncode != 0
        assert cleanup_lab.records() == before
        assert cleanup_lab.manifest.exists()
        # A known full-ID key is sufficient for the existing stale operation.
        result = cleanup_lab.provider("unregister", "--container", IDENTITY, "--stale")
    assert result.returncode == 0, result.stderr
    assert cleanup_lab.records() == {}
    assert not cleanup_lab.manifest.exists()


def test_replacement_mismatch_keeps_all_original_and_replacement_records(cleanup_lab):
    cleanup_lab.register("both")
    cleanup_lab.containers({IDENTITY: "cr-owned-renamed", REPLACEMENT: NAME})
    before = cleanup_lab.records()
    result = cleanup_lab.wrapper("bridge-unregister")
    assert result.returncode != 0
    assert cleanup_lab.records() == before
    explicit = cleanup_lab.provider("unregister", "--container", NAME, "--container-id", IDENTITY)
    assert explicit.returncode == 2
    assert cleanup_lab.records() == before
    # Cleanup by the original full ID must not touch the new name-keyed owner.
    assert cleanup_lab.provider("register", "--container", NAME).returncode == 0
    new_name_record = (cleanup_lab.registrations / f"{NAME}.json").read_bytes()
    own = cleanup_lab.provider("unregister", "--container", IDENTITY, "--container-id", IDENTITY)
    assert own.returncode == 0, own.stderr
    assert (cleanup_lab.registrations / f"{NAME}.json").read_bytes() == new_name_record
    assert cleanup_lab.manifest.exists()


def test_ambiguous_abbreviated_expected_id_refuses_cleanup(cleanup_lab):
    cleanup_lab.register()
    before = cleanup_lab.records()
    cleanup_lab.containers({IDENTITY: NAME, "a" * 12 + "c" * 52: "cr-other"})
    result = cleanup_lab.provider("unregister", "--container", NAME, "--container-id", IDENTITY[:12])
    assert result.returncode == 2
    assert cleanup_lab.records() == before


def test_unreadable_id_record_refuses_all_alias_cleanup(cleanup_lab):
    cleanup_lab.register("both")
    (cleanup_lab.registrations / f"{IDENTITY}.json").write_text("{", encoding="utf-8")
    before = cleanup_lab.records()
    result = cleanup_lab.wrapper("bridge-unregister")
    assert result.returncode != 0
    assert cleanup_lab.records() == before
    assert cleanup_lab.manifest.exists()
