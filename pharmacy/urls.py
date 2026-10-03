from django.urls import path

from pharmacy import views

app_name = "pharmacy"

urlpatterns = [
    path("medicines/", views.MedicineListView.as_view(), name="medicine_list"),
    path("medicines/new/", views.MedicineCreateView.as_view(), name="medicine_create"),
    path("medicines/<int:pk>/edit/", views.MedicineUpdateView.as_view(), name="medicine_update"),
    path(
        "medicines/<int:pk>/toggle-active/",
        views.MedicineToggleActiveView.as_view(),
        name="medicine_toggle_active",
    ),
]
