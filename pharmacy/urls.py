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
    path("inventory/", views.InventoryListView.as_view(), name="inventory_list"),
    path(
        "inventory/<int:medicine_pk>/",
        views.InventoryDetailView.as_view(),
        name="inventory_detail",
    ),
    path(
        "inventory/<int:medicine_pk>/receive/",
        views.ReceiveStockView.as_view(),
        name="receive_stock",
    ),
    path("batches/<int:pk>/adjust/", views.AdjustStockView.as_view(), name="adjust_stock"),
    path("batches/<int:pk>/write-off/", views.WriteOffView.as_view(), name="write_off"),
    path("alerts/", views.AlertsView.as_view(), name="alerts"),
    path("prescriptions/", views.DispensingQueueView.as_view(), name="dispensing_queue"),
    path("prescriptions/<int:pk>/", views.DispensePageView.as_view(), name="dispense_page"),
    path("prescriptions/<int:pk>/dispense/", views.DispenseView.as_view(), name="dispense"),
]
