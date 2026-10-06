from django.urls import path

from admissions import views

app_name = "admissions"

urlpatterns = [
    path("", views.AdmissionListView.as_view(), name="admission_list"),
    path("beds/", views.BedBoardView.as_view(), name="bed_board"),
    path("new/", views.AdmitView.as_view(), name="admit"),
    path("<int:pk>/", views.AdmissionDetailView.as_view(), name="admission_detail"),
    path("<int:pk>/transfer/", views.TransferView.as_view(), name="transfer"),
    path("<int:pk>/notes/add/", views.NoteAddView.as_view(), name="note_add"),
    path("<int:pk>/discharge/", views.DischargeView.as_view(), name="discharge"),
    path(
        "<int:pk>/discharge-summary/",
        views.DischargeSummaryView.as_view(),
        name="discharge_summary",
    ),
    path("wards/", views.WardListView.as_view(), name="ward_list"),
    path("wards/new/", views.WardFormView.as_view(), name="ward_create"),
    path("wards/<int:pk>/edit/", views.WardFormView.as_view(), name="ward_update"),
    path(
        "wards/<int:pk>/toggle-active/", views.WardToggleView.as_view(), name="ward_toggle_active"
    ),
    path("wards/<int:pk>/beds/", views.BedListView.as_view(), name="bed_list"),
    path("wards/<int:pk>/beds/add/", views.BedAddView.as_view(), name="bed_add"),
    path("beds/<int:pk>/edit/", views.BedUpdateView.as_view(), name="bed_update"),
    path("beds/<int:pk>/toggle-active/", views.BedToggleView.as_view(), name="bed_toggle_active"),
]
