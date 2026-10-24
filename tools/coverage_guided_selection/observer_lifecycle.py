"""Bounded owned-test setup/completion accounting, not a process observer or authority."""

import time


def _error_evidence(error, *, output=True):
    import json

    record = {"error_type": type(error).__name__, "truncated_fields": []}
    for name, maximum in (("message", 512), ("stdout", 1024), ("stderr", 2048)):
        try:
            value = str(error) if name == "message" else getattr(error, name, None) if output else None
            if isinstance(value, bytes):
                value = value.decode("utf-8", "replace")
            if not isinstance(value, str):
                value = None
        except BaseException:
            value = None
        if value is not None:
            candidate = value[:maximum]
            low, high = 0, len(candidate)
            # Completion embeds phases_json inside JSON; charge both escaping layers.
            while low < high:
                middle = (low + high + 1) // 2
                if len(json.dumps(json.dumps(candidate[:middle]))) <= maximum * 2:
                    low = middle
                else:
                    high = middle - 1
            if low < len(value):
                record["truncated_fields"].append(name)
            value = candidate[:low]
        record[name] = value
    try:
        code = getattr(error, "returncode", None)
    except BaseException:
        code = None
    record["returncode"] = code if type(code) is int and -(2**31) <= code < 2**32 else None
    return record


class SetupPhaseAccounting:
    """Bounded observer diagnostics; never an admission or timing exemption."""

    NAMES = frozenset({
        "runtime-compile-import", "component-extraction", "compiler-install",
        "fixture-build", "native-build", "component-import", "source-equivalence",
        "executable-catalog", "signed-fixture-inputs", "controls-catalog",
        "compiler-extract-verify", "runtime-link-inputs", "native-artifact-verification",
        "immutable-artifact-stage",
    })

    def __init__(self):
        self.rows = []

    def run(self, name, operation, *args, **kwargs):
        if name not in self.NAMES or len(self.rows) >= len(self.NAMES):
            raise ValueError("unknown or excessive observer setup phases")
        if any(row["phase"] == name for row in self.rows):
            raise ValueError("repeated observer setup phase")
        row = {"phase": name, "started_ns": time.monotonic_ns(), "completed_ns": None,
               "outcome": "failed"}
        self.rows.append(row)
        try:
            result = operation(*args, **kwargs)
            row["outcome"] = "returned"
            return result
        except BaseException as error:
            row["error"] = _error_evidence(error)
            raise
        finally:
            row["completed_ns"] = time.monotonic_ns()

    def serialize(self):
        started = time.monotonic_ns()
        import json
        if any(row["completed_ns"] is None for row in self.rows):
            raise RuntimeError("observer setup phase still active")
        encoded = json.dumps(self.rows, separators=(",", ":"), sort_keys=True)
        return {"schema": "CGS-SETUP-PHASES/1", "phases_json": encoded,
                "reached_phase": self.rows[-1]["phase"] if self.rows else None,
                "failed_phase": next((row["phase"] for row in self.rows if row["outcome"] == "failed"), None),
                "serialization_started_ns": started,
                "serialization_completed_ns": time.monotonic_ns(),
                "diagnostic_only": True}


class ObserverSetupRefused(RuntimeError):
    pass


class OwnedObserverLifecycle:
    def __init__(self, initialize, capture, finish, cleanup):
        self.initialize = initialize
        self.capture = capture
        self.finish = finish
        self.cleanup = cleanup
        self.session = None
        self.begun = self.completed = False
        self.refusal = None
        self.first_failure = None
        self.cleanup_errors = []
        self.cleanup_failures = []
        self.times = {}

    def _cleanup(self):
        try:
            self.cleanup(self.session)
        except BaseException as error:
            self.cleanup_errors.append(type(error).__name__)
            detail = {"stage": "cleanup", **_error_evidence(error, output=False)}
            if self.first_failure is None:
                self.first_failure = detail
            self.cleanup_failures.append(detail)
            return None
        return time.monotonic_ns()

    def begin(self, *, setup_started_ns=None):
        if self.begun:
            raise RuntimeError("owned setup already attempted")
        self.begun = True
        self.times["setup_started_ns"] = time.monotonic_ns() if setup_started_ns is None else setup_started_ns
        self.times["setup_completed_ns"] = None
        self.times["capture_started_ns"] = None
        try:
            self.initialize()
            self.times["setup_completed_ns"] = time.monotonic_ns()
            self.session = self.capture()
            self.times["capture_started_ns"] = time.monotonic_ns()
        except BaseException as error:
            self.refusal = {"stage": "setup", "error_type": type(error).__name__}
            self.first_failure = {"stage": "setup", **_error_evidence(error)}
            self.times["setup_failed_ns"] = time.monotonic_ns()
            self.times["initial_cleanup_completed_ns"] = self._cleanup()
            return False
        return True

    def complete(self):
        if not self.begun or self.completed:
            raise RuntimeError("invalid owned completion state")
        self.completed = True
        self.times["stop_started_ns"] = time.monotonic_ns()
        if self.refusal is not None:
            outcome = {"all_admitted": False, "completed_cases": 0,
                       "refusal": "observer setup refused", "refusal_detail": self.refusal}
        else:
            try:
                outcome = self.finish(self.session)
            except BaseException as error:
                self.first_failure = {"stage": "completion", **_error_evidence(error)}
                outcome = {"all_admitted": False, "completed_cases": 0,
                           "refusal": "observer completion refused",
                           "refusal_detail": {"stage": "completion", "error_type": type(error).__name__}}
        self.times["parse_completed_ns"] = time.monotonic_ns()
        self.times["all_cleanup_completed_ns"] = self._cleanup()
        if self.cleanup_errors or self.times["all_cleanup_completed_ns"] is None:
            outcome.update(all_admitted=False, refusal="observer cleanup not proven",
                           cleanup_error_types=list(self.cleanup_errors))
        outcome.update(self.times)
        outcome["observer_setup_refused"] = self.refusal is not None
        outcome["first_failure"] = self.first_failure
        outcome["cleanup_failures"] = list(self.cleanup_failures)
        outcome["diagnostic_only"] = True
        return outcome
