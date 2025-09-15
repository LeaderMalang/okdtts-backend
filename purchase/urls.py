from rest_framework.routers import DefaultRouter
from .views import (
    PurchaseInvoiceViewSet,
    PurchaseReturnViewSet,
    InvestorTransactionViewSet,
)

router = DefaultRouter()
router.register(r'invoices', PurchaseInvoiceViewSet)
router.register(r'returns', PurchaseReturnViewSet)
router.register(r'investor-transactions', InvestorTransactionViewSet)

urlpatterns = router.urls
