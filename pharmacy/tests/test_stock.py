from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Role
from pharmacy import selectors, services
from pharmacy.models import Dispense, DispenseItem, MovementType, StockBatch, StockMovement
from pharmacy.tests.ledger import assert_ledger_balanced

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture
def pharmacist(make_user):
    return make_user(role=Role.PHARMACIST)


def days(n):
    return TODAY + timedelta(days=n)


# --- Model constraints -------------------------------------------------------------


def test_unit_price_not_negative(make_medicine):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_medicine(unit_price=Decimal("-1"))


def test_batch_number_unique_per_medicine_ignoring_case(make_batch, make_medicine):
    medicine = make_medicine()
    make_batch(medicine=medicine, batch_number="AB12")

    with pytest.raises(IntegrityError), transaction.atomic():
        make_batch(medicine=medicine, batch_number="ab12")
    make_batch(medicine=make_medicine(), batch_number="AB12")  # another medicine: fine


def test_on_hand_cannot_exceed_received(make_batch):
    batch = make_batch(qty=10)

    with pytest.raises(IntegrityError), transaction.atomic():
        StockBatch.objects.filter(pk=batch.pk).update(quantity_on_hand=11)


@pytest.mark.parametrize(
    ("movement_type", "quantity", "reason"),
    [
        (MovementType.RECEIVE, 0, ""),  # zero is never allowed
        (MovementType.RECEIVE, -5, ""),  # receive must be positive
        (MovementType.DISPENSE, 5, ""),  # dispense must be negative
        (MovementType.DISPENSE, -5, ""),  # dispense needs a dispense item
        (MovementType.EXPIRY_WRITE_OFF, 5, "x"),  # write-off must be negative
        (MovementType.ADJUSTMENT, -1, ""),  # adjustment needs a reason
        (MovementType.EXPIRY_WRITE_OFF, -1, ""),  # write-off needs a reason
    ],
)
def test_movement_constraints(make_batch, pharmacist, movement_type, quantity, reason):
    batch = make_batch()

    with pytest.raises(IntegrityError), transaction.atomic():
        StockMovement.objects.create(
            batch=batch,
            movement_type=movement_type,
            quantity=quantity,
            reason=reason,
            created_by=pharmacist,
        )


# --- Stock services ------------------------------------------------------------------


def receive(medicine, user, **kwargs):
    data = {
        "batch_number": "lot-1",
        "expiry_date": days(200),
        "quantity": 50,
        "supplier": "State Pharmaceuticals",
        "notes": "",
    }
    data.update(kwargs)
    return services.receive_stock(medicine=medicine, acting_user=user, **data)


def test_receive_stock_creates_batch_and_movement(make_medicine, pharmacist):
    batch = receive(make_medicine(), pharmacist)

    assert batch.batch_number == "LOT-1"
    assert (batch.quantity_received, batch.quantity_on_hand) == (50, 50)
    assert batch.movements.get().movement_type == MovementType.RECEIVE
    assert_ledger_balanced()


def test_receive_rejections(make_medicine, pharmacist):
    medicine = make_medicine()
    with pytest.raises(ValidationError, match="already expired"):
        receive(medicine, pharmacist, expiry_date=days(-1))
    with pytest.raises(ValidationError, match="inactive"):
        receive(make_medicine(is_active=False), pharmacist)

    receive(medicine, pharmacist)
    with pytest.raises(ValidationError, match="already exists for this medicine"):
        receive(medicine, pharmacist, batch_number="LOT-1")
    assert StockBatch.objects.count() == 1


def test_receive_batch_expiring_today_allowed(make_medicine, pharmacist):
    assert receive(make_medicine(), pharmacist, expiry_date=TODAY).pk


def test_adjust_stock(make_batch, pharmacist):
    batch = make_batch(qty=20)

    with pytest.raises(ValidationError, match="reason"):
        services.adjust_stock(batch=batch, quantity_change=-2, reason=" ", acting_user=pharmacist)
    with pytest.raises(ValidationError, match="only 20 on hand"):
        services.adjust_stock(batch=batch, quantity_change=-21, reason="x", acting_user=pharmacist)
    with pytest.raises(ValidationError, match="more than the 20"):
        services.adjust_stock(batch=batch, quantity_change=1, reason="x", acting_user=pharmacist)

    services.adjust_stock(batch=batch, quantity_change=-3, reason="Broken", acting_user=pharmacist)
    services.adjust_stock(batch=batch, quantity_change=1, reason="Recount", acting_user=pharmacist)
    batch.refresh_from_db()
    assert batch.quantity_on_hand == 18
    assert_ledger_balanced()


def test_write_off_only_expired_with_stock(make_batch, pharmacist):
    fresh = make_batch()
    with pytest.raises(ValidationError, match="Only expired"):
        services.write_off_expired(batch=fresh, acting_user=pharmacist)

    expired = make_batch(qty=7, expiry=days(-1))
    movement = services.write_off_expired(batch=expired, acting_user=pharmacist)
    expired.refresh_from_db()
    assert expired.quantity_on_hand == 0
    assert movement.quantity == -7 and movement.movement_type == MovementType.EXPIRY_WRITE_OFF

    with pytest.raises(ValidationError, match="no stock left"):
        services.write_off_expired(batch=expired, acting_user=pharmacist)
    assert_ledger_balanced()


# --- FEFO allocation -------------------------------------------------------------------


