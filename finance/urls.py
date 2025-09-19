from rest_framework.routers import DefaultRouter
from django.urls import path, include
from .views import FinancialYearViewSet, PaymentScheduleViewSet
from finance.api.views import CustomerReceiptViewSet, opening_balance_view
from finance.admin_opening_balance import opening_balance_view_admin
from .api.views import CustomerReceiptCreateView


router = DefaultRouter()
router.register(r'schedules', PaymentScheduleViewSet)
router.register(r'financial-years', FinancialYearViewSet)
router.register(r"receipts", CustomerReceiptViewSet, basename="customer-receipt")

urlpatterns = [
    path("", include(router.urls)),
    path("receipts/", CustomerReceiptCreateView.as_view()),   
    path("ar/opening-balance/", opening_balance_view, name="ar-opening-balance"),
    path("ar/opening-balance-admin/", opening_balance_view_admin, name="ar-opening-balance-admin"),
]
