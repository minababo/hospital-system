import csv
import io
from datetime import date, datetime

from django.utils import timezone

from common.csv_export import csv_response, safe_cell
from common.dates import local_day_bounds, local_range_bounds, this_month
from reports.dates import DateRangeForm


def colombo(*args):
    return timezone.make_aware(datetime(*args))


def test_local_day_bounds_are_colombo_midnights():
    start, end = local_day_bounds(date(2026, 10, 15))

    assert start == colombo(2026, 10, 15, 0, 0)
    assert end == colombo(2026, 10, 16, 0, 0)
    # 23:30 local on the 15th is inside, 00:10 local on the 16th is not.
    assert start <= colombo(2026, 10, 15, 23, 30) < end
    assert not colombo(2026, 10, 16, 0, 10) < end
    # In UTC the day starts at 18:30 the previous evening (Colombo is UTC+5:30).
    assert start.astimezone(timezone.UTC).hour == 18


def test_local_range_bounds_include_both_days():
    start, end = local_range_bounds(date(2026, 10, 1), date(2026, 10, 31))

    assert (start, end) == (colombo(2026, 10, 1, 0, 0), colombo(2026, 11, 1, 0, 0))


def test_this_month():
    assert this_month(date(2026, 2, 10)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert this_month(date(2028, 2, 10)) == (date(2028, 2, 1), date(2028, 2, 29))


def test_date_range_form_defaults_to_this_month():
    form = DateRangeForm({})

    assert form.is_valid()
    assert form.date_range() == this_month(timezone.localdate())


def test_date_range_form_rejects_reversed_and_long_ranges():
    reversed_range = DateRangeForm({"date_from": "2026-10-10", "date_to": "2026-10-01"})
    too_long = DateRangeForm({"date_from": "2025-01-01", "date_to": "2026-01-02"})
    exactly_366 = DateRangeForm({"date_from": "2025-01-01", "date_to": "2026-01-01"})

    assert not reversed_range.is_valid() and "on or before" in str(reversed_range.errors)
    assert not too_long.is_valid() and "at most 366 days" in str(too_long.errors)
    assert exactly_366.is_valid()
    assert reversed_range.date_range() == this_month(timezone.localdate())  # safe fallback


def test_safe_cell_escapes_formula_starts_only():
    for dangerous in ("=1+1", "+94771234567", "-5", "@SUM(A1)", "\tx", "\rx"):
        assert safe_cell(dangerous) == "'" + dangerous
    for harmless in ("Kamal Perera", "", "a=b"):
        assert safe_cell(harmless) == harmless
    assert safe_cell(-5) == -5  # numbers are not text, so they stay numbers


def test_csv_response_has_header_and_escaped_rows():
    response = csv_response("x.csv", ["Name", "Amount"], [["=HYPERLINK(1)", 1500]])

    assert response["Content-Type"].startswith("text/csv")
    assert response["Content-Disposition"] == 'attachment; filename="x.csv"'
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert rows == [["Name", "Amount"], ["'=HYPERLINK(1)", "1500"]]
