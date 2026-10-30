"""Cold isolated import regression; opt-in real subprocess, not a promotion unit."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
RUNNER = Path(__file__).with_name("sdk_visibility.py")


def test_isolated_older_runner_cannot_qualify_another_sdk_root(tmp_path):
    helper_root = tmp_path / "older-sdk/libs/agent-procutil/tests"
    helper_root.mkdir(parents=True)
    for name in (
        "sdk_visibility.py", "sdk_desktop_observer.py", "sdk_desktop_win32.py",
        "sdk_visibility_sources.py", "sdk_visibility_cases.py",
    ):
        shutil.copyfile(RUNNER.with_name(name), helper_root / name)
    script = """
import hashlib, json, runpy, sys, time
from pathlib import Path
runner, source, output = sys.argv[1:]
namespace = runpy.run_path(runner)
binding = sys.modules['sdk_visibility_sources']
sdk = Path(source)
sources = []
for relative in binding.SDK_FILES:
    path = sdk / relative
    data = path.read_bytes()
    sources.append({'path': str(path), 'bytes': len(data),
                    'sha256': hashlib.sha256(data).hexdigest()})
cases = sys.modules['sdk_visibility_cases']
nodes = [
    str(sdk / cases.NATIVE_TEST) + '::'
    + f'{cases.FUNCTION}[{cycle}-{mode}-{composition}-powershell.exe]'
    for cycle in (0, 1)
    for mode in ('completed', 'abrupt', 'caller-exit', 'cancel')
    for composition in ('path', 'test-venv')
]
def forbidden():
    raise AssertionError('mixed checkout reached a native binding')
namespace['worker'].__globals__['new_api'] = forbidden
request = {'nonce': 'a' * 32, 'deadline': time.monotonic() + 10,
           'parent_pid': 1, 'parent_birth': 2, 'sdk_root': source,
           'output': output, 'sources': sources, 'nodes': nodes, 'packet': 0}
for invoke in (
    lambda: namespace['validate_request']('worker', request),
    lambda: namespace['worker'](request),
):
    try:
        invoke()
    except namespace['ObservationBlocked'] as error:
        assert 'runner belongs to another SDK root' in str(error)
    else:
        raise AssertionError('mixed-root runner was accepted')
assert 'sdk_desktop_win32' not in sys.modules
assert not Path(output).exists()
print(json.dumps({'kind': 'mixed-root-refused', 'native_binding': False}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script,
         str(helper_root / RUNNER.name), str(ROOT), str(tmp_path / "output")],
        capture_output=True, text=True, timeout=10,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"kind": "mixed-root-refused", "native_binding": False}


