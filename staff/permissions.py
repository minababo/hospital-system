from accounts.models import Role
from accounts.permissions import ALL_ROLES

# HR work (employees, attendance, approving leave) is done by the admin.
HR = (Role.ADMIN,)
# Self-service leave: any logged-in staff role, but the view also requires the user
# to be linked to an employee record.
SELF = ALL_ROLES
