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
# Shown to any user linked to an active employee record (see context_processors).
MY_LEAVE = ("My leave", "staff:my_leave")
REPORTS = ("Reports", "reports:index")

NAV_ITEMS = {
    Role.ADMIN: [
        DASHBOARD,
        PATIENTS,
        APPOINTMENTS,
        ADMISSIONS,
        LABORATORY,
        BILLING,
        ("Users", "accounts:user_list"),
        ("Staff", "staff:employee_list"),
        ("Attendance", "staff:attendance"),
        ("Leave", "staff:leave_list"),
        ("Audit log", "audit:log_list"),
        ("Departments", "doctors:department_list"),
        ("Wards & beds", "admissions:ward_list"),
        DOCTORS,
        MEDICINES,
        INVENTORY,
        STOCK_ALERTS,
        LAB_TESTS,
        REPORTS,
        CHANGE_PASSWORD,
    ],
    Role.DOCTOR: [
        DASHBOARD,
        PATIENTS,
        APPOINTMENTS,
        ADMISSIONS,
        LABORATORY,
        ("My profile", "doctors:me"),
        REPORTS,
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
        REPORTS,
        CHANGE_PASSWORD,
    ],
    Role.LAB_STAFF: [
        DASHBOARD,
        ("Lab worklist", "laboratory:worklist"),
        LAB_TESTS,
        REPORTS,
        CHANGE_PASSWORD,
    ],
    Role.PHARMACIST: [
        DASHBOARD,
        ("Prescriptions", "pharmacy:dispensing_queue"),
        INVENTORY,
        STOCK_ALERTS,
        MEDICINES,
        REPORTS,
        CHANGE_PASSWORD,
    ],
    Role.ACCOUNTANT: [
        DASHBOARD,
        BILLING,
        REPORTS,
        CHANGE_PASSWORD,
    ],
}

# Sidebar grouping. Groups show in this order, each only when the role has an item in it;
# items keep their order from NAV_ITEMS within a group.
NAV_GROUPS = [
    "Overview",
    "Clinical",
    "Front desk",
    "Finance",
    "Pharmacy & Lab",
    "Administration",
    "Account",
]

# url name -> (group, icon). Icons are names in templates/partials/icon.html.
# Every url name used above needs an entry (a test checks this).
NAV_META = {
    "dashboard": ("Overview", "home"),
    "reports:index": ("Overview", "chart-bar"),
    "admissions:admission_list": ("Clinical", "building-office-2"),
    "doctors:me": ("Clinical", "identification"),
    "doctors:doctor_list": ("Clinical", "user-circle"),
    "patients:patient_list": ("Front desk", "users"),
    "appointments:appointment_list": ("Front desk", "calendar"),
    "billing:invoice_list": ("Finance", "banknotes"),
    "laboratory:worklist": ("Pharmacy & Lab", "beaker"),
    "laboratory:test_list": ("Pharmacy & Lab", "list-bullet"),
    "pharmacy:dispensing_queue": ("Pharmacy & Lab", "clipboard-document-list"),
    "pharmacy:inventory_list": ("Pharmacy & Lab", "archive-box"),
    "pharmacy:alerts": ("Pharmacy & Lab", "exclamation-triangle"),
    "pharmacy:medicine_list": ("Pharmacy & Lab", "eye-dropper"),
    "accounts:user_list": ("Administration", "shield-check"),
    "staff:employee_list": ("Administration", "briefcase"),
    "staff:attendance": ("Administration", "clipboard-document-check"),
    "staff:leave_list": ("Administration", "calendar-days"),
    "audit:log_list": ("Administration", "document-magnifying-glass"),
    "doctors:department_list": ("Administration", "building-office"),
    "admissions:ward_list": ("Administration", "squares-2x2"),
    "staff:my_leave": ("Account", "calendar-days"),
    "accounts:password_change": ("Account", "key"),
}
