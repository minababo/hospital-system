from accounts.models import Role
from accounts.selectors import user_list


def test_search_matches_name_username_and_email(make_user):
    ana = make_user(username="asilva", first_name="Ana", last_name="Silva", email="ana@x.com")
    make_user(username="bperera", first_name="Bimal", last_name="Perera", email="bim@x.com")

    assert list(user_list(search="silva")) == [ana]
    assert list(user_list(search="ASIL")) == [ana]
    assert list(user_list(search="ana@x")) == [ana]


def test_filter_by_role_and_active(make_user):
    nurse = make_user(role=Role.NURSE)
    inactive_nurse = make_user(role=Role.NURSE, is_active=False)
    make_user(role=Role.DOCTOR)

    assert set(user_list(role=Role.NURSE)) == {nurse, inactive_nurse}
    assert list(user_list(role=Role.NURSE, is_active=True)) == [nurse]
    assert list(user_list(is_active=False)) == [inactive_nurse]


def test_ordered_by_last_name_then_username(make_user):
    b = make_user(username="b", last_name="Zoysa")
    a = make_user(username="a", last_name="Alwis")
    c = make_user(username="c", last_name="Alwis")

    assert list(user_list()) == [a, c, b]
