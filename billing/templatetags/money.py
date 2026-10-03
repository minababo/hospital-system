from django import template
from django.template.defaultfilters import floatformat

register = template.Library()


@register.filter
def rupees(value):
    """{{ amount|rupees }} -> "Rs. 1,500.00". Empty values show as Rs. 0.00."""
    return f"Rs. {floatformat(value or 0, '2g')}"
