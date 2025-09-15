from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    CityViewSet,
    AreaViewSet,
    CompanyViewSet,
    GroupViewSet,
    DistributorViewSet,
    BranchViewSet,
    WarehouseViewSet,
    management_all,
)


router = DefaultRouter()
router.register(r'cities', CityViewSet)
router.register(r'areas', AreaViewSet)
router.register(r'companies', CompanyViewSet)
router.register(r'product-groups', GroupViewSet)
router.register(r'distributors', DistributorViewSet)
router.register(r'branches', BranchViewSet)
router.register(r'warehouses', WarehouseViewSet)

urlpatterns = [
    path('all/', management_all),
    path('', include(router.urls)),
]
