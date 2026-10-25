"""Read command ownership published by an installation's existing lifecycle."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Protocol

SCHEMA = "copilot-extensions.command-installation-binding"
VERSION = 1


class CommandOwnershipConflict(RuntimeError):
    """An existing lifecycle owner cannot be replaced implicitly."""


class SourceProvenanceUnavailable(RuntimeError):
    """An unowned legacy installation has no publishable source evidence."""


class InstallationPrimitive(Protocol):
    InstallationContextError: type[Exception]

    def resolve_payload_source(self, **arguments: Any) -> dict[str, Any]: ...
    def resolve_installation_mode(self, **arguments: Any) -> dict[str, Any]: ...
    def validate_context_receipt(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def read_json(self, path: Path) -> Any: ...
    def read_deployment_manifest(self, path: Path) -> Any: ...
    def _DirectoryLock(self, path: Path, **arguments: Any) -> Any: ...
    def _atomic_write_json(self, path: Path, value: Any, **arguments: Any) -> None: ...
    def _pid_is_live(self, pid: int) -> bool: ...
    def probe_legacy_entrypoint(self, **arguments: Any) -> dict[str, Any]: ...
    def normalize_source(self, *args: Any, **arguments: Any) -> Any: ...
    def source_identity(self, *args: Any, **arguments: Any) -> dict[str, Any]: ...


def _identity(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _legacy_receipt(
    provenance: Mapping[str, Any], mode: Mapping[str, Any]
) -> dict[str, Any]:
    owner = {
        "sourceFingerprint": provenance["sourceFingerprint"],
        "pluginId": provenance["pluginId"],
        "mode": "legacy",
        "runtimeRoot": mode["runtimeRoot"],
        "environment": mode["environment"],
    }
    return {
        "schema": SCHEMA,
        "version": VERSION,
        **owner,
        "installationId": _identity(owner),
        "source": provenance["source"],
        "payloadRoot": provenance["payloadRoot"],
    }


def publish_legacy_binding(
    primitive: InstallationPrimitive, **arguments: Any
) -> dict[str, Any]:
    """Build ownership metadata only inside an authorized legacy lifecycle."""
    manifest_path = Path(arguments["legacy_root"]) / "deploy-manifest.json"
    previous = None
    reservation = None
    if os.path.lexists(manifest_path):
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise CommandOwnershipConflict("The legacy deploy manifest is not an owned file.")
        try:
            incumbent = getattr(
                primitive, "read_deployment_manifest", primitive.read_json,
            )(manifest_path)
        except (primitive.InstallationContextError, OSError, ValueError) as error:
            raise CommandOwnershipConflict(
                f"Cannot validate incumbent command ownership: {error}"
            ) from error
        if (
            not isinstance(incumbent, dict)
            or incumbent.get("schema_version") != 3
            or incumbent.get("service") != arguments["plugin_id"]
        ):
            raise CommandOwnershipConflict("The legacy deploy manifest is not owned by this plugin.")
        previous = incumbent.get("commandBinding")
        reservation = incumbent.get("commandLifecycle")
    source_arguments = {
        "payload_root": arguments["payload_root"],
        "plugin_id": arguments["plugin_id"],
        "project_root": arguments.get("project_root"),
        "environment": arguments.get("environment"),
    }
    try:
        provenance = primitive.resolve_payload_source(**source_arguments)
    except primitive.InstallationContextError as error:
        if previous is not None:
            raise CommandOwnershipConflict(
                f"Cannot revalidate incumbent command source: {error}"
            ) from error
        raise SourceProvenanceUnavailable(str(error)) from error
    mode = primitive.resolve_installation_mode(
        **source_arguments, legacy_root=arguments["legacy_root"],
        **dict(arguments.get("mode_arguments") or {}),
    )
    if (
        mode["status"] != "ready"
        or mode["actualMode"] != "legacy"
        or mode["desiredMode"] != "legacy"
        or mode["legacy"]["tombstone"] is not None
    ):
        raise ValueError(
            "Cannot publish legacy command ownership: "
            f"status={mode['status']}, reason={mode['reason']}."
        )
    receipt = _legacy_receipt(provenance, mode)
    if reservation is not None and (
        not isinstance(reservation, dict)
        or type(reservation.get("version")) is not int
        or reservation.get("version") != 1
        or reservation.get("state") not in {"pending", "complete"}
        or reservation.get("owner") != {
            key: value for key, value in receipt.items() if key != "payloadRoot"
        }
        or set(reservation) != {"version", "state", "owner"}
    ):
        raise CommandOwnershipConflict("Runtime lifecycle reservation names another owner.")
    if previous is not None:
        expected_owner = {key: value for key, value in receipt.items() if key != "payloadRoot"}
        if (
            not isinstance(previous, dict)
            or type(previous.get("version")) is not int
            or {key: value for key, value in previous.items() if key != "payloadRoot"}
            != expected_owner
        ):
            raise CommandOwnershipConflict(
                "Legacy command ownership belongs to another source, installation, "
                "or environment; explicit transfer is required."
            )
    return receipt


@contextmanager
def legacy_manifest_publication(
    primitive: InstallationPrimitive, manifest: dict[str, Any], **arguments: Any
) -> Iterator[tuple[dict[str, Any], dict[str, str], Any]]:
    """Retain validated command ownership through the caller's manifest commit."""
    root = Path(arguments["legacy_root"])
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("Legacy manifest publication requires an existing absolute runtime root.")
    if (
        manifest.get("schema_version") != 3
        or manifest.get("service") != arguments["plugin_id"]
        or "commandBinding" in manifest
    ):
        raise ValueError("The lifecycle must supply an unbound schema-3 plugin manifest.")
    lifecycle = _load_lifecycle()
    lease = lifecycle.borrowed_lease(_rules(), primitive, **arguments)
    purpose = lease.purpose if lease is not None else lifecycle._purpose(_rules(), {})
    lifecycle._publication(_rules(), primitive, arguments, purpose)
    if lease is None:
        lifecycle._reservation(
            _rules(), primitive, root, lifecycle._owner(_rules(), primitive, arguments),
        )
    with (nullcontext(lease) if lease is not None else lifecycle._lock(primitive, arguments)) as lock:
        lifecycle._publication(_rules(), primitive, arguments, purpose)
        try:
            receipt = publish_legacy_binding(primitive, **arguments)
        except SourceProvenanceUnavailable as error:
            if lease is None and (root / "deploy-manifest.json").exists():
                incumbent = getattr(
                    primitive, "read_deployment_manifest", primitive.read_json,
                )(root / "deploy-manifest.json")
                if "commandLifecycle" in incumbent:
                    owner = lifecycle._owner(_rules(), primitive, arguments)
                    lifecycle._reservation(_rules(), primitive, root, owner)
                    manifest["commandLifecycle"] = incumbent["commandLifecycle"]
            # A successful unattributed lifecycle has no normalized owner to
            # persist. Only an unfinished operation retains its reservation.
            result = {"status": "unresolved", "reason": str(error)}
        else:
            manifest = {**manifest, "commandBinding": receipt}
            if lease is not None:
                manifest["commandLifecycle"] = {
                    "version": 1, "state": "complete", "owner": lease.owner,
                }
            elif (root / "deploy-manifest.json").exists():
                incumbent = getattr(
                    primitive, "read_deployment_manifest", primitive.read_json,
                )(root / "deploy-manifest.json")
                if "commandLifecycle" in incumbent:
                    manifest["commandLifecycle"] = incumbent["commandLifecycle"]
            result = {"status": "published", "reason": "ownership-validated"}
        lock.assert_owned()
        yield manifest, result, lock
        lock.assert_owned()