@pytest.fixture
def dispense_item(make_issued_prescription, pharmacist):
    """allocate_and_issue needs a DispenseItem to link DISPENSE movements to."""
    prescription = make_issued_prescription()
    dispense = Dispense.objects.create(
        prescription=prescription, patient=prescription.patient, dispensed_by=pharmacist
    )
    return DispenseItem.objects.create(
        dispense=dispense,
        prescription_item=prescription.items.get(),
        quantity=1,
        unit_price=Decimal("0"),
    )


def allocate(medicine, quantity, dispense_item, user):
    with transaction.atomic():
        return services.allocate_and_issue(
            medicine, quantity, dispense_item=dispense_item, acting_user=user, today=TODAY
        )


def test_fefo_takes_earliest_expiry_first(make_medicine, make_batch, dispense_item, pharmacist):
    medicine = make_medicine()
    later = make_batch(medicine=medicine, qty=10, expiry=days(300))
    sooner = make_batch(medicine=medicine, qty=10, expiry=days(30))

    allocate(medicine, 4, dispense_item, pharmacist)

    sooner.refresh_from_db()
    later.refresh_from_db()
    assert (sooner.quantity_on_hand, later.quantity_on_hand) == (6, 10)
    assert_ledger_balanced()


def test_fefo_spans_batches(make_medicine, make_batch, dispense_item, pharmacist):
    medicine = make_medicine()
    sooner = make_batch(medicine=medicine, qty=5, expiry=days(30))
    later = make_batch(medicine=medicine, qty=10, expiry=days(300))

    movements = allocate(medicine, 8, dispense_item, pharmacist)

    sooner.refresh_from_db()
    later.refresh_from_db()
    assert (sooner.quantity_on_hand, later.quantity_on_hand) == (0, 7)
    assert [m.quantity for m in movements] == [-5, -3]
    assert_ledger_balanced()


def test_fefo_skips_expired_and_uses_today(make_medicine, make_batch, dispense_item, pharmacist):
    medicine = make_medicine()
    expired = make_batch(medicine=medicine, qty=10, expiry=days(-1))
    today_batch = make_batch(medicine=medicine, qty=3, expiry=TODAY)

    assert selectors.usable_stock(medicine, TODAY) == 3
    allocate(medicine, 3, dispense_item, pharmacist)

    expired.refresh_from_db()
    today_batch.refresh_from_db()
    assert (expired.quantity_on_hand, today_batch.quantity_on_hand) == (10, 0)


def test_insufficient_stock_names_available_amount(
    make_medicine, make_batch, dispense_item, pharmacist
):
    medicine = make_medicine(name="Amoxicillin")
    make_batch(medicine=medicine, qty=4)

    with pytest.raises(ValidationError, match="4 available, 5 needed"):
        allocate(medicine, 5, dispense_item, pharmacist)
    assert_ledger_balanced()


# --- Selectors ---------------------------------------------------------------------


def test_inventory_annotations_with_several_batches(make_medicine, make_batch):
    medicine = make_medicine(reorder_level=10)
    make_batch(medicine=medicine, qty=8, expiry=days(100))
    make_batch(medicine=medicine, qty=5, expiry=days(20))
    make_batch(medicine=medicine, qty=6, expiry=days(-5))  # expired: on hand, not usable
    empty = make_medicine()

    rows = {m.pk: m for m in selectors.inventory_list(today=TODAY)}

    assert rows[medicine.pk].usable_stock == 13
    assert rows[medicine.pk].total_on_hand == 19
    assert rows[medicine.pk].nearest_expiry == days(20)
    assert rows[medicine.pk].stock_status == "OK"
    assert rows[empty.pk].usable_stock == 0 and rows[empty.pk].stock_status == "OUT_OF_STOCK"


def test_inventory_status_filter(make_medicine, make_batch):
    low = make_medicine(reorder_level=10)
    make_batch(medicine=low, qty=10)  # exactly at the reorder level counts as low
    ok = make_medicine(reorder_level=10)
    make_batch(medicine=ok, qty=11)
    out = make_medicine()

    assert list(selectors.inventory_list(status="LOW", today=TODAY)) == [low]
    assert list(selectors.inventory_list(status="OUT_OF_STOCK", today=TODAY)) == [out]
    assert ok in selectors.inventory_list(today=TODAY)


def test_alert_boundaries(make_medicine, make_batch):
    expired = make_batch(expiry=days(-1))
    at_limit = make_batch(expiry=days(30))
    beyond = make_batch(expiry=days(31))
    today_batch = make_batch(expiry=TODAY)

    result = selectors.alerts(today=TODAY, warning_days=30)

    assert list(result["expired"]) == [expired]
    assert set(result["expiring_soon"]) == {at_limit, today_batch}
    assert beyond not in result["expiring_soon"]


def test_low_and_out_of_stock_alerts_and_summary(make_medicine, make_batch):
    low = make_medicine(reorder_level=10)
    make_batch(medicine=low, qty=3)
    out = make_medicine()
    make_medicine(is_active=False)  # inactive: no alert
    fine = make_medicine(reorder_level=1)
    make_batch(medicine=fine, qty=50)

    result = selectors.alerts(today=TODAY, warning_days=30)
    summary = selectors.pharmacy_alerts_summary(today=TODAY)

    assert list(result["low_stock"]) == [low]
    assert list(result["out_of_stock"]) == [out]
    assert summary["low_stock"] == 1 and summary["out_of_stock"] == 1
