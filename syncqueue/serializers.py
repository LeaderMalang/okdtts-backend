from rest_framework import serializers
from .models import QueuedOperation


class QueuedOperationSerializer(serializers.ModelSerializer):
    class Meta:
        model = QueuedOperation
        fields = '__all__'
