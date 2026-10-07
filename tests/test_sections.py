"""In-page actions return to the section that was changed (#45 follow-up, issue #48 item 4)."""

import re

import pytest
from django.urls import reverse

from accounts.models import Role
from conftest import SAMPLE_FILE_BYTES
from records.models import RecordStatus

pytestmark = pytest.mark.django_db


def ends_with_anchor(response, anchor):
    assert response.status_code == 302, response.status_code
    assert response.url.endswith(f"#{anchor}"), response.url


@pytest.fixture
def draft(make_record, client):
    """A draft consultation, with its doctor logged in."""
    record = make_record()
    client.force_login(record.doctor.user)
    return record


def url(name, *args):
    return reverse(name, args=args)


# --- Consultation ------------------------------------------------------------------------


def test_record_update_returns_to_notes(client, draft):
    response = client.post(
        url("records:record_update", draft.pk),
        {"presenting_complaint": "Cough for 3 days", "clinical_notes": "Mild wheeze"},
    )
    ends_with_anchor(response, "notes")


def test_diagnosis_add_and_remove_return_to_diagnoses(client, draft):
    response = client.post(
        url("records:diagnosis_add", draft.pk),
        {"description": "Asthma", "diagnosis_type": "PRIMARY"},
    )
    ends_with_anchor(response, "diagnoses")

    diagnosis = draft.diagnoses.get()
    response = client.post(url("records:diagnosis_remove", draft.pk, diagnosis.pk))
    ends_with_anchor(response, "diagnoses")


def test_prescription_item_add_and_remove_return_to_prescription(client, draft, make_medicine):
    medicine = make_medicine()
    response = client.post(
        url("records:item_add", draft.pk),
        {
            "medicine": medicine.pk,
            "dose": "1 tablet",
            "frequency": "TDS",
            "route": "ORAL",
            "duration_days": 5,
            "quantity": 15,
        },
    )
    ends_with_anchor(response, "prescription")

    item = draft.prescription.items.get()
    response = client.post(url("records:item_remove", draft.pk, item.pk))
    ends_with_anchor(response, "prescription")


def test_report_upload_returns_to_reports(client, draft, make_upload):
    response = client.post(
        url("records:report_upload", draft.pk),
        {"file": make_upload("xray.pdf"), "category": "IMAGING", "description": ""},
    )
    ends_with_anchor(response, "reports")


def test_lab_order_from_consultation_returns_to_lab_tests(client, draft, make_lab_test):
    test = make_lab_test()
    response = client.post(
        url("laboratory:order_for_record", draft.pk),
        {"tests": [test.pk], "priority": "ROUTINE", "clinical_notes": ""},
    )
    ends_with_anchor(response, "lab-tests")


def test_addendum_returns_to_addenda(client, make_record):
    record = make_record(status=RecordStatus.FINALIZED)
    client.force_login(record.doctor.user)
    response = client.post(url("records:addendum_add", record.pk), {"text": "Spoke to patient"})
    ends_with_anchor(response, "addenda")


def test_invalid_diagnosis_marks_its_section_for_scrolling(client, draft):
    response = client.post(url("records:diagnosis_add", draft.pk), {"description": ""})
    html = response.content.decode()

    assert response.status_code == 200
    assert re.search(r'<section id="diagnoses"[^>]*data-scroll-target', html)
    # Only that one element is marked (the layout script mentions the attribute too).
    assert len(re.findall(r"<\w+ [^>]*data-scroll-target", html)) == 1


def test_invalid_medicine_marks_the_prescription_section(client, draft):
    response = client.post(url("records:item_add", draft.pk), {"dose": "1 tablet"})
    html = response.content.decode()

    assert response.status_code == 200
    assert re.search(r'<section id="prescription"[^>]*data-scroll-target', html)
    assert not re.search(r'<section id="diagnoses"[^>]*data-scroll-target', html)


def test_consultation_sections_have_stable_ids(client, draft, make_issued_prescription):
    make_issued_prescription(record=draft)
    html = client.get(url("records:record_detail", draft.pk)).content.decode()
    for section in ("notes", "diagnoses", "prescription", "lab-tests", "reports", "dispensing"):
        assert f'id="{section}"' in html, section
    assert html.count('class="section-anchor') >= 6


def test_finalized_consultation_has_addenda_section(client, make_record):
    record = make_record(status=RecordStatus.FINALIZED)
    client.force_login(record.doctor.user)
    html = client.get(url("records:record_detail", record.pk)).content.decode()
    assert 'id="addenda"' in html


# --- Patient documents ---------------------------------------------------------------------


def test_document_upload_and_delete_return_to_documents(client_for_role, make_patient, make_upload):
    patient = make_patient()
    client = client_for_role(Role.ADMIN)
    response = client.post(
        url("patients:document_upload", patient.pk),
        {"file": make_upload("scan.pdf"), "category": "IMAGING", "description": ""},
    )
    ends_with_anchor(response, "documents")

    document = patient.documents.get()
    response = client.post(url("patients:document_delete", patient.pk, document.pk))
    ends_with_anchor(response, "documents")


def test_patient_detail_sections_have_ids(client_for_role, make_patient):
    html = (
        client_for_role(Role.RECEPTIONIST)
        .get(url("patients:patient_detail", make_patient().pk))
        .content.decode()
    )
    assert 'id="documents"' in html and 'id="appointments"' in html


# --- Lab order page ---------------------------------------------------------------------------


def test_lab_report_upload_returns_to_reports(client_for_role, make_lab_order, make_upload):
    order = make_lab_order()
    response = client_for_role(Role.LAB_STAFF).post(
        url("laboratory:report_upload", order.pk),
        {
            "file": make_upload("cbc.pdf", SAMPLE_FILE_BYTES["pdf"]),
            "category": "LAB_REPORT",
            "description": "",
        },
    )
    ends_with_anchor(response, "reports")


def test_lab_order_page_sections_have_ids(client_for_role, make_lab_order):
    html = (
        client_for_role(Role.LAB_STAFF)
        .get(url("laboratory:order_detail", make_lab_order().pk))
        .content.decode()
    )
    assert 'id="results"' in html and 'id="reports"' in html
