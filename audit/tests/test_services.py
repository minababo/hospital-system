from datetime import date, datetime, time
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from accounts.models import Role
from audit.context import reset_request_context, set_request_context
from audit.services import (
    Action,
    created_changes,
    diff,
    json_safe,
    log_action,
    snapshot,
    tracked_fields,
)

pytestmark = pytest.mark.django_db


def test_json_safe_converts_money_dates_times_and_objects(make_department):
    department = make_department(name="Cardiology")
    aware = timezone.make_aware(datetime(2026, 10, 7, 9, 30))
    assert json_safe(Decimal("1500.50")) == "1500.50"
    assert json_safe(date(2026, 10, 7)) == "2026-10-07"
    assert json_safe(time(9, 15)) == "09:15:00"
    assert json_safe(aware) == "2026-10-07T09:30:00+05:30"
    assert json_safe(department) == f"Cardiology (#{department.pk})"
    assert json_safe([Decimal("1"), None, True]) == ["1", None, True]
    assert json_safe({"a": date(2026, 1, 1)}) == {"a": "2026-01-01"}


def test_json_safe_never_raises():
    class Broken:
        def __str__(self):
            raise RuntimeError("no")

    assert json_safe(Broken()) == "<Broken>"


def test_log_action_stores_snapshots_and_object(make_user, make_patient):
    actor = make_user(role=Role.RECEPTIONIST, first_name="Kamala", last_name="Silva")
    patient = make_patient()
    entry = log_action(
        actor=actor,
        action=Action.UPDATE,
        event="patients.patient.updated",
        obj=patient,
        patient=patient,
        changes={"phone": ["0771111111", "0772222222"], "fee": [Decimal("1"), Decimal("2")]},
        message="x" * 300,
    )
    entry.refresh_from_db()
    assert entry.actor == actor
    assert entry.actor_name == "Kamala Silva"
    assert entry.actor_role == Role.RECEPTIONIST
    assert entry.content_type == ContentType.objects.get_for_model(patient)
    assert entry.object_id == str(patient.pk)
    assert entry.object_repr == str(patient)
    assert entry.patient == patient
    assert entry.changes == {"phone": ["0771111111", "0772222222"], "fee": ["1", "2"]}
    assert len(entry.message) == 255


def test_log_action_anonymous_actor_has_empty_snapshots():
    entry = log_action(actor=None, action=Action.LOGIN_FAILED, event="accounts.user.login_failed")
    assert entry.actor is None and entry.actor_name == "" and entry.actor_role == ""


def test_log_action_drops_excluded_fields_and_odd_change_values(make_user):
    entry = log_action(
        actor=None,
        action=Action.UPDATE,
        event="accounts.user.updated",
        changes={"password": ["a", "b"], "last_login": [None, "x"], "note": "single value"},
    )
    assert entry.changes == {"note": [None, "single value"]}


def test_log_action_uses_the_request_context(make_user):
    token = set_request_context("203.0.113.9", "Firefox")
    try:
        entry = log_action(actor=None, action=Action.VIEW, event="tests.thing.viewed")
    finally:
        reset_request_context(token)
    assert entry.ip_address == "203.0.113.9"
    assert entry.user_agent == "Firefox"
    # Outside a request (management commands, tests) there is no IP.
    entry = log_action(actor=None, action=Action.VIEW, event="tests.thing.viewed")
    assert entry.ip_address is None and entry.user_agent == ""


def test_diff_returns_only_changed_fields():
    before = {"a": 1, "b": "x", "password": "old"}
    after = {"a": 1, "b": "y", "c": 3, "password": "new"}
    assert diff(before, after) == {"b": ["x", "y"], "c": [None, 3]}


def test_user_fields_never_include_password():
    fields = tracked_fields(get_user_model())
    assert "password" not in fields and "last_login" not in fields
    assert "username" in fields


def test_snapshot_and_created_changes(make_department):
    department = make_department(name="ENT")
    assert snapshot(department, ["name", "password"]) == {"name": "ENT"}
    assert created_changes(department, ["name"]) == {"name": [None, "ENT"]}


def test_file_fields_store_the_name_only(make_patient, make_upload, make_user):
    from patients import services as patient_services

    document = patient_services.upload_document(
        patient=make_patient(),
        file=make_upload("scan.pdf"),
        category="LAB_REPORT",
        description="",
        acting_user=make_user(role=Role.ADMIN),
    )
    value = json_safe(document.file)
    assert value == document.file.name and isinstance(value, str)
