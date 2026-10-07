from django.urls import path

from billing import views

app_name = "billing"

urlpatterns = [
    path("", views.InvoiceListView.as_view(), name="invoice_list"),
    path("patients/", views.PatientSearchView.as_view(), name="patient_search"),
    path("patients/<int:pk>/", views.PatientBillingView.as_view(), name="patient_billing"),
    path(
        "patients/<int:pk>/charges/add/",
        views.PatientChargeAddView.as_view(),
        name="patient_charge_add",
    ),
    path(
        "patients/<int:pk>/invoices/create/",
        views.InvoiceCreateView.as_view(),
        name="invoice_create",
    ),
    path("charges/<int:pk>/edit/", views.ChargeEditView.as_view(), name="charge_edit"),
    path("charges/<int:pk>/void/", views.ChargeVoidView.as_view(), name="charge_void"),
    path("invoices/<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice_detail"),
    path("invoices/<int:pk>/print/", views.InvoicePrintView.as_view(), name="invoice_print"),
    path(
        "invoices/<int:pk>/charges/add/",
        views.InvoiceChargeAddView.as_view(),
        name="invoice_charge_add",
    ),
    path(
        "invoices/<int:pk>/charges/<int:charge_pk>/remove/",
        views.InvoiceChargeRemoveView.as_view(),
        name="invoice_charge_remove",
    ),
    path("invoices/<int:pk>/discount/", views.DiscountView.as_view(), name="invoice_discount"),
    path("invoices/<int:pk>/issue/", views.IssueView.as_view(), name="invoice_issue"),
    path("invoices/<int:pk>/payments/add/", views.PaymentAddView.as_view(), name="payment_add"),
    path("invoices/<int:pk>/void/", views.InvoiceVoidView.as_view(), name="invoice_void"),
    path("payments/<int:pk>/receipt/", views.ReceiptView.as_view(), name="receipt"),
    path("payments/<int:pk>/void/", views.PaymentVoidView.as_view(), name="payment_void"),
]
