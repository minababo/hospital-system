"""Template tags that let other apps' pages show pharmacy data.

records templates use {% load pharmacy %}{% prescription_dispensing prescription %} to show
what the pharmacy has handed over. The records app's Python code never imports the
dispensing modules: the dependency exists only in the template, and pharmacy owns its
queries and HTML (the same pattern as {% record_lab_section %}).
"""

from django import template

from pharmacy.dispensing_selectors import dispense_log, dispensing_summary

register = template.Library()


@register.inclusion_tag("pharmacy/partials/prescription_dispensing.html")
def prescription_dispensing(prescription, anchor=True):
    """The "Dispensing" card: prescribed/dispensed/remaining and the dispense log.
    anchor=False leaves out the #dispensing id (pages that show several prescriptions)."""
    return {
        "prescription": prescription,
        "rows": dispensing_summary(prescription),
        "log": dispense_log(prescription),
        "anchor": anchor,
    }
