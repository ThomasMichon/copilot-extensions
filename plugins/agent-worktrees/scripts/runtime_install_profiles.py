"""Bind shared profile evidence to the native Worktrees lifecycle."""
from __future__ import annotations

import hashlib
import json
import re


def runtime_profile_policy(receipt: dict, source) -> dict | None:
    binding = receipt["binding"]
    if binding.get("schema") != "copilot-extensions.artifact-runtime-inputs":
        return source_profile_policy(receipt, source)
    if set(binding) != {
        "schema", "version", "profile", "source", "policy", "artifact_manifest",
        "release_subject", "bundle", "acquisition",
    } or type(binding["version"]) is not int or binding["version"] != 1:
        raise ValueError("Worktrees artifact profile has incomplete construction evidence")
    profile, authentication, policy, manifest = (
        binding["profile"], binding["source"], binding["policy"], binding["artifact_manifest"],
    )
    if not all(isinstance(value, dict) for value in (profile, authentication, policy, manifest)):
        raise ValueError("Worktrees artifact profile has invalid construction evidence")
    artifact = profile.get("artifact")
    if (
        policy.get("status") != "ready"
        or policy.get("owner", {}).get("pluginId") != "agent-worktrees"
        or authentication.get("source") != source.document()
        or authentication.get("owner_policy") != policy.get("policyId")
        or authentication.get("mode") != "origin" or authentication.get("trigger") is not None
        or profile.get("component") != "agent-worktrees" or profile.get("mode") != "artifact"
        or profile.get("backend") != policy.get("selection", {}).get("backend")
        or profile.get("approval_source") != "promotion-main"
        or not isinstance(artifact, dict) or set(artifact) != {"artifact_id", "manifest_sha256"}
        or manifest.get("artifact_id") != artifact["artifact_id"]
        or manifest.get("plugin") != "agent-worktrees"
        or manifest.get("version") != source.package_version
        or profile.get("release") != {
            "identity": "agent-worktrees:" + source.package_version,
            "sha256": manifest.get("payload_hash"),
        }
        or any(
            not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
            for value in (
                binding["release_subject"], binding["bundle"],
                artifact["artifact_id"], artifact["manifest_sha256"],
            )
        )
    ):
        raise ValueError("Worktrees artifact profile differs from its source or installation owner")
    wheels = profile.get("wheels")
    first_party = manifest.get("wheels")
    if not isinstance(wheels, list) or not isinstance(first_party, list):
        raise ValueError("Worktrees artifact profile lacks its complete wheel set")
    distributions = {
        row["name"]: {key: row[key] for key in ("version", "filename", "sha256")}
        for row in wheels
    }
    if receipt["distributions"] != distributions:
        raise ValueError("Worktrees artifact profile differs from its retained construction inputs")
    by_name = {row["name"]: row for row in first_party}
    if len(by_name) != len(first_party) or "agent-worktrees" not in by_name or any(
        row["name"] in distributions and any(
            row[key] != distributions[row["name"]][key] for key in ("filename", "sha256")
        ) for row in first_party
    ):
        raise ValueError("Worktrees artifact profile differs from its first-party bundle")
    expected = {
        row["filename"]: row["sha256"] for row in wheels if row["name"] not in by_name
    }
    acquisition = binding["acquisition"]
    if expected:
        if (
            not isinstance(acquisition, dict)
            or acquisition.get("schema") != "copilot-extensions.wheel-acquisition"
            or type(acquisition.get("version")) is not int or acquisition["version"] != 1
            or not isinstance(acquisition.get("files"), list)
            or len(acquisition["files"]) != len(expected)
            or {row["filename"]: row["sha256"] for row in acquisition["files"]} != expected
        ):
            raise ValueError("Worktrees artifact dependencies lack exact native feed evidence")
    elif acquisition is not None:
        raise ValueError("Worktrees first-party-only artifact must not assert feed acquisition")
    return policy


def source_profile_policy(receipt: dict, source) -> dict | None:
    """Keep the complete construction binding; return only its policy snapshot.

    The producer owns source authentication, resolution and acquisition. This
    boundary checks their association, not their approval, and the native
    governance owner independently rereads the returned policy before effects.
    """
    binding = receipt["binding"]
    if "profile" not in binding:
        return None
    if set(binding) != {"profile", "source_lock", "build_toolchain", "build_profile"}:
        raise ValueError("Worktrees source profile has incomplete construction evidence")
    profile, locked, build = (
        binding["profile"], binding["source_lock"], binding["build_profile"]
    )
    if not all(isinstance(value, dict) for value in (profile, locked, build)):
        raise ValueError("Worktrees source profile has invalid construction evidence")
    policy = locked.get("policy")
    authentication, acquisition = locked.get("source_authentication"), locked.get("acquisition")
    if (
        locked.get("schema") != "copilot-extensions.source-lock-candidate"
        or type(locked.get("version")) is not int or locked["version"] != 1
        or locked.get("source") != source.document()
        or locked.get("component") != "agent-worktrees" or locked.get("mode") != "source"
        or not isinstance(policy, dict) or policy.get("status") != "ready"
        or policy.get("owner", {}).get("pluginId") != "agent-worktrees"
        or not isinstance(authentication, dict) or not isinstance(acquisition, dict)
        or authentication.get("source") != source.document()
        or authentication.get("owner_policy") != policy.get("policyId")
        or authentication.get("trigger") != locked.get("trigger")
        or locked.get("trigger") not in (
            "release-index-unavailable", "artifact-unavailable", "approved-profile-unavailable",
        )
    ):
        raise ValueError("Worktrees source profile differs from its source or installation owner")
    release = {
        "identity": "source:" + source.source_commit,
        "sha256": "sha256:" + source.assembly_hash,
    }
    digest = "sha256:" + hashlib.sha256(json.dumps(
        locked, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    if (
        any(record.get(key) != value for record in (profile, build) for key, value in {
            "component": "agent-worktrees", "mode": "source", "artifact": None,
            "release": release, "target": locked.get("target"),
            "backend": policy.get("selection", {}).get("backend"),
        }.items())
        or profile.get("approval_source") != policy.get("policyId")
        or profile.get("acquisition_policy") != digest
        or build.get("roots") != locked.get("build_roots")
        or build.get("acquisition_policy") != acquisition.get("policy_id")
        or binding["build_toolchain"] != {
            "packages": {row["name"]: row["version"] for row in build.get("wheels", ())},
            "admitted_profile": build,
        }
        or receipt["distributions"] != {
            row["name"]: {key: row[key] for key in ("version", "filename", "sha256")}
            for row in profile.get("wheels", ())
        }
    ):
        raise ValueError("Worktrees source profile differs from its retained construction inputs")
    return policy
