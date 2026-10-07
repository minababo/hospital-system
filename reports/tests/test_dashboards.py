from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from billing.models import Charge, Invoice, Payment
from laboratory.models import LabOrder, LabResult
from patients.models import Patient
from pharmacy.models import Dispense
from records.models import Prescription
from reports import selectors
from staff.models import Attendance

pytestmark = pytest.mark.django_db


def colombo(*args):
    return timezone.make_aware(datetime(*args))


NOW = colombo(2026, 10, 15, 12, 0)
TODAY = NOW.date()


# --- Shared scenario pieces ---------------------------------------------------------


@pytest.fixture
def money(make_invoice, make_user):
    """Two open invoices and a draft. The Rs. 2,000 invoice gets 1,900 in valid payments
    (balance 100); the other owes 500, so 600 is outstanding in total."""
    cashier = make_user()
    issued = make_invoice(amounts=["2000"], status="ISSUED", issued_at=NOW)
    make_invoice(amounts=["500"], status="PARTIALLY_PAID", issued_at=NOW - timedelta(days=3))
    make_invoice(amounts=["999"])  # draft: not outstanding

    def pay(amount, at, **kwargs):
        return Payment.objects.create(
            invoice=issued,
            amount=Decimal(amount),
            method=kwargs.pop("method", "CASH"),
            reference=kwargs.pop("reference", ""),
            received_by=cashier,
            received_at=at,
            **kwargs,
        )

    pay("1000", NOW)
    pay("200", colombo(2026, 10, 15, 23, 30))  # late tonight: still today
    pay("300", colombo(2026, 10, 16, 0, 10), method="CARD", reference="SLIP-1")  # tomorrow
    pay("400", colombo(2026, 10, 14, 9, 0))  # yesterday
    pay("500", NOW, is_voided=True)  # voided: never counted
    return issued


@pytest.fixture
def lab_orders(make_lab_order):
    make_lab_order()
    make_lab_order(priority="URGENT")
    make_lab_order(status="SAMPLE_COLLECTED")
    done = make_lab_order(status="COMPLETED")
    LabOrder.objects.filter(pk=done.pk).update(released_at=NOW - timedelta(hours=1))
    old = make_lab_order(status="COMPLETED")
    LabOrder.objects.filter(pk=old.pk).update(released_at=NOW - timedelta(days=2))


@pytest.fixture
def stock(make_medicine, make_batch):
    """One medicine each: only expired stock (also out of stock), expiring soon, low."""
    make_batch(medicine=make_medicine(), qty=5, expiry=TODAY - timedelta(days=1))
    make_batch(medicine=make_medicine(), qty=100, expiry=TODAY + timedelta(days=10))
    make_batch(medicine=make_medicine(reorder_level=10), qty=5, expiry=TODAY + timedelta(days=365))


# --- Admin ---------------------------------------------------------------------------


def test_admin_dashboard_numbers(
    admin_user_obj,
    make_patient,
    make_appointment,
    money,
    lab_orders,
    stock,
    make_bed,
    make_admission,
    make_leave,
    make_employee,
):
    for status, hour in [("BOOKED", 9), ("CHECKED_IN", 10), ("COMPLETED", 11), ("CANCELLED", 12)]:
        make_appointment(date=TODAY, status=status, start_time=time(hour))
    make_appointment(date=TODAY + timedelta(days=1))  # tomorrow
    old_patient = make_patient()
    Patient.objects.filter(pk=old_patient.pk).update(created_at=NOW - timedelta(days=40))
    occupied = make_bed()
    make_bed()
    make_admission(bed=occupied)
    make_leave(status="PENDING")
    present, absent = make_employee(), make_employee()
    Attendance.objects.create(
        employee=present, date=TODAY, status="HALF_DAY", recorded_by=admin_user_obj
    )
    Attendance.objects.create(
        employee=absent, date=TODAY, status="ABSENT", recorded_by=admin_user_obj
    )

    data = selectors.dashboard_admin(admin_user_obj, now=NOW)

    assert data["patients"]["total"] == Patient.objects.count()
    assert data["patients"]["new_this_month"] == Patient.objects.count() - 1
    assert data["appointments_total"] == 4
    assert data["appointments"]["CHECKED_IN"] == 1
    assert data["collected_today"] == Decimal("1200.00")  # 1000 + 200 at 23:30
    assert data["collected_month"] == Decimal("1900.00")  # + 300 (16th) + 400 (14th)
    assert (data["outstanding_count"], data["outstanding_total"]) == (2, Decimal("600.00"))
    assert (data["lab_pending"], data["lab_urgent"]) == (3, 1)
    assert data["pharmacy_alerts"] == {
        "expired": 1,
        "expiring_soon": 1,
        "low_stock": 1,
        "out_of_stock": 1,
    }
    assert (data["occupancy"]["occupied"], data["occupancy"]["total"]) == (1, 2)
    assert data["pending_leave"] == 1
    assert data["staff_present"] == 1
    assert all(card["href"] for card in data["cards"])


