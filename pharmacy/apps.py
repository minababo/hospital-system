from django.apps import AppConfig


class PharmacyConfig(AppConfig):
    name = "pharmacy"

    def ready(self):
        # Add dispensed medicines to the patient medical history. The patients app
        # never imports pharmacy.
        from patients.selectors import PROVIDERS
        from pharmacy.dispensing_selectors import dispense_history_events

        if dispense_history_events not in PROVIDERS:
            PROVIDERS.append(dispense_history_events)
