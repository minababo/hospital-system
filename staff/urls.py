from django.urls import path

from staff import views

app_name = "staff"

urlpatterns = [
    path("", views.EmployeeListView.as_view(), name="employee_list"),
    path("new/", views.EmployeeFormView.as_view(), name="employee_create"),
    path("<int:pk>/", views.EmployeeDetailView.as_view(), name="employee_detail"),
    path("<int:pk>/edit/", views.EmployeeFormView.as_view(), name="employee_update"),
    path("<int:pk>/end-employment/", views.EndEmploymentView.as_view(), name="end_employment"),
    path("attendance/", views.AttendanceSheetView.as_view(), name="attendance"),
    path(
        "attendance/monthly/",
        views.MonthlyAttendanceView.as_view(),
        name="attendance_monthly",
    ),
    path("leave/", views.LeaveListView.as_view(), name="leave_list"),
    path("leave/<int:pk>/", views.LeaveDetailView.as_view(), name="leave_detail"),
    path("leave/<int:pk>/approve/", views.ApproveLeaveView.as_view(), name="leave_approve"),
    path("leave/<int:pk>/reject/", views.RejectLeaveView.as_view(), name="leave_reject"),
    path("leave/<int:pk>/cancel/", views.CancelLeaveView.as_view(), name="leave_cancel"),
    path("my-leave/", views.MyLeaveView.as_view(), name="my_leave"),
]
