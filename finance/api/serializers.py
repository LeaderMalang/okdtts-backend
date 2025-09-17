# finance/api/serializers.py
from rest_framework import serializers
from decimal import Decimal
from finance.models_receipts import CustomerReceipt, CustomerReceiptAllocation
from sale.models import SaleInvoice

class OpeningBalanceSerializer(serializers.Serializer):
    date = serializers.DateField()
    customer = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True)

class ReceiptAllocationWriteSerializer(serializers.Serializer):
    invoice = serializers.IntegerField()
    amount  = serializers.DecimalField(max_digits=12, decimal_places=2)

class CustomerReceiptWriteSerializer(serializers.ModelSerializer):
    allocations = ReceiptAllocationWriteSerializer(many=True, required=False)

    class Meta:
        model = CustomerReceipt
        fields = ("id", "number", "date", "customer", "warehouse", "amount", "description", "allocations")
        read_only_fields = ("number",)

    def create(self, validated):
        allocs = validated.pop("allocations", [])
        rcpt = CustomerReceipt.objects.create(**validated)
        rcpt.post()  # post once

        # Optional immediate allocations
        for row in allocs:
            inv = SaleInvoice.objects.select_related("customer").get(pk=row["invoice"])
            rcpt.allocate(inv, Decimal(row["amount"]))
        return rcpt

class CustomerReceiptReadSerializer(serializers.ModelSerializer):
    allocations = serializers.SerializerMethodField()

    class Meta:
        model = CustomerReceipt
        fields = ("id", "number", "date", "customer", "warehouse", "amount", "unallocated_amount",
                  "description", "hordak_txn", "allocations")

    def get_allocations(self, obj):
        return [
            {"invoice": a.invoice_id, "invoice_no": a.invoice.invoice_no, "amount": str(a.amount)}
            for a in obj.allocations.select_related("invoice")
        ]
