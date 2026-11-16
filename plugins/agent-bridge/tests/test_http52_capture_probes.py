"""Extend the unchanged current portfolio with original SDK wire controls."""

from test_http51_capture_probes import *  # noqa: F403 - Preserve the existing source-bound portfolio.
from test_unconfigured_sdk_receiving import (
    test_original_sdk_wire_works_with_genuinely_unconfigured_governance
    as test_original_sdk_wire_works_with_genuinely_unconfigured_governance,
    test_original_sdk_compatibility_does_not_replace_configured_or_portable_admission
    as test_original_sdk_compatibility_does_not_replace_configured_or_portable_admission,
    test_original_sdk_process_recovery_preserves_original_receipts
    as test_original_sdk_process_recovery_preserves_original_receipts,
    test_original_sdk_receiving_rechecks_the_registered_venue_before_effects
    as test_original_sdk_receiving_rechecks_the_registered_venue_before_effects,
    test_explicit_native_receiving_keeps_its_registered_venue_support
    as test_explicit_native_receiving_keeps_its_registered_venue_support,
)
