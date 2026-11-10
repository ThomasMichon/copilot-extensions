"""Append exact public candidate commits without changing a caller's checkout."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from windows_qualification_contract import TRANSPORT_MARKER, TRANSPORT_REF, canonical, sha


def rejected_attempt(state: dict) -> bool:
    """Recognize only Git's exact attempted-ref contention rejection."""
    failure = state.get("transport_failure") or {}
    envelope, previous = state.get("envelope_sha"), state.get("previous_tip")
    if failure.get("returncode") != 1 or not envelope:
        return False
    try:
        sha(envelope)
        if previous is not None:
            sha(previous)
    except ValueError:
        return False
    rows = [line for line in failure.get("stdout", "").splitlines() if "\t" in line]
    if len(rows) != 1:
        return False
    prefix = "!\t" + envelope + ":" + TRANSPORT_REF + "\t"
    if not rows[0].startswith(prefix):
        return False
    reason = rows[0][len(prefix):]
    if reason in {"[rejected] (fetch first)", "[rejected] (non-fast-forward)"}:
        return True
    expected = previous or "0" * 40
    return re.fullmatch(
        re.escape("[remote rejected] (cannot lock ref '" + TRANSPORT_REF + "': is at ")
        + r"[0-9a-f]{40}"
        + re.escape(" but expected " + expected + ")"),
        reason,
    ) is not None


def command(
    argv: list[str], *, cwd: Path, data: str | None = None, check=True, transport_push=False,
):
    """Run a bounded, caller-owned, captured command without a shell."""
    root = Path(__file__).resolve().parents[1]
    for relative in ("libs/agent-procutil/src", "plugins/agent-worktrees/src"):
        source = str(root / relative)
        if source not in sys.path:
            sys.path.insert(0, source)
    from agent_procutil.owned_process import run_caller_owned
    from agent_worktrees.git_repository_environment import repository_identity_env
    environment = repository_identity_env()
    if transport_push:
        if (
            len(argv) < 6
            or argv[0] != "git"
            or argv[-5:-2] != ["push", "--porcelain", "--no-follow-tags"]
            or argv[-2].startswith("-")
            or argv[-1].split(":")[1:] != [TRANSPORT_REF]
        ):
            raise ValueError("mediated transport must push only the reserved qualification ref")
        sha(argv[-1].split(":")[0])
        environment["AGENT_WORKTREES_PR_PUSH"] = "1"
    result = run_caller_owned(
        argv, cwd=cwd, input_text=data, timeout=90, env=environment,
        max_stdout_bytes=8 * 1024 * 1024, max_stderr_bytes=1024 * 1024,
    )
    if check and result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, argv, output=result.stdout, stderr=result.stderr,
        )
    return result


