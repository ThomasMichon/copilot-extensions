"""Assemble the installed Gateway and isolated real SDK peers, without DinD."""

from __future__ import annotations

import copy
import json
import time
from typing import Callable

from engine import Engine
from source import ProofError, digest, save


class Witness:
    def __init__(self, engine: Engine, image: str, report: dict, receipt: dict) -> None:
        self.engine, self.image = engine, image
        self.report, self.receipt = report, receipt
        self.root = engine.root
        self.fixture = self.root / "fixtures"
        self.proof = self.root / "proof"
        self.gateway: str | None = None
        self.observer: str | None = None
        self.phase = 0
        self.initial_identity: dict | None = None

    def passed(self, message: str, evidence: dict | None = None) -> None:
        self.phase += 1
        self.report["ran_until_phase"] = self.phase
        self.report["passed"] += 1
        self.report["results"].append({
            "kind": "PASS", "phase": self.phase, "msg": message,
            "evidence": evidence or {},
        })
        self.engine.journal()
        save(self.root / "cr-report.json", self.report)

    def unchanged(self) -> None:
        assert self.gateway is not None
        observed = self.engine.identity(self.gateway)
        if self.initial_identity is None:
            self.initial_identity = observed
        elif observed != self.initial_identity:
            raise ProofError("gateway-process-replaced")
        self.receipt.setdefault("gateway_identity_samples", []).append(observed)
        self.engine.journal()

    def eventually(self, action: Callable[[], object], *, seconds: int = 20) -> object:
        deadline = min(self.engine.deadline, time.monotonic() + seconds)
        while True:
            try:
                return action()
            except ProofError:
                if time.monotonic() + 0.3 >= deadline:
                    raise
                time.sleep(0.3)

    def probe(self, mode: str) -> dict:
        assert self.observer is not None
        value = json.loads(self.engine.execute(
            self.observer, "python", "/proof/probe.py", mode))
        if value.get("result") != "pass" or value.get("mode") != mode:
            raise ProofError("probe-did-not-pass")
        return value

    def publish(self, document: dict, label: str) -> None:
        path = self.fixture / "gateway/policy.json"
        save(path, document)
        self.receipt.setdefault("policy_candidates", []).append({
            "label": label, "revision": document["manifest"]["revision"],
            "sha256": digest(path.read_bytes()),
        })
        self.engine.journal()

    def service(self, letter: str, instance: str) -> str:
        cid = self.engine.create(
            self.image, name=instance,
            command=["/proof/fixture_service.py", letter, instance],
            mounts=[(self.proof, "/proof", False),
                    (self.fixture / letter, "/credentials", False)])
        self.engine.start(cid)
        return cid

    def run(self) -> None:
        self.fixture.mkdir(mode=0o700)
        prep = self.engine.create(
            self.image, name="fixture-prep", isolated=True,
            command=["/proof/fixture_auth.py", "/output"],
            mounts=[(self.proof, "/proof", False), (self.fixture, "/output", True)])
        prepared = json.loads(self.engine.start(prep, attached=True))
        state = json.loads(self.engine.call("inspect", "--format", "{{json .State}}", prep))
        if state["ExitCode"] != 0 or prepared.get("result") != "pass":
            raise ProofError("synthetic-fixture-preparation-failed")
        if prepared.get("machine") != "x86_64" or prepared.get("platform") != "linux":
            raise ProofError("native-image-execution-not-observed")
        self.receipt["architecture"]["execution"] = "native-amd64"
        self.receipt["initial_policy_sha256"] = digest(
            (self.fixture / "gateway/policy.json").read_bytes())
        self.passed("Installed noneditable wheel; offline synthetic fixture preparation", prepared)
        self.engine.create_network()
        self.gateway = self.engine.create(
            self.image, name="gateway", gateway=True,
            mounts=[(self.fixture / "gateway", "/config", False),
                    (self.fixture / "tls", "/tls", False)])
        self.engine.start(self.gateway)
        self.observer = self.engine.create(
            self.image, name="observer", command=["/proof/probe.py", "idle"],
            mounts=[(self.proof, "/proof", False),
                    (self.fixture / "probe", "/credentials", False)])
        self.engine.start(self.observer)
        self.eventually(lambda: self.probe("ready"))
        self.unchanged()
        self.probe("absent")
        self.passed("Both manifest-declared slots explicitly unavailable before connection")

        original_a = self.service("a", "a-original")
        service_b = self.service("b", "b-original")
        self.eventually(lambda: self.probe("routes"))
        self.probe("denials")
        self.unchanged()
        self.passed("Two isolated SDK services: API, UX, scoped caller and slot/principal denial")
        stream = self.probe("stream")
        self.passed("Real SSE first chunk observed while backend remains unfinished", stream)

        policy = json.loads((self.fixture / "gateway/policy.json").read_text())
        self.publish(policy, "initial-noop")
        self.probe("routes")
        invalid = copy.deepcopy(policy)
        invalid["manifest"]["revision"] = 2
        invalid["unexpected"] = "invalid-synthetic-candidate"
        self.publish(invalid, "invalid")

        def refused() -> None:
            log = self.engine.call("logs", "--tail", "80", self.gateway)
            # Python logging is on stderr, which call preserves in its log file.
            path = self.engine.logs / f"{self.engine.sequence:03d}-logs.log"
            if "service-reload-refused code=" not in log + path.read_text():
                raise ProofError("invalid-candidate-not-observed")

        self.eventually(refused)
        self.probe("routes")
        self.unchanged()
        self.passed("Invalid candidate observed refused; last-known-good routes preserved")

        policy["manifest"]["revision"] = 2
        policy["manifest"]["slots"][1]["role"] = "assets"
        policy["manifest"]["slots"][1]["routes"][0]["prefix"] = "/v2/b"
        self.publish(policy, "valid-role-and-route-update")
        self.eventually(lambda: self.probe("updated"))
        self.unchanged()
        self.passed("Higher candidate role/route update observed without Gateway replacement")

        replacement = self.service("a", "a-replacement")
        self.eventually(lambda: self.probe("replacement"))

        def retired() -> None:
            state = json.loads(self.engine.call(
                "inspect", "--format", "{{json .State}}", original_a))
            if state["Running"] or state["ExitCode"] != 0:
                raise ProofError("replaced-connector-not-retired")

        self.eventually(retired)
        self.receipt["connector_replacement"] = {"old": original_a, "new": replacement}
        self.unchanged()
        self.passed("Authenticated replacement routes to successor; predecessor exits cleanly")
        self.engine.owned(service_b)
        self.engine.call("stop", "--time", "5", service_b, timeout=10)
        self.engine.start(service_b)
        self.eventually(lambda: self.probe("reconnected"))
        self.unchanged()
        self.passed("Owned service container restart establishes a fresh real outbound connection")

        policy["manifest"]["revision"] = 3
        policy["callers"] = [item for item in policy["callers"]
                             if item["principal"] != "caller-a"]
        self.publish(policy, "caller-revocation")
        self.eventually(lambda: self.probe("revoked"))
        self.unchanged()
        self.passed("Caller revocation denies API and UX under unchanged Gateway PID/start")
