from django.db import models, transaction
from django.db.models import F, Sum
from django.utils import timezone
from inventory.models import Party, Product, Batch
from sale.models import SaleInvoice, SaleInvoiceItem
from hr.models import Employee
from decimal import Decimal
from setting.models import Warehouse

class Order(models.Model):
    STATUS_CHOICES = (
        ("Pending",   "Pending"),
        ("Confirmed", "Confirmed"),
        ("Cancelled", "Cancelled"),
        ("Completed", "Completed"),
    )

    order_no     = models.CharField(max_length=50, unique=True)
    date         = models.DateField()
    customer     = models.ForeignKey(
        Party, on_delete=models.CASCADE,
        limit_choices_to={"party_type": "customer"}
    )
    salesman     = models.ForeignKey(
        Employee, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="salesman_orders",
        limit_choices_to={"role": "SALES"}
    )
    status       = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Pending")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    sale_invoice = models.OneToOneField(
        "sale.SaleInvoice", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="from_order"
    )

    address      = models.TextField(blank=True, null=True)

    def __str__(self) -> str:
        return self.order_no

    # ---- totals ----
    def _recompute_total_from_items(self) -> Decimal:
        agg = self.items.aggregate(
            total=Sum(F("quantity") * F("price"))
        )
        total = Decimal(agg["total"] or 0).quantize(Decimal("0.01"))
        if self.total_amount != total:
            self.total_amount = total
            super().save(update_fields=["total_amount"])
        return total

    # ---- business workflow ----
    @transaction.atomic
    def confirm(self, *, warehouse: Warehouse) -> SaleInvoice:
        """
        Create & link a SaleInvoice that mirrors this Order.
        - Invoice number = order_no
        - Items cloned from OrderItem
        - No payment method/terms here; payments handled on the invoice later.
        - Do NOT deliver here (supports partial delivery later from the invoice).
        Idempotent: if already linked, just returns it.
        """
        if self.sale_invoice_id:
            return self.sale_invoice

        # Ensure order total reflects items
        self._recompute_total_from_items()

        # 1) Create the SaleInvoice (start DRAFT, then confirm)
        inv = SaleInvoice.objects.create(
            invoice_no=self.order_no,           # same number
            date=self.date,
            customer=self.customer,
            warehouse=warehouse,
            total_amount=self.total_amount,     # SaleInvoice will compute grand_total/tax/discount internally
            paid_amount=self.paid_amount or 0,  # upfront paid (if any)
        )

        # 2) Create invoice lines from order items
        #    Pick a Batch for each product (FEFO if available, else any with stock, else None).
        for item in self.items.select_related("product").all():
            batch = (
                Batch.objects
                .filter(product=item.product, warehouse=warehouse, quantity__gt=0)
                .order_by("expiry_date")  # FEFO
                .first()
            )
            amount = (Decimal(item.quantity) * Decimal(item.price)).quantize(Decimal("0.01"))

            SaleInvoiceItem.objects.create(
                invoice=inv,
                product=item.product,
                batch=batch,                 # can be None; your deliver step can enforce batch then
                quantity=item.quantity,
                bonus=0,
                packing=0,
                rate=item.price,
                discount1=0,
                discount2=0,
                amount=amount,        # kept for schema compatibility
            )

        # 3) Confirm the invoice (posts ledger). If your SaleInvoice has confirm(), use it.
        if hasattr(inv, "confirm") and callable(inv.confirm):
            inv.confirm()
        else:
            # Fallback: set status if your model doesn't have confirm()
            if hasattr(inv, "status"):
                inv.status = "CONFIRMED"
                inv.save(update_fields=["status"])

        # 4) Link & update order status
        self.sale_invoice = inv
        self.status = "Confirmed"
        super().save(update_fields=["sale_invoice", "status"])

        return inv

    def sync_from_invoice(self) -> None:
        """
        (Optional) Keep order status in sync with its invoice:
        - Completed when invoice is PAID (and, if you track it, fully delivered).
        """
        if not self.sale_invoice_id:
            return
        inv = self.sale_invoice
        inv_paid = getattr(inv, "payment_status", None) == "PAID"
        inv_delivered = getattr(inv, "is_fully_delivered", None)
        # If you don’t track delivery completion flag, rely on PAID only.
        done = inv_paid and (inv_delivered is True or inv_delivered is None)
        new_status = "Completed" if done else "Confirmed"
        if new_status != self.status:
            self.status = new_status
            super().save(update_fields=["status"])

    # Convenience: call after recording a payment/delivery on the invoice
    def maybe_complete(self):
        self.sync_from_invoice()


class OrderItem(models.Model):
    order   = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    price    = models.DecimalField(max_digits=10, decimal_places=2)
    bid_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount    = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self) -> str:
        return f"{self.product} x {self.quantity} @ {self.price}"