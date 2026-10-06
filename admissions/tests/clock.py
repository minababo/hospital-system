"""Fixed times for service tests (passed as now=...), in the hospital time zone."""

from datetime import datetime

from django.utils import timezone


def colombo(year, month, day, hour=12, minute=0):
    return timezone.make_aware(datetime(year, month, day, hour, minute))


NOW = colombo(2026, 10, 10, 12, 0)
