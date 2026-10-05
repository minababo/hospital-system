from django.apps import AppConfig


class LaboratoryConfig(AppConfig):
    name = "laboratory"

    def ready(self):
        # Add lab requests and released results to the patient medical history.
        # The patients app never imports laboratory.
        from laboratory.selectors import lab_history_events
        from patients.selectors import PROVIDERS

        if lab_history_events not in PROVIDERS:
            PROVIDERS.append(lab_history_events)
