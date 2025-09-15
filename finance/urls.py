from rest_framework.routers import DefaultRouter
from .views import FinancialYearViewSet, PaymentScheduleViewSet

router = DefaultRouter()
router.register(r'schedules', PaymentScheduleViewSet)
router.register(r'financial-years', FinancialYearViewSet)

urlpatterns = router.urls
