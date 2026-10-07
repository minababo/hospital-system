"""Local-day helpers shared by reports and audit.

Datetime columns (received_at, created_at, ...) are stored in UTC. A "day" in the
hospital is a Sri Lankan calendar day, so filters use the local midnight bounds below
instead of UTC dates. DateFields (appointment date, leave dates) need no conversion.
"""

import calendar
from datetime import date, datetime, time, timedelta

from django.utils import timezone


def local_day_bounds(day):
    """(start, end) of a local calendar day as aware datetimes. The range is half-open:
    filter with `field__gte=start, field__lt=end`."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min), tz)
    return start, end


def local_range_bounds(date_from, date_to):
    """(start of date_from, start of the day after date_to): half-open, both days included."""
    return local_day_bounds(date_from)[0], local_day_bounds(date_to)[1]


def this_month(today):
    """(first day, last day) of the month containing `today`."""
    last = calendar.monthrange(today.year, today.month)[1]
    return date(today.year, today.month, 1), date(today.year, today.month, last)
