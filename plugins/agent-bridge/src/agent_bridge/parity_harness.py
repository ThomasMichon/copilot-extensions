"""Redacted, container-first acceptance harness for remote venue parity."""

from __future__ import annotations

import json
import re
import shlex
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

_RESULT_MARKER = "VENUE_PARITY_JSON:"
_PROBE_MARKER = "VENUE_PARITY_PROBE:"
_REATTACH_MARKER = "VENUE_PARITY_REATTACH_OK"
_FRONTEND_RESTART_HOSTINDEX_LOSS = "frontend-restart-hostindex-loss"
_TERMINAL = {"failed", "ended", "stopped"}
_INACTIVE_FOR_FAULT = _TERMINAL
_SECRET_SHAPES = (
    re.compile(r"(?i)\b(?:password|token)=[^\s]+"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\."),
)


class ParityFailure(RuntimeError):
    """A parity scenario failed its explicit acceptance gate."""

    def __init__(
        self,
        message: str,
        *,
        evidence: ParityEvidence | None = None,
    ) -> None:
        self.evidence = evidence
        super().__init__(message)


@dataclass
class ParityEvidence:
    target: str
    session_id: str = ""
    initial_acp_session_id: str | None = None
    resumed_acp_session_id: str | None = None
    initial_host_pid: int | None = None
    resumed_host_pid: int | None = None
    initial_child_pid: int | None = None
    resumed_child_pid: int | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    observed: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "target": self.target,
            "session_id": self.session_id,
            "initial_acp_session_id": self.initial_acp_session_id,
            "resumed_acp_session_id": self.resumed_acp_session_id,
            "initial_host_pid": self.initial_host_pid,
            "resumed_host_pid": self.resumed_host_pid,
            "initial_child_pid": self.initial_child_pid,
            "resumed_child_pid": self.resumed_child_pid,
            "checks": self.checks,
            "observed": self.observed,
        }


