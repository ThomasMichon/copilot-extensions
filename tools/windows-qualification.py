#!/usr/bin/env python3
"""Submit focused Windows tests to the owner-only hosted workflow; resume by ID."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from windows_qualification_contract import (
    NATIVE_PACKETS, PACKETS, SUPPORT_PATH, TRANSPORT_MARKER, TRANSPORT_REF, VERSION, WORKFLOW,
    canonical, request, selection, sha, validate_result,
)
from windows_qualification_transport import Transport, command, rejected_attempt


def request_lock(directory: Path):
    """Serialize one request's local state, never the shared transport ref."""
    source = str(Path(__file__).resolve().parents[1] / "libs/single-instance-lease/src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from single_instance_lease import OperationLock
    return OperationLock(directory / "operation.lock")


class Client:
    """Repository-scoped GitHub calls and durable, non-replayed remote effects."""

    def __init__(self, root: Path, worktrees: Path, repository: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("invalid owner/repository")
        self.root, self.repository = root, repository
        self.argv = [str(worktrees)]
        if os.name == "nt" and worktrees.suffix == ".ps1":
            self.argv = ["pwsh", "-NoProfile", "-File", str(worktrees)]
        self.transport = Transport(root, "https://github.com/" + repository + ".git")
        origin = self.transport.git("config", "--get", "remote.origin.url").stdout.strip()
        if origin.endswith(".git"):
            origin = origin[:-4]
        match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([^/]+/[^/]+)", origin)
        if not match or match[1].lower() != repository.lower():
            raise ValueError("candidate origin differs from the requested public repository")
        git_path = self.transport.git("rev-parse", "--git-path", "windows-qualification").stdout.strip()
        self.state_root = (root / git_path).resolve()

    def gh(self, *args):
        result = command([
            *self.argv, "repos", "gh", self.repository, "--", *args,
        ], cwd=self.root)
        if "could not mint" in result.stderr.lower() or "ambient" in result.stderr.lower():
            raise RuntimeError("repository identity injection failed")
        return result.stdout

    def api(self, endpoint: str, *args):
        return json.loads(self.gh("api", endpoint, *args))

    def save(self, directory: Path, state: dict) -> None:
        """Replace only this client's own request state atomically."""
        temporary = directory / "state.json.new"
        temporary.write_text(canonical(state) + "\n", encoding="utf-8")
        temporary.replace(directory / "state.json")

    def preflight(self, selected: dict) -> dict:
        """Check actual protected-provider support before any remote mutation."""
        repo = self.api("repos/" + self.repository)
        login = self.api("user")["login"]
        if (
            repo["private"] or repo["owner"]["type"] != "User"
            or login.lower() != repo["owner"]["login"].lower()
        ):
            raise ValueError("only the individual repository owner may submit")
        branch = repo["default_branch"]
        if not self.api(f"repos/{self.repository}/branches/{branch}")["protected"]:
            raise ValueError("the default controller branch must be protected")
        control = sha(self.api(f"repos/{self.repository}/commits/{branch}")["sha"])
        try:
            declaration = self.api(f"repos/{self.repository}/contents/{SUPPORT_PATH}?ref={control}")
        except subprocess.CalledProcessError:
            raise ValueError(
                "protected controller support is unavailable; no source staged or request dispatched",
            ) from None
        support = json.loads(base64.b64decode(declaration["content"], validate=False))
        if (
            VERSION not in support.get("request_versions", [])
            or VERSION not in support.get("result_versions", [])
            or support.get("workflow") != WORKFLOW
            or support.get("transport_ref") != TRANSPORT_REF
            or support.get("transport_format") != TRANSPORT_MARKER
            or selected["kind"] not in support.get("selection_kinds", [])
            or (
                selected["kind"] == "packet"
                and selected["name"] not in support.get("packets", [])
            )
        ):
            raise ValueError("protected controller does not support this request/result contract")
        try:
            workflow = self.api(f"repos/{self.repository}/actions/workflows/{WORKFLOW}")
        except subprocess.CalledProcessError:
            raise ValueError(
                "protected qualification workflow is unavailable; no source staged or request dispatched",
            ) from None
        if workflow["state"] != "active" or workflow["path"] != ".github/workflows/" + WORKFLOW:
            raise ValueError("protected qualification workflow is unavailable")
        return {"branch": branch, "control_sha": control, "owner": login}

    def submit(self, selected: dict, source: str) -> dict:
        """Create one public request; an unknown dispatch is never sent twice."""
        selection(selected)
        from plugin_test_selection import resolve_plugin_root
        candidate = sha(self.transport.git(
            "rev-parse", "--verify", "--end-of-options", source + "^{commit}",
        ).stdout.strip())
        base = (
            resolve_plugin_root(self.root, selected["plugin"]).relative_to(self.root).as_posix() + "/"
            if selected["kind"] == "plugin" else ""
        )
        files = [base + file for file in selected.get("files", [])]
        if selected["kind"] == "packet":
            native = NATIVE_PACKETS.get(selected["name"])
            if native and native[0] == "agent-worktrees":
                files = [
                    "plugins/agent-worktrees/tests/native/test_native_seed_application.py",
                    "plugins/agent-worktrees/tests/native/test_manager_application_launch.py",
                    "plugins/agent-worktrees/tests/native/test_headed_worktrees_copilot.py",
                ]
            elif native and native[0] == "agent-remote-driver":
                files = [
                    f"plugins/agent-remote-driver/tests/native/{native[1]}.py",
                    "plugins/agent-remote-driver/tests/headed_mux_fixture.py",
                ]
            else:
                files = [
                    "tools/capture_bridge_http52.py", "tools/check-agent-bridge-contracts.py",
                    "tools/bridge_http52_artifact.py",
                ]
        if files:
            entries = self.transport.git("ls-tree", candidate, "--", *files).stdout.splitlines()
            actual = {
                line.split("\t", 1)[1] for line in entries
                if line.startswith(("100644 blob ", "100755 blob ")) and "\t" in line
            }
            if actual != set(files):
                raise ValueError("selected source files are missing or not regular candidate files")
        provider = self.preflight(selected)
        self.transport.scoped_credentials(provider["owner"])
        value = request({
            "version": VERSION, "request_id": uuid.uuid4().hex,
            "source_sha": candidate, "envelope_sha": None, "selection": selected,
        })
        directory = self.state_root / value["request_id"]
        directory.mkdir(parents=True, exist_ok=False)
        state = {
            "repository": self.repository, "request": value, "provider": provider,
            "phase": "prepared", "created_at": time.time(),
        }
        with request_lock(directory):
            self.save(directory, state)
            print("request_id=" + value["request_id"], file=sys.stderr, flush=True)
            self.stage(directory, state)
            return self.dispatch(directory, state)

    def stage(self, directory: Path, state: dict) -> None:
        """Publish only a source whose transport attempt has not yet started."""
        value = state["request"]
        candidate = value["source_sha"]

        # A commit readable by the receiving repository needs no transport write.
        try:
            published = self.api(f"repos/{self.repository}/commits/{candidate}")["sha"] == candidate
        except subprocess.CalledProcessError as exc:
            if not any(status in (exc.stderr or "") for status in ("HTTP 404", "HTTP 422")):
                raise
            published = False
        if not published:
            self.append_transport(directory, state)

    def append_transport(
        self, directory: Path, state: dict, *, required_history: str | None = None,
    ) -> None:
        """Retain superseded attempt evidence before starting another envelope."""
        value = state["request"]
        required_history = required_history or state.get("transport_history_anchor")

        def persist(delta):
            replacing = delta.get("phase") in {"transport-attempt", "transport-confirmed"}
            if replacing and state.get("envelope_sha") and (
                delta["phase"] == "transport-attempt"
                or delta["envelope_sha"] != state["envelope_sha"]
            ):
                state.setdefault("transport_history", []).append({
                    key: state.get(key) for key in
                    ("envelope_sha", "previous_tip", "phase", "transport_failure")
                })
                state["transport_failure"] = None
            state.update(delta)
            self.save(directory, state)

        try:
            options = {"required_history": required_history} if required_history else {}
            if state.get("transport_observed_tip"):
                options["observed_tip"] = state["transport_observed_tip"]
            value["envelope_sha"] = self.transport.append(
                value["source_sha"], persist, **options,
            )
        except Exception as exc:
            raise RuntimeError(
                f"transport incomplete ({exc}); resume --request-id {value['request_id']}",
            ) from None
        self.save(directory, state)

    def dispatch(self, directory: Path, state: dict) -> dict:
        """Perform the one dispatch attempt, durably excluding automatic replay."""
        value, provider = state["request"], state["provider"]
        if state["phase"] not in {"prepared", "transport-confirmed"}:
            raise ValueError("dispatch already attempted or source transport is unconfirmed")
        payload = directory / "dispatch.json"
        payload.write_text(canonical({
            "ref": provider["branch"],
            "inputs": {"request_id": value["request_id"], "request": canonical(value)},
        }), encoding="utf-8")
        state["phase"] = "dispatch-attempt"
        self.save(directory, state)
        try:
            self.gh(
                "api", f"repos/{self.repository}/actions/workflows/{WORKFLOW}/dispatches",
                "--method", "POST", "--input", str(payload),
            )
        except Exception:
            state["phase"] = "dispatch-unknown"
            self.save(directory, state)
            raise RuntimeError(f"dispatch effect unknown; status --request-id {value['request_id']}") from None
        state["phase"] = "accepted"
        self.save(directory, state)
        return state

    def resume(self, identifier: str) -> dict:
        """Reconcile original transport identities; never resend an unknown dispatch."""
        directory, state = self.load(identifier)
        if state["phase"].startswith("dispatch") or state["phase"] in {"accepted", "running", "completed"}:
            return self.status(identifier)
        provider = self.preflight(state["request"]["selection"])
        if provider["owner"] != state["provider"]["owner"] or provider["branch"] != state["provider"]["branch"]:
            raise ValueError("provider identity changed")
        self.transport.scoped_credentials(provider["owner"])
        if state.get("envelope_sha"):
            history = state.get("transport_history_anchor") or state.get("previous_tip")

            def remember(delta):
                state.update(delta)
                self.save(directory, state)

            tip = self.transport.observe(
                history, remember, observed_tip=state.get("transport_observed_tip"),
            )
            history = state.get("transport_history_anchor") or state.get("previous_tip")
            if history and (not tip or history != tip and self.transport.git(
                "merge-base", "--is-ancestor", history, tip, check=False,
            ).returncode):
                raise ValueError("shared transport history changed; refusing reconciliation")
            envelope = state["envelope_sha"]
            if not tip or self.transport.git(
                "merge-base", "--is-ancestor", envelope, tip, check=False,
            ).returncode:
                if not rejected_attempt(state):
                    raise ValueError("original transport effect remains unknown; not replaying")
                self.transport.validate_tip(envelope)
                parents = self.transport.git("show", "-s", "--format=%P", envelope).stdout.split()
                expected = ([state["previous_tip"]] if state.get("previous_tip") else []) + [
                    state["request"]["source_sha"],
                ]
                if parents != expected:
                    raise ValueError("rejected transport ancestry differs from original request")
                if not tip:
                    raise ValueError("shared transport history changed; refusing replacement")
                self.append_transport(directory, state, required_history=history or tip)
            else:
                self.transport.validate_tip(envelope)
                state["request"]["envelope_sha"] = envelope
                state["phase"] = "transport-confirmed"
                self.save(directory, state)
        elif state["phase"] != "prepared":
            raise ValueError("no original transport identity to reconcile")
        else:
            self.stage(directory, state)
        return self.dispatch(directory, state)

    def load(self, identifier: str) -> tuple[Path, dict]:
        from windows_qualification_contract import REQUEST_ID
        if not REQUEST_ID.fullmatch(identifier):
            raise ValueError("invalid request ID")
        directory = self.state_root / identifier
        state = json.loads((directory / "state.json").read_text("utf-8"))
        request(state["request"])
        if state["repository"] != self.repository:
            raise ValueError("request belongs to another repository")
        return directory, state

    def lock(self, identifier: str):
        """Acquire only this request's operation custody; busy is not replay."""
        directory, _ = self.load(identifier)
        return request_lock(directory)

    def status(self, identifier: str) -> dict:
        """Resolve the same request, including a dispatch whose response was lost."""
        directory, state = self.load(identifier)
        matches = []
        if state.get("run"):
            matches = [self.api(f"repos/{self.repository}/actions/runs/{state['run']['id']}")]
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(state["created_at"] - 60))
        for page in (range(1, 21) if not matches else ()):
            runs = self.api(
                f"repos/{self.repository}/actions/workflows/{WORKFLOW}/runs"
                f"?event=workflow_dispatch&created=%3E%3D{created}&per_page=100&page={page}",
            )["workflow_runs"]
            matches.extend(run for run in runs if run["display_title"] == "winqual " + identifier)
            if len(runs) < 100:
                break
        if len(matches) > 1:
            raise ValueError("duplicate remote request identities; refusing to select a result")
        if matches:
            run = matches[0]
            if (
                run["actor"]["login"].lower() != state["provider"]["owner"].lower()
                or run["head_branch"] != state["provider"]["branch"]
                or run["path"].split("@")[0] != ".github/workflows/" + WORKFLOW
            ):
                raise ValueError("remote run scope mismatch")
            state["run"] = {
                key: run[key] for key in
                ("id", "status", "conclusion", "head_sha", "run_attempt", "html_url")
            }
            state["phase"] = "completed" if run["status"] == "completed" else "running"
            self.save(directory, state)
        return state

    def result(self, identifier: str) -> dict:
        """Download only the bound run artifact; do not execute or apply its files."""
        state = self.status(identifier)
        run = state.get("run")
        if not run or run["status"] != "completed":
            raise ValueError("request is pending or dispatch is unknown; no result yet")
        directory, _ = self.load(identifier)
        name = f"winqual-{identifier}-{run['run_attempt']}"
        artifacts = self.api(
            f"repos/{self.repository}/actions/runs/{run['id']}/artifacts?per_page=100",
        )["artifacts"]
        matches = [artifact for artifact in artifacts if artifact["name"] == name]
        if len(matches) != 1 or matches[0]["expired"] or matches[0]["size_in_bytes"] > 64 * 1024 * 1024:
            raise ValueError("expected bounded run artifact missing, expired or ambiguous")
        cached = state.get("artifact", {})
        if cached.get("id") == matches[0]["id"] and re.fullmatch(
            r"evidence-[0-9]+-[0-9a-f]{32}", cached.get("directory", ""),
        ):
            output = directory / cached["directory"]
        else:
            output = directory / f"evidence-{run['run_attempt']}-{uuid.uuid4().hex}"
            output.mkdir()
            self.gh(
                "run", "download", str(run["id"]), "--repo", self.repository,
                "--name", name, "--dir", str(output),
            )
        manifest = output / "result.json"
        if manifest.is_symlink() or manifest.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("unsafe or oversized result manifest")
        value = json.loads(manifest.read_text("utf-8"))
        validate_result(
            value, state["request"], run_id=run["id"],
            control_sha=run["head_sha"], run_attempt=run["run_attempt"],
        )
        if value["outcome"] == "qualified" and run["conclusion"] != "success":
            raise ValueError("unsuccessful workflow cannot qualify")
        state["artifact"] = {"id": matches[0]["id"], "directory": output.name}
        self.save(directory, state)
        return value


def main(argv=None) -> int:
    """Parse local commands; no remote request contains executable arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--worktrees", type=Path, required=True, help="exact session-catalog argv[0]")
    parser.add_argument("--repo", required=True, help="public GitHub owner/repository")
    commands = parser.add_subparsers(dest="action", required=True)
    submit = commands.add_parser("submit")
    submit.add_argument("--source", default="HEAD")
    target = submit.add_mutually_exclusive_group(required=True)
    target.add_argument("--plugin")
    target.add_argument("--tools", action="store_true")
    target.add_argument("--packet", choices=PACKETS)
    submit.add_argument("--test-file", action="append", default=[])
    submit.add_argument("-k", default="")
    submit.add_argument("--full-suite", action="store_true")
    submit.add_argument("--allow-explicit-tiers", action="store_true")
    for action in ("status", "wait", "result", "resume"):
        command_parser = commands.add_parser(action)
        command_parser.add_argument("--request-id", required=True)
        if action == "wait":
            command_parser.add_argument("--seconds", type=int, default=600, choices=range(1, 3601))
    args = parser.parse_args(argv)
    try:
        client = Client(args.root.resolve(), args.worktrees.resolve(), args.repo)
        if args.action == "submit":
            if args.packet:
                if args.test_file or args.k or args.full_suite or args.allow_explicit_tiers:
                    raise ValueError("named packets retain their canonical selectors and bounds")
                selected = {"kind": "packet", "name": args.packet}
            else:
                selected = {
                    "kind": "plugin" if args.plugin else "tools",
                    "files": args.test_file, "k": args.k, "full": args.full_suite,
                    "explicit_tiers": args.allow_explicit_tiers,
                }
                if args.plugin:
                    selected["plugin"] = args.plugin
            value = client.submit(selected, args.source)
        elif args.action == "result":
            with client.lock(args.request_id):
                value = client.result(args.request_id)
        elif args.action == "resume":
            with client.lock(args.request_id):
                value = client.resume(args.request_id)
        else:
            deadline = time.monotonic() + (args.seconds if args.action == "wait" else 0)
            while True:
                with client.lock(args.request_id):
                    value = client.status(args.request_id)
                if args.action == "status" or value["phase"] == "completed" or time.monotonic() >= deadline:
                    break
                time.sleep(min(15, max(0, deadline - time.monotonic())))
        print(canonical(value))
        return 1 if args.action == "result" and value["outcome"] != "qualified" else 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
