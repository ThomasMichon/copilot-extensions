"""Fixed official ARC Helm convergence, inside an independently adopted owner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from threading import Event, Lock
import time
from urllib.parse import urlsplit

from .loadout_preparation import PreparationStore
from .node_journal import NodeJournal
from .operations import OperationIntent, TargetReceipt
from .planning import PlanError, canonical, decode, digest, identifier, integer, record
from .runtime_admission import AdmissionAuthority, Permit

_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_IMAGE = re.compile(r"[a-z0-9][a-z0-9./:_-]{0,240}@sha256:[a-f0-9]{64}\Z")
_REFERENCE_FIELDS = (
    "artifact_ref", "configuration_ref", "credential_ref", "forge_scope_ref",
    "runner_group_ref", "trust_policy_ref", "toolchain_ref", "cluster",
)


def _hash(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        raise PlanError("ARC selection requires a lowercase SHA-256")


@dataclass(frozen=True)
class ArcDeployment:
    """Current existing-owner assertion, not a wire grant or artifact authenticator.

    Its producer authenticates the official chart, selected release, support
    receipts, configuration/credential references, cluster and single-writer
    custody on every call. Constructing these bytes proves none of those things.
    """

    encoded: bytes
    chart: bytes = field(repr=False)

    def __post_init__(self):
        if type(self.encoded) is not bytes or type(self.chart) is not bytes:
            raise PlanError("ARC selection must retain immutable bytes")
        value = decode(self.encoded)
        record(value, "fleet-manager.arc-deployment", {
            "fleet", "installation", "target", "allocation", "generation", "preparation",
            "declaration", "release_digest", "policy_revision", "valid_until",
            "references", "support", "settings", "chart_sha256", "outfitting_seconds",
        })
        if canonical(value) != self.encoded:
            raise PlanError("ARC selection must be canonical")
        for name in ("fleet", "installation", "target", "allocation", "policy_revision"):
            identifier(value[name], "ARC selection reference")
        for name in ("preparation", "declaration", "release_digest", "chart_sha256"):
            _hash(value[name])
        integer(value["generation"], "ARC generation", minimum=1)
        integer(value["valid_until"], "ARC selection expiry", minimum=1)
        integer(value["outfitting_seconds"], "ARC outfitting duration", minimum=1, maximum=1800)
        if (not 0 < len(self.chart) <= 64 * 1024 * 1024
                or hashlib.sha256(self.chart).hexdigest() != value["chart_sha256"]):
            raise PlanError("ARC selected chart content mismatch")
        refs = value["references"]
        if not isinstance(refs, dict) or set(refs) != set(_REFERENCE_FIELDS):
            raise PlanError("ARC requires exact selected references")
        for ref in refs.values():
            identifier(ref, "ARC selected reference")
        settings = value["settings"]
        if not isinstance(settings, dict) or set(settings) != {
            "namespace", "release", "controller_namespace", "controller_service_account",
            "node", "github_url", "runner_group", "credential_secret", "runner_image",
        }:
            raise PlanError("ARC requires explicit supported settings")
        for name in ("namespace", "release", "controller_namespace",
                     "controller_service_account", "credential_secret"):
            if type(settings[name]) is not str or not _NAME.fullmatch(settings[name]):
                raise PlanError("ARC requires bounded Kubernetes names")
        if len(settings["release"]) > 45 or settings["namespace"] == settings["controller_namespace"]:
            raise PlanError("ARC runner and controller namespaces must be separate")
        for name in ("node", "runner_group"):
            identifier(settings[name], "ARC selected setting")
        if (type(settings["runner_image"]) is not str
                or not _IMAGE.fullmatch(settings["runner_image"])):
            raise PlanError("ARC runner image must be digest pinned")
        if type(settings["github_url"]) is not str:
            raise PlanError("ARC requires a non-secret selected HTTPS forge scope")
        try:
            url = urlsplit(settings["github_url"])
            if (url.scheme != "https" or not url.hostname or url.username is not None
                    or url.password is not None or url.query or url.fragment
                    or url.port not in (None, 443) or not url.path.strip("/")
                    or any(ord(char) <= 32 for char in settings["github_url"])):
                raise ValueError
        except (ValueError, TypeError):
            raise PlanError("ARC requires a non-secret selected HTTPS forge scope") from None
        support = value["support"]
        if not isinstance(support, dict):
            raise PlanError("ARC requires current support receipts")
        for allocation, receipt in support.items():
            identifier(allocation, "ARC support allocation")
            if not isinstance(receipt, dict) or set(receipt) != {"target", "receipt"}:
                raise PlanError("ARC support receipt binding is invalid")
            identifier(receipt["target"], "ARC support target")
            _hash(receipt["receipt"])

    @classmethod
    def from_dict(cls, value, *, chart: bytes):
        return cls(canonical(value), chart)

    def to_dict(self):
        return json.loads(self.encoded)

    @property
    def deployment_digest(self):
        return hashlib.sha256(self.encoded).hexdigest()


@dataclass(frozen=True)
class ArcTooling:
    """Adopted Helm 3 and cluster credentials; never selected by a control request."""

    helm: Path = field(repr=False)
    kubeconfig: Path = field(repr=False)
    context: str
    directory: Path = field(repr=False)
    run: Callable[..., subprocess.CompletedProcess] = field(repr=False, compare=False)

    def __post_init__(self):
        for path in (self.helm, self.kubeconfig, self.directory):
            if not isinstance(path, Path) or not path.is_absolute():
                raise PlanError("ARC tooling requires adopted absolute paths")
        if (not self.helm.is_file() or not self.kubeconfig.is_file()
                or not self.directory.is_dir() or not callable(self.run)):
            raise PlanError("ARC adopted tooling is unavailable")
        identifier(self.context, "ARC Kubernetes context")


class ArcHelmOwner:
    """One adopted scale-set owner; ARC, not this class, owns listener/runner pods.

    No installer, automatic retries, pod deletion or manager bootstrap exists here.
    The embedding owns native settlement and independent physical writer custody.
    """

    def __init__(self, *, tooling: ArcTooling, preparations: PreparationStore,
                 journal: NodeJournal, admission: AdmissionAuthority,
                 current: Callable[[], ArcDeployment], clock: Callable[[], int]):
        if not callable(current) or not callable(clock):
            raise PlanError("ARC requires current owner admission")
        self.tooling, self.preparations, self.journal = tooling, preparations, journal
        self.admission, self.current, self.clock = admission, current, clock
        self.native_failure: subprocess.SubprocessError | None = None
        self._lock = Lock()

    def _selected(self, request, expected=None):
        selected = self.current()
        if type(selected) is not ArcDeployment or (expected is not None and selected != expected):
            raise PlanError("ARC current selected deployment changed")
        value, intent = selected.to_dict(), request.to_dict()
        now = self.clock()
        integer(now, "ARC owner time")
        proposal = self.preparations.read()
        if (proposal is None or proposal["decision"] is None
                or proposal["candidate"]["status"] != "prepared"
                or proposal["generation"] != value["generation"]
                or proposal["digest"] != value["preparation"]
                or now >= min(value["valid_until"], proposal["candidate"]["inventory_expires_at"])
                or (value["fleet"], value["installation"]) != (
                    self.preparations.fleet, self.preparations.installation)
                or (self.journal.installation, self.journal.target, self.journal.authority) != (
                    value["installation"], value["target"], intent["authority"])
                or intent["command"] != "runner.converge"
                or intent["target"] != value["target"] or intent["generation"] != value["generation"]
                or intent["parameters"] != {
                    "preparation": value["preparation"], "allocation": value["allocation"],
                    "deployment": selected.deployment_digest,
                }):
            raise PlanError("ARC operation differs from current reviewed deployment")
        composition = proposal["plan"]["composition"]
        allocation = composition["allocations"].get(value["allocation"])
        if (allocation is None or allocation["template"] != {
                "id": "actions.arc", "revision": "1", "source": "fleet-manager"}
                or allocation["configuration"].get("adapter") != "helm.arc"
                or allocation["target"] != value["target"]
                or digest(allocation) != value["declaration"]
                or any(allocation["slots"][name] != value["references"][name]
                       for name in _REFERENCE_FIELDS)
                or allocation["slots"]["scale_set"] != value["settings"]["release"]
                or allocation["slots"]["architecture"] not in ("x64", "arm64")):
            raise PlanError("ARC selected declaration differs from prepared allocation")
        required = set()

        def visit(name):
            for dependency in composition["allocations"][name]["requires"]:
                if dependency not in required:
                    required.add(dependency)
                    visit(dependency)

        visit(value["allocation"])
        if (set(value["support"]) != required
                or any(receipt["target"] != composition["allocations"][name]["target"]
                       for name, receipt in value["support"].items())):
            raise PlanError("ARC support closure is not currently admitted")
        return selected, allocation

    @staticmethod
    def _values(selected, allocation):
        value, slots = selected.to_dict(), allocation["slots"]
        settings = value["settings"]
        def quantities(prefix):
            return {
                "cpu": f"{slots[prefix + 'cpu_millis']}m",
                "memory": str(slots[prefix + "memory_bytes"]),
                "ephemeral-storage": str(slots[prefix + "disk_bytes"]),
            }
        return {
            "githubConfigUrl": settings["github_url"],
            "githubConfigSecret": settings["credential_secret"],
            "runnerGroup": settings["runner_group"], "runnerScaleSetName": settings["release"],
            "minRunners": slots["min_runners"], "maxRunners": slots["max_runners"],
            "controllerServiceAccount": {
                "namespace": settings["controller_namespace"],
                "name": settings["controller_service_account"],
            },
            "template": {"spec": {
                "automountServiceAccountToken": False,
                "nodeSelector": {"kubernetes.io/hostname": settings["node"],
                                 "kubernetes.io/arch": {"x64": "amd64", "arm64": "arm64"}[
                                     slots["architecture"]]},
                "containers": [{
                    "name": "runner", "image": settings["runner_image"],
                    "command": ["/home/runner/run.sh"],
                    "resources": {"requests": quantities(""), "limits": quantities("limit_")},
                    "securityContext": {"allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]}},
                }],
            }},
        }

    def _helm(self, arguments, *, deadline, input_text=None):
        if self.native_failure is not None:
            raise PlanError("ARC native process custody remains unsettled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PlanError("ARC convergence deadline exceeded")
        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith(("HELM_", "KUBE"))}
        environment.update(HELM_NO_PLUGINS="1")
        settled = Event()
        try:
            result = self.tooling.run(
                [str(self.tooling.helm), *arguments, "--kubeconfig", str(self.tooling.kubeconfig),
                 "--kube-context", self.tooling.context],
                cwd=str(self.tooling.directory), env=environment, timeout=remaining,
                max_stdout_bytes=1024 * 1024, max_stderr_bytes=4096,
                input_text=input_text, on_settled=settled.set,
            )
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError, UnicodeError):
            raise PlanError("ARC native operation unavailable or uncertain") from None
        except subprocess.SubprocessError as error:
            if not settled.is_set():
                self.native_failure = error
                raise
            raise PlanError("ARC native operation refused or uncertain") from None
        if result.returncode != 0 or type(result.stdout) is not str:
            raise PlanError("ARC native operation refused or uncertain")
        return result.stdout

    def _inspect(self, selected, *, deadline):
        value = selected.to_dict()
        namespace, release = (value["settings"][name] for name in ("namespace", "release"))
        listed = decode(self._helm(
            ["list", "--all", "--namespace", namespace, "--filter", f"^{release}$",
             "--output", "json"], deadline=deadline,
        ).encode())
        if listed == []:
            return None
        if (not isinstance(listed, list) or len(listed) != 1
                or not isinstance(listed[0], dict) or listed[0].get("name") != release
                or listed[0].get("namespace") != namespace):
            raise PlanError("ARC release discovery differs from selection")
        status = decode(self._helm(
            ["status", release, "--namespace", namespace, "--output", "json"], deadline=deadline,
        ).encode())
        owner = "fleet-manager:" + digest({
            key: value[key] for key in ("fleet", "installation", "target", "allocation")
        })
        if not isinstance(status, dict) or not isinstance(status.get("info"), dict):
            raise PlanError("ARC release status is invalid")
        description = status["info"].get("description")
        if (status.get("name") != release or status.get("namespace") != namespace
                or type(description) is not str or not description.startswith(owner + ":")):
            raise PlanError("ARC release is not owned by this installation")
        values = decode(self._helm(
            ["get", "values", release, "--namespace", namespace, "--output", "json"],
            deadline=deadline,
        ).encode())
        return status, values, owner

    def converge(self, request: OperationIntent, permit: Permit) -> TargetReceipt:
        """Run at most one Helm effect; UNKNOWN survives restart and forbids replay.

        Success reports observed Helm convergence only, not listener readiness,
        authentication, workload use, whole-loadout completion or rollout.
        """
        if not self._lock.acquire(blocking=False):
            raise PlanError("ARC owner is busy")
        try:
            selected, allocation = self._selected(request)
            deadline = time.monotonic() + selected.to_dict()["outfitting_seconds"]
            permit = self.admission.revalidate(permit, request)
            value = selected.to_dict()
            if permit.fleet != value["fleet"]:
                raise PlanError("ARC permit fleet differs from selected deployment")
            resource = digest({
                "cluster": value["references"]["cluster"],
                "namespace": value["settings"]["namespace"], "release": value["settings"]["release"],
            })
            prior = self.journal.accept_named_external(request, resource=resource)
            if prior.to_dict()["status"] != "accepted":
                self.admission.complete(permit, request)
                return prior
            self._inspect(selected, deadline=deadline)
            values = self._values(selected, allocation)
            owner = "fleet-manager:" + digest({
                key: value[key] for key in ("fleet", "installation", "target", "allocation")
            })
            description = owner + ":" + digest({
                "chart": value["chart_sha256"], "values": digest(values),
            })
            with TemporaryDirectory(prefix="fleet-arc-", dir=self.tooling.directory) as temporary:
                chart = Path(temporary) / "gha-runner-scale-set.tgz"
                chart.write_bytes(selected.chart)
                self._selected(request, selected)
                permit = self.admission.revalidate(permit, request)
                if not self.journal.begin_external(request):
                    self.admission.complete(permit, request)
                    return self.journal.inspect(request)
                self.admission.complete(permit, request)
                remaining = math.floor(deadline - time.monotonic())
                if remaining <= 0:
                    raise PlanError("ARC convergence deadline exceeded before effect")
                self._helm([
                    "upgrade", value["settings"]["release"], str(chart), "--install",
                    "--namespace", value["settings"]["namespace"], "--reset-values",
                    "--no-hooks", "--skip-crds", "--atomic", "--wait",
                    "--timeout", f"{remaining}s", "--description", description,
                    "--values", "-", "--output", "json",
                ], deadline=deadline, input_text=canonical(values).decode())
                after = self._inspect(selected, deadline=deadline)
                if (after is None or after[0]["info"]["status"] != "deployed"
                        or after[0]["info"]["description"] != description or after[1] != values):
                    raise PlanError("ARC convergence lacks matching terminal evidence")
            evidence = {
                "deployment": selected.deployment_digest, "revision": after[0]["version"],
                "status": "deployed", "values": digest(after[1]),
            }
            integer(evidence["revision"], "ARC Helm revision", minimum=1)
            result = request.to_dict()
            del result["parameters"]
            result.update(schema="fleet-manager.target-receipt", sequence=3, status="succeeded",
                          request_digest=request.request_digest, result_ref=digest(evidence))
            return self.journal.reconcile(request, TargetReceipt.from_dict(result))
        finally:
            self._lock.release()
