from django.db import models
from django.db.models.functions import Lower


class Medicine(models.Model):
    """A medicine in the hospital's catalog. Stock and dispensing come later."""

    class Form(models.TextChoices):
        TABLET = "TABLET", "Tablet"
        CAPSULE = "CAPSULE", "Capsule"
        SYRUP = "SYRUP", "Syrup"
        SUSPENSION = "SUSPENSION", "Suspension"
        INJECTION = "INJECTION", "Injection"
        INFUSION = "INFUSION", "Infusion"
        CREAM = "CREAM", "Cream"
        OINTMENT = "OINTMENT", "Ointment"
        DROPS = "DROPS", "Drops"
        INHALER = "INHALER", "Inhaler"
        OTHER = "OTHER", "Other"

    name = models.CharField(max_length=150)
    generic_name = models.CharField(max_length=150, blank=True)
    form = models.CharField(max_length=20, choices=Form.choices)
    strength = models.CharField(max_length=50, help_text="e.g. 500 mg or 5 mg/5 ml")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "strength"]
        constraints = [
            # Each name + strength + form combination exists once, ignoring case.
            models.UniqueConstraint(
                Lower("name"),
                Lower("strength"),
                "form",
                name="pharmacy_medicine_unique",
                violation_error_message="This medicine (name, strength and form) already exists.",
            ),
        ]

    def __str__(self):
        return f"{self.name} {self.strength} ({self.get_form_display()})"

    def clean(self):
        self.name = (self.name or "").strip()
        self.generic_name = (self.generic_name or "").strip()
        self.strength = (self.strength or "").strip()