def retained_legacy_binding(
    primitive: InstallationPrimitive, lock: Any, **arguments: Any,
) -> dict[str, Any]:
    """Validate an incumbent owner for restoration, without claiming new source."""
    root = Path(arguments["legacy_root"])
    lifecycle = _load_lifecycle()
    if (
        not root.is_absolute()
        or not root.is_dir()
        or lock.path != lifecycle._lock(primitive, arguments).path
    ):
        raise CommandOwnershipConflict("Retained restoration requires this runtime's publication lock.")
    lock.assert_owned()
    lifecycle._publication(_rules(), primitive, arguments, lifecycle._purpose(_rules(), {}))
    state = primitive.probe_legacy_entrypoint(
        **{key: arguments[key] for key in (
            "payload_root", "plugin_id", "legacy_root", "project_root", "environment",
        ) if key in arguments},
        **dict(arguments.get("mode_arguments") or {}),
    )
    if (
        state.get("allowMutation") is not True
        or state.get("desiredMode") == "namespaced"
        or state["legacy"]["tombstone"] is not None
    ):
        raise CommandOwnershipConflict("Retained restoration requires an admitted legacy runtime.")
    path = root / "deploy-manifest.json"
    if path.is_symlink() or not path.is_file():
        raise CommandOwnershipConflict("Retained command ownership requires an owned manifest file.")
    try:
        incumbent = getattr(
            primitive, "read_deployment_manifest", primitive.read_json,
        )(path)
    except (primitive.InstallationContextError, OSError, ValueError) as error:
        raise CommandOwnershipConflict(f"Cannot validate retained command ownership: {error}") from error
    if (
        not isinstance(incumbent, dict)
        or incumbent.get("schema_version") != 3
        or incumbent.get("service") != arguments["plugin_id"]
    ):
        raise CommandOwnershipConflict("Retained deploy manifest belongs to another plugin.")
    receipt = incumbent.get("commandBinding")
    if (
        not isinstance(receipt, dict)
        or type(receipt.get("version")) is not int
        or not isinstance(receipt.get("source"), dict)
        or not isinstance(receipt.get("payloadRoot"), str)
        or not Path(receipt["payloadRoot"]).is_absolute()
    ):
        raise CommandOwnershipConflict("Retained command ownership receipt is invalid.")
    source = receipt["source"]
    if any(not isinstance(source.get(key), str) for key in ("kind", "canonical", "ref")):
        raise CommandOwnershipConflict("Retained command source identity is invalid.")
    try:
        identity = primitive.source_identity(
            primitive.normalize_source(
                {key: source[key] for key in ("kind", "canonical", "ref")}, from_receipt=True,
            ), "retained",
        )
    except primitive.InstallationContextError as error:
        raise CommandOwnershipConflict(f"Retained command source identity is invalid: {error}") from error
    expected = _legacy_receipt({
        "sourceFingerprint": identity["fingerprint"],
        "pluginId": arguments["plugin_id"],
        "source": {key: identity[key] for key in ("kind", "canonical", "ref", "record")},
        "payloadRoot": receipt["payloadRoot"],
    }, {"runtimeRoot": state["legacy"]["root"], "environment": state["environment"]})
    if receipt != expected:
        raise CommandOwnershipConflict("Retained command ownership names another source, root, or environment.")
    reservation = incumbent.get("commandLifecycle")
    if "commandLifecycle" in incumbent and (
        not isinstance(reservation, dict)
        or type(reservation.get("version")) is not int
        or reservation != {
            "version": 1, "state": "complete",
            "owner": {key: value for key, value in receipt.items() if key != "payloadRoot"},
        }
    ):
        raise CommandOwnershipConflict("Retained command lifecycle is not complete for this owner.")
    lock.assert_owned()
    return {
        key: incumbent[key] for key in ("commandBinding", "commandLifecycle") if key in incumbent
    }


