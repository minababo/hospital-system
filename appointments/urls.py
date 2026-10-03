from django.urls import path

from appointments import views

app_name = "appointments"

urlpatterns = [
    path("", views.AppointmentListView.as_view(), name="appointment_list"),
    path("calendar/", views.CalendarView.as_view(), name="calendar"),
    path("book/", views.BookView.as_view(), name="book"),
    path("<int:pk>/", views.AppointmentDetailView.as_view(), name="appointment_detail"),
    path("<int:pk>/reschedule/", views.RescheduleView.as_view(), name="reschedule"),
    path("<int:pk>/cancel/", views.CancelView.as_view(), name="cancel"),
    path("<int:pk>/check-in/", views.CheckInView.as_view(), name="check_in"),
    path("<int:pk>/complete/", views.CompleteView.as_view(), name="complete"),
    path("<int:pk>/no-show/", views.NoShowView.as_view(), name="no_show"),
]
