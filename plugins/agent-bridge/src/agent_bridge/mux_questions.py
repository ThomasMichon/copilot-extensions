"""One-use inspected mux answers with durable admission and truthful outcomes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from .mux_question_backend import LocalMux
from .mux_question_forms import Form, MuxQuestionError, answer_text, completion_echo, parse_form, stable_screen

CAPABILITY = "mux_question_answers"
INSPECTION_TTL = 60.0
MAX_INSPECTIONS = 128


@dataclass
class Inspection:
    session_id: str
    binding: dict[str, Any]
    screen: str
    form: Form
    request: dict
    fingerprint: str
    expires: float


class MuxQuestions:
    """Own only this daemon's bounded inspections; restart revokes them all."""

    def __init__(self) -> None:
        self.backend = LocalMux()
        self.inspections: dict[str, Inspection] = {}
        self.active: set[str] = set()

    async def read(self, session_id: str, row: dict[str, Any]) -> tuple[dict, str]:
        """Freshly resolve the native binding before capturing its current pane."""
        binding = await asyncio.to_thread(self.backend.binding, session_id, row)
        return binding, await asyncio.to_thread(self.backend.capture, binding)

    async def inspect(self, session_id: str, row: dict[str, Any], db: Any) -> dict:
        """Issue one expiring token for one positively identified current screen."""
        if session_id in self.active or db.execute_read(
            "SELECT inspection_id FROM mux_question_answers WHERE session_id=? "
            "AND state IN ('admitted','unverified') LIMIT 1", (session_id,),
        ):
            raise MuxQuestionError("a prior mux input is in flight or unverified; inspect its audit, never replay")
        binding, screen = await self.read(session_id, row)
        form = parse_form(screen)
        if form is None or form.typed:
            raise MuxQuestionError("no supported pending single-question form")
        evidence = row["_mux_request"]
        if " ".join(evidence["message"].split()) != " ".join(" ".join(form.question[:-1]).split()):
            raise MuxQuestionError("the rendered question does not match the observed native request")
        name, field = next(iter(evidence["requested_schema"]["properties"].items()))
        labels = field.get("enumNames", field.get("enum"))
        if labels is None and isinstance(field.get("oneOf"), list):
            labels = [item.get("title", item.get("const")) for item in field["oneOf"]
                      if isinstance(item, dict)]
        if (
            not isinstance(labels, list) or not labels
            or [str(label) for label in labels] != [
                label for index, label in enumerate(form.options) if index != form.other
            ]
            or form.question[-1].rstrip(":").casefold() not in {
                name.casefold(), str(field.get("title", name)).casefold(),
            }
        ):
            raise MuxQuestionError("rendered choices or field label do not match the native schema")
        fingerprint = hashlib.sha256(json.dumps(
            [binding, evidence, stable_screen(screen)], sort_keys=True,
        ).encode()).hexdigest()
        prior = db.execute_read(
            "SELECT inspection_id,state FROM mux_question_answers WHERE session_id=? AND fingerprint=?",
            (session_id, fingerprint),
        )
        if prior:
            raise MuxQuestionError("this inspected screen already has an answer attempt; inspect its audit, never replay")
        now = time.monotonic()
        self.inspections = {k: v for k, v in self.inspections.items() if v.expires > now}
        if len(self.inspections) >= MAX_INSPECTIONS:
            raise MuxQuestionError("mux inspection capacity reached; let existing inspections expire")
        token = str(uuid4())
        self.inspections[token] = Inspection(
            session_id, binding, screen, form, evidence, fingerprint, now + INSPECTION_TTL,
        )
        return {
            "session_id": session_id, "via": "mux", "inspection_id": token,
            "expires_in": INSPECTION_TTL, "question": list(form.question),
            "options": [{"number": index + 1, "label": label}
                        for index, label in enumerate(form.options)],
            "freeform": form.other is not None,
        }

    async def answer(
        self, session_id: str, row: dict, body: Any, db: Any, store: Any, *, refresh,
    ) -> dict:
        """Consume before keys; uncertainty is durable and never retryable."""
        inspected = self.inspections.get(body.inspection_id)
        if (
            session_id in self.active or inspected is None or inspected.session_id != session_id
            or inspected.expires <= time.monotonic()
            or inspected.request != row["_mux_request"]
            or db.execute_read(
                "SELECT inspection_id FROM mux_question_answers WHERE session_id=? "
                "AND state IN ('admitted','unverified') LIMIT 1", (session_id,),
            )
        ):
            raise MuxQuestionError("inspection is missing, expired or belongs to another native session")
        form = inspected.form
        if body.text is not None:
            text = answer_text(body.text)
            if form.other is None:
                raise MuxQuestionError("the inspected form has no freeform option")
            target, expected = form.other, text
        else:
            if body.option is None or not 1 <= body.option <= len(form.options):
                raise MuxQuestionError("select one offered option number")
            target = body.option - 1
            if target == form.other:
                raise MuxQuestionError("the freeform option requires --text")
            text, expected = "", form.options[target]
        # There is no await between consumption and the durable unique admission.
        del self.inspections[body.inspection_id]
        try:
            admission = db.execute_write(
                "INSERT INTO mux_question_answers "
                "(inspection_id,session_id,fingerprint,sender,answer,state,created_at) "
                "SELECT ?,?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM mux_question_answers "
                "WHERE session_id=? AND state IN ('admitted','unverified'))",
                (body.inspection_id, session_id, inspected.fingerprint, body.sender,
                 expected, "admitted", time.time(), session_id),
            )
            if admission.rowcount != 1:
                raise MuxQuestionError("another mux input is in flight or unverified; never replay")
        except sqlite3.IntegrityError as exc:
            raise MuxQuestionError("this screen already has an admitted answer; never replay") from exc
        result = {
            "session_id": session_id, "inspection_id": body.inspection_id,
            "via": "mux", "state": "unverified", "accepted": None,
        }
        log = store.get_or_create(session_id, worktree_id=row["worktree_id"])
        self.active.add(session_id)
        sent = False
        effect_deadline = time.monotonic() + 45

        async def current(cursor: int, typed: str = "") -> str:
            if time.monotonic() >= effect_deadline:
                raise MuxQuestionError("mux answer deadline expired; never replay")
            fresh = refresh()
            if fresh["_mux_request"] != inspected.request:
                raise MuxQuestionError("native pending request changed")
            binding, screen = await self.read(session_id, row)
            if binding != inspected.binding:
                raise MuxQuestionError("native pane/process incarnation changed")
            observed = parse_form(screen, typed=typed)
            if (
                observed is None or not form.same_question(observed)
                or observed.cursor != cursor
                or time.monotonic() >= effect_deadline
            ):
                raise MuxQuestionError("form changed or another terminal participant answered")
            return screen

        try:
            log.append("mux_question_answer", {
                **result, "sender": body.sender, "answer": expected, "phase": "admitted",
            })
            screen = await current(form.cursor)
            if stable_screen(screen) != stable_screen(inspected.screen):
                raise MuxQuestionError("screen changed since inspection")
            cursor = form.cursor
            while cursor != target:
                await current(cursor)
                sent = True
                await asyncio.to_thread(
                    self.backend.key, inspected.binding, "Down" if target > cursor else "Up",
                )
                cursor += 1 if target > cursor else -1
                await asyncio.sleep(.1)
            await current(cursor)
            if text:
                sent = True
                await asyncio.to_thread(self.backend.key, inspected.binding, text, literal=True)
                await asyncio.sleep(.1)
                await current(cursor, text)
            else:
                await current(cursor)
            sent = True
            await asyncio.to_thread(self.backend.key, inspected.binding, "Enter")
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                binding, screen = await self.read(session_id, row)
                if binding != inspected.binding:
                    raise MuxQuestionError("native pane/process changed after submission")
                if parse_form(screen) is None and completion_echo(
                    inspected.screen, screen, form, expected,
                ):
                    result.update(state="answered", accepted=True)
                    break
                await asyncio.sleep(.15)
        except asyncio.CancelledError:
            result["detail"] = "request cancelled after admission; inspect audit, never replay"
            raise
        except Exception as exc:
            result.update(
                state="unverified" if sent else "raced",
                detail=str(exc) if isinstance(exc, MuxQuestionError) else "native request or mux input changed; never replay",
                accepted=None if sent else False,
            )
        finally:
            self.active.discard(session_id)
            db.execute_write(
                "UPDATE mux_question_answers SET state=?,detail=? WHERE inspection_id=?",
                (result["state"], result.get("detail"), body.inspection_id),
            )
            log.append("mux_question_answer", {
                **result, "sender": body.sender, "answer": expected, "phase": "outcome",
            })
        return result
