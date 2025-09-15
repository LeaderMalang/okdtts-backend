from django.contrib import admin
from .models import Lead, Interaction



@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "party", "assigned_to", "created_at")
    search_fields = ("title",)


@admin.register(Interaction)
class InteractionAdmin(admin.ModelAdmin):
    list_display = (
        "lead",
        "interaction_type",
        "employee",
        "created_at",
        "follow_up_date",
    )
    list_filter = ("interaction_type",)

