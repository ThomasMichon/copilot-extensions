"""Opt-in fixed Authelia experiment; never a deployment or enrollment command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import signal
import sys
import time

from diagnostics import CaptureError, diagnostic_records, fixture_secrets, redact

BASE = Path(__file__).resolve().parent
SHARED = BASE.parent / "gateway-role-tunnels"
# The sibling's exact committed bytes are subsequently checked by materialize.
sys.path.insert(0, str(SHARED))
from engine import Engine, IMAGE_ID, container_args  # noqa: E402
from source import ProofError, digest, fresh_run, git, materialize, private_root, save  # noqa: E402

PREFIX = "tools/clean-room/authelia-profile-qualification/"
FILES = frozenset({"run.py", "prepare.py", "probe.py", "diagnostics.py", "README.md"})
PROVIDER = (
    "authelia/authelia@sha256:"
    "bd97cff4fcbf715b5ff1f9ae286afbe6033afce385302520b0368122d43a6f54"
)
VERSION = "4.39.28"
SIGNALS = (signal.SIGINT, signal.SIGTERM)
PROBE_ERRORS = frozenset({
    "fixture-preparation-failed", "response-size-refused", "response-shape-refused",
    "token-size-refused", "token-shape-refused", "provider-jwks-shape-refused",
    "provider-key-selection-refused", "resource-transport-not-tls",
    "resource-challenge-failed", "resource-response-refused", "provider-discovery-failed",
    "provider-tls-failed", "provider-unavailable", "provider-discovery-mismatch",
    "provider-jwks-failed", "provider-token-request-failed", "provider-access-token-missing",
    "provider-probe-failed",
})


def diagnostic_traits(data: bytes) -> dict:
    """Project known categories without retaining any message or arbitrary JSON value."""
    found = set()
    unknown = 0
    for line in data.splitlines()[:160]:
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            unknown += 1
            continue
        if not isinstance(value, dict):
            unknown += 1
            continue
        if (value.get("result") == "fail" and isinstance(value.get("code"), str)
                and value["code"] in PROBE_ERRORS):
            found.add(value["code"])
        elif value.get("result") == "prepared":
            found.add("fixture-prepared")
        elif value.get("result") in ("verified", "incompatible"):
            found.add("probe-finished")
        elif value.get("level") in ("error", "fatal"):
            found.add("provider-error")
            messages = [value.get(field) for field in ("msg", "message", "error")]
            for message in messages:
                if not isinstance(message, str):
                    continue
                message = message.lower()
                for field in ("access_control", "identity_providers", "session",
                              "authentication_backend", "storage", "server", "notifier"):
                    if field in message:
                        found.add("provider-config-" + field.replace("_", "-"))
                for fragment, category in (
                    ("permission denied", "permission-denied"),
                    ("read-only file system", "readonly-filesystem"),
                    ("address already in use", "address-in-use"),
                    ("certificate", "certificate"), ("tls", "tls"),
                    ("jwks", "jwks"), ("client_secret", "client-secret"),
                    ("sqlite", "sqlite"), ("database", "database"),
                    ("no such file", "file-missing"), ("not found", "not-found"),
                    ("unmarshal", "decode"), ("password", "password"),
                    ("user", "user-backend"), ("startup", "startup"),
                    ("configuration", "configuration"), ("context canceled", "cancelled"),
                ):
                    if fragment in message:
                        found.add("provider-cause-" + category)
        else:
            unknown += 1
    return {"output_bytes": len(data), "raw_output_retained": False,
            "classifications": sorted(found), "unclassified_lines": unknown}


class Terminated(BaseException):
    """Signal control flow still owes exact owned cleanup."""


def materialize_probe(repository: Path, ref: str, root: Path) -> dict:
    result = materialize(repository, ref, root)
    sha = result["source_commit"]
    proof = root / "qualifier"
    proof.mkdir(mode=0o700)
    hashes = {}
    for name in sorted(FILES):
        path = PREFIX + name
        listing = git(repository, "ls-tree", "-z", sha, "--", path).split(b"\0")
        if len(listing) != 2 or listing[-1]:
            raise ProofError("qualifier-source-missing", "repo-config")
        metadata, supplied_path = listing[0].split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        if mode not in ("100644", "100755") or kind != "blob" or supplied_path.decode() != path:
            raise ProofError("qualifier-source-not-regular", "repo-config")
        data = git(repository, "cat-file", "blob", oid)
        if len(data) > 128 * 1024 or data != (BASE / name).read_bytes():
            raise ProofError("qualifier-source-mismatch", "repo-config")
        (proof / name).write_bytes(data)
        hashes[path] = digest(data)
    manifest = {"source_commit": sha, "blobs": hashes}
    save(root / "qualifier-source.json", manifest)
    result["qualifier_manifest_sha256"] = digest((root / "qualifier-source.json").read_bytes())
    return result


class QualifierEngine(Engine):
    """A separate closed profile; the Gateway witness's create policy is unchanged."""

    def write_log(self, path: Path, data: bytes) -> None:
        value = diagnostic_traits(data)
        try:
            value.update(diagnostic_records(data, self.root / "fixtures"))
        except CaptureError as exc:
            value.update(diagnostic_capture="unavailable", capture_error=str(exc))
            self.receipt["diagnostic_capture_error"] = str(exc)
        Engine.write_log(path, json.dumps(value).encode("ascii") + b"\n")

    def cleanup(self) -> list[str]:
        states = []
        for cid in self.containers:
            try:
                value = json.loads(self.call(
                    "inspect", "--format", "{{json .State}}", cid, cleanup=True))
                status, exit_code, oom = value["Status"], value["ExitCode"], value["OOMKilled"]
                if (status not in ("created", "running", "paused", "restarting", "removing",
                                   "exited", "dead")
                        or type(exit_code) is not int or type(oom) is not bool
                        or not isinstance(value.get("Error"), str)):
                    raise ProofError("container-state-shape-refused")
                states.append({
                    "container_id": cid, "status_before_cleanup": status,
                    "exit_code": exit_code, "oom_killed": oom,
                    "error": redact(value["Error"], fixture_secrets(self.root / "fixtures")),
                })
            except (ProofError, CaptureError, OSError, ValueError, KeyError, TypeError):
                states.append({"container_id": cid, "inspection": "unavailable"})
        self.receipt["states_before_cleanup"] = states
        errors = []
        try:
            self.journal()
        except (OSError, ProofError):
            errors.append("pre-cleanup-receipt-write-failed")
        finally:
            try:
                errors.extend(super().cleanup())
            except (OSError, ProofError):
                errors.extend(self.receipt.get("cleanup_errors", []))
                errors.append("cleanup-receipt-write-failed")
        self.receipt["cleanup_errors"] = errors
        return errors

    def create_fixture(self, image: str, kind: str) -> str:
        if not IMAGE_ID.fullmatch(image) or kind not in ("prepare", "provider", "probe"):
            raise ProofError("invalid-qualifier-container")
        root = self.root
        if kind == "prepare":
            mounts = [(root / "qualifier", "/proof", False), (root / "fixtures", "/output", True)]
            network, command = "none", ["/proof/prepare.py", "/output"]
        elif kind == "probe":
            mounts = [(root / "qualifier", "/proof", False),
                      (root / "fixtures/client", "/credentials", False)]
            network, command = self.network, ["/proof/probe.py"]
        else:
            mounts = [(root / "fixtures/provider", "/config", False)]
            network, command = self.network, None
        for path, _, _ in mounts:
            if path.is_symlink() or path.resolve() != path or root not in path.parents:
                raise ProofError("qualifier-mount-indirection")
        cidfile = root / (kind + ".cid")
        args = container_args(
            image, cidfile, root.name + "-" + kind, network, mounts, False, command)
        if kind == "provider":
            args[-1:] = [
                "--network-alias", "issuer.fixture.test", "--no-healthcheck",
                "--entrypoint", "authelia", image, "--config", "/config/configuration.yml",
            ]
        self.pending.append(cidfile)

        def retain(_: bytes) -> None:
            cid = self.read_cid(cidfile)
            if cid not in self.containers:
                self.containers.append(cid)
            self.journal()

        self.call(*args, on_success=retain)
        return self.read_cid(cidfile)

    def attached(self, cid: str, timeout: int) -> dict:
        self.owned(cid)
        raw = self.call("start", "--attach", cid, timeout=timeout)
        try:
            result = json.loads(raw)
        except ValueError:
            raise ProofError("qualifier-output-shape-refused") from None
        if not isinstance(result, dict):
            raise ProofError("qualifier-output-shape-refused")
        return result

    def provider_image(self) -> str:
        self.call("pull", "--platform", self.architecture.platform, PROVIDER, timeout=120)
        metadata = json.loads(self.call("image", "inspect", PROVIDER))[0]
        image = metadata["Id"]
        if (not IMAGE_ID.fullmatch(image) or metadata["Architecture"] != self.architecture.name
                or metadata["Os"] != "linux" or PROVIDER not in metadata["RepoDigests"]):
            raise ProofError("provider-image-pin-mismatch")
        self.receipt.update(provider_image_id=image, provider_image=PROVIDER,
                            provider_version=VERSION)
        self.journal()
        return image