class Transport:
    """One append-only ref; normal fast-forward rejection supplies contention CAS."""

    def __init__(self, root: Path, remote: str, *, run=command):
        self.root, self.remote, self.run = root, remote, run
        self.credential_args: list[str] = []

    def git(self, *args, data=None, check=True, transport_push=False):
        credentials = self.credential_args if args[0] in {"fetch", "push", "ls-remote"} else []
        return self.run(
            ["git", *credentials, *args], cwd=self.root, data=data, check=check,
            transport_push=transport_push,
        )

    def scoped_credentials(self, login: str) -> None:
        """Use the native credential-pin owner's helper without changing Git config."""
        import re
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", login):
            raise ValueError("invalid Git credential principal")
        # Same gh-backed helper as agent_worktrees.git_ops.pin_git_credential;
        # the password travels only over Git's credential protocol.
        helper = (
            "!f() { if test x$1 = xget; then "
            f"token=$(gh auth token --hostname github.com --user '{login}') || exit $?; "
            "printf '%s\\n' \"password=$token\"; fi; }; f"
        )
        self.credential_args = [
            "-c", "credential.https://github.com.helper=",
            "-c", "credential.https://github.com.username=" + login,
            "-c", "credential.https://github.com.helper=" + helper,
        ]

    def tip(self, *, persist: Callable[[dict], None] | None = None) -> str | None:
        output = self.git("ls-remote", "--refs", self.remote, TRANSPORT_REF).stdout.strip()
        if not output:
            return None
        fields = output.split()
        if len(fields) != 2 or fields[1] != TRANSPORT_REF:
            raise ValueError("unexpected transport ref response")
        tip = sha(fields[0])
        if persist is not None:
            persist({"transport_observed_tip": tip})
        self.git("fetch", "--no-tags", "--no-write-fetch-head", self.remote, tip)
        return tip

    def observe(
        self, history: str | None, persist: Callable[[dict], None], *,
        observed_tip: str | None = None,
    ) -> str | None:
        """Recover a pending read, then advance only to verified descendant history."""
        if history:
            sha(history)

        def adopt(tip):
            try:
                self.validate_tip(tip)
                if history and tip != history and self.git(
                    "merge-base", "--is-ancestor", history, tip, check=False,
                ).returncode:
                    raise ValueError("Original transport history is unavailable or changed")
            except ValueError:
                persist({
                    "transport_observed_tip": None,
                    "transport_history_failure": {"required": history, "observed": tip},
                })
                raise
            persist({
                "transport_history_anchor": tip, "transport_observed_tip": None,
                "transport_history_failure": None,
            })

        if observed_tip:
            sha(observed_tip)
            self.git("fetch", "--no-tags", "--no-write-fetch-head", self.remote, observed_tip)
            adopt(observed_tip)
            history = observed_tip
        tip = self.tip(persist=persist)
        if tip:
            adopt(tip)
        elif history:
            persist({"transport_history_failure": {"required": history, "observed": None}})
            raise ValueError("Original transport history is unavailable or changed")
        return tip

    def validate_tip(self, tip: str) -> None:
        body = self.git("show", "-s", "--format=%B", tip).stdout.strip()
        try:
            marker = json.loads(body)
        except ValueError as exc:
            raise ValueError("reserved transport ref is unrelated; refusing adoption") from exc
        if (
            set(marker) != {"format", "candidate"}
            or marker["format"] != TRANSPORT_MARKER
        ):
            raise ValueError("reserved transport ref has an unknown format")
        candidate = sha(marker["candidate"])
        parents = self.git("show", "-s", "--format=%P", tip).stdout.split()
        if candidate not in parents or len(parents) not in {1, 2}:
            raise ValueError("invalid transport envelope ancestry")
        if self.git("rev-parse", tip + "^{tree}").stdout != self.git(
            "rev-parse", candidate + "^{tree}",
        ).stdout:
            raise ValueError("invalid transport envelope tree")

    def append(
        self, candidate: str, persist: Callable[[dict], None], *,
        required_history: str | None = None, observed_tip: str | None = None,
    ) -> str:
        """Persist each attempted envelope before pushing; never force a shared ref."""
        sha(candidate)
        if required_history is not None:
            sha(required_history)
        history = required_history
        tree = self.git("rev-parse", candidate + "^{tree}").stdout.strip()
        for _ in range(4):
            previous = self.observe(history, persist, observed_tip=observed_tip)
            observed_tip = None
            if previous:
                history = previous
                if self.git("merge-base", "--is-ancestor", candidate, previous, check=False).returncode == 0:
                    persist({"envelope_sha": previous, "phase": "transport-confirmed"})
                    return previous
            parents = (["-p", previous] if previous else []) + ["-p", candidate]
            body = canonical({"format": TRANSPORT_MARKER, "candidate": candidate}) + "\n"
            envelope = sha(self.git(
                "-c", "user.name=Windows qualification transport",
                "-c", "user.email=qualification@users.noreply.github.com",
                "commit-tree", tree, *parents, data=body,
            ).stdout.strip())
            persist({
                "envelope_sha": envelope, "previous_tip": previous,
                "phase": "transport-attempt", "transport_failure": None,
            })
            result = self.git(
                "push", "--porcelain", "--no-follow-tags", self.remote,
                envelope + ":" + TRANSPORT_REF, check=False, transport_push=True,
            )
            if result.returncode == 0:
                persist({
                    "envelope_sha": envelope, "phase": "transport-confirmed",
                    "transport_history_anchor": envelope,
                })
                return envelope
            failure = {
                "returncode": result.returncode,
                "stdout": result.stdout[-8192:], "stderr": result.stderr[-8192:],
            }
            persist({"transport_failure": failure})
            # A lost response can still have published this exact envelope.
            actual = self.observe(history, persist)
            if actual and self.git(
                "merge-base", "--is-ancestor", envelope, actual, check=False,
            ).returncode == 0:
                persist({"envelope_sha": envelope, "phase": "transport-confirmed"})
                return envelope
            if actual != previous and rejected_attempt({
                "envelope_sha": envelope, "previous_tip": previous, "transport_failure": failure,
            }):
                history = actual or history
                continue
            persist({"envelope_sha": envelope, "phase": "transport-unknown"})
            raise RuntimeError(
                "transport effect unknown or rejected; resume this request, do not replay: "
                + (result.stdout[-8192:] + "\n" + result.stderr[-8192:]).strip(),
            )
        raise RuntimeError("transport contention limit reached; resume this request")