def write_legacy_manifest(
    primitive: InstallationPrimitive, manifest: dict[str, Any], **arguments: Any
) -> dict[str, Any]:
    """Serialize validation and atomic publication at the existing lifecycle."""
    with legacy_manifest_publication(primitive, manifest, **arguments) as (bound, result, lock):
        primitive._atomic_write_json(
            Path(arguments["legacy_root"]) / "deploy-manifest.json", bound, lock=lock,
        )
        return result


def resolve_command_binding(
    primitive: InstallationPrimitive, **arguments: Any
) -> dict[str, Any]:
    """Return explicit selection or refusal without provisioning or activation."""
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "version": VERSION,
        "status": "unresolved",
        "reason": "source-unavailable",
        "source": None,
        "installation": None,
    }
    try:
        provenance = primitive.resolve_payload_source(
            payload_root=arguments["payload_root"],
            plugin_id=arguments["plugin_id"],
            project_root=arguments.get("project_root"),
            environment=arguments.get("environment"),
        )
    except primitive.InstallationContextError as error:
        result["diagnostic"] = str(error)
        return result
    result["source"] = {
        "identity": provenance["source"],
        "fingerprint": provenance["sourceFingerprint"],
        "marketplaceId": provenance["marketplaceId"],
        "pluginId": provenance["pluginId"],
    }
    try:
        mode = primitive.resolve_installation_mode(
            payload_root=arguments["payload_root"],
            plugin_id=arguments["plugin_id"],
            legacy_root=arguments["legacy_root"],
            project_root=arguments.get("project_root"),
            environment=arguments.get("environment"),
            **dict(arguments.get("mode_arguments") or {}),
        )
        result["governance"] = {
            key: mode[key] for key in ("status", "reason", "desiredMode", "actualMode")
        }
        if mode["status"] != "ready" or mode["actualMode"] not in {"legacy", "namespaced"}:
            result.update(status="blocked", reason=str(mode["reason"]))
            return result
        if mode["marketplaceId"] != provenance["marketplaceId"]:
            result.update(status="blocked", reason="source-installation-mismatch")
            return result
        if mode["actualMode"] == "namespaced":
            # The receipt validator enforces durable/marketplaces/id/plugins/plugin/install.json.
            selected = primitive.validate_context_receipt(
                mode["context"],
                (Path(mode["context"]).parents[4]),
                expected_marketplace_id=provenance["marketplaceId"],
                expected_plugin_id=provenance["pluginId"],
                expected_payload_root=provenance["payloadRoot"],
                environment=arguments.get("environment"),
            )
            if selected["sourceFingerprint"] != provenance["sourceFingerprint"]:
                result.update(status="blocked", reason="source-installation-mismatch")
                return result
            if (
                selected["generation"] != mode["installGeneration"]
                or selected["pluginRoot"] != mode["runtimeRoot"]
            ):
                result.update(status="blocked", reason="installation-changed")
                return result
            owner = {
                "sourceFingerprint": provenance["sourceFingerprint"],
                "pluginId": provenance["pluginId"],
                "mode": "namespaced",
                "runtimeRoot": mode["runtimeRoot"],
                "environment": mode["environment"],
            }
            result["installation"] = {
                **owner,
                "installationId": _identity(owner),
                "installReceipt": selected["installReceipt"],
                "installGeneration": mode["installGeneration"],
                "activationGeneration": mode["activationGeneration"],
            }
        else:
            if (
                mode["desiredMode"] != "legacy"
                or mode["legacy"]["tombstone"] is not None
            ):
                result.update(status="blocked", reason="legacy-ownership-transferred")
                return result
            manifest_path = Path(mode["runtimeRoot"]) / "deploy-manifest.json"
            if not os.path.lexists(manifest_path):
                result["reason"] = "legacy-ownership-missing"
                return result
            if manifest_path.is_symlink() or not manifest_path.is_file():
                result.update(status="blocked", reason="legacy-manifest-invalid")
                return result
            manifest = getattr(
                primitive, "read_deployment_manifest", primitive.read_json,
            )(manifest_path)
            if (
                not isinstance(manifest, dict)
                or manifest.get("schema_version") != 3
                or manifest.get("service") != provenance["pluginId"]
            ):
                result.update(status="blocked", reason="legacy-manifest-invalid")
                return result
            receipt = manifest.get("commandBinding")
            lifecycle = manifest.get("commandLifecycle")
            if lifecycle is not None and (
                not isinstance(lifecycle, dict)
                or type(lifecycle.get("version")) is not int
                or lifecycle.get("version") != 1
                or lifecycle.get("state") != "complete"
                or lifecycle.get("owner") != {
                    key: value for key, value in _legacy_receipt(provenance, mode).items()
                    if key != "payloadRoot"
                }
                or set(lifecycle) != {"version", "state", "owner"}
            ):
                result.update(status="blocked", reason="legacy-lifecycle-incomplete")
                return result
            if receipt is None:
                result["reason"] = "legacy-ownership-missing"
                return result
            expected = _legacy_receipt(provenance, mode)
            if (
                not isinstance(receipt, dict)
                or type(receipt.get("version")) is not int
                or receipt != expected
            ):
                result.update(status="blocked", reason="legacy-ownership-mismatch")
                return result
            result["installation"] = {
                key: expected[key] for key in (
                    "installationId", "mode", "runtimeRoot", "environment",
                    "sourceFingerprint", "pluginId",
                )
            }
            result["installation"]["deployManifest"] = str(manifest_path)
        result.update(status="selected", reason="ownership-validated")
        return result
    except (primitive.InstallationContextError, OSError, ValueError) as error:
        result.update(status="blocked", reason="installation-invalid", diagnostic=str(error))
        return result


