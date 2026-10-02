from django.db.models import Q

from accounts.models import User


def user_list(search=None, role=None, is_active=None):
    users = User.objects.all()
    if search:
        users = users.filter(
            Q(username__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
        )
    if role:
        users = users.filter(role=role)
    if is_active is not None:
        users = users.filter(is_active=is_active)
    return users.order_by("last_name", "username")
