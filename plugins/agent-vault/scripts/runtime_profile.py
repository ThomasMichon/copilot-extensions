"""Vault's installation-owned dependency selection and retained native candidate."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

PROFILE_FILE = "runtime-profile.json"
PROFILE_SCHEMA = "agent-vault.runtime-profile"
PROFILES: dict[str, tuple[str, ...]] = {
    "base": (),
    "ssh": ("ssh",),
    "federated-relay": ("federated-relay",),
    "ssh-federated-relay": ("federated-relay", "ssh"),
}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate Vault runtime-profile key")
        result[key] = value
    return result


def selected_profile(root: Path, requested: str | None = None) -> str:
    path = root / PROFILE_FILE
    recorded = "base"
    if os.path.lexists(path):
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            or info.st_size > 4096
        ):
            raise ValueError("Vault runtime profile must be a bounded ordinary file")
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        if (
            not isinstance(value, dict) or set(value) != {"schema", "version", "profile"}
            or value["schema"] != PROFILE_SCHEMA
            or type(value["version"]) is not int or value["version"] != 1
            or value["profile"] not in PROFILES
        ):
            raise ValueError("Unsupported Vault runtime profile")
        recorded = value["profile"]
    if requested is not None and requested not in PROFILES:
        raise ValueError("Unsupported requested Vault runtime profile")
    return recorded if requested is None else requested


def selection(root: Path, payload: Path, requested: str | None = None) -> dict[str, Any]:
    import versioned_runtime_source as source

    profile = selected_profile(root, requested)
    project = importlib.import_module("tomllib").loads(
        (payload / "pyproject.toml").read_text(encoding="utf-8"),
    )["project"]
    version = project["version"]
    if (
        project["name"] != "agent-vault" or not isinstance(version, str)
        or not re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", version) or version.endswith(".")
    ):
        raise ValueError("Dependency selection requires the owning Vault payload")
    identity = source.read_source_identity(payload, version, plugin="agent-vault")
    profile_version = version + ("." if "+" in version else "+") + profile
    runtime_version = (
        source.runtime_version(profile_version, identity.source_commit, identity.assembly_hash)
        if identity is not None else profile_version
    )
    return {
        "profile": profile, "package_version": version, "runtime_version": runtime_version,
        "source_version": identity.runtime_version if identity is not None else version,
        "source_identity": source.manifest_fields(identity) if identity is not None else None,
    }


def persist_profile(root: Path, profile: str) -> None:
    selected_profile(root, profile)
    root.mkdir(parents=True, exist_ok=True)
    path = root / PROFILE_FILE
    body = json.dumps({"schema": PROFILE_SCHEMA, "version": 1, "profile": profile}) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == body:
        return
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            stream.write(body)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fingerprint(payload: Path, profile: str) -> str:
    import installer_engine as engine

    extras = PROFILES[profile]
    extras = engine._selected_extras(payload, extras)
    return engine._construction_hash(engine._source_files(engine._package_roots(payload)), extras)


def dependency_health(profile: str) -> tuple[str, str | None]:
    extras = PROFILES[profile]
    if "federated-relay" in extras:
        return (
            "agent_vault.federated_relay",
            "ssh_dependency_health" if "ssh" in extras else "dependency_health",
        )
    if "ssh" in extras:
        return "agent_vault.ssh_key", "_crypto"
    return "agent_vault", None


def wheel_selection(
    root: Path, payload: Path, admitted: dict[str, Any], requested: str | None = None,
) -> dict[str, Any]:
    from runtime_source_target import check_target
    import installer_provider_roots as providers

    selected = selection(root, payload, requested)
    roots = providers.select(root, admitted.get("provider_roots", []))
    if (
        selected["source_identity"] is not None or admitted.get("identity") != {}
        or admitted.get("extras") != list(PROFILES[selected["profile"]])
        or not isinstance(admitted.get("runtime_version"), str)
        or not re.fullmatch(
            re.escape(selected["package_version"]) + r"\.profile\.[0-9a-f]{16}",
            admitted["runtime_version"],
        )
        or not isinstance(admitted.get("payload_hash"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", admitted["payload_hash"])
    ):
        raise ValueError("Closed wheels differ from the selected Vault dependency profile")
    selected["runtime_version"] = admitted["runtime_version"]
    check_target(root, selected)
    module, callback = dependency_health(selected["profile"])
    return {
        **selected, "health_module": module, "health_callable": callback,
        "provider_roots": roots,
    }


def construction_session(root: Path, payload: Path, version: str, expected_hash: str) -> int:
    import installer_engine as engine
    from runtime_source_target import record_profile
    from versioned_runtime_construction import retain_helpers
    import installer_provider_roots as providers

    providers.require_inputs(root, closed_wheels=False)

    selected = selection(root, payload)
    if version != selected["runtime_version"] or expected_hash != fingerprint(
        payload, selected["profile"],
    ):
        raise ValueError("Vault source/profile selection changed before construction")
    uv = os.environ.get("AGENT_VAULT_INSTALL_UV")
    if not uv:
        raise ValueError("Vault profile construction requires its native installer's selected uv")
    extras = PROFILES[selected["profile"]]
    health_module, health_callable = dependency_health(selected["profile"])
    with engine.numbered_candidate(
        root, version, plugin=payload, uv=Path(uv), base_python=Path(sys.executable),
        health_module=health_module, health_callable=health_callable,
        extras=extras, process_retention=True,
        expected_source_identity=selected["source_identity"],
        expected_payload_hash=expected_hash,
        validate_candidate=lambda slot: record_profile(slot, selected, expected_hash),
    ) as candidate:
        if candidate.payload_hash != expected_hash:
            raise ValueError("Vault candidate differs from its admitted dependency profile")
        retain_helpers(candidate.slot, complete=True)
        print(json.dumps({"admission": "reuse"}), flush=True)
        for line in sys.stdin:
            if len(line) > 4096:
                raise ValueError("Vault construction command exceeds its protocol bound")
            request = json.loads(line, object_pairs_hook=_unique_object)
            if not isinstance(request, dict):
                raise ValueError("Vault construction command must be an object")
            command = request.get("command")
            if command == "complete":
                if request.get("payload_hash") != expected_hash or selection(
                    root, payload,
                ) != selected or fingerprint(payload, selected["profile"]) != expected_hash:
                    raise ValueError("Vault source/profile changed before native completion")
                print(json.dumps({"complete": True}), flush=True)
            elif command == "settled":
                print(json.dumps({"settled": True}), flush=True)
                return 0
            elif command == "abort":
                print(json.dumps({"aborted": True}), flush=True)
                return 1
            else:
                raise ValueError("Unsupported Vault construction command")
    return 1


def current(root: Path, package_version: str, payload: Path | None = None) -> bool:
    from runtime_source_target import current as target_current

    return target_current(root, payload or SCRIPTS.parent, package_version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--link-name", default=".venv")
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select", allow_abbrev=False)
    select.add_argument("--profile", choices=PROFILES)
    select.add_argument("--persist", action="store_true")
    wheels = commands.add_parser("select-wheels", allow_abbrev=False)
    wheels.add_argument("--selection", required=True)
    wheels.add_argument("--profile", choices=PROFILES)
    wheels.add_argument("--persist", action="store_true")
    commands.add_parser("fingerprint", allow_abbrev=False)
    commands.add_parser("fields", allow_abbrev=False)
    commands.add_parser("require-inputs", allow_abbrev=False)
    check = commands.add_parser("current", allow_abbrev=False)
    check.add_argument("--package-version", required=True)
    session = commands.add_parser("construction-session", allow_abbrev=False)
    session.add_argument("version")
    session.add_argument("--payload-hash", required=True)
    providers_command = commands.add_parser("providers", allow_abbrev=False)
    providers_command.add_argument("--roots")
    providers_command.add_argument("--persist", action="store_true")
    args = parser.parse_args(argv)
    payload = SCRIPTS.parent
    if args.command == "require-inputs":
        import installer_provider_roots as providers

        providers.require_inputs(args.root, closed_wheels=False)
        return 0
    if args.command == "providers":
        import installer_provider_roots as providers

        roots = providers.read(args.root) or []
        if args.roots is not None:
            roots = providers.normalize(json.loads(args.roots, object_pairs_hook=_unique_object))
            if roots != providers.normalize(roots, core="agent-vault"):
                raise ValueError("Vault provider roots must not include the owning core")
            if args.persist:
                providers.persist(args.root, roots)
        elif args.persist:
            raise ValueError("Persisting Vault provider roots requires an explicit roots list")
        print(json.dumps({"roots": roots}, sort_keys=True))
        return 0
    if args.command == "select-wheels":
        admitted = json.loads(args.selection, object_pairs_hook=_unique_object)
        if not isinstance(admitted, dict):
            raise ValueError("Closed-wheel admission must be an object")
        result = wheel_selection(args.root, payload, admitted, args.profile)
        if args.persist and (args.profile is not None or os.path.lexists(args.root / PROFILE_FILE)):
            persist_profile(args.root, result["profile"])
        if args.persist:
            import installer_provider_roots as providers

            providers.select(args.root, admitted.get("provider_roots", []), write=True)
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.command == "select":
        from runtime_source_target import check_target

        result = selection(args.root, payload, args.profile)
        check_target(args.root, result)
        if args.persist:
            persist_profile(args.root, result["profile"])
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.command == "fingerprint":
        print(fingerprint(payload, selected_profile(args.root)))
        return 0
    if args.command == "fields":
        selected = selection(args.root, payload)
        fields = selected["source_identity"] or {}
        fields.update(
            runtime_profile=selected["profile"], runtime_version=selected["runtime_version"],
        )
        print(json.dumps(fields, sort_keys=True)[1:-1])
        return 0
    if args.command == "current":
        return 0 if current(args.root, args.package_version) else 1
    return construction_session(args.root, payload, args.version, args.payload_hash)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
