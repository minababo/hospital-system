from accounts.models import Role

VIEW = (Role.ADMIN, Role.DOCTOR, Role.NURSE, Role.RECEPTIONIST)
# Clinical content (progress notes, discharge summary) is hidden from reception.
CLINICAL = (Role.ADMIN, Role.DOCTOR, Role.NURSE)
ADMIT = (Role.DOCTOR, Role.NURSE, Role.RECEPTIONIST)
CARE = (Role.DOCTOR, Role.NURSE)
DISCHARGE = (Role.DOCTOR,)
WARDS = (Role.ADMIN,)
