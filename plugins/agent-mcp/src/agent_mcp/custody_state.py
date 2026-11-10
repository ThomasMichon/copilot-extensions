"""Bounded retained-session ledgers and the custodian's exclusive epoch fence."""

from __future__ import annotations

from dataclasses import dataclass
import json

from . import checkpoint as cp
from .custody_contract import MAX_APPLICATION, MAX_CONTROL
from .pipeline import is_request

MAX_BUFFERED_BYTES = 16 * 1024 * 1024
MAX_CONTROL_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024


class CapacityUnavailable(ValueError):
    """This input was not accepted; custody can wait without blocking control."""


@dataclass(frozen=True, eq=False)
class Attachment:
    """Local attachment identity; a copied wire epoch is not an ownership grant."""

    epoch: int


@dataclass
class Accepted:
    message: dict
    attachment: Attachment
    control: bool
    size: int
    response: dict | None = None
    response_confirmed: bool = False
    response_size: int = 0


class CustodyState:
    """State transitions only; the authenticated IPC/transport owner performs I/O."""

    def __init__(self) -> None:
        self.current = None
        self.candidate = None
        self.candidate_ready = False
        self.fenced = True
        self.uncertain = False
        self.activating = False
        self.accepted = {}
        self.server_requests = {}
        self.deliveries = {}
        self._bytes = {"application": 0, "control": 0, "output": 0}
        self._next_epoch = 0
        self._last_origin = 0
        self._next_delivery = 0

    def _known(self):
        if self.uncertain:
            raise ConnectionError("custody outcome is unknown; no replay or rollback")

    def authorize(self, attachment, epoch):
        self._known()
        if (
            not isinstance(attachment, Attachment) or attachment is not self.current
            or type(epoch) is not int or epoch != attachment.epoch
            or self.activating
        ):
            raise ValueError("superseded or uncommitted backend epoch is fenced")

    def prepare(self) -> Attachment:
        self._known()
        if self.candidate is not None or self.activating:
            raise ValueError("a custody preparation is already outstanding")
        self._next_epoch += 1
        self.candidate = Attachment(self._next_epoch)
        self.candidate_ready = False
        return self.candidate

    def prepared(self, attachment):
        self._known()
        if attachment is not self.candidate:
            raise ValueError("preparation receipt belongs to another custody epoch")
        self.candidate_ready = True

    def fence(self):
        self._known()
        self.fenced = True

    def require_quiescent(self):
        self._known()
        if not self.fenced or self.accepted or self.server_requests or self.deliveries:
            raise ValueError("custody has unsettled execution, delivery or server requests")

    def commit(self, attachment):
        """Publish the irreversible fence before sending activation to the backend."""
        self.require_quiescent()
        if attachment is not self.candidate or not self.candidate_ready:
            raise ValueError("incoming custody attachment is not prepared")
        self.current, self.candidate = attachment, None
        self.candidate_ready = False
        self.activating = True

    def activated(self, attachment):
        self._known()
        if attachment is not self.current or not self.activating:
            raise ValueError("activation receipt belongs to another custody epoch")
        self.activating, self.fenced = False, False

    def discard(self, attachment):
        """Only an uncommitted preparation can be discarded."""
        self._known()
        if attachment is not self.candidate:
            raise ValueError("committed custody epoch cannot be rolled back")
        self.candidate = None
        self.candidate_ready = False

    def resumed(self, attachment):
        self.authorize(attachment, attachment.epoch)
        if self.candidate is not None:
            raise ValueError("incoming preparation has not been discarded")
        self.fenced = False

    def unknown(self):
        self.uncertain, self.fenced = True, True

    @staticmethod
    def _message(message):
        message = cp.data_copy(message)
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ValueError("custody requires a JSON-RPC 2.0 object")
        if "method" in message:
            if not isinstance(message["method"], str) or not message["method"]:
                raise ValueError("invalid custody method")
        elif "id" not in message or ("result" in message) == ("error" in message):
            raise ValueError("invalid custody response")
        if "id" in message and (
            type(message["id"]) not in {str, int} or message["id"] == ""
        ):
            raise ValueError("invalid custody request identity")
        size = len(json.dumps(message, separators=(",", ":")).encode())
        return message, size

    @property
    def buffered_bytes(self):
        return sum(self._bytes.values())

    def _reserve(self, size, lane):
        limit = {
            "application": MAX_BUFFERED_BYTES, "control": MAX_CONTROL_BYTES,
            "output": MAX_OUTPUT_BYTES,
        }[lane]
        if self._bytes[lane] + size > limit:
            raise CapacityUnavailable("custody byte window is full; message not accepted")
        self._bytes[lane] += size

    def admit(self, attachment, origin, message):
        self.authorize(attachment, attachment.epoch)
        message, size = self._message(message)
        if type(origin) is not int or origin <= self._last_origin:
            raise ValueError("invalid or duplicate custody input")
        control = (
            "method" not in message or message["method"] == "notifications/cancelled"
        )
        if self.fenced and not control:
            raise CapacityUnavailable("new custody application work is fenced")
        limit = MAX_CONTROL if control else MAX_APPLICATION
        if sum(item.control == control for item in self.accepted.values()) >= limit:
            raise CapacityUnavailable("custody input window is full; message not accepted")
        if is_request(message) and any(
            is_request(item.message) and item.message["id"] == message["id"]
            for item in self.accepted.values()
        ):
            raise ValueError("client request identity is already pending")
        if control and "method" not in message and message["id"] not in self.server_requests:
            raise ValueError("response has no retained server request")
        self._reserve(size, "control" if control else "application")
        self._last_origin = origin
        self.accepted[origin] = Accepted(message, attachment, control, size)
        return message

    def upstream(self, message):
        """Retain server requests before delivery, including across a session fence."""
        self._known()
        message, size = self._message(message)
        if is_request(message):
            mid = message["id"]
            if mid in self.server_requests:
                raise ValueError("server request identity is already pending")
            if len(self.server_requests) >= MAX_CONTROL:
                raise ValueError("custody server-request window is full")
            self._reserve(size, "control")
            self.server_requests[mid] = (message, size)
        return message

    def sent(self, attachment, message):
        """Call only after retained transport confirms sending a server response."""
        self.authorize(attachment, attachment.epoch)
        message, _ = self._message(message)
        if "method" not in message:
            pending = self.server_requests.pop(message["id"], None)
            if pending is None:
                raise ValueError("server response has no retained request")
            self._bytes["control"] -= pending[1]

    def sending(self, attachment, origin, message):
        """Bind every upstream effect, including generated subcalls, to admitted work."""
        self.authorize(attachment, attachment.epoch)
        message, _ = self._message(message)
        accepted = self.accepted.get(origin)
        if type(origin) is not int or accepted is None or accepted.attachment is not attachment:
            raise ValueError("upstream send has no accepted input in this epoch")
        if "method" not in message and (
            "method" in accepted.message or message["id"] != accepted.message["id"]
        ):
            raise ValueError("server response belongs to another custody input")
        if "method" in message and "method" not in accepted.message:
            raise ValueError("server response input cannot authorize a new application send")
        return message

    def output(self, attachment, origin, message):
        """Reserve a delivery; suppress an already-confirmed final response."""
        self.authorize(attachment, attachment.epoch)
        message, size = self._message(message)
        if type(origin) is not int or origin < 0:
            raise ValueError("invalid custody output origin")
        accepted = self.accepted.get(origin) if origin else None
        if origin and (accepted is None or accepted.attachment is not attachment):
            raise ValueError("output has no accepted input in this epoch")
        final = "method" not in message
        if final:
            if (
                accepted is None or not is_request(accepted.message)
                or message["id"] != accepted.message["id"]
            ):
                raise ValueError("response belongs to another custody input")
            if accepted.response is not None:
                if message != accepted.response:
                    raise ValueError("custody input has contradictory responses")
                if accepted.response_confirmed:
                    return None
                raise ValueError("custody response delivery is already outstanding")
        self._reserve(size, "output")
        self._next_delivery += 1
        delivery = self._next_delivery
        self.deliveries[delivery] = (origin, message, size, final)
        if final:
            accepted.response = message
        return delivery

    def delivered(self, attachment, delivery):
        self.authorize(attachment, attachment.epoch)
        if type(delivery) is not int or delivery not in self.deliveries:
            raise ValueError("unknown custody delivery receipt")
        origin, message, size, final = self.deliveries.pop(delivery)
        if final:
            self.accepted[origin].response_confirmed = True
            self.accepted[origin].response_size += size
        else:
            self._bytes["output"] -= size

    def settled(self, attachment, origin):
        self.authorize(attachment, attachment.epoch)
        accepted = self.accepted.get(origin)
        if type(origin) is not int or accepted is None or accepted.attachment is not attachment:
            raise ValueError("settlement has no accepted input in this epoch")
        if any(item[0] == origin for item in self.deliveries.values()) or (
            is_request(accepted.message) and not accepted.response_confirmed
        ):
            raise ValueError("custody input output has not been acknowledged")
        if "method" not in accepted.message and accepted.message["id"] in self.server_requests:
            raise ValueError("retained server response has not been sent")
        self.accepted.pop(origin)
        self._bytes["control" if accepted.control else "application"] -= accepted.size
        self._bytes["output"] -= accepted.response_size
