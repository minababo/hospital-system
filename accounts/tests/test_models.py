import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from accounts.models import Role, User


@pytest.mark.django_db
def test_create_user_requires_role():
    with pytest.raises(ValueError, match="role"):
        User.objects.create_user(username="norole", password="x")


@pytest.mark.django_db
def test_create_superuser_defaults_to_admin_role():
    user = User.objects.create_superuser(username="root", password="x")

    assert user.role == Role.ADMIN


@pytest.mark.django_db
def test_database_rejects_invalid_role(make_user):
    user = make_user(role=Role.NURSE)

    # update() skips model validation, so this reaches the CHECK constraint.
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(role="JANITOR")


@pytest.mark.django_db
def test_str_uses_full_name_then_username(make_user):
    assert str(make_user(first_name="Ana", last_name="Silva")) == "Ana Silva"
    assert str(make_user(username="noname", first_name="", last_name="")) == "noname"


@pytest.mark.django_db(transaction=True)
def test_role_migration_gives_existing_users_admin():
    before = [("accounts", "0001_initial")]
    after = [("accounts", "0002_user_role")]

    executor = MigrationExecutor(connection)
    executor.migrate(before)
    OldUser = executor.loader.project_state(before).apps.get_model("accounts", "User")
    OldUser.objects.create(username="legacy", password="x")

    executor = MigrationExecutor(connection)
    executor.migrate(after)
    NewUser = executor.loader.project_state(after).apps.get_model("accounts", "User")

    assert NewUser.objects.get(username="legacy").role == Role.ADMIN
    assert not NewUser.objects.filter(role="").exists()

    # Leave the database fully migrated for the tests that follow.
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())
