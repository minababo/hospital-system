from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.models import Role
from billing.models import Charge
from patients.selectors import patient_history
from pharmacy.dispensing import dispense_prescription
from pharmacy.dispensing_selectors import dispensing_queue, item_progress
from pharmacy.models import Dispense, StockMovement
from pharmacy.tests.ledger import assert_ledger_balanced
from records import services as records_services
from records.models import Prescription, PrescriptionStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def pharmacist(make_user):
    return make_user(role=Role.PHARMACIST)


@pytest.fixture
def stocked(make_medicine, make_batch, make_issued_prescription):
    """A prescription for 10 x Amoxicillin (Rs. 25) and 6 x Paracetamol (Rs. 5),
    with 100 units of each in stock."""
    amox = make_medicine(name="Amoxicillin", unit_price=Decimal("25.00"))
    para = make_medicine(name="Paracetamol", unit_price=Decimal("5.00"))
    make_batch(medicine=amox, qty=100)
    make_batch(medicine=para, qty=100)
    prescription = make_issued_prescription(items=[(amox, 10), (para, 6)])
    amox_item = prescription.items.get(medicine=amox)
    para_item = prescription.items.get(medicine=para)
    return prescription, amox_item, para_item


def dispense(prescription, quantities, user, notes=""):
    return dispense_prescription(
        prescription=prescription, quantities=quantities, notes=notes, acting_user=user
    )


def status_of(prescription):
    prescription.refresh_from_db()
    return prescription.status


def test_full_dispense(stocked, pharmacist):
    prescription, amox, para = stocked

    result = dispense(prescription, {amox.pk: 10, para.pk: 6}, pharmacist)

    assert result.number.startswith("DSP-")
    assert status_of(prescription) == PrescriptionStatus.DISPENSED
    assert_ledger_balanced()


def test_partial_then_complete(stocked, pharmacist):
    prescription, amox, para = stocked

    dispense(prescription, {amox.pk: 4, para.pk: 0}, pharmacist)
    assert status_of(prescription) == PrescriptionStatus.PARTIALLY_DISPENSED
    progress = {row.item.pk: row for row in item_progress(prescription)}
    assert (progress[amox.pk].dispensed, progress[amox.pk].remaining) == (4, 6)

    dispense(prescription, {amox.pk: 6, para.pk: 6}, pharmacist)
    assert status_of(prescription) == PrescriptionStatus.DISPENSED
    assert Dispense.objects.count() == 2


@pytest.mark.parametrize(
    ("quantities", "message"),
    [
        (lambda a, p: {a.pk: 11, p.pk: 0}, "Only 10 left"),
        (lambda a, p: {a.pk: 0, p.pk: 0}, "at least one"),
        (lambda a, p: {a.pk: -1, p.pk: 0}, "zero or more"),
    ],
    ids=["more-than-remaining", "all-zero", "negative"],
)
def test_quantity_rules(stocked, pharmacist, quantities, message):
    prescription, amox, para = stocked

    with pytest.raises(ValidationError, match=message):
        dispense(prescription, quantities(amox, para), pharmacist)
    assert status_of(prescription) == PrescriptionStatus.ISSUED


@pytest.mark.parametrize("status", [PrescriptionStatus.CANCELLED, PrescriptionStatus.DRAFT])
def test_only_issued_prescriptions(stocked, pharmacist, status):
    prescription, amox, _ = stocked
    Prescription.objects.filter(pk=prescription.pk).update(status=status)

    with pytest.raises(ValidationError, match="Only issued"):
        dispense(prescription, {amox.pk: 1}, pharmacist)


@pytest.mark.parametrize("role", [Role.ADMIN, Role.NURSE, Role.DOCTOR, Role.RECEPTIONIST])
def test_only_pharmacists_dispense(stocked, make_user, role):
    prescription, amox, _ = stocked

    with pytest.raises(PermissionDenied):
        dispense(prescription, {amox.pk: 1}, make_user(role=role))


