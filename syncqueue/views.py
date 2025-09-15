from rest_framework import viewsets
from .models import QueuedOperation
from .serializers import QueuedOperationSerializer


class QueuedOperationViewSet(viewsets.ModelViewSet):
    queryset = QueuedOperation.objects.all()
    serializer_class = QueuedOperationSerializer
