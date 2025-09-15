from rest_framework.routers import DefaultRouter
from .views import PriceListViewSet, PriceListItemViewSet

router = DefaultRouter()
router.register(r'pricelists', PriceListViewSet)
router.register(r'pricelist-items', PriceListItemViewSet)

urlpatterns = router.urls
