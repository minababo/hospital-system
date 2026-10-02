from accounts.models import Role

# Sidebar links per role as (label, url name), in display order.
# Later tasks add their module links here. Showing a link is cosmetic only:
# each view enforces its own roles.
NAV_ITEMS = {
    Role.ADMIN: [
        ("Dashboard", "dashboard"),
        ("Users", "accounts:user_list"),
        ("Change password", "accounts:password_change"),
    ],
    Role.DOCTOR: [
        ("Dashboard", "dashboard"),
        ("Change password", "accounts:password_change"),
    ],
    Role.NURSE: [
        ("Dashboard", "dashboard"),
        ("Change password", "accounts:password_change"),
    ],
    Role.RECEPTIONIST: [
        ("Dashboard", "dashboard"),
        ("Change password", "accounts:password_change"),
    ],
    Role.LAB_STAFF: [
        ("Dashboard", "dashboard"),
        ("Change password", "accounts:password_change"),
    ],
    Role.PHARMACIST: [
        ("Dashboard", "dashboard"),
        ("Change password", "accounts:password_change"),
    ],
    Role.ACCOUNTANT: [
        ("Dashboard", "dashboard"),
        ("Change password", "accounts:password_change"),
    ],
}