def admit_outcome(value: dict) -> dict:
    """Allow only the finite non-secret probe vocabulary into the durable receipt."""
    common = {"result", "resource_tls_verified", "identity_error", "traits",
              "provider_tls_verified", "profile", "grant", "human_flow_exercised",
              "enrollment_qualified", "policy_qualified", "production_qualified"}
    if (set(value) - common or value.get("result") not in ("verified", "incompatible")
            or value.get("profile") != "jwt-pop-v1"
            or value.get("grant") != "client_credentials"
            or value.get("provider_tls_verified") is not True
            or type(value.get("resource_tls_verified")) is not bool
            or any(value.get(key) is not False for key in (
                "human_flow_exercised", "enrollment_qualified", "policy_qualified",
                "production_qualified"))):
        raise ProofError("qualifier-outcome-refused")
    codes = {
        "invalid-trust", "invalid-key", "unsupported-profile", "invalid-credential",
        "unknown-key", "invalid-signature", "invalid-key-binding", "invalid-proof",
        "authority-unavailable", "invalid-clock", "clock-rollback", "invalid-request-context",
        "expired-credential", "proof-replayed", "resource-limit", "scheme-refused",
        "invalid-token-binding", "provider-token-type",
    }
    if "identity_error" in value and value["identity_error"] not in codes:
        raise ProofError("qualifier-outcome-refused")
    if value["result"] == "incompatible" and "identity_error" not in value:
        raise ProofError("qualifier-outcome-refused")
    traits = value.get("traits")
    booleans = {
        "access_alg_es256", "access_type_at_jwt", "audience_exact",
        "proof_binding_present", "proof_binding_matches", "traits_are_unverified",
        "token_type_dpop",
    }
    if (not isinstance(traits, dict) or set(traits) != booleans | {"audience_shape"}
            or any(type(traits[key]) is not bool for key in booleans)
            or traits["audience_shape"] not in ("string", "array", "other")
            or traits["traits_are_unverified"] is not True):
        raise ProofError("qualifier-traits-refused")
    if value["result"] == "verified" and (
            "identity_error" in value or value["resource_tls_verified"] is not True
            or any(traits[key] is not True for key in booleans - {"traits_are_unverified"})
            or traits["audience_shape"] != "string"):
        raise ProofError("qualifier-success-not-established")
    return value


