"""Containers' actual cold commands must admit publication before state writes."""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from test_adopter_publication import _adopter, _command
from test_bridge_first_use_publication import _initialize_shell_profiles, _run, _selected_cell
from test_command_binding import (
    POWERSHELL, _snapshot, binding_fixture as binding_fixture,
    powershell_host as powershell_host,
)
from test_installer_publication import _policy
from test_native_publication import _completed_slot
from test_simple_first_use_publication import _release_payload

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.timeout(60)]


@pytest.fixture(scope="module")
def containers_release(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _release_payload(tmp_path_factory.mktemp("containers-release"), "agent-containers")


@pytest.mark.parametrize(("scope", "state", "entry"), [
    (scope, state, "payload")
    for scope in ("legacy", "selected-cell")
    for state in (
        "managed", "invalid", "foreign", "future", "missing", "standalone",
        "healthy-managed", "healthy-invalid", "healthy-exit",
    )
] + [
    ("legacy", state, "global")
    for state in (
        "managed", "invalid", "foreign", "future", "missing", "standalone",
        "healthy-managed", "healthy-invalid", "healthy-exit",
    )
] + [
    ("selected-cell", state, "global")
    for state in ("managed", "standalone", "healthy-managed")
] + [
    ("legacy", state, "payload")
    for state in ("missing-helper", "old-helper", "missing-resolver", "old-resolver")
] + [
    ("legacy", state, "global")
    for state in ("missing-helper", "old-resolver")
] + [
    ("legacy", "opt-out", entry) for entry in ("payload", "global")
] + [
    ("legacy", "failed-resolver", entry) for entry in ("payload", "global")
] + [
    (scope, "policy-under-lock", "payload") for scope in ("legacy", "selected-cell")
] + [
    ("legacy", "mode-under-lock", entry) for entry in ("payload", "global")
] + [
    ("legacy", "standalone-stderr", entry) for entry in ("payload", "global")
] + [
    ("legacy", state, entry)
    for state in ("standalone-unicode", "healthy-unicode")
    for entry in ("payload", "global")
] + [
    pytest.param(
        scope, state, "compat-global",
        marks=pytest.mark.skipif(
            os.name == "nt" or POWERSHELL is None,
            reason="PowerShell installer's POSIX compatibility emitter",
        ),
    )
    for scope, state in (
        ("legacy", "managed"), ("legacy", "standalone"), ("legacy", "healthy-managed"),
        ("legacy", "standalone-unicode"), ("legacy", "healthy-unicode"),
        ("selected-cell", "standalone"),
    )
] + [
    ("legacy", state, "unavailable")
    for state in ("managed", "standalone", "healthy-managed")
] + [
    ("legacy", state, "no-pointer")
    for state in ("standalone", "healthy-managed")
] + [
    pytest.param(
        "legacy", "standalone-no-flock", entry,
        marks=pytest.mark.skipif(os.name == "nt", reason="POSIX fallback provisioning lock"),
    )
    for entry in ("payload", "global")
])
def test_containers_actual_first_use_publication(
    binding_fixture: dict, containers_release: Path, scope: str, state: str,
    entry: str, powershell_host,
) -> None:
    fixture = binding_fixture
    environment = _adopter(fixture, "agent-containers", containers_release)
    if state.endswith("-unicode"):
        root = fixture["home"] / "custom-\u00e9\u6d4b\u8bd5"
        root.mkdir()
        fixture["legacy"] = root
        fixture["arguments"]["legacy_root"] = root
    for key in (
        "COPILOT_PLUGIN_INSTALL_SMOKE", "AGENT_CONTAINERS_NO_SELFPROVISION",
        "COPILOT_EXTENSIONS_BOOT_TRACE_LOG_PATH", "COPILOT_EXTENSIONS_BOOT_TRACE",
    ):
        environment.pop(key, None)
    environment["AGENT_CONTAINERS_HOME"] = str(fixture["legacy"])
    environment["AGENT_CONTAINERS_STATE_DIR"] = str(fixture["legacy"])
    if state == "standalone-no-flock":
        environment["COPILOT_EXT_NO_FLOCK"] = "1"
    if os.name == "nt":
        environment.pop("PSModulePath", None)
        environment["LOCALAPPDATA"] = str(fixture["home"] / "AppData/Local")
        environment["APPDATA"] = str(fixture["home"] / "AppData/Roaming")
    profiles = _initialize_shell_profiles(fixture, environment)

    def snapshot_home() -> dict:
        return {name: value for name, value in _snapshot(fixture["home"]).items()
                if name not in profiles}

    shell = "powershell" if os.name == "nt" else "bash"
    suffix = "ps1" if os.name == "nt" else "sh"
    retained = None
    if entry != "payload":
        stamp_shell = "powershell" if entry == "compat-global" else shell
        stamp = [*_command(fixture, stamp_shell, "init"), "stamp"]
        stamp += ["-InstallDir" if stamp_shell == "powershell" else "--install-dir",
                  str(fixture["legacy"])]
        stamped = _run(fixture, environment, stamp, None)
        assert stamped.returncode == 0, stamped.stdout + stamped.stderr
        if entry == "compat-global":
            assert b"\r" not in (fixture["home"] / ".local/bin/agent-containers").read_bytes()
        retained = Path((fixture["legacy"] / "payload-dir").read_text(encoding="utf-8-sig").strip())
        assert (retained / "scripts" / f"runtime-gate.{suffix}").is_file()

    root, arguments = fixture["legacy"], fixture["arguments"]
    if scope == "selected-cell":
        root, arguments = _selected_cell(fixture, environment)
    healthy = state.startswith("healthy-")
    standalone = state in {
        "missing", "standalone", "standalone-stderr", "standalone-no-flock", "policy-under-lock",
        "standalone-unicode",
        "mode-under-lock",
    }
    policy_state = (
        "missing" if state == "missing" else
        "standalone" if standalone else
        "invalid" if state == "healthy-invalid" else
        state if state in {"invalid", "foreign", "future"} else "managed"
    )
    _policy(fixture, root, arguments, policy_state)
    if scope == "selected-cell":
        _policy(fixture, fixture["legacy"], fixture["arguments"],
                "managed" if standalone else "standalone")

    roots = [fixture["payload"]]
    if retained is not None and retained != fixture["payload"]:
        roots.append(retained)
    quote = lambda path: "'" + str(path).replace("'", "''") + "'"
    for payload in roots:
        installer = payload / "scripts" / f"init.{suffix}"
        installer.write_text(
            "param([string]$Action,[string]$InstallDir)\n"
            + ("[Console]::Error.WriteLine('recorded installer diagnostic')\n"
               if state == "standalone-stderr" else "")
            + "$record=@{action=$Action;root=$InstallDir;"
            "environmentRoot=$env:AGENT_CONTAINERS_HOME}|ConvertTo-Json -Compress\n"
            "[IO.File]::WriteAllText((Join-Path $InstallDir 'installer-ran.json'),$record)\n"
            "exit 37\n"
            if os.name == "nt" else
            "#!/usr/bin/env bash\n"
            '[[ "$1" == provision && "$2" == --install-dir && '
            '"$3" == "$AGENT_CONTAINERS_HOME" ]] || exit 98\n'
            + ('printf "recorded installer diagnostic\\n" >&2\n'
               if state == "standalone-stderr" else "")
            + 'printf reached > "$3/installer-ran"\nexit 37\n',
            encoding="utf-8-sig" if os.name == "nt" else "utf-8",
        )

    if healthy:
        python = _completed_slot({**fixture, "legacy": root})
        site = python.parent.parent / (
            "Lib/site-packages" if os.name == "nt" else
            f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
        (site / "agent_containers.py").write_text(
            "import json,os,sys\n"
            "print(json.dumps({'arguments':sys.argv[1:],"
            "'root':os.environ['AGENT_CONTAINERS_HOME'],"
            "'state':os.environ['AGENT_CONTAINERS_STATE_DIR'],'prefix':sys.prefix}))\n"
            + ('print("recorded runtime diagnostic",file=sys.stderr)\nsys.exit(29)\n'
               if state == "healthy-exit" else ""),
            encoding="utf-8",
        )
    if state in {"missing-helper", "old-helper", "missing-resolver", "old-resolver"}:
        for payload in roots:
            target = payload / "scripts" / (
                f"installation-context/installation-context.{suffix}"
                if state.endswith("helper") else f"resolve-runtime.{suffix}"
            )
            if state.startswith("missing-"):
                target.rename(target.with_name(target.name + ".unavailable"))
            else:
                original = target.read_text(encoding="utf-8-sig")
                token = (
                    "probe-plugin-publication" if state == "old-helper" else
                    "# Trace contract: normal/read-only/ready-v1"
                )
                assert token in original
                target.write_text(
                    original.replace(token, "retired-publication-capability"),
                    encoding="utf-8-sig" if os.name == "nt" else "utf-8",
                )
    if state == "opt-out":
        environment["AGENT_CONTAINERS_NO_SELFPROVISION"] = "1"
    if state == "failed-resolver":
        for payload in roots:
            (payload / "scripts" / f"resolve-runtime.{suffix}").write_text(
                "# Trace contract: normal/read-only/ready-v1\n"
                + ("param([string]$TraceMode='normal')\nthrow 'recorded resolver failure'\n"
                   if os.name == "nt" else "return 19\n"),
                encoding="utf-8-sig" if os.name == "nt" else "utf-8",
            )
    if state in {"policy-under-lock", "mode-under-lock"}:
        policy_path = root / "runtime-deployment.json"
        if state == "mode-under-lock":
            policy_path.rename(policy_path.with_name("retained-runtime-deployment.json"))
            policy_path = fixture["home"] / ".copilot-extensions/installation-mode.json"
            policy_path.parent.mkdir(parents=True, exist_ok=True)
            policy_path.write_text(json.dumps({
                "schema": "copilot-extensions.installation-mode", "version": 1,
                "installationMode": {"enabled": True, "marketplaces": {}},
            }), encoding="utf-8")
        else:
            _policy(fixture, root, arguments, "managed")
        peer_policy = fixture["home"] / "peer-decision.json"
        policy_path.rename(peer_policy)
        resolver = fixture["payload"] / "scripts" / f"resolve-runtime.{suffix}"
        real_resolver = resolver.with_name(f"recorded-real-resolver.{suffix}")
        resolver.rename(real_resolver)
        resolver.write_text(
            "# Trace contract: normal/read-only/ready-v1\n"
            "param([string]$TraceMode='normal')\n"
            f". {quote(real_resolver)} -TraceMode $TraceMode\n"
            f"if([IO.File]::Exists({quote(root / '.provision.lock')})){{\n"
            f" [IO.File]::Copy({quote(peer_policy)},{quote(policy_path)},$true)\n}}\n"
            if os.name == "nt" else
            "# Trace contract: normal/read-only/ready-v1\n"
            f". {shlex.quote(str(real_resolver))}\n_rc=$?\n"
            f"if [[ -f {shlex.quote(str(root / '.provision.lock'))} ]]; then\n"
            f" cp {shlex.quote(str(peer_policy))} {shlex.quote(str(policy_path))}\n"
            'fi\nreturn "$_rc"\n',
            encoding="utf-8-sig" if os.name == "nt" else "utf-8",
        )
    if entry == "unavailable":
        fixture["payload"].rename(fixture["payload"].with_name("publisher-unavailable"))
    if entry == "no-pointer":
        pointer = fixture["legacy"] / "payload-dir"
        pointer.rename(pointer.with_name(pointer.name + ".unavailable"))

    path = (
        fixture["payload"] / "bin/agent-containers" if entry == "payload" else
        fixture["home"] / ".local/bin/agent-containers"
    )
    if os.name == "nt":
        path = path.with_suffix(".ps1")
    forwarded = (
        ["--help", 'spaces "quotes" & pipes | ; bangs !', "", "tail space\\"]
        if healthy else ["--help"]
    )
    command = (
        [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(path), *forwarded]
        if os.name == "nt" else ["bash", str(path), *forwarded]
    )
    host = powershell_host(fixture, environment) if os.name == "nt" else None
    before = snapshot_home()
    result = _run(fixture, environment, command, host)
    after = snapshot_home()
    if host:
        host.close()
        assert snapshot_home() == after
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    marker = root / ("installer-ran.json" if os.name == "nt" else "installer-ran")
    evidence = (
        f"scope={scope} state={state} entry={entry} rc={result.returncode} "
        f"changed={changed}\n{result.stdout}{result.stderr}"
    )
    relative = root.relative_to(fixture["home"]).as_posix()
    if entry in {"unavailable", "no-pointer"} and not healthy:
        assert result.returncode == 126, evidence
        assert "retained runtime gate is unavailable" in result.stderr, evidence
        assert not changed and not marker.exists(), evidence
        return
    if state in {"policy-under-lock", "mode-under-lock"}:
        assert result.returncode == 3, evidence
        assert ("migration-required" if state == "mode-under-lock" else "deployment-managed") in result.stderr, evidence
        assert policy_path.read_bytes() == peer_policy.read_bytes(), evidence
        assert not marker.exists() and not (root / ".provision-status").exists(), evidence
        assert set(changed) <= {
            relative, f"{relative}/logs", f"{relative}/logs/boot-trace.jsonl",
            f"{relative}/.provision.lock", policy_path.relative_to(fixture["home"]).as_posix(),
            policy_path.parent.relative_to(fixture["home"]).as_posix(),
        }, evidence
        return
    if not (standalone or healthy):
        expected = (
            "runtime resolver failed" if state == "failed-resolver" else
            "read-only runtime resolver is unavailable" if state == "old-resolver" else
            "installation-context runtime support is unavailable" if state.startswith("missing-") else
            "Runtime publication refused" if state == "old-helper" else
            "NO_SELFPROVISION set" if state == "opt-out" else
            "deployment-policy-invalid" if state in {"invalid", "foreign"} else
            "deployment-policy-version-unsupported" if state == "future" else "deployment-managed"
        )
        assert result.returncode != 0 and expected in result.stderr, evidence
        if state == "failed-resolver":
            assert result.returncode == 126, evidence
        assert not changed and not marker.exists(), evidence
        return
    allowed = {relative, f"{relative}/logs", f"{relative}/logs/boot-trace.jsonl"}
    if healthy:
        assert result.returncode == (29 if state == "healthy-exit" else 0), evidence
        assert json.loads(result.stdout) == {
            "arguments": forwarded, "root": str(root), "state": str(root),
            "prefix": str(python.parent.parent),
        }, evidence
        assert not marker.exists() and not (root / ".provision.lock").exists(), evidence
    else:
        assert result.returncode == 37 and marker.is_file(), evidence
        assert "failed rc=37" in (root / ".provision-status").read_text(encoding="utf-8-sig"), evidence
        if os.name == "nt":
            assert json.loads(marker.read_text(encoding="utf-8")) == {
                "action": "provision", "root": str(root), "environmentRoot": str(root),
            }, evidence
        if state == "standalone-stderr":
            assert "recorded installer diagnostic" in result.stderr, evidence
        allowed.update(f"{relative}/{name}" for name in (
            marker.name, ".provision-status", ".provision.lock",
        ))
    assert set(changed) <= allowed, evidence
