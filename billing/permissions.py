from accounts.models import Role

# Separation of duties: cashiers take money, the accountant adjusts bills, and only
# the admin can void invoices and payments (undoing money that was recorded).
VIEW_BILLING = (Role.ADMIN, Role.ACCOUNTANT, Role.RECEPTIONIST)
CASHIER = (Role.ACCOUNTANT, Role.RECEPTIONIST)
ACCOUNTS = (Role.ACCOUNTANT,)
VOID_MONEY = (Role.ADMIN,)
VOID_CHARGE = (Role.ACCOUNTANT, Role.ADMIN)
