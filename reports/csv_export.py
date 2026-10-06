import csv

from django.http import HttpResponse

# A cell starting with one of these can be run as a formula by Excel or LibreOffice
# ("CSV injection"), e.g. a patient named =HYPERLINK(...). Such cells get a leading '.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def csv_response(filename, header, rows):
    """A downloadable CSV. Numbers (including Decimal money) are written as plain
    values, e.g. 1500.00, without "Rs." or thousands separators."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(value) for value in row])
    return response
