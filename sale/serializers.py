from rest_framework import serializers

from .models import (
    SaleInvoice,
    SaleInvoiceItem,
    SaleReturn,
    SaleReturnItem,
    RecoveryLog,
)
from finance.serializers import PaymentScheduleSerializer


class SaleInvoiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleInvoiceItem
        fields = [
            "id",
            "product",
            "batch",
            "quantity",
            "bonus",
            "packing",
            "rate",
            "discount1",
            "discount2",
            "amount",
            "net_amount",
            "bid_amount",
        ]


class RecoveryLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecoveryLog
        fields = ["id", "invoice", "employee", "date", "notes"]


class SaleInvoiceSerializer(serializers.ModelSerializer):
    items = SaleInvoiceItemSerializer(many=True)
    recovery_logs = RecoveryLogSerializer(many=True, read_only=True)
    payment_schedules = PaymentScheduleSerializer(many=True, read_only=True)

    class Meta:
        model = SaleInvoice
        fields = [
            "id",
            "invoice_no",
            "company_invoice_number",
            "date",
            "customer",
            "warehouse",
            "booking_man_id",
            "delivery_man_id",
            "city_id",
            "area_id",
            "discount",
            "tax",
            "paid_amount",
            "grand_total",
            "net_amount",
            "payment_method",
            "payment_term",
            "status",
            "qr_code",
            "items",
            "recovery_logs",
            "payment_schedules",
            "total_amount",
        ]
        read_only_fields = ("grand_total", "net_amount")

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        invoice = SaleInvoice.objects.create(**validated_data)
        for item in items_data:
            SaleInvoiceItem.objects.create(invoice=invoice, **item)
        return invoice

    def validate(self, data):
        total = data.get("sub_total") or 0
        discount = data.get("discount") or 0
        tax = data.get("tax") or 0
        grand_total = total - discount + tax
        paid = data.get("paid_amount") or 0
        if paid > grand_total:
            raise serializers.ValidationError(
                {"paid_amount": "Paid amount cannot exceed grand total."}
            )
        return data


class SaleReturnItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleReturnItem
        fields = [
            "id",
            "return_invoice",
            "product",
            "batch_number",
            "expiry_date",
            "quantity",
            "rate",
            "discount1",
            "discount2",
            "amount",
            "net_amount",
        ]


class SaleReturnSerializer(serializers.ModelSerializer):
    items = SaleReturnItemSerializer(many=True)

    class Meta:
        model = SaleReturn
        fields = [
            "id",
            "return_no",
            "date",
            "invoice",
            "customer",
            "warehouse",
            "total_amount",
            "items",
        ]

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        sr = SaleReturn.objects.create(**validated_data)
        for item in items_data:
            SaleReturnItem.objects.create(return_invoice=sr, **item)
        sr.save()
        return sr

