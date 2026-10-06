"""Template tags that show admissions data in other apps' pages (appointments, patients)
without those apps importing admissions. Same pattern as laboratory's record_lab_section."""

from django import template

from accounts.permissions import user_has_role
from admissions.models import AdmissionStatus
from admissions.permissions import ADMIT, VIEW
from admissions.selectors import patient_admissions, patient_current_admission
from appointments.models import Status as AppointmentStatus

register = template.Library()


@register.inclusion_tag("admissions/partials/appointment_admit_action.html")
def appointment_admit_action(appointment, user):
    """ "Admit patient" for a checked-in/completed visit, or a link to the current stay."""
    current = patient_current_admission(appointment.patient) if user_has_role(user, *VIEW) else None
    can_admit = (
        current is None
        and user_has_role(user, *ADMIT)
        and appointment.status in (AppointmentStatus.CHECKED_IN, AppointmentStatus.COMPLETED)
    )
    return {"appointment": appointment, "current": current, "can_admit": can_admit}


@register.inclusion_tag("admissions/partials/patient_admission_summary.html")
def patient_admission_summary(patient, user):
    """Current-admission banner plus the list of past stays, for VIEW roles."""
    if not user_has_role(user, *VIEW):
        return {"visible": False}
    admissions = list(patient_admissions(patient))
    return {
        "visible": True,
        "patient": patient,
        "current": next((a for a in admissions if a.status == AdmissionStatus.ADMITTED), None),
        "admissions": admissions,
        "can_admit": user_has_role(user, *ADMIT),
    }
