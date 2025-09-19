# finance/api/views.py
from decimal import Decimal
from django.shortcuts import get_object_or_404
from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from inventory.models import Party
from finance.hordak_posting import post_ar_opening
from finance.models_receipts import CustomerReceipt
from sale.models import SaleInvoice
from .serializers import (
    OpeningBalanceSerializer,
    CustomerReceiptWriteSerializer, CustomerReceiptReadSerializer,
    ReceiptAllocationWriteSerializer,CustomerReceiptCreateSerializer
)
from rest_framework import generics, permissions

class CustomerReceiptViewSet(viewsets.ModelViewSet):
    queryset = CustomerReceipt.objects.all().select_related("customer", "warehouse").prefetch_related("allocations__invoice")
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return CustomerReceiptWriteSerializer
        return CustomerReceiptReadSerializer

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def allocate(self, request, pk=None):
        rcpt = self.get_object()
        ser = ReceiptAllocationWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        inv = get_object_or_404(SaleInvoice.objects.select_related("customer"), pk=ser.validated_data["invoice"])
        rcpt.allocate(inv, Decimal(ser.validated_data["amount"]))
        return Response(CustomerReceiptReadSerializer(rcpt).data, status=200)




class CustomerReceiptCreateView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CustomerReceiptCreateSerializer


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def opening_balance_view(request):
    ser = OpeningBalanceSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    customer = get_object_or_404(Party, pk=ser.validated_data["customer"], party_type="customer")
    txn = post_ar_opening(
        date=ser.validated_data["date"],
        description=ser.validated_data.get("description") or f"Opening for {customer}",
        customer_account=customer.chart_of_account,
        amount=ser.validated_data["amount"],
    )
    # Operational (running) balance up
    customer.current_balance = (customer.current_balance or 0) + Decimal(ser.validated_data["amount"])
    customer.save(update_fields=["current_balance"])
    return Response({"status": "ok", "txn_id": txn.pk})
