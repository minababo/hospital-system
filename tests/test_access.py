"""Default deny: every page needs login unless it is on the allowlist."""

import pytest
from django.urls import NoReverseMatch, URLResolver, get_resolver, reverse

PUBLIC_URL_NAMES = {"accounts:login", "healthz"}


def named_urls_without_args(patterns, namespace=None):
    """Yield (name, path) for every named URL that can be reversed without arguments."""
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            if pattern.namespace == "admin":
                continue  # Django admin has its own login
            if pattern.namespace and namespace:
                child_namespace = f"{namespace}:{pattern.namespace}"
            else:
                child_namespace = pattern.namespace or namespace
            yield from named_urls_without_args(pattern.url_patterns, child_namespace)
        elif pattern.name:
            name = f"{namespace}:{pattern.name}" if namespace else pattern.name
            try:
                yield name, reverse(name)
            except NoReverseMatch:
                continue  # needs arguments, e.g. <int:pk>


PROTECTED_URLS = [
    (name, path)
    for name, path in named_urls_without_args(get_resolver().url_patterns)
    if name not in PUBLIC_URL_NAMES
]


def test_walker_found_the_main_pages():
    names = {name for name, _ in PROTECTED_URLS}
    assert {"dashboard", "accounts:user_list", "accounts:password_change"} <= names


@pytest.mark.django_db
@pytest.mark.parametrize(("name", "path"), PROTECTED_URLS)
def test_anonymous_is_redirected_to_login(client, name, path):
    response = client.get(path)

    assert response.status_code == 302, name
    assert response.url.startswith(reverse("accounts:login")), name


@pytest.mark.django_db
@pytest.mark.parametrize("name", sorted(PUBLIC_URL_NAMES))
def test_public_urls_are_reachable_anonymously(client, name):
    assert client.get(reverse(name)).status_code == 200
