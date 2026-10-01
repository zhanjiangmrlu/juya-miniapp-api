import pytest

from juya_miniapp_api.infrastructure.analytics_events import validate_event


@pytest.mark.parametrize("event", ["CONTACT_SUBMITTED", "CONTACT_CHANGED"])
def test_contact_cohort_accepted_without_identifying_payload(event: str) -> None:
    validate_event(event, "ALL", {"contact_cohort": "a" * 32})


@pytest.mark.parametrize("cohort", ["user-123", "a" * 31, "Z" * 32])
def test_invalid_contact_cohort_rejected(cohort: str) -> None:
    with pytest.raises(ValueError):
        validate_event("CONTACT_SUBMITTED", "ALL", {"contact_cohort": cohort})