def test_one_pharmacy_charge_per_item_with_snapshot_price(stocked, pharmacist):
    prescription, amox, para = stocked

    result = dispense(prescription, {amox.pk: 4, para.pk: 6}, pharmacist)
    amox.medicine.unit_price = Decimal("999")
    amox.medicine.save()

    charges = Charge.objects.filter(source_type="dispense_item").order_by("amount")
    assert [(c.charge_type, c.quantity, c.unit_price, c.amount) for c in charges] == [
        ("PHARMACY", 6, Decimal("5.00"), Decimal("30.00")),
        ("PHARMACY", 4, Decimal("25.00"), Decimal("100.00")),
    ]
    assert all(result.number in c.description for c in charges)


def test_zero_price_item_posts_no_charge(
    make_medicine, make_batch, make_issued_prescription, pharmacist
):
    free = make_medicine(unit_price=Decimal("0"))
    make_batch(medicine=free, qty=10)
    prescription = make_issued_prescription(items=[(free, 2)])

    dispense(prescription, {prescription.items.get().pk: 2}, pharmacist)

    assert not Charge.objects.exists()
    assert status_of(prescription) == PrescriptionStatus.DISPENSED


def test_all_or_nothing_when_one_item_lacks_stock(
    make_medicine, make_batch, make_issued_prescription, pharmacist
):
    plenty = make_medicine(name="Plenty", unit_price=Decimal("10"))
    scarce = make_medicine(name="Scarce", unit_price=Decimal("10"))
    make_batch(medicine=plenty, qty=50)
    make_batch(medicine=scarce, qty=2)
    prescription = make_issued_prescription(items=[(plenty, 5), (scarce, 5)])
    ids = {item.medicine.name: item.pk for item in prescription.items.all()}
    movements_before = StockMovement.objects.count()

    with pytest.raises(ValidationError, match="Not enough stock of Scarce"):
        dispense(prescription, {ids["Plenty"]: 5, ids["Scarce"]: 5}, pharmacist)

    assert StockMovement.objects.count() == movements_before
    assert not Charge.objects.exists()
    assert not Dispense.objects.exists()
    assert status_of(prescription) == PrescriptionStatus.ISSUED
    assert_ledger_balanced()


def test_queue_lists_waiting_prescriptions_oldest_first(make_issued_prescription):
    first = make_issued_prescription()
    second = make_issued_prescription()
    done = make_issued_prescription()
    Prescription.objects.filter(pk=done.pk).update(status=PrescriptionStatus.DISPENSED)

    assert list(dispensing_queue()) == [first, second]


def test_history_event(stocked, pharmacist):
    prescription, amox, para = stocked

    result = dispense(prescription, {amox.pk: 10, para.pk: 6}, pharmacist)

    titles = [event.title for event in patient_history(prescription.patient)]
    assert f"Medicines dispensed ({result.number}, 2 items)" in titles


# --- records.update_dispensing_status ----------------------------------------------


def test_update_dispensing_status_transitions(make_issued_prescription, pharmacist):
    prescription = make_issued_prescription()

    records_services.update_dispensing_status(
        prescription, fully_dispensed=False, acting_user=pharmacist
    )
    assert status_of(prescription) == PrescriptionStatus.PARTIALLY_DISPENSED

    records_services.update_dispensing_status(
        prescription, fully_dispensed=True, acting_user=pharmacist
    )
    assert status_of(prescription) == PrescriptionStatus.DISPENSED

    with pytest.raises(ValidationError, match="dispensed prescription can't be dispensed"):
        records_services.update_dispensing_status(
            prescription, fully_dispensed=True, acting_user=pharmacist
        )


def test_partially_dispensed_prescription_cannot_be_cancelled(make_issued_prescription, pharmacist):
    prescription = make_issued_prescription()
    records_services.update_dispensing_status(
        prescription, fully_dispensed=False, acting_user=pharmacist
    )

    with pytest.raises(ValidationError, match="Only issued"):
        records_services.cancel_prescription(
            prescription, reason="Changed my mind", acting_user=prescription.doctor.user
        )
    assert status_of(prescription) == PrescriptionStatus.PARTIALLY_DISPENSED
