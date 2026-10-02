from django.urls import path

from reports import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
]
