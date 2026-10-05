from django.urls import path

from laboratory import views

app_name = "laboratory"

urlpatterns = [
    path("", views.WorklistView.as_view(), name="worklist"),
    path("tests/", views.LabTestListView.as_view(), name="test_list"),
    path("tests/new/", views.LabTestCreateView.as_view(), name="test_create"),
    path("tests/<int:pk>/edit/", views.LabTestUpdateView.as_view(), name="test_update"),
    path(
        "tests/<int:pk>/toggle-active/",
        views.LabTestToggleActiveView.as_view(),
        name="test_toggle_active",
    ),
    path("tests/<int:pk>/parameters/add/", views.ParameterAddView.as_view(), name="parameter_add"),
    path("parameters/<int:pk>/edit/", views.ParameterUpdateView.as_view(), name="parameter_update"),
    path(
        "parameters/<int:pk>/remove/", views.ParameterRemoveView.as_view(), name="parameter_remove"
    ),
    path("walk-in/", views.WalkInView.as_view(), name="walk_in"),
    path(
        "records/<int:record_pk>/order/",
        views.OrderForRecordView.as_view(),
        name="order_for_record",
    ),
    path("orders/<int:pk>/", views.OrderDetailView.as_view(), name="order_detail"),
    path(
        "orders/<int:pk>/items/<int:item_pk>/results/",
        views.ResultEntryView.as_view(),
        name="result_entry",
    ),
    path("orders/<int:pk>/collect/", views.CollectView.as_view(), name="collect"),
    path("orders/<int:pk>/release/", views.ReleaseView.as_view(), name="release"),
    path("orders/<int:pk>/cancel/", views.CancelView.as_view(), name="cancel"),
    path("orders/<int:pk>/reports/upload/", views.ReportUploadView.as_view(), name="report_upload"),
    path("orders/<int:pk>/report/", views.ReportPrintView.as_view(), name="report_print"),
]