def test_isolated_worker_collects_against_verified_source_not_ambient(tmp_path):
    ambient = tmp_path / "ambient/agent_procutil"
    ambient.mkdir(parents=True)
    (ambient / "__init__.py").write_text("raise AssertionError('unfrozen ambient package')\n")
    collected = tmp_path / "test_bound_source.py"
    expected = ROOT / "libs/agent-procutil/src/agent_procutil/__init__.py"
    collected.write_text(
        "from pathlib import Path\n"
        "import agent_procutil\n"
        "def test_exact_import():\n"
        f"    assert Path(agent_procutil.__file__).resolve() == Path({str(expected)!r})\n",
    )
    config = tmp_path / "pytest.ini"
    config.write_text("")
    script = """
import hashlib, json, os, runpy, sys
from pathlib import Path
from types import SimpleNamespace
runner, root, ambient, test, config = sys.argv[1:]
assert sys.flags.isolated and 'agent_procutil' not in sys.modules
namespace = runpy.run_path(runner)
binding = sys.modules['sdk_visibility_sources']
sdk = Path(root)
sources = []
for relative in binding.SDK_FILES:
    path = sdk / relative
    data = path.read_bytes()
    sources.append({'path': str(path), 'bytes': len(data),
                    'sha256': hashlib.sha256(data).hexdigest()})
namespace['require_sdk_sources'](sdk, sources, runner=Path(runner))
namespace['verify_sources'](sources)
sys.path.insert(0, ambient)
state = namespace['worker'].__globals__
state['new_api'] = lambda: SimpleNamespace(
    require_desktop=lambda: None,
    require_parent_pipe=lambda request: SimpleNamespace(gone=lambda: False, close=lambda: None),
    identity=lambda: {'pid': 1, 'birth': 2},
)
state['command'] = lambda *args: 'run'
case_contract = sys.modules['sdk_visibility_cases']
nodes = [
    str(sdk / case_contract.NATIVE_TEST) + '::'
    + f'{case_contract.FUNCTION}[0-{mode}-{composition}-C:\\\\PowerShell\\\\pwsh.EXE]'
    for mode in ('completed', 'abrupt', 'caller-exit', 'cancel')
    for composition in ('path', 'test-venv')
]
nodes += [node.replace('[0-', '[1-') for node in nodes]
def arguments(root, output, cases, packet):
    return ['-q', '--noconftest', '-p', 'no:cacheprovider', '-c', config, test]
state['native_arguments'] = arguments
request = {'sdk_root': root, 'sources': sources, 'output': str(Path(config).parent),
           'nonce': 'a' * 32, 'deadline': 1, 'nodes': nodes, 'packet': 0}
code = namespace['worker'](request)
assert code == 0
assert Path(sys.modules['agent_procutil'].__file__).resolve() == (
    sdk / 'libs/agent-procutil/src/agent_procutil/__init__.py'
).resolve()
import pytest
code = pytest.main([
    '-q', '--noconftest', '-p', 'no:cacheprovider', '-c', config, '--collect-only',
    str(sdk / case_contract.NATIVE_TEST) + '::' + case_contract.FUNCTION,
])
print(json.dumps({'kind': 'cold-bound-import', 'pytest_exit': code}), flush=True)
raise SystemExit(code)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(RUNNER), str(ROOT),
         str(ambient.parent), str(collected), str(config)],
        capture_output=True, text=True, timeout=20,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "kind": "cold-bound-import", "pytest_exit": 0,
    }


def test_native_packet_metadata_has_source_identity_with_external_config(tmp_path):
    sdk = tmp_path / "synthetic-sdk"
    module = sdk / "libs/agent-procutil/tests/test_shell_caller_owned_native.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('host', ['powershell.exe', 'pwsh.exe'])\n"
        "@pytest.mark.parametrize('composition', ['path', 'test-venv'])\n"
        "@pytest.mark.parametrize('mode', ['completed', 'abrupt', 'caller-exit', 'cancel'])\n"
        "@pytest.mark.parametrize('cycle', [0])\n"
        "def test_sdk_scope_retires_before_observer_cleanup(host, composition, mode, cycle):\n"
        "    assert cycle == 0\n",
    )
    script = """
import json, os, runpy, sys
from pathlib import Path
import xml.etree.ElementTree as ET
runner, source, output, mode = sys.argv[1:]
namespace = runpy.run_path(runner)
contract = sys.modules['sdk_visibility_cases']
sdk, directory = Path(source), Path(output)
nodes = [
    str(sdk / contract.NATIVE_TEST) + '::'
    + f'{contract.FUNCTION}[0-{outcome}-{composition}-{host}]'
    for outcome in ('completed', 'abrupt', 'caller-exit', 'cancel')
    for composition in ('path', 'test-venv')
    for host in ('powershell.exe', 'pwsh.exe')
]
arguments = namespace['native_arguments'](sdk, directory, nodes, 0)
if mode == 'old-root':
    index = arguments.index('--rootdir')
    del arguments[index:index + 2]
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
import pytest
code = pytest.main(arguments)
assert code == 0
xml = directory / 'native-01.xml'
cases = ET.parse(xml).findall('.//testcase')
assert len(cases) == 16
assert {case.attrib['name'] for case in cases} == {node.partition('::')[2] for node in nodes}
if mode == 'old-root':
    assert all(case.attrib['classname'] == '' for case in cases)
    try:
        contract.verify_packet_junit(xml, nodes)
    except namespace['ObservationBlocked']:
        pass
    else:
        raise AssertionError('Strict source identity unexpectedly accepted an empty classname')
else:
    assert all(case.attrib['classname'] == contract.NATIVE_CLASSNAME for case in cases)
    assert contract.verify_packet_junit(xml, nodes)['case_count'] == 16
