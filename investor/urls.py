from rest_framework.routers import DefaultRouter
from .views import InvestorTransactionViewSet, InvestorViewSet

router = DefaultRouter()
router.register(r'transactions', InvestorTransactionViewSet)
router.register(r'investors', InvestorViewSet, basename='investor')

urlpatterns = router.urls
