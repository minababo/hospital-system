import pytest
from django.http import Http404

from patients import selectors, services
from patients.models import Patient


def search(query):
    return list(selectors.search_patients(query))


def test_empty_query_returns_everyone_newest_first(make_patient):
    older = make_patient()
    newer = make_patient()

    assert search("") == [newer, older]
    assert search(None) == [newer, older]


def test_search_by_single_name(make_patient):
    kamal = make_patient(first_name="Kamal", last_name="Perera")
    make_patient(first_name="Nimali", last_name="Silva")

    assert search("kamal") == [kamal]
    assert search("PERERA") == [kamal]


def test_search_by_first_and_last_name(make_patient):
    kamal_perera = make_patient(first_name="Kamal", last_name="Perera")
    make_patient(first_name="Kamal", last_name="Silva")
    make_patient(first_name="Sunil", last_name="Perera")

    assert search("kamal perera") == [kamal_perera]


def test_search_by_mrn_forms(make_patient):
    make_patient()
    patient = make_patient()

    for query in (patient.mrn, patient.mrn.lower(), f"p{patient.pk}", str(patient.pk)):
        assert patient in search(query), query


def test_search_by_nic_any_case(make_patient):
    patient = make_patient(nic="123456789V")
    make_patient(nic="987654321V")

    assert search("123456789v") == [patient]


@pytest.mark.parametrize("query", ["077 123 4567", "0771234567", "+94771234567", "+94 77-123-4567"])
def test_search_by_phone_formats(make_patient, query):
    patient = make_patient(phone="0771234567")
    make_patient(phone="0719999999")

    assert search(query) == [patient]


def test_get_patient_document_404_for_other_patient(make_patient, make_upload, make_user):
    owner, other = make_patient(), make_patient()
    document = services.upload_document(
        patient=owner, file=make_upload(), category="OTHER", description="", acting_user=make_user()
    )

    assert selectors.get_patient_document(owner.pk, document.pk) == document
    with pytest.raises(Http404):
        selectors.get_patient_document(other.pk, document.pk)


def test_history_lists_documents_newest_first(make_patient, make_upload, make_user):
    patient: Patient = make_patient()
    user = make_user()
    first = services.upload_document(
        patient=patient,
        file=make_upload("first.pdf"),
        category="LAB_REPORT",
        description="",
        acting_user=user,
    )
    second = services.upload_document(
        patient=patient,
        file=make_upload("second.pdf"),
        category="REFERRAL",
        description="x",
        acting_user=user,
    )

    events = selectors.patient_history(patient)

    assert [event.title for event in events] == [
        "Referral uploaded: second.pdf",
        "Lab report uploaded: first.pdf",
    ]
    assert events[0].url.endswith(f"/documents/{second.pk}/")
    assert events[1].timestamp == first.uploaded_at
