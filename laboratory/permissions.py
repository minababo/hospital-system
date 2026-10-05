from accounts.models import Role

CATALOG = (Role.ADMIN, Role.LAB_STAFF)
LAB = (Role.LAB_STAFF,)
WALK_IN = (Role.RECEPTIONIST, Role.LAB_STAFF)
VIEW_LAB = (Role.ADMIN, Role.DOCTOR, Role.NURSE, Role.LAB_STAFF)
# Printable reports (and the list of completed orders) are also for reception.
REPORT = (*VIEW_LAB, Role.RECEPTIONIST)
ORDER_FROM_RECORD = (Role.DOCTOR,)
# The service also checks a doctor is the order's own ordering doctor.
CANCEL = (Role.LAB_STAFF, Role.DOCTOR)
