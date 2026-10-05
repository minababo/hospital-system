import pytest
from django.core.exceptions import ValidationError

from accounts.models import Role
from records import services
from records.models import Prescription, PrescriptionStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def pharmacist(make_user):
    return make_user(role=Role.PHARMACIST)


def prescription_with_status(make_issued_prescription, status):
    prescription = make_issued_prescription()
    Prescription.objects.filter(pk=prescription.pk).update(status=status)
    prescription.refresh_from_db()
    return prescription


# --- update_dispensing_status -------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "fully_dispensed", "expected"),
    [
        (PrescriptionStatus.ISSUED, False, PrescriptionStatus.PARTIALLY_DISPENSED),
        (PrescriptionStatus.ISSUED, True, PrescriptionStatus.DISPENSED),
        (PrescriptionStatus.PARTIALLY_DISPENSED, True, PrescriptionStatus.DISPENSED),
    ],
    ids=["issued-to-partial", "issued-to-dispensed", "partial-to-dispensed"],
)
def test_allowed_transitions(
    make_issued_prescription, pharmacist, start, fully_dispensed, expected
):
    prescription = prescription_with_status(make_issued_prescription, start)

    services.update_dispensing_status(
        prescription, fully_dispensed=fully_dispensed, acting_user=pharmacist
    )

    prescription.refresh_from_db()
    assert prescription.status == expected


@pytest.mark.parametrize(
    "start",
    [PrescriptionStatus.DRAFT, PrescriptionStatus.CANCELLED, PrescriptionStatus.DISPENSED],
)
@pytest.mark.parametrize("fully_dispensed", [False, True])
def test_rejected_statuses_leave_status_unchanged(
    make_issued_prescription, pharmacist, start, fully_dispensed
):
    prescription = prescription_with_status(make_issued_prescription, start)

    with pytest.raises(ValidationError, match="can't be dispensed"):
        services.update_dispensing_status(
            prescription, fully_dispensed=fully_dispensed, acting_user=pharmacist
        )

    prescription.refresh_from_db()
    assert prescription.status == start


# --- cancel_prescription after dispensing has started ------------------------------


@pytest.mark.parametrize(
    "start", [PrescriptionStatus.PARTIALLY_DISPENSED, PrescriptionStatus.DISPENSED]
)
def test_cancel_refused_once_dispensing_started(make_issued_prescription, start):
    prescription = prescription_with_status(make_issued_prescription, start)

    with pytest.raises(ValidationError, match="Only issued"):
        services.cancel_prescription(
            prescription, reason="No longer needed", acting_user=prescription.doctor.user
        )

    prescription.refresh_from_db()
    assert prescription.status == start
