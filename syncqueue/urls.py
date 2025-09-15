from rest_framework.routers import DefaultRouter
from .views import QueuedOperationViewSet

router = DefaultRouter()
router.register(r'queued-operations', QueuedOperationViewSet)

urlpatterns = router.urls