def run(repository: Path, ref: str, root: Path, build_timeout: int) -> dict:
    receipt = {
        "version": 1, "scenario": "authelia-profile-qualification", "result": "fail",
        "cleanup": "pending", "started_at": int(time.time()),
        "host_ports_published": False, "host_socket_granted": False,
        "production_qualified": False, "human_flow_exercised": False,
    }
    engine = None
    previous = {number: signal.getsignal(number) for number in SIGNALS}

    def terminated(signum: int, frame: object) -> None:
        for number in SIGNALS:
            signal.signal(number, signal.SIG_IGN)
        raise Terminated

    for number in SIGNALS:
        signal.signal(number, terminated)
    try:
        receipt.update(materialize_probe(repository, ref, root))
        (root / "fixtures").mkdir(mode=0o700)
        engine = QualifierEngine(root, receipt)
        engine.journal()
        engine.preflight()
        image = engine.build(receipt["source_commit"], build_timeout)
        provider_image = engine.provider_image()
        prepared = engine.attached(engine.create_fixture(image, "prepare"), 35)
        if prepared != {"result": "prepared", "credentials": "synthetic-files-only"}:
            raise ProofError("qualifier-preparation-failed")
        engine.create_network()
        provider = engine.create_fixture(provider_image, "provider")
        engine.start(provider)
        outcome = admit_outcome(engine.attached(engine.create_fixture(image, "probe"), 110))
        receipt.update(result=outcome["result"], outcome=outcome)
    except (ProofError, OSError, ValueError, KeyError, TypeError, Terminated) as exc:
        receipt["error"] = str(exc) if isinstance(exc, ProofError) else "qualification-run-failed"
    finally:
        for number in SIGNALS:
            signal.signal(number, signal.SIG_IGN)
        try:
            errors = engine.cleanup() if engine else []
            receipt["cleanup"] = "failed" if errors else "complete"
            if errors:
                receipt["result"] = "fail"
            receipt["finished_at"] = int(time.time())
            try:
                save(root / "receipt.json", receipt)
            except OSError:
                receipt.update(result="fail", error="final-receipt-write-failed",
                               receipt_persistence="failed")
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--repository", type=Path, default=BASE.parents[2])
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--build-timeout", type=int, default=900)
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        parser.error("requires native Linux/WSL and an existing Linux Docker engine")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        parser.error("requires an exact full source commit")
    if not 30 <= args.build_timeout <= 1800:
        parser.error("build timeout must be 30..1800 seconds")
    try:
        root = private_root(args.results_root, args.repository.resolve(strict=True))
        results = fresh_run(root)
    except (ProofError, OSError):
        parser.error("private results root unavailable")
    receipt = run(args.repository, args.source_commit, results, args.build_timeout)
    print(json.dumps({"results": str(results), "result": receipt["result"],
                      "cleanup": receipt["cleanup"], "error": receipt.get("error"),
                      "receipt_persistence": receipt.get("receipt_persistence", "complete")}))
    return {"verified": 0, "incompatible": 2}.get(receipt["result"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
