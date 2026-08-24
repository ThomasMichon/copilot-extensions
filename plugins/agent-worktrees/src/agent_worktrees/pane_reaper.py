"""Conservative resident classification and repair of mux pane drift."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from . import config as cfg
from . import sessions
from . import tracking

_REPAIR_ENV = "AGENT_WORKTREES_PANE_REPAIR"
_OFF = frozenset({"0", "false", "no", "off"})
_TARGET_MAX_AGE = 60.0
_RETRY_COOLDOWN = 120.0


def repair_enabled() -> bool:
    return (
        os.environ.get(_REPAIR_ENV, "").strip().lower() not in _OFF
        and not (_report_dir() / "DISABLED").exists()
    )


def _report_dir() -> Path:
    return cfg.install_dir() / "pane-drift"


def _write_report(session_name: str, report: dict) -> None:
    tmp: str | None = None
    try:
        target_dir = _report_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(
            c if c.isalnum() or c in "-._" else "_" for c in session_name)
        target = target_dir / f"{safe_name}.json"
        fd, tmp = tempfile.mkstemp(
            prefix=safe_name + ".", suffix=".tmp", dir=str(target_dir))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle)
        os.replace(tmp, target)
    except Exception:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _list_panes(mux_bin: str, session_name: str) -> list[dict] | None:
    try:
        result = subprocess.run(
            [
                mux_bin, "list-panes", "-s", "-t", session_name, "-F",
                "#{session_name}\t#{pane_id}\t#{pane_dead}\t#{pane_pid}\t"
                "#{pane_current_command}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    panes: list[dict] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 5 or parts[0] != session_name or not parts[1]:
            continue
        try:
            pid = int(parts[3])
        except ValueError:
            pid = None
        panes.append({
            "pane": parts[1],
            "dead": parts[2] == "1",
            "pid": pid,
            "command": parts[4],
        })
    return panes


def _session_is_live(session_id: str) -> bool | None:
    try:
        entry = sessions._session_state_dir() / session_id
        if not entry.is_dir():
            return None
        return sessions._has_live_session(entry)
    except Exception:
        return None


def _pane_has_live_copilot(pane: dict) -> bool | None:
    pane_pid = pane.get("pid")
    if not isinstance(pane_pid, int) or pane_pid <= 0:
        return None
    try:
        from . import reclaim

        table = reclaim.build_process_table()
        if not table:
            return None
        tree = {pane_pid, *reclaim.descendants_of(pane_pid, table)}
        for pid in tree:
            name = str(table.get(pid, {}).get("name", "")).lower()
            if "copilot" in name or sessions._is_copilot_process(pid):
                return True
        if pane_pid not in table:
            return None
        return False
    except Exception:
        return None


def _kill_dead_pane(pane_id: str, *, mux: str) -> dict:
    try:
        result = subprocess.run(
            [mux, "kill-pane", "-t", pane_id],
            capture_output=True,
            timeout=5,
        )
        gone = not sessions._mux_pane_alive(pane_id, mux)
        return {
            "ok": result.returncode == 0 and gone,
            "pane": pane_id,
            "gone": gone,
            "method": "dead-hard" if gone else "failed",
        }
    except Exception:
        return {
            "ok": False, "pane": pane_id, "gone": False, "method": "failed"}


def _client_count(mux_bin: str, session_name: str) -> int | None:
    try:
        result = subprocess.run(
            [mux_bin, "list-clients", "-t", session_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        return len([
            line for line in (result.stdout or "").splitlines() if line.strip()])
    except Exception:
        return None


def classify_panes(
    record: tracking.WorktreeRecord,
    panes: list[dict],
    *,
    active_pane: str | None,
    session_is_live: Callable[[str], bool | None] = _session_is_live,
) -> dict:
    """Classify panes without using command names as evidence."""
    protected: dict[str, str] = {}
    concluded: dict[str, str] = {}
    if active_pane:
        protected[active_pane] = "current-mux-pane"
    for entry in record.sessions or ():
        pane_id = entry.pane_id
        if not pane_id:
            continue
        if entry.state in ("concluded", "handed-off"):
            concluded[pane_id] = entry.session_id
        else:
            protected[pane_id] = "active-session-record"

    repairable: list[dict] = []
    ambiguous: list[dict] = []
    for pane in panes:
        pane_id = pane["pane"]
        if pane_id in protected:
            continue
        if pane.get("dead"):
            repairable.append({**pane, "reason": "dead-pane"})
            continue
        concluded_session = concluded.get(pane_id)
        if concluded_session and active_pane is None:
            ambiguous.append({
                **pane,
                "reason": "active-pane-unresolved-veto",
                "session_id": concluded_session,
            })
            continue
        concluded_live = (
            session_is_live(concluded_session) if concluded_session else None)
        if concluded_session and concluded_live is False:
            repairable.append({
                **pane,
                "reason": "concluded-session",
                "session_id": concluded_session,
            })
            continue
        reason = (
            "concluded-session-live-veto"
            if concluded_session and concluded_live is True
            else "concluded-session-state-missing-veto"
            if concluded_session
            else "unproven-extra-pane"
        )
        ambiguous.append({**pane, "reason": reason})

    return {
        "protected": [
            {"pane": pane, "reason": reason}
            for pane, reason in sorted(protected.items())
        ],
        "repairable": repairable,
        "ambiguous": ambiguous,
    }


class ResidentPaneReconciler:
    """Inspect one worktree session and repair at most one proven pane per step."""

    def __init__(
        self,
        *,
        activate_project: Callable[..., None],
        retire_pane: Callable[..., dict] = sessions.mux_retire_pane,
        retire_dead_pane: Callable[..., dict] = _kill_dead_pane,
        session_is_live: Callable[[str], bool | None] = _session_is_live,
        pane_has_live_copilot: Callable[[dict], bool | None] = (
            _pane_has_live_copilot),
        client_count: Callable[[str, str], int | None] = _client_count,
    ) -> None:
        self.activate_project = activate_project
        self.retire_pane = retire_pane
        self.retire_dead_pane = retire_dead_pane
        self.session_is_live = session_is_live
        self.pane_has_live_copilot = pane_has_live_copilot
        self.client_count = client_count
        self._targets: dict[str, tuple[str, float]] = {}
        self._cursor = 0
        self._attempts: dict[str, tuple[float, str]] = {}

    def observe(self, session_name: str, path: str) -> None:
        if session_name.startswith("wt-") and path:
            self._targets[session_name] = (path, time.monotonic())

    def _next_target(self) -> tuple[str, str] | None:
        now = time.monotonic()
        self._targets = {
            session: target
            for session, target in self._targets.items()
            if now - target[1] <= _TARGET_MAX_AGE
        }
        names = sorted(self._targets)
        if not names:
            return None
        session_name = names[self._cursor % len(names)]
        self._cursor = (self._cursor + 1) % len(names)
        return session_name, self._targets[session_name][0]

    def step(self, mux_bin: str | None) -> dict | None:
        if not mux_bin:
            return None
        target = self._next_target()
        if target is None:
            return None
        session_name, path = target
        try:
            self.activate_project(path, force=True)
            worktree_id = session_name[3:]
            yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
            if not yaml_path.is_file():
                return None
            record = tracking.load_record(yaml_path)
            if record.status != "active":
                return None
            panes = _list_panes(mux_bin, session_name)
            if panes is None:
                return None
            active_pane = sessions.mux_active_pane(
                record.worktree_id, mux=mux_bin)
            clients = self.client_count(mux_bin, session_name)
            classification = classify_panes(
                record,
                panes,
                active_pane=active_pane,
                session_is_live=self.session_is_live,
            )
            # Repair requires a positive current-pane answer and at least one
            # other pane. These are independent last-pane/session safety proofs.
            if active_pane is None or len(panes) < 2 \
                    or clients is None or clients > 1:
                reason = (
                    "active-pane-unresolved-veto"
                    if active_pane is None
                    else "last-pane-veto"
                    if len(panes) < 2
                    else "client-set-unresolved-veto"
                    if clients is None
                    else "multiple-clients-veto")
                classification["ambiguous"].extend(
                    {**candidate, "reason": reason}
                    for candidate in classification["repairable"])
                classification["repairable"] = []

            safe_candidates: list[dict] = []
            for candidate in classification["repairable"]:
                pane_live = self.pane_has_live_copilot(candidate)
                if pane_live is False:
                    safe_candidates.append(candidate)
                else:
                    classification["ambiguous"].append({
                        **candidate,
                        "reason": (
                            "pane-live-copilot-veto"
                            if pane_live is True
                            else "pane-process-unresolved-veto"),
                    })
            classification["repairable"] = sorted(
                safe_candidates,
                key=lambda candidate: (
                    candidate.get("reason") != "dead-pane",
                    candidate.get("pane", ""),
                ),
            )
            action = None
            if classification["repairable"] and repair_enabled():
                candidate = classification["repairable"][0]
                last_attempt = self._attempts.get(candidate["pane"])
                if (last_attempt is None
                        or time.monotonic() - last_attempt[0] >= _RETRY_COOLDOWN):
                    # Destructive TOCTOU guard: re-read every protection signal
                    # immediately before the one permitted repair.
                    fresh_record = tracking.load_record(yaml_path)
                    fresh_panes = _list_panes(mux_bin, session_name)
                    fresh_active = sessions.mux_active_pane(
                        fresh_record.worktree_id, mux=mux_bin)
                    fresh_clients = self.client_count(mux_bin, session_name)
                    if fresh_panes is not None and fresh_active is not None \
                            and len(fresh_panes) >= 2 \
                            and fresh_clients is not None \
                            and fresh_clients <= 1:
                        fresh_class = classify_panes(
                            fresh_record,
                            fresh_panes,
                            active_pane=fresh_active,
                            session_is_live=self.session_is_live,
                        )
                        fresh_candidate = next(
                            (item for item in fresh_class["repairable"]
                             if item["pane"] == candidate["pane"]),
                            None,
                        )
                        if (fresh_candidate is not None
                                and self.pane_has_live_copilot(
                                    fresh_candidate) is False
                                and fresh_record.status == "active"
                                and repair_enabled()):
                            if fresh_candidate["reason"] == "dead-pane":
                                action = self.retire_dead_pane(
                                    fresh_candidate["pane"], mux=mux_bin)
                            else:
                                action = self.retire_pane(
                                    fresh_candidate["pane"], mux=mux_bin)
                            outcome = str(
                                action.get("method", "unknown")
                                if isinstance(action, dict) else "unknown")
                            self._attempts[candidate["pane"]] = (
                                time.monotonic(), outcome)
                            try:
                                from . import activity

                                activity.log_event(
                                    "pane_reaper_repair",
                                    worktree_id=record.worktree_id,
                                    mux_session=session_name,
                                    pane=candidate["pane"],
                                    reason=candidate["reason"],
                                    method=outcome,
                                    gone=bool(
                                        isinstance(action, dict)
                                        and action.get("gone")),
                                )
                            except Exception:
                                pass
            report = {
                "session": session_name,
                "worktree_id": record.worktree_id,
                "observed_at": time.time(),
                **classification,
                "action": action,
            }
            _write_report(session_name, report)
            return report
        except Exception:
            return None
