from django.apps import AppConfig


class AppointmentsConfig(AppConfig):
    name = "appointments"

    def ready(self):
        # Add appointments to the patient medical history. Registering from here keeps
        # the patients app free of any import of appointments (no circular imports).
        from appointments.selectors import appointment_history_events
        from patients.selectors import PROVIDERS

        if appointment_history_events not in PROVIDERS:
            PROVIDERS.append(appointment_history_events)
