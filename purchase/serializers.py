from rest_framework import serializers
from .models import (
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseReturn,
    PurchaseReturnItem,
    InvestorTransaction,
)
from finance.serializers import PaymentScheduleSerializer


class PurchaseInvoiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseInvoiceItem
        fields = "__all__"


class PurchaseInvoiceSerializer(serializers.ModelSerializer):
    items = PurchaseInvoiceItemSerializer(many=True, read_only=True)
    payment_schedules = PaymentScheduleSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseInvoice
        fields = "__all__"


class PurchaseReturnItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseReturnItem
        fields = "__all__"


class PurchaseReturnSerializer(serializers.ModelSerializer):
    items = PurchaseReturnItemSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseReturn
        fields = "__all__"


class InvestorTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvestorTransaction
        fields = "__all__"
