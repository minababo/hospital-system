from django.urls import path

from records import views

app_name = "records"

urlpatterns = [
    path("appointments/<int:appt_pk>/start/", views.StartConsultationView.as_view(), name="start"),
    path("appointments/<int:appt_pk>/vitals/", views.VitalsView.as_view(), name="vitals"),
    path(
        "patients/<int:patient_pk>/history/",
        views.TreatmentHistoryView.as_view(),
        name="treatment_history",
    ),
    path("<int:pk>/", views.RecordDetailView.as_view(), name="record_detail"),
    path("<int:pk>/update/", views.RecordUpdateView.as_view(), name="record_update"),
    path("<int:pk>/diagnoses/add/", views.DiagnosisAddView.as_view(), name="diagnosis_add"),
    path(
        "<int:pk>/diagnoses/<int:d_pk>/remove/",
        views.DiagnosisRemoveView.as_view(),
        name="diagnosis_remove",
    ),
    path("<int:pk>/items/add/", views.ItemAddView.as_view(), name="item_add"),
    path("<int:pk>/items/<int:i_pk>/remove/", views.ItemRemoveView.as_view(), name="item_remove"),
    path("<int:pk>/reports/upload/", views.ReportUploadView.as_view(), name="report_upload"),
    path("<int:pk>/finalize/", views.FinalizeView.as_view(), name="finalize"),
    path("<int:pk>/addenda/add/", views.AddendumAddView.as_view(), name="addendum_add"),
    path(
        "<int:pk>/prescription/cancel/",
        views.PrescriptionCancelView.as_view(),
        name="prescription_cancel",
    ),
    path("<int:pk>/print/", views.PrintRecordView.as_view(), name="print_record"),
    path(
        "<int:pk>/prescription/print/",
        views.PrintPrescriptionView.as_view(),
        name="print_prescription",
    ),
]
