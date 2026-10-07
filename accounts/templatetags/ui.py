"""Template filters for the shared form_field partial (templates/partials/form_field.html).

They pick the design-system CSS class for a form widget, so forms don't need classes set
in Python and every field looks the same.
"""

from django import forms, template

register = template.Library()


@register.filter
def widget_kind(bound_field):
    """ "checkbox", "multiple" (checkbox/radio lists), "select", "textarea", "file" or "input"."""
    widget = bound_field.field.widget
    if isinstance(widget, forms.CheckboxInput):
        return "checkbox"
    if isinstance(widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
        return "multiple"
    if isinstance(widget, forms.Select):  # includes SelectMultiple
        return "select"
    if isinstance(widget, forms.Textarea):
        return "textarea"
    if isinstance(widget, forms.FileInput):  # includes ClearableFileInput
        return "file"
    return "input"


@register.filter
def add_class(bound_field, css_class):
    """Render the field's widget with an extra CSS class (keeping any class it already has)."""
    existing = bound_field.field.widget.attrs.get("class", "")
    return bound_field.as_widget(attrs={"class": f"{existing} {css_class}".strip()})


@register.filter
def accept_label(accept):
    """'.pdf,.jpg,.png' (a file input's accept attribute) -> 'PDF, JPG, PNG'."""
    kinds = [part.strip().lstrip(".").upper() for part in (accept or "").split(",")]
    return ", ".join(kind for kind in kinds if kind)
