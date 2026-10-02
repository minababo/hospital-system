from django.shortcuts import render

from accounts.permissions import ALL_ROLES, role_required


@role_required(*ALL_ROLES)
def dashboard(request):
    # One template per role, e.g. reports/dashboards/lab_staff.html
    return render(request, f"reports/dashboards/{request.user.role.lower()}.html")
