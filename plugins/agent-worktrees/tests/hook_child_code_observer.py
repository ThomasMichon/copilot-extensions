"""Late, once-per-worker source diagnostic; original cleanup owns termination."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
import time
from typing import Any

from hook_child_code_sample import WindowsInstructionSample
from hook_child_observer import (
    ChildObserver,
    NativeSample,
    PostExitChildObserver,
    Sample,
)


class CodeSampleClaim:
    def __init__(self, path: Path) -> None:
        if not path.is_absolute() or not path.parent.is_dir():
            raise ValueError("A runner-owned absolute code-sample claim path is required")
        self.path = path

    def take(self) -> bool:
        try:
            self.path.mkdir(mode=0o700)
        except FileExistsError:
            return False
        return True


def native_sampler(process: subprocess.Popen[Any], *, deadline: float | None) -> NativeSample:
    return WindowsInstructionSample(process, deadline=deadline)


class CodeChildObserver(ChildObserver):
    INITIAL_DELAY = 8.0

    def __init__(
        self, process: subprocess.Popen[Any], deadline: float | None, *,
        claim: Callable[[], bool],
        factory: Callable[[subprocess.Popen[Any]], NativeSample] | None = None,
    ) -> None:
        super().__init__(
            process, factory=factory if factory is not None else
            lambda selected: native_sampler(selected, deadline=deadline),
        )
        self.deadline, self.claim = deadline, claim
        self.post_exit = PostExitChildObserver(process)
        self.selection = "not-eligible"

    def collect(self) -> None:
        try:
            if self.stop.wait(self.INITIAL_DELAY):
                return
            if self.deadline is None or time.monotonic() >= self.deadline or self.stop.is_set():
                return
            if self.process.poll() is not None:
                return
            if not self.claim():
                self.selection = "already-selected"
                return
            self.selection = "selected"
            self.sample_once()
        except Exception:
            self.errors.append({"stage": "code-selection"})
        finally:
            self.closed = True

    def finish(self, outcome: str) -> Sample:
        code = {
            **super().finish(outcome), "selection": self.selection,
            "observer_started_monotonic": self.started,
        }
        post = self.post_exit.finish(outcome)
        if len(json.dumps(code, ensure_ascii=True).encode("ascii")) > 31744:
            code = {
                "selection": self.selection, "unavailable": "output-cap",
                "collector_settled": code["collector_settled"],
            }
        return {
            **post, "code_observation": code,
            "code_selection_error": bool(self.errors and self.selection != "selected"),
            "collector_settled": bool(post["collector_settled"] and code["collector_settled"]),
        }
