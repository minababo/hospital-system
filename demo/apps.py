from django.apps import AppConfig


class DemoConfig(AppConfig):
    """Demo data for reviewers (manage.py seed_demo). No models; nothing imports it."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "demo"
