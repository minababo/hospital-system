import re
from dataclasses import dataclass

from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from admissions.models import Admission, AdmissionStatus, Bed, BedAssignment, Ward
from appointments.selectors import todays_appointments
from patients.selectors import HistoryEvent, search_patients

ADMISSION_NUMBER = re.compile(r"(?:ADM-?)?(\d{1,18})", re.IGNORECASE)


def _open_assignment_exists():
    return Exists(BedAssignment.objects.filter(bed=OuterRef("pk"), ended_at__isnull=True))


# --- Wards and beds ------------------------------------------------------------------


def ward_list(is_active=None):
    open_assignments = Q(beds__assignments__ended_at__isnull=True)
    wards = Ward.objects.select_related("department").annotate(
        bed_count=Count("beds", distinct=True),
        occupied_count=Count("beds", filter=open_assignments, distinct=True),
    )
    if is_active is not None:
        wards = wards.filter(is_active=is_active)
    return wards.order_by("name")


def free_beds(ward=None):
    """Active beds in active wards with no open assignment. Occupancy is derived from
    BedAssignment rows (an Exists subquery), not stored on the bed."""
    beds = (
        Bed.objects.filter(is_active=True, ward__is_active=True)
        .filter(~_open_assignment_exists())
        .select_related("ward")
        .order_by("ward__name", "bed_number")
    )
    if ward is not None:
        beds = beds.filter(ward=ward)
    return beds


def free_beds_by_ward(exclude_bed=None):
    """[(ward, [beds]), ...] for grouped select boxes."""
    groups = {}
    for bed in free_beds():
        if exclude_bed is not None and bed.pk == exclude_bed.pk:
            continue
        groups.setdefault(bed.ward, []).append(bed)
    return list(groups.items())


@dataclass
class BedTile:
    bed: Bed
    state: str  # FREE / OCCUPIED / INACTIVE
    assignment: BedAssignment | None = None

    @property
    def admission(self):
        return self.assignment.admission if self.assignment else None


def bed_board(now=None):
    """Active wards with a tile per bed. One query per level (wards, beds, open
    assignments with their admission and patient), not one per bed."""
    open_assignments = BedAssignment.objects.filter(ended_at__isnull=True).select_related(
        "admission__patient"
    )
    wards = Ward.objects.filter(is_active=True).prefetch_related(
        Prefetch(
            "beds",
            queryset=Bed.objects.order_by("bed_number").prefetch_related(
                Prefetch("assignments", queryset=open_assignments, to_attr="open_assignments")
            ),
        )
    )
    board = []
    for ward in wards.order_by("name"):
        tiles = []
        for bed in ward.beds.all():
            assignment = bed.open_assignments[0] if bed.open_assignments else None
            if assignment:
                state = "OCCUPIED"
            elif not bed.is_active:
                state = "INACTIVE"
            else:
                state = "FREE"
            tiles.append(BedTile(bed=bed, state=state, assignment=assignment))
        board.append((ward, tiles))
    return board


def occupancy_summary():
    """Bed numbers for the bed board and the dashboard (active beds in active wards)."""
    beds = Bed.objects.filter(is_active=True, ward__is_active=True)
    total = beds.count()
    occupied = beds.filter(_open_assignment_exists()).count()
    return {
        "total": total,
        "occupied": occupied,
        "free": total - occupied,
        "percent": round(occupied * 100 / total) if total else 0,
    }


# --- Admissions ----------------------------------------------------------------------


def _admissions():
    return Admission.objects.select_related("patient", "admitting_doctor__user").prefetch_related(
        Prefetch("assignments", queryset=BedAssignment.objects.select_related("bed__ward"))
    )


def _search(admissions, q):
    q = (q or "").strip()
    if not q:
        return admissions
    matches = Q(patient__in=search_patients(q))
    number = ADMISSION_NUMBER.fullmatch(q)
    if number:
        matches |= Q(pk=int(number.group(1)))
    return admissions.filter(matches)


def current_admissions(*, ward=None, q=None, admitting_doctor_user=None):
    admissions = _admissions().filter(status=AdmissionStatus.ADMITTED)
    if admitting_doctor_user is not None:
        admissions = admissions.filter(admitting_doctor__user=admitting_doctor_user)
    if ward is not None:
        in_ward = BedAssignment.objects.filter(
            admission=OuterRef("pk"), ended_at__isnull=True, bed__ward=ward
        )
        admissions = admissions.filter(Exists(in_ward))
    return _search(admissions, q).order_by("admitted_at")


def discharged_admissions(*, date_from=None, date_to=None, q=None):
    admissions = _admissions().filter(status=AdmissionStatus.DISCHARGED)
    if date_from:
        admissions = admissions.filter(discharged_at__date__gte=date_from)
    if date_to:
        admissions = admissions.filter(discharged_at__date__lte=date_to)
    return _search(admissions, q).order_by("-discharged_at")


def get_admission(pk):
    return get_object_or_404(
        _admissions()
        .select_related("appointment", "admitted_by", "discharged_by")
        .prefetch_related("notes__author"),
        pk=pk,
    )


def patient_current_admission(patient):
    return _admissions().filter(patient=patient, status=AdmissionStatus.ADMITTED).first()


def patient_admissions(patient):
    return _admissions().filter(patient=patient).order_by("-admitted_at")


def outpatients_today(user, now=None):
    """Outpatients = today's appointments (the appointments module owns them)."""
    return todays_appointments(user=user, now=now)


# --- Patient history provider -------------------------------------------------------


def admission_history_events(patient):
    """Registered in patients.selectors.PROVIDERS by AdmissionsConfig.ready()."""
    events = []
    for admission in patient_admissions(patient):
        url = reverse("admissions:admission_detail", args=[admission.pk])
        assignments = list(admission.assignments.all())
        first = assignments[0] if assignments else None
        place = f" to {first.bed.ward.name}/{first.bed.bed_number}" if first else ""
        events.append(
            HistoryEvent(
                timestamp=admission.admitted_at,
                kind="Admission",
                title=f"Admitted ({admission.number}){place}",
                detail=admission.reason,
                url=url,
            )
        )
        for assignment in assignments[1:]:
            events.append(
                HistoryEvent(
                    timestamp=assignment.started_at,
                    kind="Admission",
                    title=(
                        f"Transferred to {assignment.bed.ward.name}/{assignment.bed.bed_number} "
                        f"({admission.number})"
                    ),
                    url=url,
                )
            )
        if admission.discharged_at:
            events.append(
                HistoryEvent(
                    timestamp=admission.discharged_at,
                    kind="Admission",
                    title=f"Discharged ({admission.get_discharge_type_display()})",
                    detail=admission.number,
                    url=url,
                )
            )
    return events


def stay_nights(admission, now=None):
    """Total nights over all bed periods so far."""
    now = now or timezone.now()
    return sum(assignment.nights(now) for assignment in admission.assignments.all())
