"""Local admission never masquerades as a Gateway-authenticated channel."""

import pytest

from fleet_manager.gateway_service_handler import CallerContext, RequestDisconnected
from fleet_manager.local_service_handler import LocalCallerContext, LocalServiceRequest


def test_local_request_preserves_distinct_identity_and_canonical_lifetime():
    context = LocalCallerContext("namespace", "reader", 100)
    request = LocalServiceRequest(context, "POST", "/v1/repositories/target/query", (), b"{}")
    assert not isinstance(context, CallerContext)
    assert not hasattr(context, "gateway") and not hasattr(context, "fleet")
    request.lifetime.check()
    request.lifetime.close()
    with pytest.raises(RequestDisconnected):
        request.lifetime.check()


@pytest.mark.parametrize("expires", [True, 0, "100"])
def test_local_context_requires_typed_expiry(expires):
    with pytest.raises(ValueError):
        LocalCallerContext("namespace", "reader", expires)