print(json.dumps({'mode': mode, 'case_count': 16, 'pytest_exit': code}), flush=True)
"""
    for mode in ("old-root", "explicit-sdk-root"):
        output = tmp_path / mode
        output.mkdir()
        (output / "pytest.ini").write_text("")
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(RUNNER), str(sdk), str(output), mode],
            capture_output=True, text=True, timeout=20,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout.splitlines()[-1]) == {
            "mode": mode, "case_count": 16, "pytest_exit": 0,
        }


def test_all_four_packets_retain_tmp_evidence_until_reconciliation(tmp_path: Path) -> None:
    sdk = tmp_path / "synthetic-sdk"
    module = sdk / "libs/agent-procutil/tests/test_shell_caller_owned_native.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "import json\n"
        "import pytest\n"
        "def record(tmp_path, request):\n"
        "    (tmp_path / 'ready.json').write_text(json.dumps({'name': request.node.name}))\n"
        "@pytest.mark.parametrize('host', ['powershell.exe', 'pwsh.exe'])\n"
        "@pytest.mark.parametrize('composition', ['path', 'test-venv'])\n"
        "@pytest.mark.parametrize('mode', ['completed', 'abrupt', 'caller-exit', 'cancel'])\n"
        "@pytest.mark.parametrize('cycle', [0, 1])\n"
        "def test_sdk_scope_retires_before_observer_cleanup("
        "tmp_path, request, host, composition, mode, cycle):\n"
        "    record(tmp_path, request)\n"
        "@pytest.mark.parametrize('mode', ["
        "'no-permission', 'wrong-permission', 'abort', 'write-abort', "
        "'write-eof', 'wrong-ack', 'missing-ack', 'adopted'])\n"
        "def test_native_offer_failure_preserves_parent_handle_custody(tmp_path, request, mode):\n"
        "    record(tmp_path, request)\n"
        "@pytest.mark.parametrize('host', ['powershell.exe', 'pwsh.exe'])\n"
        "@pytest.mark.parametrize('failure', ['eof', 'malformed'])\n"
        "def test_startup_receipt_failure_releases_input_before_waiting_for_worker("
        "tmp_path, request, failure, host):\n"
        "    record(tmp_path, request)\n",
    )
    namespace = runpy.run_path(str(RUNNER))
    contract = sys.modules["sdk_visibility_cases"]
    sdk_nodes = [
        str(module) + "::" + f"{contract.FUNCTION}[{cycle}-{mode}-{composition}-{host}]"
        for cycle in (0, 1)
        for mode in ("completed", "abrupt", "caller-exit", "cancel")
        for composition in ("path", "test-venv")
        for host in ("powershell.exe", "pwsh.exe")
    ]
    packets = [
        sdk_nodes[:16], sdk_nodes[16:],
        [str(module) + "::" + f"{contract.OFFER_FUNCTION}[{mode}]" for mode in contract.OFFER_MODES],
        [
            str(module) + "::" + f"{contract.STARTUP_FUNCTION}[{failure}-{host}]"
            for failure in ("eof", "malformed")
            for host in ("powershell.exe", "pwsh.exe")
        ],
    ]
    contract.validate_case_packets(sdk, packets)
    output, temporary = tmp_path / "output", tmp_path / "shared-packet-temp"
    output.mkdir()
    temporary.mkdir()
    (output / "pytest.ini").write_text("")
    environment = {
        **os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TMPDIR": str(temporary), "TMP": str(temporary), "TEMP": str(temporary),
        "PYTEST_DEBUG_TEMPROOT": str(temporary),
        "PYTEST_ADDOPTS": "-o tmp_path_retention_count=0 -o tmp_path_retention_policy=failed",
    }
    script = """
import json, sys
arguments = json.loads(sys.argv[1])
assert [
    arguments[index + 1] for index, value in enumerate(arguments) if value == '-o'
] == ['tmp_path_retention_count=4', 'tmp_path_retention_policy=all']
import pytest
raise SystemExit(pytest.main(arguments))
"""
    retained: dict[Path, bytes] = {}
    expected: set[str] = set()
    for index, nodes in enumerate(packets):
        arguments = namespace["native_arguments"](sdk, output, nodes, index)
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, json.dumps(arguments)],
            capture_output=True, text=True, timeout=20, env=environment,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert all(path.read_bytes() == data for path, data in retained.items())
        expected.update(node.partition("::")[2] for node in nodes)
        retained = {path: path.read_bytes() for path in temporary.rglob("ready.json")}
        assert len(retained) == len(expected)
        assert {json.loads(data)["name"] for data in retained.values()} == expected
    assert len(retained) == 44
    assert namespace["verify_native_junit"](output, sdk, packets)["case_count"] == 44
