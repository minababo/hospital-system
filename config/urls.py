from django.contrib import admin
from django.urls import include, path

from config import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", views.healthz, name="healthz"),
    path("accounts/", include("accounts.urls")),
    path("", include("reports.urls")),
    path("", views.home, name="home"),
]
