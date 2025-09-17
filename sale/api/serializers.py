# sale/api/serializers.py
from decimal import Decimal
from rest_framework import serializers
from sale.models import SaleInvoice, SaleInvoiceItem
from inventory.models import Product, Batch

class SaleInvoiceItemWriteSerializer(serializers.ModelSerializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    batch   = serializers.PrimaryKeyRelatedField(queryset=Batch.objects.all(), allow_null=True, required=False)

    class Meta:
        model  = SaleInvoiceItem
        fields = ("id", "product", "batch", "quantity", "rate", "amount", "bonus", "packing", "discount1", "discount2")

class SaleInvoiceItemReadSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    batch_number = serializers.CharField(source="batch.batch_number", read_only=True)
    expiry_date  = serializers.DateField(source="batch.expiry_date", read_only=True)
    remaining_to_deliver = serializers.IntegerField(read_only=True)

    class Meta:
        model  = SaleInvoiceItem
        fields = (
            "id", "product", "product_name", "batch", "batch_number", "expiry_date",
            "quantity", "delivered_qty", "remaining_to_deliver", "rate", "amount",
            "bonus", "packing", "discount1", "discount2"
        )

class SaleInvoiceWriteSerializer(serializers.ModelSerializer):
    items = SaleInvoiceItemWriteSerializer(many=True)

    class Meta:
        model  = SaleInvoice
        fields = (
            "id", "invoice_no", "date", "customer", "warehouse",
            "total_amount", "discount", "tax", "grand_total",
            "paid_amount", "status", "payment_status", "items",
        )
        read_only_fields = ("status", "payment_status", "grand_total")

    def validate(self, data):
        # compute totals if not provided correctly
        total = Decimal("0")
        for it in data.get("items", []):
            qty  = Decimal(it.get("quantity") or 0)
            rate = Decimal(it.get("rate") or 0)
            amt  = it.get("amount")
            if amt is None:
                amt = qty * rate
                it["amount"] = amt
            total += Decimal(amt)
        data["total_amount"] = total
        discount = Decimal(data.get("discount") or 0)
        tax      = Decimal(data.get("tax") or 0)
        data["grand_total"] = (total - discount + tax)
        return data

    def create(self, validated):
        items = validated.pop("items", [])
        inv = SaleInvoice.objects.create(**validated)   # keep provided invoice_no as-is
        SaleInvoiceItem.objects.bulk_create([
            SaleInvoiceItem(invoice=inv, **row) for row in items
        ])
        # Recalc one more time from actual DB
        inv.total_amount = sum((it.amount for it in inv.items.all()), Decimal("0"))
        inv.grand_total  = (inv.total_amount - Decimal(inv.discount or 0) + Decimal(inv.tax or 0))
        inv.save(update_fields=["total_amount", "grand_total"])
        return inv

class SaleInvoiceReadSerializer(serializers.ModelSerializer):
    items = SaleInvoiceItemReadSerializer(many=True, read_only=True)
    outstanding = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model  = SaleInvoice
        fields = (
            "id", "invoice_no", "date", "customer", "warehouse",
            "total_amount", "discount", "tax", "grand_total",
            "paid_amount", "outstanding", "status", "payment_status", "items",
        )


# --- Custom action payloads ---

class ConfirmSerializer(serializers.Serializer):
    # no payload needed now; reserved for future hooks
    pass

class DeliverLineSerializer(serializers.Serializer):
    invoice_item_id = serializers.IntegerField()
    quantity        = serializers.IntegerField(min_value=1)

class DeliverPayloadSerializer(serializers.Serializer):
    date  = serializers.DateField()
    note  = serializers.CharField(allow_blank=True, required=False)
    lines = DeliverLineSerializer(many=True)

class PaymentSerializer(serializers.Serializer):
    date        = serializers.DateField()
    amount      = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    description = serializers.CharField(allow_blank=True, required=False)
