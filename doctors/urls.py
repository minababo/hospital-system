from django.urls import path

from doctors import views

app_name = "doctors"

urlpatterns = [
    path("", views.DoctorListView.as_view(), name="doctor_list"),
    path("new/", views.DoctorCreateView.as_view(), name="doctor_create"),
    path("me/", views.MyProfileView.as_view(), name="me"),
    path(
        "complete-profile/<int:user_pk>/",
        views.DoctorCompleteProfileView.as_view(),
        name="doctor_complete_profile",
    ),
    path("<int:pk>/", views.DoctorDetailView.as_view(), name="doctor_detail"),
    path("<int:pk>/edit/", views.DoctorUpdateView.as_view(), name="doctor_update"),
    path("<int:pk>/schedule/new/", views.ScheduleCreateView.as_view(), name="schedule_create"),
    path("schedules/<int:pk>/edit/", views.ScheduleUpdateView.as_view(), name="schedule_update"),
    path("schedules/<int:pk>/delete/", views.ScheduleDeleteView.as_view(), name="schedule_delete"),
    path("departments/", views.DepartmentListView.as_view(), name="department_list"),
    path("departments/new/", views.DepartmentCreateView.as_view(), name="department_create"),
    path(
        "departments/<int:pk>/edit/",
        views.DepartmentUpdateView.as_view(),
        name="department_update",
    ),
    path(
        "departments/<int:pk>/toggle-active/",
        views.DepartmentToggleActiveView.as_view(),
        name="department_toggle_active",
    ),
]
