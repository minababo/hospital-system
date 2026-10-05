import pytest
from django.urls import reverse

from accounts.models import Role
from laboratory import services
from laboratory.models import LabOrder, LabResult, OrderStatus
from laboratory.permissions import VIEW_LAB
from patients.views import LAB_VIEW_ROLES

CATALOG = {Role.ADMIN, Role.LAB_STAFF}
LAB = {Role.LAB_STAFF}
WALK_IN = {Role.RECEPTIONIST, Role.LAB_STAFF}
VIEW = {Role.ADMIN, Role.DOCTOR, Role.NURSE, Role.LAB_STAFF}
REPORT = VIEW | {Role.RECEPTIONIST}


@pytest.fixture
def objects(
    make_lab_test, make_lab_order, make_record, make_user, make_doctor, make_checked_in_appointment
):
    """Builds data for one role. For DOCTOR, that doctor owns the record and orders."""

    def _objects(role):
        user = make_user(role=role)
        doctor = make_doctor(user=user) if role == Role.DOCTOR else make_doctor()
        record = make_record(appointment=make_checked_in_appointment(doctor=doctor))
        test = make_lab_test()
        open_order = make_lab_order(record=record, tests=[test])
        collected = make_lab_order(
            record=record, tests=[make_lab_test()], status="SAMPLE_COLLECTED"
        )
        completed = make_lab_order(record=record, tests=[make_lab_test()], status="COMPLETED")
        return user, {
            "test": test,
            "parameter": test.parameters.get(),
            "record": record,
            "open": open_order,
            "collected": collected,
            "collected_item": collected.items.get(),
            "completed": completed,
        }

    return _objects


def build(name, o):
    return {
        "laboratory:test_update": lambda: [o["test"].pk],
        "laboratory:test_toggle_active": lambda: [o["test"].pk],
        "laboratory:parameter_add": lambda: [o["test"].pk],
        "laboratory:parameter_update": lambda: [o["parameter"].pk],
        "laboratory:parameter_remove": lambda: [o["parameter"].pk],
        "laboratory:order_for_record": lambda: [o["record"].pk],
        "laboratory:order_detail": lambda: [o["open"].pk],
        "laboratory:result_entry": lambda: [o["collected"].pk, o["collected_item"].pk],
        "laboratory:collect": lambda: [o["open"].pk],
        "laboratory:release": lambda: [o["collected"].pk],
        "laboratory:cancel": lambda: [o["open"].pk],
        "laboratory:report_upload": lambda: [o["open"].pk],
        "laboratory:report_print": lambda: [o["completed"].pk],
    }.get(name, lambda: [])()


# (url name, method, allowed roles, status for allowed role)
URLS = [
    ("laboratory:worklist", "get", REPORT, 200),
    ("laboratory:test_list", "get", CATALOG, 200),
    ("laboratory:test_create", "get", CATALOG, 200),
    ("laboratory:test_update", "get", CATALOG, 200),
    ("laboratory:parameter_update", "get", CATALOG, 200),
    ("laboratory:walk_in", "get", WALK_IN, 200),
    ("laboratory:order_detail", "get", VIEW, 200),
    ("laboratory:result_entry", "get", LAB, 200),
    ("laboratory:report_print", "get", REPORT, 200),
    ("laboratory:test_toggle_active", "post", CATALOG, 302),
    ("laboratory:parameter_add", "post", CATALOG, 200),  # empty form re-renders with errors
    ("laboratory:parameter_remove", "post", CATALOG, 302),
    ("laboratory:order_for_record", "post", {Role.DOCTOR}, 302),
    ("laboratory:collect", "post", LAB, 302),
    ("laboratory:release", "post", LAB, 302),
    ("laboratory:cancel", "post", {Role.LAB_STAFF, Role.DOCTOR}, 302),
    ("laboratory:report_upload", "post", LAB, 302),
]
POST_ONLY = [url for url in URLS if url[1] == "post"]


@pytest.mark.parametrize(("name", "method", "_roles", "_status"), URLS)
def test_anonymous_redirected(client, objects, name, method, _roles, _status):
    _, o = objects(Role.ADMIN)

    response = getattr(client, method)(reverse(name, args=build(name, o)))

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("name", "method", "roles", "status"), URLS)
def test_rbac_matrix(client, objects, role, name, method, roles, status):
    user, o = objects(role)
    client.force_login(user)

    response = getattr(client, method)(
        reverse(name, args=build(name, o)), {"reason": "x"} if method == "post" else None
    )

    assert response.status_code == (status if role in roles else 403)


