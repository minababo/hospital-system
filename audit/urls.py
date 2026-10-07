from django.urls import path

from audit import views

app_name = "audit"

urlpatterns = [
    path("", views.AuditLogListView.as_view(), name="log_list"),
    path("<int:pk>/", views.AuditLogDetailView.as_view(), name="log_detail"),
]
