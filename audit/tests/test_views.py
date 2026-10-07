from datetime import datetime, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from audit.models import Action, AuditLog
from audit.services import log_action

pytestmark = pytest.mark.django_db

OTHER_ROLES = [role for role in Role.values if role != Role.ADMIN]


@pytest.fixture
def entry(make_user):
    return log_action(
        actor=make_user(role=Role.NURSE),
        action=Action.UPDATE,
        event="patients.patient.updated",
        changes={"phone": ["0771111111", "0772222222"]},
        message="Phone changed",
    )


def urls(entry):
    return [
        reverse("audit:log_list"),
        reverse("audit:log_detail", args=[entry.pk]),
        reverse("audit:log_list") + "?export=csv",
    ]


# --- RBAC ------------------------------------------------------------------------------


def test_anonymous_is_redirected_to_login(client, entry):
    for url in urls(entry):
        response = client.get(url)
        assert response.status_code == 302
        assert reverse("accounts:login") in response["Location"]


@pytest.mark.parametrize("role", OTHER_ROLES)
def test_other_roles_get_403(client_for_role, role, entry):
    client = client_for_role(role)
    for url in urls(entry):
        assert client.get(url).status_code == 403


def test_admin_sees_list_detail_and_csv(client_for_role, entry):
    client = client_for_role(Role.ADMIN)
    list_page = client.get(reverse("audit:log_list"))
    assert list_page.status_code == 200
    assert "patients.patient.updated" in list_page.text

    detail = client.get(reverse("audit:log_detail", args=[entry.pk]))
    assert detail.status_code == 200
    assert "0771111111" in detail.text and "0772222222" in detail.text

    csv = client.get(reverse("audit:log_list") + "?export=csv")
    assert csv.status_code == 200
    assert csv["Content-Type"].startswith("text/csv")
    assert "patients.patient.updated" in csv.content.decode()


def test_unknown_entry_is_404(client_for_role):
    assert (
        client_for_role(Role.ADMIN).get(reverse("audit:log_detail", args=[999])).status_code == 404
    )


# --- Filters ---------------------------------------------------------------------------


def at(entry, when):
    # created_at is auto_now_add; tests move it with a raw update on the base manager
    # (a plain queryset; AuditLog.objects refuses on purpose).
    AuditLog._base_manager.filter(pk=entry.pk).update(created_at=when)


def listed(client, **params):
    response = client.get(reverse("audit:log_list"), params)
    assert response.status_code == 200
    return {e.pk for e in response.context["entries"]}


@pytest.fixture
def admin_client(client_for_role):
    return client_for_role(Role.ADMIN)


def test_date_filter_uses_local_day_bounds(admin_client):
    tz = timezone.get_current_timezone()
    day = timezone.localdate() - timedelta(days=3)
    # 00:10 Colombo time on `day` is still the previous day in UTC.
    early = log_action(actor=None, action=Action.VIEW, event="a.b.viewed")
    at(
        early,
        timezone.make_aware(datetime.combine(day, datetime.min.time()), tz) + timedelta(minutes=10),
    )
    before = log_action(actor=None, action=Action.VIEW, event="a.b.viewed")
    at(
        before,
        timezone.make_aware(datetime.combine(day, datetime.min.time()), tz) - timedelta(minutes=10),
    )

    found = listed(admin_client, date_from=day.isoformat(), date_to=day.isoformat())
    assert found == {early.pk}


def test_date_range_must_be_in_order(admin_client):
    response = admin_client.get(
        reverse("audit:log_list"), {"date_from": "2026-10-07", "date_to": "2026-10-01"}
    )
    assert response.status_code == 200
    assert "on or after the start date" in response.text
    assert list(response.context["entries"]) == []


def test_actor_role_action_module_and_event_filters(admin_client, make_user):
    nurse, accountant = make_user(role=Role.NURSE), make_user(role=Role.ACCOUNTANT)
    a = log_action(actor=nurse, action=Action.CREATE, event="admissions.note.added")
    b = log_action(actor=accountant, action=Action.STATUS_CHANGE, event="billing.invoice.issued")

    assert listed(admin_client, actor=nurse.pk) == {a.pk}
    assert listed(admin_client, role=Role.ACCOUNTANT) == {b.pk}
    assert listed(admin_client, action=Action.STATUS_CHANGE) == {b.pk}
    assert listed(admin_client, module="admissions") == {a.pk}
    assert listed(admin_client, event="invoice") == {b.pk}


