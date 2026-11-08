"""Vault's read-only source target and pre-completion profile evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
TARGET_ENV = "COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET"
TARGET_SCHEMA = "copilot-extensions.runtime-source-target"


def selected(root: Path, payload: Path, requested: str | None = None) -> dict:
    import runtime_profile as profile

    if requested is None and not os.path.lexists(root / profile.PROFILE_FILE):
        import versioned_runtime_source as source

        version = source.payload_package_version(payload, "agent-vault")
        identity = source.read_source_identity(payload, version, plugin="agent-vault")
        actual = identity.runtime_version if identity else version
        return {
            "profile": "base", "package_version": version, "runtime_version": actual,
            "source_version": actual,
            "source_identity": source.manifest_fields(identity) if identity else None,
        }
    return profile.selection(root, payload, requested)


def document(root: Path, selection: dict) -> dict:
    return {
        "schema": TARGET_SCHEMA, "version": 1, "plugin": "agent-vault",
        "runtime_root": str(root.resolve()), "source": selection["source_identity"],
        "profile": selection["profile"], "runtime_version": selection["runtime_version"],
    }


def current(root: Path, payload: Path, package_version: str) -> bool:
    from runtime_profile import _unique_object
    import versioned_runtime_source as source

    expected = selected(root, payload)
    pin = os.environ.get(source.SOURCE_PIN_ENV)
    if pin is not None and json.dumps(expected["source_identity"] or {}, sort_keys=True) != pin:
        raise ValueError("Source identity differs from the update transaction selection")
    manifest_path = root / "deploy-manifest.json"
    marker = root / "current-version"
    if expected["package_version"] != package_version or not manifest_path.is_file() or not marker.is_file():
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("source"), dict):
        raise ValueError("Vault deployment metadata must name its source")
    fields = expected["source_identity"]
    version = expected["runtime_version"]
    if (
        manifest["source"].get("version") != package_version
        or manifest.get("runtime_profile", "base") != expected["profile"]
        or manifest.get("runtime_version", package_version) != version
        or marker.read_text(encoding="utf-8").strip() != version
        or manifest.get("runtime_source") != (fields["runtime_source"] if fields else None)
    ):
        return False
    python = root / "versions" / version / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    return python.is_file()


def check_target(root: Path, selection: dict) -> None:
    if TARGET_ENV not in os.environ:
        return
    from runtime_profile import _unique_object

    expected = json.loads(os.environ[TARGET_ENV], object_pairs_hook=_unique_object)
    if (
        not isinstance(expected, dict) or type(expected.get("version")) is not int
        or not isinstance(expected.get("runtime_root"), str)
        or not Path(expected["runtime_root"]).is_absolute()
        or Path(expected["runtime_root"]).resolve() != root.resolve()
    ):
        raise ValueError("Vault source target names a different runtime root")
    expected["runtime_root"] = str(root.resolve())
    if expected != document(root, selection):
        raise ValueError("Vault source/profile changed after target planning")


def record_profile(slot: Path, selection: dict, payload_hash: str) -> None:
    import versioned_runtime as runtime
    from runtime_profile import _unique_object

    root = slot.parent.parent
    check_target(root, selection)
    if (
        slot.name != selection["runtime_version"] or not isinstance(payload_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", payload_hash)
    ):
        raise ValueError("Vault completion evidence differs from the selected source/profile")
    fields = selection["source_identity"]
    value = {
        "profile": selection["profile"], "packageVersion": selection["package_version"],
        "payloadHash": payload_hash, "runtimeSource": fields["runtime_source"] if fields else None,
    }
    path = slot / "runtime-profile.json"
    if os.path.lexists(path):
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("Vault slot profile evidence must be a bounded ordinary file")
        if json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object) != value:
            raise ValueError("Vault slot profile evidence changed")
        return
    if runtime.is_complete(root, slot.name):
        raise ValueError("Completed Vault runtime has no profile evidence; refusing mutation")
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--payload", type=Path, default=SCRIPTS.parent)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan-source", action="store_true")
    mode.add_argument("--check-target", action="store_true")
    mode.add_argument("--record-slot", type=str)
    parser.add_argument("--payload-hash")
    parser.add_argument("--expected-source-version")
    parser.add_argument("--expected-source-identity")
    args = parser.parse_args(argv)
    selection = selected(args.root, args.payload)
    check_target(args.root, selection)
    if args.plan_source:
        from runtime_profile import _unique_object

        if (
            selection["source_identity"] is None
            or selection["source_version"] != args.expected_source_version
            or not isinstance(args.expected_source_identity, str)
            or selection["source_identity"] != json.loads(
                args.expected_source_identity, object_pairs_hook=_unique_object,
            )
        ):
            raise ValueError("Vault target planner received a different prepared source")
        print(json.dumps(document(args.root, selection), sort_keys=True))
    elif args.record_slot:
        if args.record_slot != selection["runtime_version"]:
            raise ValueError("Vault slot differs from the planned source target")
        record_profile(args.root / "versions" / args.record_slot, selection, args.payload_hash)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
