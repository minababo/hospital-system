from django.apps import AppConfig


class RecordsConfig(AppConfig):
    name = "records"

    def ready(self):
        # Add consultations and prescriptions to the patient medical history, the same
        # way appointments does: the patients app never imports records.
        from patients.selectors import PROVIDERS
        from records.selectors import record_history_events

        if record_history_events not in PROVIDERS:
            PROVIDERS.append(record_history_events)