@pytest.mark.parametrize(("name", "_method", "_roles", "_status"), POST_ONLY)
def test_post_only_reject_get(client, objects, name, _method, _roles, _status):
    user, o = objects(Role.LAB_STAFF)
    client.force_login(user)

    assert client.get(reverse(name, args=build(name, o))).status_code == 405


# --- Receptionist ------------------------------------------------------------------


def test_receptionist_sees_only_completed_and_can_print(client_for_role, make_lab_order):
    open_order = make_lab_order()
    done = make_lab_order(status=OrderStatus.COMPLETED)
    client = client_for_role(Role.RECEPTIONIST)

    response = client.get(reverse("laboratory:worklist"), {"status": "ALL"})

    assert list(response.context["orders"]) == [done]
    assert client.get(reverse("laboratory:report_print", args=[done.pk])).status_code == 200
    assert client.get(reverse("laboratory:report_print", args=[open_order.pk])).status_code == 404
    assert client.get(reverse("laboratory:order_detail", args=[done.pk])).status_code == 403


# --- Result entry and report ---------------------------------------------------------


def test_dynamic_result_form(client_for_role, make_lab_order, make_lab_test):
    test = make_lab_test(
        parameters=[
            {"name": "Haemoglobin", "unit": "g/dL", "ref_low": "12", "ref_high": "16"},
            {"name": "WBC", "unit": "x10^9/L", "ref_low": "4", "ref_high": "11"},
            {"name": "Film", "result_type": "TEXT", "ref_text": "Normal"},
        ]
    )
    order = make_lab_order(tests=[test], status=OrderStatus.SAMPLE_COLLECTED)
    item = order.items.get()
    hb, wbc, film = test.parameters.all()
    client = client_for_role(Role.LAB_STAFF)
    url = reverse("laboratory:result_entry", args=[order.pk, item.pk])

    page = client.get(url)
    assert [name for name in page.context["form"].fields] == [
        f"param_{hb.pk}",
        f"param_{wbc.pk}",
        f"param_{film.pk}",
        "comment",
    ]

    bad = client.post(url, {f"param_{hb.pk}": "abc"})
    assert bad.status_code == 200 and f"param_{hb.pk}" in bad.context["form"].errors

    client.post(
        url, {f"param_{hb.pk}": "10.5", f"param_{wbc.pk}": "7", f"param_{film.pk}": "Normocytic"}
    )
    assert {r.parameter.name: r.flag for r in LabResult.objects.all()} == {
        "Haemoglobin": "L",
        "WBC": "N",
        "Film": "",
    }


def test_printable_report_content(client_for_role, make_lab_order, make_user):
    lab_staff = make_user(role=Role.LAB_STAFF)
    order = make_lab_order(status=OrderStatus.SAMPLE_COLLECTED)
    item = order.items.get()
    hb = item.test.parameters.get()
    services.save_results(
        order, item=item, values={hb.pk: "17.5"}, comment="Recheck", acting_user=lab_staff
    )
    services.release_results(order, acting_user=lab_staff)

    html = (
        client_for_role(Role.NURSE)
        .get(reverse("laboratory:report_print", args=[order.pk]))
        .content.decode()
    )

    assert order.number in html
    assert "<strong>17.5</strong>" in html and "<strong>H</strong>" in html
    assert "g/dL" in html and "12–16 g/dL" in html and "Recheck" in html


def test_release_with_missing_results_shows_message(client_for_role, make_lab_order):
    order = make_lab_order(status=OrderStatus.SAMPLE_COLLECTED)

    response = client_for_role(Role.LAB_STAFF).post(
        reverse("laboratory:release", args=[order.pk]), follow=True
    )

    assert "Missing:" in response.content.decode()


def test_walk_in_flow(client_for_role, make_patient, make_lab_test):
    patient, test = make_patient(), make_lab_test()
    client = client_for_role(Role.RECEPTIONIST)

    form_page = client.get(reverse("laboratory:walk_in"), {"patient": patient.pk})
    response = client.post(
        reverse("laboratory:walk_in"),
        {
            "patient": patient.pk,
            "tests": [test.pk],
            "referred_by": "Dr. Perera",
            "priority": "ROUTINE",
        },
    )

    assert test.name in form_page.content.decode()
    assert response.status_code == 302
    assert LabOrder.objects.get().referred_by == "Dr. Perera"