def test_patient_filter_accepts_mrn_or_patient_id(admin_client, make_patient):
    patient, other = make_patient(), make_patient()
    mine = log_action(
        actor=None, action=Action.VIEW, event="patients.document.viewed", patient=patient
    )
    log_action(actor=None, action=Action.VIEW, event="patients.document.viewed", patient=other)

    assert listed(admin_client, patient=patient.mrn) == {mine.pk}
    assert listed(admin_client, patient=str(patient.pk)) == {mine.pk}
    assert listed(admin_client, patient_id=patient.pk) == {mine.pk}


def test_bad_mrn_shows_an_error(admin_client):
    response = admin_client.get(reverse("audit:log_list"), {"patient": "X12"})
    assert "Enter an MRN like P000123." in response.text


def test_search_matches_object_and_message(admin_client, make_department):
    department = make_department(name="Cardiology")
    a = log_action(
        actor=None, action=Action.CREATE, event="doctors.department.created", obj=department
    )
    b = log_action(actor=None, action=Action.VIEW, event="x.y.viewed", message="cardiology scan")
    log_action(actor=None, action=Action.VIEW, event="x.y.viewed", message="other")
    assert listed(admin_client, q="cardio") == {a.pk, b.pk}


def test_list_is_paginated_by_50(admin_client):
    for _ in range(51):
        log_action(actor=None, action=Action.VIEW, event="x.y.viewed")
    response = admin_client.get(reverse("audit:log_list"))
    assert len(response.context["entries"]) == 50
    assert response.context["page_obj"].paginator.num_pages == 2


# --- CSV -------------------------------------------------------------------------------


def test_csv_escapes_formula_cells_and_follows_filters(admin_client):
    log_action(actor=None, action=Action.VIEW, event="x.y.viewed", message="=HYPERLINK(1)")
    log_action(actor=None, action=Action.CREATE, event="z.w.created", message="other")
    body = admin_client.get(reverse("audit:log_list"), {"export": "csv", "action": "VIEW"})
    text = body.content.decode()
    assert "'=HYPERLINK(1)" in text
    assert "z.w.created" not in text
    assert text.splitlines()[0].startswith("Time,Actor,Role,Action,Event")


# --- Links -----------------------------------------------------------------------------


def test_sidebar_link_is_admin_only(client_for_role):
    url = reverse("audit:log_list")
    assert url in client_for_role(Role.ADMIN).get(reverse("dashboard")).text
    assert url not in client_for_role(Role.RECEPTIONIST).get(reverse("dashboard")).text


def test_patient_page_has_audit_trail_link_for_admin_only(client_for_role, make_patient):
    patient = make_patient()
    link = f"{reverse('audit:log_list')}?patient_id={patient.pk}"
    page = reverse("patients:patient_detail", args=[patient.pk])
    assert link in client_for_role(Role.ADMIN).get(page).text
    assert link not in client_for_role(Role.DOCTOR).get(page).text


def test_document_download_logs_a_view_with_the_patient(
    client_for_role, make_patient, make_upload, admin_user_obj
):
    from patients import services as patient_services

    patient = make_patient()
    document = patient_services.upload_document(
        patient=patient,
        file=make_upload("scan.pdf"),
        category="IMAGING",
        description="",
        acting_user=admin_user_obj,
    )
    client = client_for_role(Role.NURSE)
    url = reverse("patients:document_view", args=[patient.pk, document.pk])
    b"".join(client.get(url).streaming_content)
    b"".join(client.get(url + "?download=1").streaming_content)

    viewed = AuditLog.objects.get(event="patients.document.viewed")
    downloaded = AuditLog.objects.get(event="patients.document.downloaded")
    for entry in (viewed, downloaded):
        assert entry.action == Action.VIEW
        assert entry.patient == patient
        assert entry.actor_role == Role.NURSE
