"""Date helpers for dashboards and reports.

Datetime columns (received_at, created_at, ...) are stored in UTC. A "day" in the
hospital is a Sri Lankan calendar day, so filters use the local midnight bounds below
instead of UTC dates. DateFields (appointment date, leave dates) need no conversion.
"""

import calendar
from datetime import date, datetime, time, timedelta

from django import forms
from django.utils import timezone

MAX_RANGE_DAYS = 366


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


DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class DateRangeForm(forms.Form):
    """From/to dates for a report. Both empty means the current month."""

    date_from = forms.DateField(required=False, widget=DATE_INPUT, label="From")
    date_to = forms.DateField(required=False, widget=DATE_INPUT, label="To")

    def clean(self):
        cleaned = super().clean()
        first, last = this_month(timezone.localdate())
        date_from = cleaned.get("date_from") or first
        date_to = cleaned.get("date_to") or last
        if date_from > date_to:
            raise forms.ValidationError("The start date must be on or before the end date.")
        if (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
            raise forms.ValidationError(f"Choose a range of at most {MAX_RANGE_DAYS} days.")
        cleaned["date_from"], cleaned["date_to"] = date_from, date_to
        return cleaned

    def date_range(self):
        """The chosen range, or the current month when the input is invalid."""
        if self.is_valid():
            return self.cleaned_data["date_from"], self.cleaned_data["date_to"]
        return this_month(timezone.localdate())
