"""The report date-range form. Local-day helpers live in common/dates.py."""

from django import forms
from django.utils import timezone

from common.dates import this_month

MAX_RANGE_DAYS = 366

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
