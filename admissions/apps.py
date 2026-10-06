from django.apps import AppConfig


class AdmissionsConfig(AppConfig):
    name = "admissions"

    def ready(self):
        # Add admissions, transfers and discharges to the patient medical history.
        # The patients app never imports admissions.
        from admissions.selectors import admission_history_events
        from patients.selectors import PROVIDERS

        if admission_history_events not in PROVIDERS:
            PROVIDERS.append(admission_history_events)
