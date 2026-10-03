from accounts.models import Role

# Who can read finalized medical records (drafts are visible only to their own doctor).
VIEW_RECORDS = (Role.ADMIN, Role.DOCTOR, Role.NURSE)
# Writing clinical notes, diagnoses and prescriptions.
DOCTOR_ONLY = (Role.DOCTOR,)
# Recording vital signs.
RECORD_VITALS = (Role.NURSE, Role.DOCTOR)
