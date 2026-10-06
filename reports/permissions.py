from accounts.models import Role

PATIENT_REPORT = (Role.ADMIN, Role.RECEPTIONIST)
APPOINTMENT_REPORT = (Role.ADMIN, Role.RECEPTIONIST, Role.DOCTOR)
REVENUE_REPORT = (Role.ADMIN, Role.ACCOUNTANT)
PHARMACY_REPORT = (Role.ADMIN, Role.PHARMACIST)
LABORATORY_REPORT = (Role.ADMIN, Role.LAB_STAFF)
STAFF_REPORT = (Role.ADMIN,)

# Roles that can open at least one report (they get the "Reports" sidebar link).
ANY_REPORT = tuple(
    dict.fromkeys(
        PATIENT_REPORT
        + APPOINTMENT_REPORT
        + REVENUE_REPORT
        + PHARMACY_REPORT
        + LABORATORY_REPORT
        + STAFF_REPORT
    )
)
