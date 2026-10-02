from django.urls import path

from patients import views

app_name = "patients"

urlpatterns = [
    path("", views.PatientListView.as_view(), name="patient_list"),
    path("new/", views.PatientCreateView.as_view(), name="patient_create"),
    path("<int:pk>/", views.PatientDetailView.as_view(), name="patient_detail"),
    path("<int:pk>/edit/", views.PatientUpdateView.as_view(), name="patient_update"),
    path("<int:pk>/history/", views.PatientHistoryView.as_view(), name="patient_history"),
    path("<int:pk>/documents/upload/", views.DocumentUploadView.as_view(), name="document_upload"),
    path("<int:pk>/documents/<int:doc_pk>/", views.DocumentView.as_view(), name="document_view"),
    path(
        "<int:pk>/documents/<int:doc_pk>/delete/",
        views.DocumentDeleteView.as_view(),
        name="document_delete",
    ),
]
