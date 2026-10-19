"""A normally installed PR payload is both an actual same-cell caller and peer."""

import importlib.util
import json
import os
import shutil
import subprocess
import venv
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from test_cell_payload_upgrade import native_profile as native_profile
from test_watch_lease_refusal import _completed_normal_slot

from agent_pull_requests import _installation_context as api

ROOT = Path(__file__).resolve().parents[3]
_FORWARD = """
import subprocess, sys, time
from pathlib import Path
from agent_procutil import no_window_kwargs
from agent_pull_requests import __main__ as cli
from agent_pull_requests.watch_invocation import WatchInvocation
trace = Path(sys.argv[1])
def mark(phase):
    with trace.open("a", encoding="utf-8") as handle:
        handle.write(f"{phase} {time.monotonic()}\\n")
mark("capture")
cli._WATCH_INVOCATION = WatchInvocation.capture()
assert cli._WATCH_INVOCATION is not None
mark("prefix")
prefix = cli._agent_worktrees_prefix()
mark("launch")
result = subprocess.call(
    [*prefix, "streams"], stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
    **no_window_kwargs(),
)
mark("returned")
cli._WATCH_INVOCATION.require_current()
mark("validated")
raise SystemExit(result)
"""


def _worktrees_backend(root, installation, profile):
    payload = root / "m" / "plugins" / "agent-worktrees"
    scripts = payload / "scripts"
    primitives = scripts / "installation-context"
    primitives.mkdir(parents=True)
    source = ROOT / "plugins" / "agent-worktrees" / "scripts"
    for name in ("resolve-runtime.ps1", "resolve-runtime.sh"):
        shutil.copyfile(source / name, scripts / name)
    for path in (source / "installation-context").glob("*.py"):
        shutil.copyfile(path, primitives / path.name)
    stamped = api.stamp_context(
        payload_root=payload, plugin_id="agent-worktrees",
        durable_home=installation.durable_home, payload_version="1.0.0", payload_origin="explicit",
        source_descriptor={"source": "directory", "path": str(root / "m")},
        marketplace_key="fixture",
        expected_namespace_generation=installation.namespace_generation,
        expected_install_generation=0, environment={},
    )
    assert stamped["marketplaceId"] == installation.marketplace_id
    api.compare_and_swap_activation(
        context=stamped["installReceipt"], durable_home=installation.durable_home,
        expected_marketplace_id=installation.marketplace_id, expected_plugin_id="agent-worktrees",
        expected_namespace_generation=stamped["namespaceGeneration"],
        expected_install_generation=stamped["generation"], expected_activation_generation=0,
        activation_mode="namespaced", activation_state="active", legacy_disposition="absent",
        legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
        environment={}, os_profile=profile,
    )
    own = Path(stamped["pluginRoot"])
    slot = own / "versions" / "1.0.0"
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = subprocess.run(  # noqa: S603 -- exact test-owned backend venv
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True, text=True, check=True, timeout=15, **no_window_kwargs(),
    )
    package = Path(result.stdout.strip()) / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    observed = root / "observed.json"
    (package / "__main__.py").write_text(f"""
import json, os, sys
from pathlib import Path
Path({str(observed)!r}).write_text(json.dumps({{
    "context": os.environ.get("COPILOT_EXTENSIONS_CONTEXT"),
    "runtimeRoot": os.environ.get("AGENT_RT_ROOT"),
    "foreignPrHome": os.environ.get("AGENT_PULL_REQUESTS_HOME"),
    "tokensInherited": any(name in os.environ for name in ("GH_TOKEN", "GITHUB_TOKEN")),
    "argv": sys.argv[1:], "isolated": sys.flags.isolated, "utf8": sys.flags.utf8_mode,
}}), encoding="utf-8")
assert sys.argv[1:] == ["streams"]
print(sys.stdin.read(), end="")
print("peer-stderr", file=sys.stderr)
raise SystemExit(7)
""", encoding="utf-8")
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.0.0", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
    }), encoding="utf-8")
    (own / api.CURRENT_VERSION_FILE).write_text("1.0.0", encoding="utf-8")
    return own, payload, observed