def test_payment_just_after_midnight_is_tomorrow(admin_user_obj, money):
    tomorrow = selectors.dashboard_admin(admin_user_obj, now=NOW + timedelta(days=1))

    assert tomorrow["collected_today"] == Decimal("300.00")


# --- Other roles ---------------------------------------------------------------------


def test_receptionist_dashboard(make_user, make_appointment, money):
    statuses = ["BOOKED", "CHECKED_IN", "CHECKED_IN", "COMPLETED", "NO_SHOW", "CANCELLED"]
    for hour, status in enumerate(statuses, start=8):
        make_appointment(date=TODAY, status=status, start_time=time(hour))
    for hour in range(14, 22):  # 8 more booked later today
        make_appointment(date=TODAY, start_time=time(hour))

    data = selectors.dashboard_receptionist(make_user(role=Role.RECEPTIONIST), now=NOW)

    assert len(data["upcoming"]) == 10
    assert all(a.status in ("BOOKED", "CHECKED_IN") for a in data["upcoming"])
    assert [a.start_time for a in data["upcoming"]] == sorted(
        a.start_time for a in data["upcoming"]
    )
    assert data["checked_in"] == 2
    assert (data["unpaid_count"], data["outstanding_total"]) == (2, Decimal("600.00"))


def test_doctor_dashboard_shows_only_own_work(
    make_doctor, make_appointment, make_record, make_lab_order, make_lab_test, make_admission
):
    mine, other = make_doctor(), make_doctor()
    checked_in = make_appointment(doctor=mine, date=TODAY, status="CHECKED_IN", start_time=time(9))
    with_draft = make_appointment(doctor=mine, date=TODAY, status="CHECKED_IN", start_time=time(10))
    make_appointment(doctor=other, date=TODAY, start_time=time(9))
    draft = make_record(appointment=with_draft)
    make_record(
        appointment=make_appointment(
            doctor=other, date=TODAY, status="CHECKED_IN", start_time=time(11)
        )
    )

    test = make_lab_test()
    order = make_lab_order(record=draft, tests=[test], status="COMPLETED")
    LabOrder.objects.filter(pk=order.pk).update(released_at=NOW - timedelta(days=1))
    item = order.items.get()
    LabResult.objects.create(
        item=item, parameter=test.parameters.get(), value_numeric=20, flag="H", entered_at=NOW
    )
    stale = make_lab_order(record=draft, tests=[make_lab_test()], status="COMPLETED")
    LabOrder.objects.filter(pk=stale.pk).update(released_at=NOW - timedelta(days=8))
    own_inpatient = make_admission(admitting_doctor=mine)
    make_admission(admitting_doctor=other)

    data = selectors.dashboard_doctor(mine.user, now=NOW)

    assert {a.pk for a in data["appointments"]} == {checked_in.pk, with_draft.pk}
    actions = {a.pk: a.action_label for a in data["appointments"]}
    assert actions == {checked_in.pk: "Start consultation", with_draft.pk: "Open consultation"}
    assert data["waiting"] == 2
    assert (data["draft_count"], data["drafts"]) == (1, [draft])
    assert [(o.pk, o.abnormal) for o in data["lab_results"]] == [(order.pk, 1)]
    assert data["inpatients"] == [own_inpatient]


def test_nurse_dashboard(make_user, make_appointment, make_admission, make_bed, lab_orders):
    make_appointment(date=TODAY, status="CHECKED_IN", start_time=time(9))
    make_appointment(date=TODAY, status="BOOKED", start_time=time(10))
    make_admission()
    make_bed()

    data = selectors.dashboard_nurse(make_user(role=Role.NURSE), now=NOW)

    assert len(data["checked_in"]) == 1
    assert data["inpatients"] == 1
    assert data["occupancy"]["total"] == 2
    assert data["pending_lab"] == 3


def test_lab_staff_dashboard(make_user, lab_orders):
    data = selectors.dashboard_lab_staff(make_user(role=Role.LAB_STAFF), now=NOW)

    assert (data["requested"], data["sample_collected"]) == (2, 1)
    assert (data["urgent_open"], data["completed_today"]) == (1, 1)


def test_pharmacist_dashboard(make_user, make_issued_prescription, stock):
    issued = make_issued_prescription()
    partial = make_issued_prescription()
    Prescription.objects.filter(pk=partial.pk).update(status="PARTIALLY_DISPENSED")
    done = make_issued_prescription()
    Prescription.objects.filter(pk=done.pk).update(status="DISPENSED")
    pharmacist = make_user(role=Role.PHARMACIST)
    for at in (NOW, NOW - timedelta(days=1)):
        Dispense.objects.create(
            prescription=issued, patient=issued.patient, dispensed_by=pharmacist, dispensed_at=at
        )

    data = selectors.dashboard_pharmacist(pharmacist, now=NOW)

    assert data["awaiting"] == 2
    assert data["pharmacy_alerts"]["expired"] == 1
    assert data["dispensed_today"] == 1