def _load_primitive(payload_root: Path) -> InstallationPrimitive:
    helper = payload_root / "scripts" / "installation-context" / "installation_context.py"
    spec = importlib.util.spec_from_file_location("_command_binding_context", helper)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load installation primitive '{helper}'.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_lifecycle() -> Any:
    name = f"{__package__}._command_lifecycle" if __package__ else "_payload_command_lifecycle"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).with_name(
        "_command_lifecycle.py" if __package__ else "command_lifecycle.py"
    )
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load lifecycle helper '{path}'.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _rules() -> Any:
    return SimpleNamespace(
        publish_legacy_binding=publish_legacy_binding,
        CommandOwnershipConflict=CommandOwnershipConflict,
        SourceProvenanceUnavailable=SourceProvenanceUnavailable,
    )


def binding_for_payload(
    payload_root: Path, plugin_id: str, legacy_root: Path
) -> dict[str, Any]:
    """Load only this payload's vendored, read-only installation primitive."""
    return resolve_command_binding(
        _load_primitive(payload_root), payload_root=payload_root, plugin_id=plugin_id,
        legacy_root=legacy_root, project_root=Path.cwd(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-root", required=True, type=Path)
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--legacy-root", required=True, type=Path)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--publish-legacy", action="store_true")
    parser.add_argument("--source-payload-root", type=Path)
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--run-lifecycle", action="store_true")
    parser.add_argument("--check-lifecycle", action="store_true")
    parser.add_argument("--lifecycle-operation", choices=("publish", "teardown"))
    parser.add_argument("--deferred-runtime", action="store_true")
    parser.add_argument("--managed-deployment", action="store_true")
    parser.add_argument("--teardown", action="store_true")
    parser.add_argument("--lifecycle-timeout", type=float)
    parser.add_argument("--human-operation", choices=("install", "update"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if arguments.payload_root.resolve() != Path(__file__).resolve().parents[1]:
        parser.error("The requested payload does not own this binding helper.")
    lifecycle_action = arguments.run_lifecycle or arguments.check_lifecycle
    if sum((arguments.run_lifecycle, arguments.check_lifecycle, arguments.publish_legacy)) > 1:
        parser.error("Specify only one lifecycle operation.")
    if arguments.deferred_runtime and not arguments.run_lifecycle:
        parser.error("--deferred-runtime requires --run-lifecycle.")
    if arguments.managed_deployment and (not arguments.run_lifecycle or arguments.teardown):
        parser.error("--managed-deployment requires --run-lifecycle without --teardown.")
    if arguments.lifecycle_operation is not None and not arguments.check_lifecycle:
        parser.error("--lifecycle-operation requires --check-lifecycle.")
    if arguments.lifecycle_timeout is not None and not arguments.run_lifecycle:
        parser.error("--lifecycle-timeout requires --run-lifecycle.")
    if arguments.human_operation and (not arguments.run_lifecycle or arguments.teardown):
        parser.error("--human-operation requires --run-lifecycle without --teardown.")
    if arguments.teardown and (not arguments.run_lifecycle or arguments.deferred_runtime):
        parser.error("--teardown requires --run-lifecycle without --deferred-runtime.")
    if arguments.source_payload_root is not None and not (arguments.publish_legacy or lifecycle_action):
        parser.error("--source-payload-root belongs to explicit lifecycle publication.")
    if arguments.project_root is not None and not (arguments.publish_legacy or lifecycle_action):
        parser.error("--project-root belongs to explicit lifecycle publication.")
    if arguments.write_manifest and not arguments.publish_legacy:
        parser.error("--write-manifest requires --publish-legacy.")
    try:
        if arguments.publish_legacy or lifecycle_action:
            source_payload = arguments.source_payload_root or arguments.payload_root
            publication_arguments = dict(
                payload_root=source_payload, plugin_id=arguments.plugin_id,
                legacy_root=arguments.legacy_root, project_root=arguments.project_root or Path.cwd(),
                environment={**os.environ, "COPILOT_PLUGIN_ROOT": str(source_payload)},
            )
            primitive = _load_primitive(arguments.payload_root)
            if lifecycle_action:
                lifecycle = _load_lifecycle()
                if arguments.check_lifecycle:
                    if lifecycle.borrowed_lease(
                        _rules(), primitive, operation=arguments.lifecycle_operation or "publish",
                        **publication_arguments,
                    ) is None:
                        raise CommandOwnershipConflict("Runtime lifecycle ticket is missing.")
                    return 0
                command = arguments.command
                if command and command[0] == "--":
                    command = command[1:]
                return lifecycle.run_lifecycle(
                    _rules(), primitive, command,
                    deferred_runtime=arguments.deferred_runtime,
                    managed_deployment=arguments.managed_deployment,
                    timeout=arguments.lifecycle_timeout,
                    teardown=arguments.teardown, **publication_arguments,
                    human_operation=arguments.human_operation,
                )
            elif arguments.write_manifest:
                manifest = json.load(sys.stdin)
                if not isinstance(manifest, dict):
                    raise ValueError("Lifecycle manifest input must be an object.")
                binding = write_legacy_manifest(primitive, manifest, **publication_arguments)
                if binding["status"] == "unresolved":
                    print(
                        f"Command ownership was not published: {binding['reason']}",
                        file=sys.stderr,
                    )
            else:
                binding = publish_legacy_binding(primitive, **publication_arguments)
        else:
            binding = binding_for_payload(
                arguments.payload_root, arguments.plugin_id, arguments.legacy_root
            )
    except SourceProvenanceUnavailable as error:
        print(f"Command ownership was not published: {error}", file=sys.stderr)
        return 2
    except CommandOwnershipConflict as error:
        print(f"Command ownership conflict: {error}", file=sys.stderr)
        return 3
    except (OSError, ValueError) as error:
        print(f"Command binding unavailable: {error}", file=sys.stderr)
        return 3 if arguments.publish_legacy else 1
    print(json.dumps(binding, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
