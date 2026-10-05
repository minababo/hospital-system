"""Template tags that let other apps' pages show laboratory data.

records templates use {% load laboratory %}{% record_lab_section record request.user %}.
That way the records app's Python code never imports laboratory: the dependency
exists only in the template, and laboratory owns all of its own queries and HTML.
"""

from django import template

from accounts.models import Role
from laboratory.models import Priority
from laboratory.selectors import item_rows, orderable_tests_by_section, orders_for_record

register = template.Library()


@register.inclusion_tag("laboratory/partials/record_lab_section.html", takes_context=True)
def record_lab_section(context, record, user, read_only=False):
    """Lab orders for one consultation, plus the order form for the record's doctor."""
    can_order = not read_only and user.role == Role.DOCTOR and record.doctor.user_id == user.pk
    orders = [
        (order, [(item, item_rows(item)) for item in order.items.all()])
        for order in orders_for_record(record)
    ]
    return {
        # An inclusion tag gets a fresh context, so pass on what the form needs.
        "csrf_token": context.get("csrf_token"),
        "record": record,
        "orders": orders,
        "read_only": read_only,
        "can_order": can_order,
        "tests_by_section": orderable_tests_by_section() if can_order else [],
        "priorities": Priority.choices,
    }