def test_accountant_dashboard(make_user, money, make_charge, make_invoice):
    make_charge(unit_price="250")  # unbilled
    make_charge(unit_price="999", is_voided=True)  # voided: not counted
    invoiced = make_invoice(amounts=["100"])  # its charge is on an invoice
    Invoice.objects.filter(pk=invoiced.pk).update(issued_at=NOW - timedelta(days=1))

    data = selectors.dashboard_accountant(make_user(role=Role.ACCOUNTANT), now=NOW)

    assert data["received_today"]["total"] == Decimal("1200.00")
    assert data["received_today"]["by_method"] == {"CASH": Decimal("1200.00")}
    assert data["received_month"]["by_method"]["CARD"] == Decimal("300.00")
    assert data["outstanding_total"] == Decimal("600.00")
    assert data["issued_today"] == 1
    assert data["unbilled_total"] == Decimal("250.00")
    assert Charge.objects.filter(is_voided=False, invoice__isnull=True).count() == 1


# --- Links and query counts (through the view, real clock) ------------------------------


def test_cards_only_link_to_permitted_pages(make_user):
    nurse = make_user(role=Role.NURSE)

    assert selectors.link(nurse, (Role.ADMIN,), "staff:leave_list") is None
    assert selectors.link(nurse, (Role.NURSE,), "admissions:bed_board") == reverse(
        "admissions:bed_board"
    )


def test_receptionist_page_has_no_admin_only_links(client_for_role):
    content = client_for_role(Role.RECEPTIONIST).get(reverse("dashboard")).content.decode()

    assert reverse("pharmacy:alerts") not in content
    assert reverse("staff:leave_list") not in content


QUERY_BOUND = 25


@pytest.mark.parametrize("role", list(Role))
def test_dashboard_query_count_is_bounded(
    client,
    make_user,
    make_doctor,
    make_appointment,
    make_admission,
    make_lab_order,
    make_issued_prescription,
    make_invoice,
    role,
    django_assert_max_num_queries,
):
    user = make_user(role=role) if role != Role.DOCTOR else make_doctor().user
    doctor = getattr(user, "doctor_profile", None) or make_doctor()
    today = timezone.localdate()
    for hour in range(8, 14):  # several rows of everything: queries must not grow per row
        make_appointment(doctor=doctor, date=today, status="CHECKED_IN", start_time=time(hour))
        make_admission(admitting_doctor=doctor)
        make_lab_order()
        make_issued_prescription()
        make_invoice(status="ISSUED", issued_at=timezone.now())
    client.force_login(user)

    with django_assert_max_num_queries(QUERY_BOUND):
        response = client.get(reverse("dashboard"))

    assert response.status_code == 200


# --- Each card opens the matching filtered page --------------------------------------------


def hrefs(data):
    return {card["label"]: card["href"] for card in data["cards"]}


def test_lab_cards_open_filtered_worklists(client, make_user, lab_orders):
    user = make_user(role=Role.LAB_STAFF)
    links = hrefs(selectors.dashboard_lab_staff(user, now=NOW))
    client.force_login(user)

    def listed(label):
        return client.get(links[label]).context["orders"]

    assert links["Requested"].endswith("?status=REQUESTED")
    assert [o.status for o in listed("Requested")] == ["REQUESTED", "REQUESTED"]
    assert [o.status for o in listed("Sample collected")] == ["SAMPLE_COLLECTED"]
    assert [o.priority for o in listed("Urgent (open)")] == ["URGENT"]
    assert links["Completed today"] is None


def test_accountant_cards_open_different_pages(make_user):
    links = hrefs(selectors.dashboard_accountant(make_user(role=Role.ACCOUNTANT), now=NOW))

    assert links["Collected today"].endswith("?date_from=2026-10-15&date_to=2026-10-15")
    assert links["Collected this month"].endswith("?date_from=2026-10-01&date_to=2026-10-31")
    assert links["Outstanding balance"].endswith("?outstanding_only=on")
    assert links["Invoices issued today"] is None


def test_checked_in_cards_filter_by_status(make_user):
    for role, function in [
        (Role.NURSE, selectors.dashboard_nurse),
        (Role.RECEPTIONIST, selectors.dashboard_receptionist),
    ]:
        links = hrefs(function(make_user(role=role), now=NOW))
        checked_in = links.get("Checked in today") or links["Checked in"]
        assert checked_in.endswith("?status=CHECKED_IN")


def test_doctor_inpatients_card_opens_own_inpatients(client, make_doctor, make_admission):
    mine, other = make_doctor(), make_doctor()
    own = make_admission(admitting_doctor=mine)
    make_admission(admitting_doctor=other)
    link = hrefs(selectors.dashboard_doctor(mine.user, now=NOW))["Your inpatients"]
    client.force_login(mine.user)

    filtered = client.get(link)
    everyone = client.get(reverse("admissions:admission_list"))

    assert link.endswith("?mine=on")
    assert list(filtered.context["admissions"]) == [own]
    assert len(everyone.context["admissions"]) == 2  # without the filter: all inpatients
