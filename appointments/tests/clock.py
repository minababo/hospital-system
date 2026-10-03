"""Fixed dates and times for service/selector tests (passed as now=...)."""

from datetime import date, datetime

from django.utils import timezone

MONDAY = date(2026, 10, 5)
SUNDAY_BEFORE = date(2026, 10, 4)
TUESDAY = date(2026, 10, 6)
NEXT_MONDAY = date(2026, 10, 12)


def colombo(day, hour, minute=0):
    """Aware datetime in the hospital time zone."""
    return timezone.make_aware(datetime(day.year, day.month, day.day, hour, minute))


MONDAY_8AM = colombo(MONDAY, 8)
