"""Payload-owned dispatch into one governed, completed installation."""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
PAYLOAD = Path(__file__).resolve().parent.parent
PLUGIN = "agent-pull-requests"


def installer():
    spec = importlib.util.spec_from_file_location(
        "apr_invocation_installer", PAYLOAD / "scripts" / "cell_install.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def discover(api):
    _, profile = api._current_environment(
        environment=os.environ, os_profile=None, platform=None, wsl_distro=None,
    )
    durable = profile / ".copilot-extensions"
    pointer = os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
    if pointer:
        receipt = Path(pointer)
        if (
            not receipt.is_absolute() or receipt.name != "install.json"
            or receipt.parent.name != PLUGIN or receipt.parent.parent.name != "plugins"
            or receipt.parent.parent.parent.parent.name != "marketplaces"
        ):
            raise PermissionError("Explicit context must be an absolute canonical install receipt")
        durable = receipt.parents[4]
        api.validate_context_receipt(
            receipt, durable, expected_plugin_id=PLUGIN, expected_payload_root=PAYLOAD,
            environment={},
        )
    legacy = profile / ".agent-pull-requests"
    probe = {
        "declared": not os.path.lexists(legacy),
        "result": "unknown" if os.path.lexists(legacy) else "absent", "checkedAt": None,
    }
    status = api.resolve_installation_mode(
        payload_root=PAYLOAD, plugin_id=PLUGIN, expected_plugin_id=PLUGIN,
        expected_payload_root=PAYLOAD, legacy_root=legacy, legacy_probe=probe,
        context=pointer, durable_home=durable, copilot_home=profile / ".copilot",
        os_profile=profile,
    )
    return status, durable, profile, probe


def require_active(status):
    if (status["status"], status["reason"], status["actualMode"]) != (
        "ready", "namespaced-active", "namespaced",
    ):
        raise PermissionError(
            f"Installation blocks invocation: status={status['status']} reason={status['reason']}"
        )


def activate_fresh(api, status, durable, profile, probe, *, version):
    """Only an absent legacy installation may enter canonical first-touch activation."""
    if (
        (status["status"], status["reason"], status["desiredMode"]) != (
            "ready", "activation-required", "namespaced",
        )
        or os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
        or probe["result"] != "absent"
    ):
        require_active(status)
        return
    resolved = api.resolve_context(
        payload_root=PAYLOAD, plugin_id=PLUGIN, durable_home=durable,
        copilot_home=profile / ".copilot",
    )
    namespace = Path(resolved["namespaceReceipt"])
    receipt = Path(resolved["installReceipt"])
    # Existing installation receipts require an explicit lifecycle decision.
    if os.path.lexists(receipt):
        raise PermissionError("Unactivated existing installation requires explicit activation")
    generation = (
        api.validate_namespace_receipt(namespace, durable)["receipt"]["generation"]
        if os.path.lexists(namespace) else 0
    )
    stamped = api.stamp_context(
        payload_root=PAYLOAD, plugin_id=PLUGIN, durable_home=durable,
        copilot_home=profile / ".copilot",
        payload_version=version, payload_origin="explicit",
        expected_namespace_generation=generation, expected_install_generation=0,
    )
    fresh, _, _, fresh_probe = discover(api)
    if (
        fresh["status"] != "ready" or fresh["reason"] != "activation-required"
        or fresh_probe["result"] != "absent"
    ):
        raise PermissionError("First-touch governance or legacy absence changed before activation")
    api.compare_and_swap_activation(
        context=stamped["installReceipt"], durable_home=durable,
        expected_marketplace_id=stamped["marketplaceId"], expected_plugin_id=PLUGIN,
        expected_namespace_generation=stamped["namespaceGeneration"],
        expected_install_generation=stamped["generation"], expected_activation_generation=0,
        activation_mode="namespaced", activation_state="active", legacy_disposition="absent",
        legacy_probe=fresh_probe, legacy_root=profile / ".agent-pull-requests",
        os_profile=profile,
    )


def refresh_management(api, status, durable, profile, *, version):
    """Bootstrap may advance only this already-active source and payload."""
    require_active(status)
    validated = api.validate_context_receipt(
        status["context"], durable, expected_marketplace_id=status["marketplaceId"],
        expected_plugin_id=PLUGIN, expected_payload_root=PAYLOAD, environment={},
    )
    recorded = api._payload_identity_from_install(validated["installReceipt"])
    if recorded["version"] == version:
        return
    environment = {
        key: value for key, value in os.environ.items() if key != "COPILOT_EXTENSIONS_CONTEXT"
    }
    resolved = api.resolve_context(
        payload_root=PAYLOAD, plugin_id=PLUGIN, durable_home=durable,
        copilot_home=profile / ".copilot", environment=environment,
    )
    if not api.paths_equal(resolved["installReceipt"], validated["installReceipt"]):
        raise PermissionError("Management refresh resolves a different source installation")
    activation = api.read_json(status["activation"])
    if activation["generation"] != status["activationGeneration"]:
        raise PermissionError("Activation changed before management refresh")
    fresh, _, _, _ = discover(api)
    if fresh != status:
        raise PermissionError("Governance changed before management refresh")
    stamped = api.stamp_context(
        payload_root=PAYLOAD, plugin_id=PLUGIN, durable_home=durable,
        copilot_home=profile / ".copilot", payload_version=version, payload_origin="explicit",
        expected_namespace_generation=validated["namespaceGeneration"],
        expected_install_generation=validated["generation"], environment=environment,
    )
    result = api.compare_and_swap_activation(
        context=validated["installReceipt"], durable_home=durable,
        expected_marketplace_id=status["marketplaceId"], expected_plugin_id=PLUGIN,
        expected_namespace_generation=stamped["namespaceGeneration"],
        expected_install_generation=stamped["generation"],
        expected_activation_generation=status["activationGeneration"],
        activation_mode="namespaced", activation_state="active",
        legacy_disposition=activation["legacy"]["disposition"],
        legacy_probe=activation["legacy"]["probe"],
        legacy_root=profile / ".agent-pull-requests", os_profile=profile,
    )
    if result["status"] != "ready":
        raise PermissionError("Management activation changed; explicit revalidation required")


def selection(api, status, durable):
    """No LKG/newest fallback: all selected metadata must describe one completed slot."""
    require_active(status)
    context = status["context"]
    validated = api.validate_context_receipt(
        context, durable, expected_marketplace_id=status["marketplaceId"],
        expected_plugin_id=PLUGIN, expected_payload_root=PAYLOAD, environment={},
    )
    root = Path(validated["pluginRoot"])
    current = api._read_runtime_marker(root / api.CURRENT_VERSION_FILE, "Current runtime")
    lkg = api._read_runtime_marker(root / api.LAST_KNOWN_GOOD_FILE, "Last known good")
    manifest_path = root / "deploy-manifest.json"
    if current is None and lkg is None and not os.path.lexists(manifest_path):
        return None
    if current is None or lkg != current:
        raise PermissionError(
            "Selected runtime markers are incomplete or incoherent; repair required"
        )
    slot = Path(validated["versionsRoot"]) / current
    owner = api.read_json(slot / api.RUNTIME_SLOT_OWNERSHIP_FILE)
    snapshot = owner.get("snapshot") if isinstance(owner, dict) else None
    snapshot_id = snapshot.get("id") if isinstance(snapshot, dict) else None
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise PermissionError("Selected slot lacks snapshot identity")
    common = dict(
        context=context, durable_home=durable, expected_plugin_id=PLUGIN,
        expected_marketplace_id=status["marketplaceId"],
        snapshot_id=snapshot_id, runtime_version=current, environment={},
    )
    ownership = api.validate_runtime_slot_ownership(**common)
    provenance = api.read_json(ownership["snapshotProvenance"])
    payload = provenance["payload"]
    completed = api.validate_runtime_slot_completion(
        **common, expected_payload_root=payload["root"],
        expected_payload_version=payload["version"],
    )
    manifest = api.read_json(manifest_path)
    source = manifest.get("source")
    if (
        manifest.get("schema_version") != 3 or manifest.get("service") != PLUGIN
        or manifest.get("runtime") != "python" or not isinstance(source, dict)
        or source.get("plugin") != PLUGIN or source.get("version") != payload["version"]
        or source.get("snapshot_id") != snapshot_id
        or source.get("content_hash") != completed["payloadSha256"]
        or not isinstance(source.get("path"), str)
        or not api.paths_equal(source["path"], payload["root"])
        or not isinstance(manifest.get("venv"), str)
        or not api.paths_equal(manifest["venv"], completed["slotRoot"])
    ):
        raise PermissionError("Selected manifest differs from the completed runtime")
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise PermissionError("Selected completed runtime lacks its interpreter")
    return python, root


def execute(arguments, *, bootstrap=False):
    adapter = installer()
    api = adapter.foundation()
    status, durable, profile, probe = discover(api)
    if (
        not bootstrap and "AGENT_PULL_REQUESTS_NO_SELFPROVISION" in os.environ
        and status["reason"] == "activation-required"
    ):
        raise PermissionError("Runtime not provisioned (AGENT_PULL_REQUESTS_NO_SELFPROVISION set)")
    version = adapter.management_version()
    activate_fresh(api, status, durable, profile, probe, version=version)
    status, durable, _, _ = discover(api)
    require_active(status)
    if bootstrap:
        refresh_management(api, status, durable, profile, version=version)
        status, durable, _, _ = discover(api)
    selected = selection(api, status, durable)
    if bootstrap or selected is None:
        if not bootstrap and "AGENT_PULL_REQUESTS_NO_SELFPROVISION" in os.environ:
            raise PermissionError(
                "Runtime not provisioned (AGENT_PULL_REQUESTS_NO_SELFPROVISION set)"
            )
        result = subprocess.run([  # noqa: S603 -- exact owning payload's installer, argv only
            sys.executable, "-I", str(PAYLOAD / "scripts" / "cell_install.py"), "install",
            "--context", status["context"], "--durable-home", str(durable),
            "--expected-marketplace-id", status["marketplaceId"],
        ], capture_output=True, text=True, check=False,
            **adapter.process_helpers().no_window_kwargs())
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        if result.returncode:
            raise RuntimeError(
                f"Owning installer failed ({result.returncode}): {result.stdout.strip()}"
            )
        status, durable, _, _ = discover(api)
        selected = selection(api, status, durable)
        if selected is None:
            raise PermissionError("Owning installer returned without a selected completed runtime")
    if bootstrap:
        return 0
    before = status
    status, _, _, _ = discover(api)
    require_active(status)
    if status != before or selection(api, status, durable) != selected:
        raise PermissionError("Installation selection changed before command dispatch")
    python, root = selected
    environment = os.environ.copy()
    environment.update(
        COPILOT_EXTENSIONS_CONTEXT=status["context"], COPILOT_PLUGIN_ROOT=str(PAYLOAD),
        AGENT_RT_ROOT=str(root),
    )
    # Windows execv does not preserve the native target's exit status.
    return subprocess.run(  # noqa: S603 -- canonically validated same-cell target, argv only
        [str(python), "-I", "-m", "agent_pull_requests", *arguments],
        env=environment, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
        check=False, **adapter.process_helpers().no_window_kwargs(),
    ).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    try:
        return execute(arguments, bootstrap=args.bootstrap)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Payload invocation refused: {error}", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
