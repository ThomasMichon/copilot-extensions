"""Provider-first optional host-status client; never replay effects after skew."""

import urllib.parse

from .status_schema import CAPABILITY


class StatusClientMixin:
    def _status_endpoint(self):
        from .client import BridgeClientError
        endpoint = self._base
        health = self._request("GET", "/health", _endpoint=endpoint, allow_retry=False) or {}
        capability = health.get("capabilities", {}).get(CAPABILITY)
        if type(capability) is not int or capability != 1:
            raise BridgeClientError(426, {
                "code": "host_status_unsupported",
                "message": "Selected serving provider must advertise host_status_feed v1",
            })
        return endpoint

    @staticmethod
    def _status_path(subscription_id, suffix=""):
        return "/api/v1/status/subscriptions/" + urllib.parse.quote(subscription_id, safe="") + suffix

    def status_subscribe(self, subscription_id, selection):
        return self._request(
            "PUT", self._status_path(subscription_id), selection,
            _endpoint=self._status_endpoint(), allow_retry=False,
        )

    def status_feed(self, subscription_id, *, after=0, epoch=None, limit=100):
        return self._request(
            "GET", self._status_path(subscription_id, "/feed"),
            params={"after": after, "epoch": epoch, "limit": limit},
            _endpoint=self._status_endpoint(), allow_retry=False,
        )

    def status_acknowledge(self, subscription_id, *, through, epoch):
        return self._request(
            "POST", self._status_path(subscription_id, "/ack"),
            {"through": through, "epoch": epoch},
            _endpoint=self._status_endpoint(), allow_retry=False,
        )

    def status_unsubscribe(self, subscription_id):
        return self._request(
            "DELETE", self._status_path(subscription_id),
            _endpoint=self._status_endpoint(), allow_retry=False,
        )
