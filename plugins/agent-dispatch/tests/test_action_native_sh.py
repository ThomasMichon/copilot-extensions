"""Native maintenance inhibition at the real installer effect boundaries."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH or os.name == "nt", reason="native POSIX bash")


def _function(name: str) -> str:
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}\n", start) + 2]


def _run_boundary(tmp_path: Path, function: str, action: str) -> subprocess.CompletedProcess[str]:
    root = tmp_path / "install"
    root.mkdir()
    (root / "action-controls.json").write_text(json.dumps({action: {"disabled": True}}))
    marker = tmp_path / "effect"
    script = "\n".join([
        "set -eu",
        f"SCRIPT_DIR={str(SCRIPTS)!r}",
        f"INSTALL_DIR={str(root)!r}",
        "NO_SERVICE=0; NO_SUPERVISOR=1; ACTION=update",
        "SUPERVISOR_UNIT=test-supervisor; SUPERVISOR_ENV_FILE=unused.env",
        '_fail() { echo "$*" >&2; }',
        '_step() { :; }; _warn() { :; }; _ok() { :; }; _skip() { :; }',
        f"_bootstrap_python() {{ echo {sys.executable!r}; }}",
        '_write_update_marker() { :; }; _clear_update_marker() { :; }',
        '_lifecycle_finish() { :; }; _downgrade_guard() { :; }',
        f"_ensure_runtime() {{ touch {str(marker)!r}; return 17; }}",
        f"_remove_all_supervisor_units() {{ touch {str(marker)!r}; }}",
        "_supervisor_audit() { :; }",
        f". {str(SCRIPTS / 'source-runtime.sh')!r}",
        _function(function),
        function,
    ])
    return subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, timeout=10,
    )


@pytest.mark.parametrize("action", [
    "runtime-activation", "coordinator-replacement", "supervisor-reconcile",
])
def test_update_preflight_precedes_runtime_publication(tmp_path: Path, action: str) -> None:
    result = _run_boundary(tmp_path, "do_update", action)
    assert not (tmp_path / "effect").exists()
    assert result.returncode != 0
    assert action in result.stderr


def test_supervisor_inhibition_precedes_explicit_removal(tmp_path: Path) -> None:
    result = _run_boundary(tmp_path, "_install_supervisor_service", "supervisor-reconcile")
    assert not (tmp_path / "effect").exists()
    assert result.returncode != 0
    assert "supervisor-reconcile" in result.stderr


def _gate_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, dict[str, str]]:
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    helpers = scripts / "installation-context"
    helpers.mkdir(parents=True)
    home = tmp_path / "home"
    root = home / ".agent-dispatch"
    root.mkdir(parents=True)
    shutil.copytree(PLUGIN / "src", payload / "src")
    for name in ("runtime-gate.sh", "action_controls.py", "bootstrap-check.sh"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    (payload / "plugin.json").write_text('{"name":"agent-dispatch"}')
    shutil.copyfile(
        SCRIPTS / "installation-context" / "json-query.awk", helpers / "json-query.awk",
    )
    decision = {
        "status": "ready", "actualMode": "legacy", "desiredMode": "legacy",
        "installGeneration": "test-generation",
        "legacy": {"root": str(root), "disposition": "active", "tombstone": None},
        "environment": {"homeRealPath": str(home)}, "allowMutation": True,
        "deployment": {
            "allowPluginPublication": True, "path": str(root / "runtime-deployment.json"),
        },
    }
    (helpers / "installation-context.sh").write_text(
        "#!/bin/bash\ncat <<'DECISION'\n" + json.dumps(decision) + "\nDECISION\n",
    )
    started = tmp_path / "started"
    provisioned = tmp_path / "provisioned"
    python = tmp_path / "ready-python"
    python.write_text(f"#!/bin/bash\ntouch {str(started)!r}\n")
    python.chmod(0o755)
    (scripts / "resolve-runtime.sh").write_text(
        "# Trace contract: normal/read-only/ready-v1\n"
        f'if [[ "${{TEST_READY:-}}" == 1 ]]; then AGENT_RT_PY={str(python)!r}; fi\n'
        "true\n",
    )
    (scripts / "install.sh").write_text(
        "#!/bin/bash\ncase \"$1\" in\n"
        f"stamp) printf 'effect\\n' >> {str(provisioned)!r} ;;\n"
        f"*) printf 'effect\\n' >> {str(provisioned)!r} ;;\nesac\nexit 0\n",
    )
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
               AGENT_DISPATCH_INSTALL_DIR=str(root), AGENT_DISPATCH_PAYLOAD_ROOT=str(payload))
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "BOOTSTRAP_KILLSWITCH_STATE_FILE",
                 "AGENT_DISPATCH_NO_SELFPROVISION"):
        env.pop(name, None)
    return scripts / "runtime-gate.sh", root, started, provisioned, env


def test_warm_runtime_start_ignores_action_inhibition(tmp_path: Path) -> None:
    gate, root, started, provisioned, env = _gate_fixture(tmp_path)
    (root / "action-controls.json").write_text(
        '{"automatic-runtime-reconcile":{"disabled":true}}',
    )
    env["TEST_READY"] = "1"
    gate.with_name("action_controls.py").rename(gate.with_name("reader-not-installed.py"))
    result = subprocess.run(
        [BASH, str(gate), "serve"], env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert started.exists() and not provisioned.exists()
    assert not (root / ".provision.lock").exists()


def test_missing_cold_policy_reader_is_a_named_prerequisite_failure(tmp_path: Path) -> None:
    gate, root, started, provisioned, env = _gate_fixture(tmp_path)
    gate.with_name("action_controls.py").rename(gate.with_name("reader-not-installed.py"))
    result = subprocess.run(
        [BASH, str(gate), "serve"], env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 126 and "action reader is missing" in result.stderr
    assert not started.exists() and not provisioned.exists()
    assert not (root / ".provision.lock").exists()


@pytest.mark.parametrize("shared", [False, True])
def test_cold_runtime_denial_precedes_lock_status_fetch_and_installer(
    tmp_path: Path, shared: bool,
) -> None:
    gate, root, started, provisioned, env = _gate_fixture(tmp_path)
    if shared:
        state = Path(env["HOME"]) / ".copilot-extensions" / "bootstrap-killswitch.json"
        state.parent.mkdir()
        state.write_text('{"active":true}')
    else:
        state = root / "action-controls.json"
        state.write_text('{"automatic-runtime-reconcile":{"disabled":true}}')
    result = subprocess.run(
        [BASH, str(gate), "serve"], env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 3, result.stderr
    assert "automatic-runtime-reconcile" in result.stderr
    assert not started.exists() and not provisioned.exists()
    assert not (root / ".provision.lock").exists()
    assert not (root / ".provision-status").exists()
    state.write_text('{"active":false}' if shared else "{}")
    result = subprocess.run(
        [BASH, str(gate), "serve"], env=env, capture_output=True, text=True, timeout=15,
    )
    assert provisioned.exists(), result.stderr
    assert result.returncode != 0  # The effect fixture deliberately creates no qualified runtime.


def test_session_bootstrap_rechecks_before_automatic_stamp(tmp_path: Path) -> None:
    gate, root, started, provisioned, env = _gate_fixture(tmp_path)
    hook = gate.with_name("bootstrap-check.sh")
    effects = 0
    for inhibited in (True, False, True, False):
        (root / "action-controls.json").write_text(json.dumps({
            "automatic-runtime-reconcile": {"disabled": inhibited},
        }))
        result = subprocess.run(
            [BASH, str(hook)], env=env, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == (3 if inhibited else 0) and result.stdout == "{}", result.stderr
        if not inhibited:
            effects += 1
        assert (provisioned.read_text().count("effect") if provisioned.exists() else 0) == effects
    assert not started.exists()


def test_late_cutover_denial_never_falls_back_when_policy_clears(tmp_path: Path) -> None:
    root = tmp_path / "install"
    root.mkdir()
    policy = root / "action-controls.json"
    policy.write_text("{}")
    forbidden = tmp_path / "forbidden"
    script = "\n".join([
        "set -eu",
        f"INSTALL_DIR={str(root)!r}; SCRIPT_DIR={str(SCRIPTS)!r}",
        "NO_SERVICE=0; SUPERVISOR_UNIT=test-supervisor; SUPERVISOR_ENV_FILE=unused.env",
        '_fail() { echo "$*" >&2; }; _warn() { :; }; _ok() { :; }; _step() { :; }',
        '_write_update_marker() { :; }; _clear_update_marker() { :; }',
        '_lifecycle_finish() { :; }; _downgrade_guard() { :; }',
        '_activation_superseded_now() { return 1; }; _supervisor_audit() { :; }',
        f"_bootstrap_python() {{ echo {sys.executable!r}; }}",
        f". {str(SCRIPTS / 'source-runtime.sh')!r}",
        "_ensure_runtime() { printf '%s' "
        f"""'{{"coordinator-replacement":{{"disabled":true}}}}' > {str(policy)!r}; }}""",
        f"_install_service() {{ touch {str(forbidden)!r}; }}",
        f"_install_supervisor_service() {{ touch {str(forbidden)!r}; }}",
        _function("_coordinator_cutover").replace(
            "_coordinator_cutover()", "_actual_coordinator_cutover()", 1,
        ),
        "_coordinator_cutover() { local result=0; _actual_coordinator_cutover || result=$?; "
        f"printf '{{}}' > {str(policy)!r}; return \"$result\"; }}",
        _function("do_update"),
        "do_update",
    ])
    result = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 3, result.stderr
    assert "after runtime publication" in result.stderr
    assert not forbidden.exists()
    assert policy.read_text() == "{}"


@pytest.mark.parametrize("state", ["absent", "present", "linked-parent"])
def test_python_free_explicit_absence_is_proven_not_assumed(
    tmp_path: Path, state: str,
) -> None:
    root = tmp_path / "install"
    root.mkdir()
    if state == "present":
        (root / "action-controls.json").write_text("{}")
    elif state == "linked-parent":
        linked = tmp_path / "linked"
        linked.symlink_to(root, target_is_directory=True)
        root = linked
    script = "\n".join([
        "set -eu",
        f"INSTALL_DIR={str(root)!r}; SCRIPT_DIR={str(SCRIPTS)!r}",
        '_fail() { echo "$*" >&2; }; _bootstrap_python() { return 1; }',
        f". {str(SCRIPTS / 'source-runtime.sh')!r}",
        "_require_actions runtime-activation",
    ])
    result = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == (0 if state == "absent" else 3), result.stderr
    if state != "absent":
        assert "no bootstrap Python" in result.stderr
