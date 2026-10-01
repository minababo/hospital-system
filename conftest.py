import pytest


@pytest.fixture(autouse=True)
def _plain_static_storage(settings):
    # Tests run with DEBUG=False, and the manifest storage fails without collectstatic.
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