# --- Records integration ------------------------------------------------------------


def test_consultation_shows_lab_section_and_form_for_own_doctor(
    client, make_record, make_lab_test, make_doctor
):
    record = make_record()
    test = make_lab_test(name="Lipid Profile")
    order_url = reverse("laboratory:order_for_record", args=[record.pk])

    client.force_login(record.doctor.user)
    own = client.get(reverse("records:record_detail", args=[record.pk])).content.decode()
    assert "Laboratory" in own and order_url in own and "Lipid Profile" in own

    client.post(order_url, {"tests": [test.pk], "priority": "URGENT"})
    order = LabOrder.objects.get()
    assert order.record == record and order.priority == "URGENT"
    page = client.get(reverse("records:record_detail", args=[record.pk])).content.decode()
    assert order.number in page


def test_finalized_record_lab_section_has_no_form_for_others(
    client_for_role, make_record, make_lab_order
):
    record = make_record(status="FINALIZED")
    order = make_lab_order(record=record)

    page = client_for_role(Role.NURSE).get(reverse("records:record_detail", args=[record.pk]))
    content = page.content.decode()

    assert order.number in content
    assert reverse("laboratory:order_for_record", args=[record.pk]) not in content


def test_patients_copy_of_lab_roles_matches():
    assert set(LAB_VIEW_ROLES) == set(VIEW_LAB)


@pytest.mark.parametrize("role", [Role.ADMIN, Role.RECEPTIONIST, Role.DOCTOR, Role.NURSE])
def test_patient_detail_lab_link(client_for_role, make_patient, role):
    patient = make_patient()

    content = (
        client_for_role(role)
        .get(reverse("patients:patient_detail", args=[patient.pk]))
        .content.decode()
    )

    assert ("Lab orders" in content) == (role in VIEW)


EXPECTED_LAB_NAV = {
    Role.ADMIN: ["Laboratory", "Lab tests"],
    Role.DOCTOR: ["Laboratory"],
    Role.NURSE: ["Laboratory"],
    Role.RECEPTIONIST: ["Laboratory"],
    Role.LAB_STAFF: ["Lab worklist", "Lab tests"],
    Role.PHARMACIST: [],
    Role.ACCOUNTANT: [],
}


@pytest.mark.parametrize("role", list(Role))
def test_lab_nav_links(client_for_role, role):
    labels = [
        item["label"]
        for item in client_for_role(role).get(reverse("dashboard")).context["nav_items"]
    ]

    assert [label for label in labels if "Lab" in label] == EXPECTED_LAB_NAV[role]


# --- Result entry is only open while the sample is collected ------------------------


def result_entry_url(order):
    return reverse("laboratory:result_entry", args=[order.pk, order.items.get().pk])


@pytest.mark.parametrize("status", [OrderStatus.REQUESTED, OrderStatus.COMPLETED])
def test_result_entry_redirects_when_closed(client_for_role, make_lab_order, status):
    order = make_lab_order(status=status)
    client = client_for_role(Role.LAB_STAFF)

    get = client.get(result_entry_url(order), follow=True)
    post = client.post(result_entry_url(order), {"comment": "x"})

    assert get.redirect_chain == [(reverse("laboratory:order_detail", args=[order.pk]), 302)]
    assert (
        "Results can only be entered after sample collection and before release."
        in get.content.decode()
    )
    assert post.status_code == 302
    assert not LabResult.objects.exists()


def test_result_entry_open_when_collected(client_for_role, make_lab_order):
    order = make_lab_order(status=OrderStatus.SAMPLE_COLLECTED)

    response = client_for_role(Role.LAB_STAFF).get(result_entry_url(order))

    assert response.status_code == 200
    assert "form" in response.context


def test_result_entry_item_from_another_order_is_404(client_for_role, make_lab_order):
    order = make_lab_order(status=OrderStatus.SAMPLE_COLLECTED)
    other_item = make_lab_order(status=OrderStatus.SAMPLE_COLLECTED).items.get()

    response = client_for_role(Role.LAB_STAFF).get(
        reverse("laboratory:result_entry", args=[order.pk, other_item.pk])
    )

    assert response.status_code == 404
