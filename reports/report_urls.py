"""Report pages under /reports/ (namespace "reports"). The dashboard keeps its own
un-namespaced URL in reports/urls.py, since many templates link to "dashboard"."""

from django.urls import path

from reports import views

app_name = "reports"

urlpatterns = [
    path("", views.ReportIndexView.as_view(), name="index"),
    path("patients/", views.PatientReportView.as_view(), name="patients"),
    path("appointments/", views.AppointmentReportView.as_view(), name="appointments"),
    path("revenue/", views.RevenueReportView.as_view(), name="revenue"),
    path("pharmacy/", views.PharmacyReportView.as_view(), name="pharmacy"),
    path("laboratory/", views.LaboratoryReportView.as_view(), name="laboratory"),
    path("staff/", views.StaffReportView.as_view(), name="staff"),
]
