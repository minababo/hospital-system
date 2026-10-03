from django.db.models import Q

from pharmacy.models import Medicine


def medicine_list(search=None, form=None, is_active=None):
    medicines = Medicine.objects.all()
    if search:
        medicines = medicines.filter(Q(name__icontains=search) | Q(generic_name__icontains=search))
    if form:
        medicines = medicines.filter(form=form)
    if is_active is not None:
        medicines = medicines.filter(is_active=is_active)
    return medicines


def active_medicines():
    return Medicine.objects.filter(is_active=True)
