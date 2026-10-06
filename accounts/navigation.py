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
INVENTORY = ("Inventory", "pharmacy:inventory_list")
STOCK_ALERTS = ("Stock alerts", "pharmacy:alerts")
BILLING = ("Billing", "billing:invoice_list")
LABORATORY = ("Laboratory", "laboratory:worklist")
LAB_TESTS = ("Lab tests", "laboratory:test_list")
ADMISSIONS = ("Admissions", "admissions:admission_list")

NAV_ITEMS = {
    Role.ADMIN: [
        DASHBOARD,
        PATIENTS,
        APPOINTMENTS,
        ADMISSIONS,
        LABORATORY,
        BILLING,
        ("Users", "accounts:user_list"),
        ("Departments", "doctors:department_list"),
        ("Wards & beds", "admissions:ward_list"),
        DOCTORS,
        MEDICINES,
        INVENTORY,
        STOCK_ALERTS,
        LAB_TESTS,
        CHANGE_PASSWORD,
    ],
    Role.DOCTOR: [
        DASHBOARD,
        PATIENTS,
        APPOINTMENTS,
        ADMISSIONS,
        LABORATORY,
        ("My profile", "doctors:me"),
        CHANGE_PASSWORD,
    ],
    Role.NURSE: [
        DASHBOARD,
        PATIENTS,
        APPOINTMENTS,
        ADMISSIONS,
        LABORATORY,
        DOCTORS,
        CHANGE_PASSWORD,
    ],
    Role.RECEPTIONIST: [
        DASHBOARD,
        PATIENTS,
        APPOINTMENTS,
        ADMISSIONS,
        LABORATORY,
        BILLING,
        DOCTORS,
        CHANGE_PASSWORD,
    ],
    Role.LAB_STAFF: [
        DASHBOARD,
        ("Lab worklist", "laboratory:worklist"),
        LAB_TESTS,
        CHANGE_PASSWORD,
    ],
    Role.PHARMACIST: [
        DASHBOARD,
        ("Prescriptions", "pharmacy:dispensing_queue"),
        INVENTORY,
        STOCK_ALERTS,
        MEDICINES,
        CHANGE_PASSWORD,
    ],
    Role.ACCOUNTANT: [
        DASHBOARD,
        BILLING,
        CHANGE_PASSWORD,
    ],
}
