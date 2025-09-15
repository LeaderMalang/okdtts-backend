from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    sale_invoice_list,
    sale_invoice_create,
    sale_invoice_edit,
    sale_invoice_detail,
    SaleInvoiceViewSet,
    SaleReturnViewSet,
    SaleReturnItemViewSet,
    RecoveryLogViewSet,
    add_recovery_payment,
    add_recovery_note,
)

router = DefaultRouter()
router.register(r'invoices', SaleInvoiceViewSet)
router.register(r'returns', SaleReturnViewSet)
router.register(r'return-items', SaleReturnItemViewSet)
router.register(r'recovery-logs', RecoveryLogViewSet)

urlpatterns = router.urls + [
    path('', sale_invoice_list, name='sale_list'),
    path('create/', sale_invoice_create, name='sale_create'),
    path('<int:pk>/edit/', sale_invoice_edit, name='sale_edit'),
    path('<int:pk>/', sale_invoice_detail, name='sale_detail'),
    path('financials/recovery/<int:order_id>/payment', add_recovery_payment, name='recovery_payment'),
    path('financials/recovery/<int:order_id>/note', add_recovery_note, name='recovery_note'),
]
