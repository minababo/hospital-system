from accounts.models import Role

# Catalog, inventory and stock alerts.
MANAGE_MEDICINES = (Role.ADMIN, Role.PHARMACIST)
# Handing over medicines (the service also checks the role).
DISPENSE = (Role.PHARMACIST,)
# The dispensing queue and pages; the admin sees them read-only.
DISPENSE_VIEW = (Role.PHARMACIST, Role.ADMIN)
