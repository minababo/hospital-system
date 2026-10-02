def add_service_errors(error, *forms):
    """Show a ValidationError raised by a service on the right form field.

    Each field error goes to the first form that has that field; anything else
    (non-field errors, or fields not on any form) goes to the first form's top errors.
    """
    if not hasattr(error, "error_dict"):
        forms[0].add_error(None, error)
        return
    for field, field_errors in error.error_dict.items():
        target = next((form for form in forms if field in form.fields), None)
        if target:
            target.add_error(field, field_errors)
        else:
            forms[0].add_error(None, field_errors)
