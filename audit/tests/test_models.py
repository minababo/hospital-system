import pytest

from accounts.models import Role
from audit.models import Action, AuditLog, ImmutableError
from audit.services import log_action

pytestmark = pytest.mark.django_db


@pytest.fixture
def entry(make_user):
    return log_action(
        actor=make_user(role=Role.ADMIN), action=Action.CREATE, event="tests.thing.created"
    )


def test_saving_an_existing_entry_raises(entry):
    entry.message = "edited"
    with pytest.raises(ImmutableError):
        entry.save()
    entry.refresh_from_db()
    assert entry.message == ""


def test_deleting_an_entry_raises(entry):
    with pytest.raises(ImmutableError):
        entry.delete()
    assert AuditLog.objects.filter(pk=entry.pk).exists()


def test_queryset_update_and_delete_raise(entry):
    with pytest.raises(ImmutableError):
        AuditLog.objects.filter(pk=entry.pk).update(message="edited")
    with pytest.raises(ImmutableError):
        AuditLog.objects.all().delete()
    assert AuditLog.objects.count() == 1


def test_queryset_update_cant_sneak_in_a_change_next_to_a_null_link(entry):
    with pytest.raises(ImmutableError):
        AuditLog.objects.update(actor=None, message="edited")


def test_deleting_the_actor_keeps_the_entry_and_its_snapshot(make_user):
    # Django's SET_NULL clears the link with a queryset update; that one is allowed.
    user = make_user(role=Role.NURSE, first_name="Nimal", last_name="Perera")
    entry = log_action(actor=user, action=Action.LOGIN, event="accounts.user.logged_in")
    user.delete()
    entry.refresh_from_db()
    assert entry.actor is None
    assert entry.actor_name == "Nimal Perera"
    assert entry.actor_role == Role.NURSE


def test_newest_first_and_module(make_user):
    first = log_action(actor=None, action=Action.VIEW, event="patients.document.viewed")
    second = log_action(actor=None, action=Action.VIEW, event="billing.invoice.issued")
    assert list(AuditLog.objects.all()) == [second, first]
    assert second.module == "billing"
