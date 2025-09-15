from rest_framework import serializers
from .models import Lead, Interaction


class LeadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead

        fields = [
            "id",
            "title",
            "description",
            "party",
            "assigned_to",
            "status",
            "created_at",
            "updated_at",
        ]



class InteractionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Interaction

        fields = [
            "id",
            "lead",
            "employee",
            "interaction_type",
            "notes",
            "follow_up_date",
            "created_at",
        ]

