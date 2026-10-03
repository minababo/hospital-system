from accounts.models import Role

# Sidebar links per role as (label, url name), in display order.
# Later tasks add their module links here. Showing a link is cosmetic only:
# each view enforces its own roles.
DASHBOARD = ("Dashboard", "dashboard")
CHANGE_PASSWORD = ("Change password", "accounts:password_change")
DOCTORS = ("Doctors", "doctors:doctor_list")
PATIENTS = ("Patients", "patients:patient_list")
APPOINTMENTS = ("Appointments", "appointments:appointment_list")
MEDICINES = ("Medicines", "pharmacy:medicine_list")

NAV_ITEMS = {
    Role.ADMIN: [
        DASHBOARD,
        PATIENTS,
        APPOINTMENTS,
        ("Users", "accounts:user_list"),
        ("Departments", "doctors:department_list"),
        DOCTORS,
        MEDICINES,
        CHANGE_PASSWORD,
    ],
    Role.DOCTOR: [
        DASHBOARD,
        PATIENTS,
        APPOINTMENTS,
        ("My profile", "doctors:me"),
        CHANGE_PASSWORD,
    ],
    Role.NURSE: [
        DASHBOARD,
        PATIENTS,
        APPOINTMENTS,
        DOCTORS,
        CHANGE_PASSWORD,
    ],
    Role.RECEPTIONIST: [
        DASHBOARD,
        PATIENTS,
        APPOINTMENTS,
        DOCTORS,
        CHANGE_PASSWORD,
    ],
    Role.LAB_STAFF: [
        DASHBOARD,
        CHANGE_PASSWORD,
    ],
    Role.PHARMACIST: [
        DASHBOARD,
        MEDICINES,
        CHANGE_PASSWORD,
    ],
    Role.ACCOUNTANT: [
        DASHBOARD,
        CHANGE_PASSWORD,
    ],
}
