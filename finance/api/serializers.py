# finance/api/serializers.py
from rest_framework import serializers
from decimal import Decimal
from finance.models_receipts import CustomerReceipt, CustomerReceiptAllocation
from sale.models import SaleInvoice
from inventory.models import Party
from setting.models import Warehouse
from django.db import transaction

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



class CustomerReceiptCreateSerializer(serializers.Serializer):
    date = serializers.DateField()
    customer_id = serializers.IntegerField()
    warehouse_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True)

    @transaction.atomic
    def create(self, validated):
        customer = Party.objects.get(pk=validated["customer_id"])
        wh = Warehouse.objects.get(pk=validated["warehouse_id"])
        receipt = CustomerReceipt.objects.create(
            date=validated["date"],
            customer=customer,
            warehouse=wh,
            amount=validated["amount"],
            description=validated.get("description", ""),
        )
        # immediately post to ledger and set full unallocated
        receipt.post()
        return {"receipt_id": receipt.id, "number": receipt.number, "unallocated": str(receipt.unallocated_amount)}
