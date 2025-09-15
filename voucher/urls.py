from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ChartOfAccountViewSet,
    JournalVoucherCreateAPIView,
    customer_ledger,
)

router = DefaultRouter()
router.register(r'accounts', ChartOfAccountViewSet, basename='account')

urlpatterns = [
    path('ledger/customer/<int:party_id>/', customer_ledger, name='customer_ledger'),
    path('journal/', JournalVoucherCreateAPIView.as_view(), name='journal_voucher_create'),
    path('', include(router.urls)),
]