def _wait_for_status(client, session_id: str, wanted: set[str], timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get_session(session_id)
        status = str(last.get("status") or "")
        if status in wanted:
            return last
        if status in _TERMINAL:
            raise ParityFailure(
                f"session entered {status}: {last.get('connect_failure') or last}"
            )
        time.sleep(1.0)
    raise ParityFailure(
        f"session {session_id} did not reach {sorted(wanted)} "
        f"within {timeout:.0f}s (last={last.get('status')!r})"
    )


def _agent_text(events: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for event in events:
        if event.get("event") != "agent_message":
            continue
        data = event.get("data") or {}
        text = data.get("text")
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks)


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _assert_redacted(values: list[str]) -> None:
    for value in values:
        if any(pattern.search(value) for pattern in _SECRET_SHAPES):
            raise ParityFailure(
                "credential-shaped output reached the durable event stream"
            )
def _extract_marker_json(text: str, marker: str) -> dict[str, Any]:
    index = text.rfind(marker)
    if index < 0:
        raise ParityFailure(f"output omitted marker {marker!r}")
    payload = text[index + len(marker):].lstrip(" `\n")
    try:
        result, _end = json.JSONDecoder().raw_decode(payload)
    except json.JSONDecodeError as exc:
        raise ParityFailure(f"output after {marker!r} was not valid JSON") from exc
    if not isinstance(result, dict):
        raise ParityFailure(f"output after {marker!r} was not a JSON object")
    return result


def _wait_for_turn(
    client,
    session_id: str,
    *,
    after_event_id: int,
    timeout: float,
) -> tuple[str, dict[str, Any] | None, int]:
    deadline = time.monotonic() + timeout
    next_id = after_event_id + 1
    agent_chunks: list[str] = []
    probe_chunks: list[str] = []
    while time.monotonic() < deadline:
        events = client.read_range(session_id, start=next_id)
        if events:
            latest = max(int(event.get("id") or 0) for event in events)
            next_id = latest + 1
            agent_chunks.append(_agent_text(events))
            for event in events:
                probe_chunks.extend(_strings(event.get("data") or {}))
                if event.get("event") == "turn_complete":
                    _assert_redacted(probe_chunks)
                    probe_text = "\n".join(probe_chunks)
                    probe = (
                        _extract_marker_json(probe_text, _PROBE_MARKER)
                        if _PROBE_MARKER in probe_text
                        else None
                    )
                    return "".join(agent_chunks), probe, latest
        session = client.get_session(session_id)
        if session.get("status") in _TERMINAL:
            raise ParityFailure(
                "session stopped before turn_complete was emitted"
            )
        time.sleep(1.0)
    raise ParityFailure(f"turn did not complete within {timeout:.0f}s")


def _parse_result(message: str) -> dict[str, Any]:
    return _extract_marker_json(message, _RESULT_MARKER)


def _quality_prompt(
    *,
    auth: bool,
    ado_url: str | None,
    azure_scope: str | None,
) -> str:
    parsed = urlparse(ado_url) if ado_url else None
    if parsed and (parsed.username or parsed.password):
        raise ParityFailure("ADO URL must not contain embedded credentials")
    probe = f"""
import json, os, subprocess

def run(argv, input_text=None):
    return subprocess.run(
        argv, input=input_text, capture_output=True, text=True, timeout=90
    )

def credential(host, path=None):
    fields = ["protocol=https", "host=" + host]
    if path:
        fields.append("path=" + path)
    result = run(["git", "credential", "fill"], "\\n".join(fields) + "\\n\\n")
    values = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    return (
        result.returncode == 0
        and bool(values.get("username"))
        and bool(values.get("password"))
    )

out = {{
    "cwd": os.getcwd(),
    "github_credential": None,
    "github_api": None,
    "ado_credential": None,
    "ado_ls_remote": None,
    "azure_token": None,
}}
if {auth!r}:
    out["github_credential"] = credential("github.com")
    out["github_api"] = run(["gh", "api", "user", "--jq", ".login"]).returncode == 0
if {bool(ado_url)!r}:
    out["ado_credential"] = credential(
        {parsed.hostname if parsed else None!r},
        {parsed.path.lstrip("/") if parsed else None!r},
    )
    out["ado_ls_remote"] = run(
        ["git", "ls-remote", {ado_url!r}, "HEAD"]
    ).returncode == 0
if {bool(azure_scope)!r}:
    token = run(
        ["azure-auth-helper", "get-access-token", {azure_scope!r}]
    )
    out["azure_token"] = token.returncode == 0 and bool(token.stdout.strip())
print("{_PROBE_MARKER}" + json.dumps(out, separators=(",", ":")))
""".strip()
    return (
        "Run the exact Python probe below as one shell tool call. It captures all "
        "credential values inside the subprocess and prints booleans only; do not "
        "replace it with direct credential commands.\n\n"
        f"python3 -c {shlex.quote(probe)}\n\n"
        "Then, without reading settings files or listing .ai, report the names of "
        "repo-local capabilities already present in your loaded context. End with "
        "one line `VENUE_PARITY_JSON:<json>` containing only "
        '`{"capabilities":["name",...]}`. Do not repeat probe output.'
    )


def run(
    client,
    target: str,
    *,
    expected_workspace: str | None = None,
    expected_capability: str | None = None,
    auth: bool = False,
    ado_url: str | None = None,
    azure_scope: str | None = None,
    startup_timeout: float = 600.0,
    turn_timeout: float = 600.0,
    keep_session: bool = False,
    fault: str | None = None,
    fault_handler: Callable[[str, float], dict[str, Any]] | None = None,
) -> ParityEvidence:
    """Run baseline quality/auth plus same-child stop/resume acceptance."""
    if fault not in {None, _FRONTEND_RESTART_HOSTINDEX_LOSS}:
        raise ParityFailure(f"unsupported parity fault: {fault}")
    if fault and fault_handler is None:
        raise ParityFailure(f"parity fault {fault!r} requires a fault handler")

    caller_id = f"venue-parity:{uuid.uuid4().hex}"
    evidence = ParityEvidence(target=target)
    session_id = ""
    try:
        try:
            created = client.start_session(
                agent=target,
                caller_id=caller_id,
                force_new=True,
                request_timeout=startup_timeout,
            )
        except Exception:
            # A timed-out POST may have created the session server-side. The
            # unique caller id lets us find and clean only this harness run.
            try:
                for candidate in client.list_sessions():
                    if candidate.get("caller_id") == caller_id:
                        client.end_session(candidate["session_id"], force=True)
            except Exception:
                pass
            raise
        session_id = str(created["session_id"])
        evidence.session_id = session_id
        session = _wait_for_status(
            client,
            session_id,
            {"idle"},
            startup_timeout,
        )
        evidence.initial_acp_session_id = session.get("acp_session_id")
        evidence.initial_child_pid = session.get("pid")
        evidence.checks["initial_idle"] = True
        evidence.checks["initial_identity"] = bool(
            evidence.initial_acp_session_id and evidence.initial_child_pid
        )
        if session.get("target_type") != "command":
            raise ParityFailure(
                "parity requires a remote command target (container: or codespace:)",
                evidence=evidence,
            )

        before = client.read_range(session_id, start=0)
        last_id = max((int(event.get("id") or 0) for event in before), default=0)
        client.submit_prompt(
            session_id,
            _quality_prompt(
                auth=auth,
                ado_url=ado_url,
                azure_scope=azure_scope,
            ),
            request_timeout=turn_timeout,
        )
        message, probe, last_id = _wait_for_turn(
            client,
            session_id,
            after_event_id=last_id,
            timeout=turn_timeout,
        )
        reported = _parse_result(message)
        if probe is None:
            raise ParityFailure(
                "tool events did not contain the redacted probe marker",
                evidence=evidence,
            )
        capabilities = [
            str(item) for item in (reported.get("capabilities") or [])
        ]
        evidence.observed = {
            "cwd": probe.get("cwd"),
            "capabilities": capabilities,
            "github_credential": probe.get("github_credential"),
            "github_api": probe.get("github_api"),
            "ado_credential": probe.get("ado_credential"),
            "ado_ls_remote": probe.get("ado_ls_remote"),
            "azure_token": probe.get("azure_token"),
        }
        if expected_workspace is not None:
            evidence.checks["workspace"] = (
                probe.get("cwd") == expected_workspace
            )
        if expected_capability is not None:
            evidence.checks["capability"] = any(
                expected_capability in item for item in capabilities
            )
        if auth:
            evidence.checks["github_credential"] = (
                probe.get("github_credential") is True
            )
            evidence.checks["github_api"] = probe.get("github_api") is True
            if ado_url:
                evidence.checks["ado_credential"] = (
                    probe.get("ado_credential") is True
                )
                evidence.checks["ado_ls_remote"] = (
                    probe.get("ado_ls_remote") is True
                )
            if azure_scope:
                evidence.checks["azure_token"] = (
                    probe.get("azure_token") is True
                )

        if fault == _FRONTEND_RESTART_HOSTINDEX_LOSS:
            active_others = [
                str(item.get("session_id") or "")
                for item in client.list_sessions()
                if item.get("session_id") != session_id
                and str(item.get("status") or "") not in _INACTIVE_FOR_FAULT
            ]
            if active_others:
                raise ParityFailure(
                    "frontend restart fault refuses to run while another "
                    "managed session is active",
                    evidence=evidence,
                )
            evidence.checks["exclusive_frontend_fault"] = True
            assert fault_handler is not None
            try:
                fault_result = fault_handler(session_id, startup_timeout)
            except Exception as exc:
                raise ParityFailure(
                    f"frontend restart fault failed: {exc}",
                    evidence=evidence,
                ) from exc
            client.refresh_endpoint()
            resumed = _wait_for_status(
                client,
                session_id,
                {"idle"},
                startup_timeout,
            )
            evidence.initial_host_pid = fault_result.get("initial_host_pid")
            evidence.resumed_host_pid = fault_result.get("recovered_host_pid")
            evidence.resumed_acp_session_id = resumed.get("acp_session_id")
            evidence.resumed_child_pid = resumed.get("pid")
            evidence.observed.update({
                "fault": fault,
                "frontend_pid_changed": (
                    fault_result.get("frontend_pid_before")
                    != fault_result.get("frontend_pid_after")
                ),
                "host_index_target_removed": fault_result.get(
                    "host_index_target_removed"
                ),
                "recovered_from_remote_authority": fault_result.get(
                    "recovered_from_remote_authority"
                ),
            })
            evidence.checks["frontend_restarted"] = (
                evidence.observed["frontend_pid_changed"] is True
            )
            evidence.checks["host_index_target_removed"] = (
                evidence.observed["host_index_target_removed"] is True
            )
            evidence.checks["recovered_from_remote_authority"] = (
                evidence.observed["recovered_from_remote_authority"] is True
            )
            evidence.checks["same_acp_session"] = (
                evidence.resumed_acp_session_id
                == evidence.initial_acp_session_id
            )
            evidence.checks["same_host"] = (
                evidence.initial_host_pid is not None
                and evidence.resumed_host_pid == evidence.initial_host_pid
            )
            evidence.checks["same_child"] = (
                evidence.initial_child_pid is not None
                and evidence.resumed_child_pid == evidence.initial_child_pid
                and fault_result.get("initial_child_pid")
                == evidence.initial_child_pid
                and fault_result.get("recovered_child_pid")
                == evidence.initial_child_pid
            )
            evidence.checks["resumed_child_live"] = bool(
                evidence.resumed_child_pid
            )
        else:
            client.stop_session(session_id)
            _wait_for_status(client, session_id, {"stopped"}, startup_timeout)
            client.resume_session(session_id, request_timeout=startup_timeout)
            resumed = _wait_for_status(
                client,
                session_id,
                {"idle"},
                startup_timeout,
            )
            evidence.resumed_acp_session_id = resumed.get("acp_session_id")
            evidence.resumed_child_pid = resumed.get("pid")
            evidence.checks["same_acp_session"] = (
                evidence.resumed_acp_session_id
                == evidence.initial_acp_session_id
            )
            evidence.observed["child_reused_on_stop_resume"] = (
                evidence.resumed_child_pid == evidence.initial_child_pid
            )
            evidence.checks["resumed_child_live"] = bool(
                evidence.resumed_child_pid
            )

        client.submit_prompt(
            session_id,
            "Reply with exactly VENUE_PARITY_REATTACH_OK and nothing else.",
            request_timeout=turn_timeout,
        )
        reattached_message, _probe, _latest = _wait_for_turn(
            client,
            session_id,
            after_event_id=last_id,
            timeout=turn_timeout,
        )
        if _REATTACH_MARKER not in reattached_message:
            raise ParityFailure(
                "reattached turn completed without the expected marker",
                evidence=evidence,
            )
        evidence.checks["reattached_turn"] = True
        if not evidence.ok:
            failed = [name for name, ok in evidence.checks.items() if not ok]
            raise ParityFailure(
                f"parity checks failed: {', '.join(failed)}",
                evidence=evidence,
            )
        return evidence
    except ParityFailure as exc:
        if exc.evidence is None:
            exc.evidence = evidence
        raise
    finally:
        if session_id and not keep_session:
            try:
                client.end_session(session_id, force=True)
            except Exception as exc:
                evidence.observed["cleanup_error"] = str(exc)
