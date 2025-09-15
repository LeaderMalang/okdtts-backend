from django.db import models



class Lead(models.Model):
    """A potential customer or opportunity."""

    STATUS_CHOICES = [
        ("NEW", "New"),
        ("IN_PROGRESS", "In Progress"),
        ("WON", "Won"),
        ("LOST", "Lost"),
    ]

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    party = models.ForeignKey(
        "inventory.Party",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
    )
    assigned_to = models.ForeignKey(
        "hr.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="NEW")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover - simple string repr
        return self.title


class Interaction(models.Model):
    """Record of communication with a lead."""

    INTERACTION_TYPES = [
        ("CALL", "Call"),
        ("EMAIL", "Email"),
        ("MEETING", "Meeting"),
        ("OTHER", "Other"),
    ]

    lead = models.ForeignKey(
        Lead, on_delete=models.CASCADE, related_name="interactions"
    )
    employee = models.ForeignKey(
        "hr.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interactions",
    )
    interaction_type = models.CharField(max_length=20, choices=INTERACTION_TYPES)
    notes = models.TextField(blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover - simple string repr
        return f"{self.lead} - {self.get_interaction_type_display()}"