@pytest.mark.portfolio_tier("T3")
@pytest.mark.timeout(600)
@pytest.mark.skipif(
    os.name != "nt" and os.environ.get("APR_NATIVE_OS_ACCOUNT_ACCEPTANCE") != "1",
    reason="native POSIX account acceptance was not explicitly selected",
)
def test_normal_payload_peer_roundtrip_uses_same_cell_and_immutable_boundary(
    tmp_path_factory, native_profile,
):
    root = tmp_path_factory.mktemp("r")
    profile = root / "p" if native_profile is None else native_profile
    installation, runtime, python, _ = _completed_normal_slot(
        root, profile, substitute_profile=native_profile is None,
    )
    own, payload, observed = _worktrees_backend(root, installation, profile)
    runtime.require_current(installation, environment={})
    snapshot = Path(installation.evidence()["snapshotsRoot"]) / runtime.snapshot_id
    digest = api._snapshot_content_sha256(snapshot)
    protected_paths = [
        *(installation.plugin_root / name for name in (
            api.CURRENT_VERSION_FILE, api.LAST_KNOWN_GOOD_FILE, "deploy-manifest.json",
        )),
        installation.run_root / "watch-owner-transaction.json",
    ]
    protected = {path: path.read_bytes() for path in protected_paths}
    foreign = root / "foreign"
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "COPILOT_EXTENSIONS_CONTEXT"}
    }
    if native_profile is None:
        environment.update(HOME=str(profile), USERPROFILE=str(profile))
    environment.update(
        COPILOT_EXTENSIONS_CONTEXT=str(installation.receipt),
        COPILOT_PLUGIN_ROOT=str(installation.payload_root),
        AGENT_RT_ROOT=str(installation.plugin_root),
        AGENT_PULL_REQUESTS_HOME=str(foreign),
    )
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        environment[name] = "fixture-must-not-reach-peer"
    seed = 'quoted "seed"\nspace and \u96ea \U0001f680\n'
    trace = root / "trace.txt"
    try:
        forward = subprocess.run(  # noqa: S603 -- completed PR caller and owned echo backend
            [str(python), "-I", "-X", "utf8", "-c", _FORWARD, str(trace)], env=environment,
            input=seed, capture_output=True, text=True, encoding="utf-8", timeout=60,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as error:
        phases = trace.read_text("utf-8") if trace.exists() else "not started"
        raise AssertionError(
            f"Owned peer roundtrip timed out: {phases}; backend_entered={observed.exists()}; "
            f"stdout={error.stdout!r}; stderr={error.stderr!r}"
        ) from error
    assert forward.returncode == 7, forward.stderr
    assert forward.stdout == seed and forward.stderr == "peer-stderr\n"
    assert json.loads(observed.read_text("utf-8")) == {
        "context": str(own / "install.json"), "runtimeRoot": str(own),
        "foreignPrHome": None, "tokensInherited": False,
        "argv": ["streams"], "isolated": 1, "utf8": 1,
    }
    boundary_path = (
        ROOT / "plugins" / "agent-worktrees" / "src" / "agent_worktrees" / "_peer_launch.py"
    )
    spec = importlib.util.spec_from_file_location("fixture_worktrees_peer", boundary_path)
    boundary = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(boundary)
    prefix = boundary.launch_prefix(
        "agent-worktrees", own, str(own / "install.json"), "agent-pull-requests",
    )
    environment.update(
        COPILOT_EXTENSIONS_CONTEXT=str(own / "install.json"),
        COPILOT_PLUGIN_ROOT=str(payload), AGENT_RT_ROOT=str(own),
    )
    reverse = subprocess.run(  # noqa: S603 -- canonical boundary enters the actual PR module
        [*prefix, "watch", "health", "--json"], env=environment,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
        timeout=60, **no_window_kwargs(),
    )
    assert reverse.returncode == 1, reverse.stderr
    assert json.loads(reverse.stdout) == {"error": "no committed installation-local watch owner"}
    for damage in ("current", "manifest"):
        marker = installation.plugin_root / api.CURRENT_VERSION_FILE
        manifest_path = installation.plugin_root / "deploy-manifest.json"
        try:
            if damage == "current":
                marker.unlink()
            else:
                manifest = json.loads(protected[manifest_path])
                manifest["source"]["content_hash"] = "0" * 64
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            refused = subprocess.run(  # noqa: S603 -- same actual peer entry, damaged owned metadata
                [*prefix, "watch", "health", "--json"], env=environment,
                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                timeout=60, **no_window_kwargs(),
            )
            assert refused.returncode != 0
            assert not refused.stdout
            assert (
                "markers are incomplete" if damage == "current" else "manifest differs"
            ) in refused.stderr
        finally:
            marker.write_bytes(protected[marker])
            manifest_path.write_bytes(protected[manifest_path])
    assert api._snapshot_content_sha256(snapshot) == digest
    runtime.require_current(installation, environment={})
    assert {path: path.read_bytes() for path in protected_paths} == protected
    assert not installation.state_root.exists()
    assert not (installation.run_root / "active.json").exists()
    assert not foreign.exists()
