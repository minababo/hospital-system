from django.db import transaction

from pharmacy.models import Medicine

# Audit logging of these actions will be added in these services (audit app).


@transaction.atomic
def create_medicine(*, acting_user, **fields):
    medicine = Medicine(**fields)
    medicine.full_clean()
    medicine.save()
    return medicine


@transaction.atomic
def update_medicine(medicine, *, acting_user, **fields):
    for name, value in fields.items():
        setattr(medicine, name, value)
    medicine.full_clean()
    medicine.save()
    return medicine


@transaction.atomic
def set_medicine_active(medicine, active, *, acting_user):
    medicine.is_active = active
    medicine.save(update_fields=["is_active", "updated_at"])
    return medicine
