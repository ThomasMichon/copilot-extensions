"""Protected original-position feedback inside the existing replay envelope."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import asdict, dataclass

from single_instance_lease import OperationLockedError

from .attention import Cursor, Interest
from .attention_state import MAX_INTERESTS
from .conversation_journal import ConversationJournal, Observation, _meaning
from .feedback_capture import FeedbackCaptures, PROTOCOL, READBACK_PROTOCOL, readback_references
from .generation_cache import CacheUnavailable
from .review_intent import identifier
from .review_workspace import _digest, _generation, _instant, build_generation
from .watch_replay_store import ReplayStore, ReplayStoreInvalid
from .workspace_conversation import conversation_key, project_generation


@dataclass(frozen=True)
class FeedbackInputs:
    """Interests selected by the actual embedding, never submit-request fields."""

    generation_id: str
    interests: tuple[Interest, ...]
    max_age_seconds: int

    def __post_init__(self) -> None:
        identifier(self.generation_id)
        if (
            type(self.interests) is not tuple
            or not 1 <= len(self.interests) <= MAX_INTERESTS
            or any(type(value) is not Interest or value.matches is not True for value in self.interests)
            or len({(value.consumer, value.role) for value in self.interests}) != len(self.interests)
            or type(self.max_age_seconds) is not int or self.max_age_seconds <= 0
        ):
            raise ValueError("bounded positively selected interests and freshness required")


class FeedbackUnavailable(RuntimeError):
    """Native outcomes remain retained; tracking cannot claim handled progress."""


class ReviewFeedback:
    """One trusted submit operation; no transport, lock or effect ledger of its own."""

    def __init__(
        self, owner: ReplayStore, generation: dict, inputs: FeedbackInputs, *,
        expires_at: int, checkpoint: Callable[[], None], position: Cursor | None = None,
        current_acquisition: Callable[[], tuple[dict, Cursor] | None] | None = None,
    ) -> None:
        if type(inputs) is not FeedbackInputs or inputs.generation_id != generation["id"]:
            raise ValueError("feedback inputs differ from the selected generation")
        if position is not None and type(position) is not Cursor:
            raise ValueError("actual acquisition position required")
        if current_acquisition is not None and not callable(current_acquisition):
            raise ValueError("actual current acquisition callback required")
        self.owner, self.generation, self.inputs = owner, copy.deepcopy(generation), inputs
        self.expires_at, self.checkpoint = expires_at, checkpoint
        self.position = position
        self.current_acquisition = current_acquisition
        self.capture: dict | None = None

    def response_receipt(self, native: object, action: dict) -> dict:
        """Retain a body-free original version only from the confirmed protected reply."""
        from .review_executor import _threads

        receipt = copy.deepcopy(native.receipt)
        response = getattr(native, "response", None)
        if not native.confirmed or action["kind"] != "comment" or response is None:
            return receipt
        try:
            ids = receipt["platform_ids"]
            thread = _threads([response])[ids["thread_id"]]
            if len(thread["comments"]) != 1 or thread["comments"][0]["id"] != ids["comment_id"]:
                return receipt
            comment = thread["comments"][0]
            if (
                comment.get("content") != action["body"]
                or (comment.get("author") or {}).get("id") != self.generation["scope"]["principal_id"]
                or thread.get("isDeleted") or comment.get("isDeleted")
            ):
                return receipt
            for value, fields in (
                (thread, ("publishedDate", "lastUpdatedDate")),
                (comment, ("publishedDate", "lastUpdatedDate", "lastContentUpdatedDate")),
            ):
                for field in fields:
                    _instant(value.get(field))
            ids["thread_state_id"] = _digest(thread)
        except (KeyError, TypeError, ValueError):
            pass  # Older/partial providers still retain their native confirmation.
        return receipt

    def _transaction(self, operation: Callable[[dict], dict]) -> dict:
        return self.owner._review_transaction(
            operation, expires_at=self.expires_at, checkpoint=self.checkpoint,
        )

    def prepare(self, operation: dict) -> None:
        """Reserve immutable selection context before the first admitted attempt."""
        selected = operation["intent"]
        interests = sorted(
            ({"consumer": value.consumer, "role": value.role} for value in self.inputs.interests),
            key=lambda value: (value["consumer"], value["role"]),
        )
        key = conversation_key(selected["scope"], self.owner.namespace)

        def capture(document: dict) -> dict:
            from .terminal_retention import require_live
            require_live(
                document, selected["scope"], operation_id=selected["operation_id"],
                generation_id=selected["generation_id"],
            )
            if document["version"] not in (5, 6):
                raise FeedbackUnavailable("capture_layout_unavailable")
            retained = self.owner._review_operation(
                document, selected["operation_id"], selected["scope"],
            )
            if retained["intent_id"] != operation["intent_id"]:
                raise FeedbackUnavailable("original_intent_changed")
            protocol = READBACK_PROTOCOL if document["version"] == 6 else PROTOCOL
            captures = FeedbackCaptures(document["feedback_captures"], protocol=protocol)
            previous = next(
                (row for row in captures.records if row["operation_id"] == selected["operation_id"]),
                None,
            )
            if previous is not None:
                if (
                    previous["generation_id"] != self.generation["id"]
                    or previous["interests"] != interests
                    or previous["candidate"] != {
                        "head": self.generation["revision"]["head_sha"],
                        "base": self.generation["revision"]["target_sha"],
                    }
                ):
                    raise FeedbackUnavailable("original_capture_binding_changed")
                return previous
            if any(row["state"] != "unattempted" for row in retained["actions"]):
                raise FeedbackUnavailable("capture_missing_after_attempt")
            record = ConversationJournal(document["conversations"])._find(key)
            if record is None:
                raise FeedbackUnavailable("conversation_unavailable")
            current = Observation.restore(record["current"])
            observed = project_generation(self.generation, self.owner.namespace, current)
            now = int(self.owner.clock())
            if (
                observed is None or not observed.complete or observed.state != "open"
                or not current.complete or current.state != "open"
                or _meaning(observed.document()) != _meaning(current.document())
                or not 0 <= now - observed.observed_from < self.inputs.max_age_seconds
                or not observed.observed_to <= current.observed_to <= now
            ):
                raise FeedbackUnavailable("selected_acquisition_unavailable")
            cursor = {"epoch": record["epoch"], "sequence": record["sequence"]}
            if self.position is None or asdict(self.position) != cursor:
                raise FeedbackUnavailable("generation_position_unavailable")
            scoped = [
                row for row in document["attention"]["records"]
                if row["facts"]["key"] == asdict(key)
            ]
            if any(row["facts"]["observed_at"] > current.observed_to for row in scoped):
                raise FeedbackUnavailable("newer_acquisition_unavailable")
            for interest in interests:
                row = next(
                    (row for row in scoped if all(row["interest"][name] == value
                                                 for name, value in interest.items())),
                    None,
                )
                if row is None or (
                    row["interest"]["matches"] is not True or not row["facts"]["complete"]
                    or row["facts"]["candidate"] != asdict(current.candidate)
                    or row["facts"]["cursor"] != cursor
                    or row["facts"]["observed_at"] != current.observed_to
                ):
                    raise FeedbackUnavailable("selected_interest_unavailable")
            value = {
                "operation_id": selected["operation_id"], "intent_id": retained["intent_id"],
                "generation_id": self.generation["id"], "source_id": selected["source_id"],
                "key": asdict(key), "candidate": asdict(current.candidate), "cursor": cursor,
                "observed_at": observed.observed_to, "interests": interests,
            }
            if document["version"] == 6:
                value["readback"] = None
            captures.records.append(value)
            document["feedback_captures"] = FeedbackCaptures(captures.snapshot(), protocol=protocol).snapshot()
            return value

        self.capture = self._transaction(capture)

    def check_target(self, current: dict) -> None:
        from .review_executor import ReviewCandidateChanged

        target = (current.get("pr") or {}).get("lastMergeTargetCommit")
        if (
            self.capture is None or not isinstance(target, dict)
            or target.get("commitId") != self.capture["candidate"]["base"]
        ):
            raise ReviewCandidateChanged("actual native target differs from captured target")

    def reconcile(
        self, operation: dict, *, context_factory: Callable, remaining: Callable[[], float],
    ) -> dict:
        """Read through the real protected owner before atomically recording progress."""
        from agent_worktrees.providers.azure_owned_review import ReviewContextUnavailable, protected_review_context
        from agent_worktrees.providers.base import ProviderError
        from .review_executor import _expected_discussions, _threads

        if self.capture is None:
            raise FeedbackUnavailable("capture_unavailable")
        if any(row["state"] != "confirmed" for row in operation["actions"]):
            return {"status": "pending", "reason": "selected_actions_unconfirmed"}
        with protected_review_context(context_factory, operation["intent"]["scope"], remaining) as context:
            current = context.read_state()
            self.check_target(current)
            if current["revision"] != operation["intent"]["revision"]:
                raise FeedbackUnavailable("source_readback_changed")
            # A mutable reviewer vote or equal VoteUpdate count is not an immutable event.
            if any(action["kind"] == "verdict" for action in operation["intent"]["actions"]):
                raise FeedbackUnavailable("exact_vote_readback_unavailable")
            baseline = _threads(self.generation["sections"]["discussions"]["data"])
            actual = _threads(current["discussions"])
            expected = _expected_discussions(baseline, actual, operation, current["discussions"])
            for state in operation["actions"]:
                ids = state["receipt"]["platform_ids"]
                expected_comments = {
                    row["id"]: row for row in expected[ids["thread_id"]]["comments"]
                }
                actual_comments = {
                    row["id"]: row for row in actual[ids["thread_id"]]["comments"]
                }
                if actual_comments.get(ids["comment_id"]) != expected_comments[ids["comment_id"]]:
                    raise FeedbackUnavailable("confirmed_effect_readback_changed")
            context.check()
        result = self._record_handled(operation)
        acquisition = None
        echo = {"status": "unavailable", "reason": "current_acquisition_unavailable"}
        proven = None
        # Optional evidence must leave the existing request time to settle/return.
        reserve = remaining() / 2

        def echo_remaining() -> float:
            budget = remaining() - reserve
            if budget <= 0:
                raise TimeoutError("optional echo budget exhausted")
            return budget

        try:
            if self.current_acquisition is not None:
                acquisition = self.current_acquisition()
        except PermissionError:
            raise
        except (OSError, ValueError, OperationLockedError, CacheUnavailable):
            pass
        if acquisition is not None:
            try:
                echo_remaining()
                proven = self._prove_echo(operation, acquisition, context_factory, echo_remaining)
                if proven is None:
                    echo = {"status": "not_observed"}
            except PermissionError:
                raise
            except (OSError, TimeoutError, ValueError, FeedbackUnavailable,
                    ReviewContextUnavailable, ProviderError) as error:
                echo = {
                    "status": "unavailable",
                    "reason": str(error) if isinstance(error, FeedbackUnavailable) else "echo_readback_unavailable",
                }
        if proven is not None:
            try:
                echo_remaining()
                echo = self._record_echo(operation, *proven)
            except PermissionError:
                raise
            except (OSError, ValueError, FeedbackUnavailable, OperationLockedError) as error:
                echo = {
                    "status": "unavailable",
                    "reason": str(error) if isinstance(error, FeedbackUnavailable) else "echo_storage_unavailable",
                }
        return {**result, "echo": echo}

    def _prove_echo(
        self, operation: dict, acquisition: tuple[dict, Cursor], context_factory: Callable,
        remaining: Callable[[], float],
    ) -> tuple[dict, Cursor] | None:
        from agent_worktrees.providers.azure_owned_evidence import AdoReadOwner, acquire_owned_evidence
        from agent_worktrees.providers.azure_owned_review import protected_review_context
        from .review_executor import _threads

        if type(acquisition) is not tuple or len(acquisition) != 2 or type(acquisition[1]) is not Cursor:
            raise FeedbackUnavailable("generation_position_unavailable")
        latest, position = copy.deepcopy(acquisition)
        _generation(latest, self.generation["scope"])
        captured = Cursor(**self.capture["cursor"])
        if position == captured:
            return None
        if position.epoch != captured.epoch or position.sequence != captured.sequence + 1:
            raise FeedbackUnavailable("intermediate_positions_unproven")
        if (
            not latest["complete"] or latest["revision"] != self.generation["revision"]
            or any(not row["complete"] or row["errors"] for row in latest["sections"].values())
        ):
            raise FeedbackUnavailable("complete_echo_acquisition_unavailable")
        for name, section in latest["sections"].items():
            if name != "discussions" and self._section_meaning(section) != self._section_meaning(
                self.generation["sections"][name],
            ):
                raise FeedbackUnavailable("independent_acquisition_input")
        expected = _threads(self.generation["sections"]["discussions"]["data"])
        actual = _threads(latest["sections"]["discussions"]["data"])
        for action, state in zip(operation["intent"]["actions"], operation["actions"]):
            ids = state["receipt"]["platform_ids"]
            if action["kind"] != "comment" or state["receipt"]["origin"] != "response":
                raise FeedbackUnavailable("action_response_version_unavailable")
            version = ids.get("thread_state_id")
            if not isinstance(version, str):
                raise FeedbackUnavailable("original_response_version_unavailable")
            thread = actual.get(ids["thread_id"])
            if thread is None or ids["thread_id"] in expected or _digest(thread) != version:
                raise FeedbackUnavailable("response_version_changed")
            expected[ids["thread_id"]] = thread
        if actual != expected:
            raise FeedbackUnavailable("independent_discussion_input")
        scope = operation["intent"]["scope"]
        with protected_review_context(context_factory, scope, remaining) as context:
            owner = AdoReadOwner(
                scope["endpoint"], scope["project_id"] + "/" + scope["repository_id"],
                scope["project_id"], scope["repository_id"], scope["principal_id"],
                "protected-feedback-readback", context.check,
                lambda number, budget, checkpoint: context.reader,
            )
            fresh = build_generation(acquire_owned_evidence(
                owner, scope["number"], budget=remaining(),
            ))["generation"]
            context.check()
        if (
            not fresh["complete"] or fresh["revision"] != latest["revision"]
            or any(self._section_meaning(fresh["sections"][name]) != self._section_meaning(section)
                   for name, section in latest["sections"].items())
        ):
            raise FeedbackUnavailable("fresh_echo_readback_changed")
        return latest, position

    @staticmethod
    def _section_meaning(section: dict) -> dict:
        return {key: value for key, value in section.items() if key not in ("observed_from", "observed_to")}

    def _record_echo(self, operation: dict, generation: dict, position: Cursor) -> dict:
        capture = copy.deepcopy(self.capture)
        cursor = asdict(position)

        def record(document: dict) -> dict:
            retained = self.owner._review_operation(
                document, operation["intent"]["operation_id"], operation["intent"]["scope"],
            )
            original = next(
                (row for row in document["feedback_captures"]["records"]
                 if row["operation_id"] == capture["operation_id"]), None,
            )
            if retained != operation or original != capture:
                raise FeedbackUnavailable("original_confirmation_changed")
            journal = ConversationJournal(document["conversations"])._find(
                conversation_key(operation["intent"]["scope"], self.owner.namespace),
            )
            if journal is None or {"epoch": journal["epoch"], "sequence": journal["sequence"]} != cursor:
                raise FeedbackUnavailable("echo_position_changed")
            current = Observation.restore(journal["current"])
            observed = project_generation(generation, self.owner.namespace, current)
            now = int(self.owner.clock())
            if (
                observed is None or not observed.complete or observed.state != "open"
                or not current.complete or current.state != "open"
                or _meaning(observed.document()) != _meaning(current.document())
                or not 0 <= now - observed.observed_from < self.inputs.max_age_seconds
                or not observed.observed_to <= current.observed_to <= now
                or not any(event["sequence"] == position.sequence and event["kind"] == "changed"
                           for event in journal["events"])
            ):
                raise FeedbackUnavailable("echo_acquisition_unavailable")
            scoped = [row for row in document["attention"]["records"] if row["facts"]["key"] == capture["key"]]
            if any(row["facts"]["observed_at"] > current.observed_to for row in scoped):
                raise FeedbackUnavailable("newer_acquisition_unavailable")
            updated = 0
            for interest in capture["interests"]:
                row = next(
                    (row for row in scoped if all(row["interest"][name] == value for name, value in interest.items())),
                    None,
                )
                if row is None or (
                    row["interest"]["matches"] is not True or not row["facts"]["complete"]
                    or row["facts"]["cursor"] != cursor
                    or row["facts"]["candidate"] != capture["candidate"]
                    or row["facts"]["observed_at"] != current.observed_to
                ):
                    raise FeedbackUnavailable("echo_interest_unavailable")
                handled = row["handled"]
                if handled is not None and handled["cursor"] == cursor:
                    continue
                if (
                    handled is None or handled["cursor"] != capture["cursor"]
                    or row["provenance"]["operation_id"] != capture["operation_id"]
                ):
                    raise FeedbackUnavailable("original_handled_binding_changed")
                row["handled"]["cursor"] = copy.deepcopy(cursor)
                updated += 1
            return {
                "status": "covered", "cursor": cursor,
                "generation_id": generation["id"], "updated": updated,
            }

        return self._transaction(record)

    def _record_handled(self, operation: dict) -> dict:
        capture = copy.deepcopy(self.capture)
        if capture is None:
            raise FeedbackUnavailable("capture_unavailable")
        readback = readback_references(capture, operation) if "readback" in capture else None

        def record(document: dict) -> dict:
            if document["version"] not in (5, 6):
                raise ReplayStoreInvalid("feedback capture requires explicit owner adoption")
            retained = self.owner._review_operation(
                document, operation["intent"]["operation_id"], operation["intent"]["scope"],
            )
            original = next(
                (row for row in document["feedback_captures"]["records"]
                 if row["operation_id"] == capture["operation_id"]), None,
            )
            if retained != operation or original != capture:
                raise FeedbackUnavailable("original_confirmation_changed")
            if document["version"] == 6:
                original["readback"] = copy.deepcopy(readback)
            proof = {
                "operation_id": capture["operation_id"], "generation_id": capture["generation_id"],
                "receipt_refs": [
                    {"action_id": row["id"], "attempt_id": row["attempt_id"]}
                    for row in operation["actions"]
                ],
            }
            journal = ConversationJournal(document["conversations"])._find(
                conversation_key(operation["intent"]["scope"], self.owner.namespace),
            )
            updated = 0
            for interest in capture["interests"]:
                row = next(
                    (row for row in document["attention"]["records"]
                     if row["facts"]["key"] == capture["key"]
                     and all(row["interest"][name] == value for name, value in interest.items())),
                    None,
                )
                if (
                    row is None or row["facts"]["cursor"]["epoch"] != capture["cursor"]["epoch"]
                    or row["facts"]["cursor"]["sequence"] < capture["cursor"]["sequence"]
                ):
                    if document["version"] == 6:
                        continue
                    raise FeedbackUnavailable("captured_epoch_or_interest_unavailable")
                handled = row["handled"]
                if (
                    handled is not None and handled["cursor"]["epoch"] == capture["cursor"]["epoch"]
                    and handled["cursor"]["sequence"] >= capture["cursor"]["sequence"]
                ):
                    continue
                row["handled"] = {
                    name: copy.deepcopy(capture[name]) for name in ("key", "candidate", "cursor")
                } | interest
                row["provenance"] = copy.deepcopy(proof)
                if journal is not None and journal["epoch"] == capture["cursor"]["epoch"]:
                    after = capture["cursor"]["sequence"] + 1
                    through = row["facts"]["cursor"]["sequence"]
                    events = [event for event in journal["events"] if after <= event["sequence"] <= through]
                    if (
                        events and len(events) == through - after + 1
                        and all(event["sequence"] == after + index for index, event in enumerate(events))
                    ):
                        row["facts"]["pending_since"] = events[0]["observed_at"]
                updated += 1
            return {
                "status": "handled", "cursor": capture["cursor"],
                "interests": len(capture["interests"]), "updated": updated,
            }

        result = self._transaction(record)
        if readback is not None:
            self.capture = {**capture, "readback": readback}
        return result
