from django.db import models

from setting.models import Warehouse

from inventory.models import Party, Product, Batch


import logging

from voucher.models import Voucher, ChartOfAccount, VoucherType
from utils.voucher import create_voucher_for_transaction
from utils.stock import stock_return, stock_out,stock_out_new
from finance.models import PaymentTerm, PaymentSchedule
from datetime import timedelta
from setting.constants import TAX_PAYABLE_ACCOUNT_CODE
from decimal import Decimal
from django.db import transaction
from utils.voucher import post_composite_sales_voucher,post_composite_sales_return_voucher

from finance.models_receipts import CustomerReceipt
from hordak.models import Transaction
from finance.hordak_posting import post_sale, post_customer_receipt,post_customer_refund,post_sale_return,post_cancel_sale,post_reverse_customer_receipt_partial
from django.core.exceptions import ValidationError
from django.db.models import Sum
from finance.models_receipts import CustomerReceiptAllocation
logger = logging.getLogger(__name__)
# Reuse your helper for selecting the warehouse cash/bank account
def _cash_or_bank_for(warehouse):
    return warehouse.default_cash_account or warehouse.default_bank_account

# Your Hordak posting helpers — align names/imports to your projectt # <- implement/align if needed
Q2 = Decimal("0.01")
class SaleInvoice(models.Model):
    STATUS = (
        ("DRAFT", "Draft (SO)"),
        ("CONFIRMED", "Confirmed (Booked)"),
        ("DELIVERED", "Delivered (Stock Out)"),
        ("CANCELLED", "Cancelled"),
    )
    PAYMENT_STATUS = (("UNPAID", "Unpaid"), ("PARTIAL", "Partially Paid"), ("PAID", "Paid"))

    # Number frozen after first save
    invoice_no = models.CharField(max_length=50, unique=True, blank=True)


    date = models.DateField()
    customer = models.ForeignKey(
        "inventory.Party",
        on_delete=models.CASCADE,
        limit_choices_to={"party_type": "customer"},
    )
    warehouse = models.ForeignKey("setting.Warehouse", on_delete=models.CASCADE)

    # amounts (no sub_total, no net_amount)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # sum of line.amount
    discount     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax          = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    grand_total  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # optional terms
    # payment_term = models.ForeignKey(PaymentTerm, on_delete=models.SET_NULL, null=True, blank=True)

    # states
    status         = models.CharField(max_length=20, choices=STATUS, default="DRAFT")
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS, default="UNPAID")

    # extras
    qr_code   = models.CharField(max_length=255, blank=True)
    hordak_txn = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL)

    # staff links you already use
    recoveries      = models.ManyToManyField("hr.Employee", through="RecoveryLog", related_name="recovery_invoices", blank=True)
    booking_man_id  = models.ForeignKey("hr.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="bookings")
    delivery_man_id = models.ForeignKey("hr.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="deliveries")

    # ---------- numbering ----------
    @staticmethod
    def _next_sequence(prefix="SINV-"):
        last = (
            SaleInvoice.objects.filter(invoice_no__startswith=prefix)
            .order_by("-id")
            .values_list("invoice_no", flat=True)
            .first()
        )
        if not last:
            return f"{prefix}1"
        try:
            n = int(str(last).split("-")[-1])
        except Exception:
            n = 0
        return f"{prefix}{n + 1}"

    def _ensure_number(self):
        if not self.invoice_no:
            self.invoice_no = self._next_sequence()

    # ---------- derived ----------
    @property
    def outstanding(self) -> Decimal:
        return max(Decimal(self.grand_total or 0) - Decimal(self.paid_amount or 0), Decimal("0"))

    def _recalc_totals_from_items(self):
        total = sum((li.amount or 0) for li in self.items.all()) or Decimal("0")
        self.total_amount = Decimal(total)
        self.grand_total  = self.total_amount - Decimal(self.discount or 0) + Decimal(self.tax or 0)

    def _recalc_payment_status(self):
        paid = Decimal(self.paid_amount or 0)
        if paid <= 0:
            self.payment_status = "UNPAID"
        elif paid >= Decimal(self.grand_total or 0):
            self.payment_status = "PAID"
        else:
            self.payment_status = "PARTIAL"

    # ---------- workflow ----------
    @transaction.atomic
    def confirm(self):
        """
        DRAFT -> CONFIRMED:
          uses post_sale(subtotal, tax) with subtotal=(total_amount - discount),
          NO cash at confirm; payments via receive_payment().
        """
        if self.status != "DRAFT":
            return
        self._ensure_number()
        self._recalc_totals_from_items()

        base_subtotal = Decimal(self.total_amount or 0) - Decimal(self.discount or 0)

        txn = post_sale(
            date=self.date,
            description=f"Sales Invoice {self.invoice_no}",
            subtotal=base_subtotal,
            tax=Decimal(self.tax or 0),
            customer_account=self.customer.chart_of_account,
            warehouse_sales_account=self.warehouse.default_sales_account,
            paid_amount=Decimal("0"),      # NO cash here
            warehouse=self.warehouse,
        )
        self.hordak_txn = txn
        self.status = "CONFIRMED"
        self._recalc_payment_status()
        if self.outstanding > 0:
            self.customer.current_balance = (self.customer.current_balance or 0) + self.outstanding
            self.customer.save(update_fields=["current_balance"])
        self.save(update_fields=[
            "invoice_no","total_amount","grand_total",
            "status","payment_status","hordak_txn"
        ])

    @transaction.atomic
    def receive_payment(self, amount: Decimal):
        """
        DR Cash/Bank   amount
        CR A/R         amount
        """
        amt = Decimal(amount or 0)
        if amt <= 0:
            return
        if self.status not in {"CONFIRMED", "DELIVERED"}:
            raise ValidationError("Receive payment only for CONFIRMED/DELIVERED invoices.")
        if not _cash_or_bank_for(self.warehouse):
            raise ValidationError("No Cash/Bank account configured for this warehouse.")

        rcpt = CustomerReceipt.objects.create(
        date=self.date,
        customer=self.customer,
        warehouse=self.warehouse,
        amount=amount,
        description=f"Receipt for {self.invoice_no}",
        )
        rcpt.post()                # GL: DR cash, CR A/R; Party.current_balance -= amount
        rcpt.allocate(self, amount)
        self.paid_amount = (Decimal(self.paid_amount or 0) + amt)
        self._recalc_payment_status()
        self.save(update_fields=["paid_amount","payment_status"])

    # ---------- stock out (partial-aware) ----------
    @transaction.atomic
    def deliver_partial(self, quantities: dict[int, int]):
        """
        Deliver per-line quantities (partial allowed).
        quantities = { SaleInvoiceItem.id : qty_to_deliver_now }
        """
        if self.status not in {"CONFIRMED", "DELIVERED"}:
            raise ValidationError("Deliver allowed only from CONFIRMED/DELIVERED.")

        items = {li.id: li for li in self.items.select_related("product")}
        any_delivered = False

        for item_id, qty in (quantities or {}).items():
            if item_id not in items:
                continue
            li = items[item_id]
            qty = int(qty or 0)
            if qty <= 0:
                continue

            remain = li.remaining_to_deliver
            if qty > remain:
                raise ValidationError(f"Line {li.id} exceeds remaining {remain}.")

            # stock_out(
            #     product=li.product,
            #     quantity=qty,
            #     reason=f"Sales Delivery {self.invoice_no}",
            # )
            stock_out_new(
                product=li.product,
                quantity=qty,
                reason=f"Sales Delivery {self.invoice_no}",
                warehouse=self.warehouse,
                batch_number=li.batch.batch_number if li.batch else None,

            )
            li.delivered_qty = (li.delivered_qty or 0) + qty
            li.save(update_fields=["delivered_qty"])
            any_delivered = True

        if not any_delivered:
            return

        # If all lines fully delivered -> mark DELIVERED
        if all(li.remaining_to_deliver <= 0 for li in self.items.all()):
            self.status = "DELIVERED"
            self.save(update_fields=["status"])

    @transaction.atomic
    def deliver_all_remaining(self):
        """Deliver everything that remains."""
        qmap = {li.id: li.remaining_to_deliver for li in self.items.all() if li.remaining_to_deliver > 0}
        self.deliver_partial(qmap)
    @transaction.atomic
    def cancel(self, *, reason: str = ""):
        """
        Cancel even if receipts/allocations exist.
        Steps:
          1) Reverse any delivered stock (put it back).
          2) For each receipt allocation to this invoice, post a reversing receipt txn
             (DR A/R, CR Cash) for the allocated amount ONLY, update the receipt numbers,
             delete the allocation; if the receipt becomes empty, delete the receipt record.
          3) Reverse the sale confirm journal (DR Sales, DR Tax, CR A/R).
          4) Adjust Party.current_balance: +reversed_receipts - grand_total_at_confirm.
          5) Mark invoice CANCELLED, zero out paid fields.
        """
        if self.status == "CANCELLED":
            return

        # ---------- 1) reverse delivered stock ----------
        delivered_lines = list(self.items.all())
        for li in delivered_lines:
            qty_del = int(getattr(li, "delivered_qty", 0) or 0)
            if qty_del > 0:

                stock_return(
                    product=li.product,
                    batch_number=li.batch.batch_number if getattr(li, "batch", None) else "",
                    quantity=qty_del,
                    reason=f"Cancel Sales {self.invoice_no}",
                )
                li.delivered_qty = 0
                li.save(update_fields=["delivered_qty"])

        # ---------- 2) reverse receipts allocated to this invoice ----------
        # group allocations by receipt
        allocs = (
            CustomerReceiptAllocation.objects
            .select_related("receipt")
            .filter(invoice=self)
        )
        reversed_receipts_total = Decimal("0.00")
        cash_or_bank = getattr(self.warehouse, "default_cash_account", None) or getattr(self.warehouse, "default_bank_account", None)
        if not cash_or_bank:
            raise ValidationError("No Cash/Bank account configured for this warehouse (needed to reverse receipts).")

        by_receipt = {}
        for a in allocs:
            by_receipt.setdefault(a.receipt_id, {"receipt": a.receipt, "amount": Decimal("0.00"), "rows": []})
            by_receipt[a.receipt_id]["amount"] += Decimal(a.amount or 0)
            by_receipt[a.receipt_id]["rows"].append(a)

        for rid, pack in by_receipt.items():
            rcpt = pack["receipt"]
            amt_to_reverse = pack["amount"]
            if amt_to_reverse <= 0:
                continue

            # post reversing GL (DR A/R, CR Cash) for just the allocated amount
            post_reverse_customer_receipt_partial(
                date=self.date,
                description=f"Reverse Receipt {rcpt.number} (cancel {self.invoice_no})",
                customer_account=self.customer.chart_of_account,
                cash_or_bank_account=cash_or_bank,
                amount=amt_to_reverse,
            )
            reversed_receipts_total += amt_to_reverse

            # delete allocations rows for this invoice
            CustomerReceiptAllocation.objects.filter(id__in=[r.id for r in pack["rows"]]).delete()

            # shrink or delete the receipt record (we're NOT keeping any advance)
            rcpt.amount = Decimal(rcpt.amount or 0) - amt_to_reverse
            rcpt.unallocated_amount = max(Decimal(rcpt.unallocated_amount or 0) - amt_to_reverse, Decimal("0"))
            rcpt.save(update_fields=["amount", "unallocated_amount"])

            if rcpt.amount <= 0 and rcpt.allocations.count() == 0 and rcpt.unallocated_amount <= 0:
                # nuke the receipt record itself (accounting already reversed above)
                rcpt.delete()

        # ---------- 3) reverse sale confirm journal ----------
        # What we posted at confirm was subtotal=(total_amount - discount), tax=self.tax
        base_subtotal = Decimal(self.total_amount or 0) - Decimal(self.discount or 0)
        tax_amount = Decimal(self.tax or 0)
        post_cancel_sale(
            date=self.date,
            description=f"Reverse Sales {self.invoice_no} (cancel)",
            subtotal=base_subtotal,
            tax=tax_amount,
            customer_account=self.customer.chart_of_account,
            warehouse_sales_account=self.warehouse.default_sales_account,
        )

        # ---------- 4) fix customer's running balance ----------
        # At confirm we increased by grand_total.
        # Each receipt originally decreased by its posted amount; we just reversed those -> increase by same.
        # Net effect to return to pre-invoice state:  + reversed_receipts_total  - grand_total
        grand_total_now = Decimal(self.grand_total or 0)
        delta = reversed_receipts_total - grand_total_now
        if delta:
            self.customer.current_balance = (self.customer.current_balance or 0) + delta
            self.customer.save(update_fields=["current_balance"])

        # ---------- 5) finalize invoice fields ----------
        self.paid_amount = Decimal("0.00")
        self.payment_status = "UNPAID"
        self.status = "CANCELLED"
        self.save(update_fields=["paid_amount", "payment_status", "status"])
    # Keep same invoice number from first save
    def save(self, *args, **kwargs):
        if self.pk is None and not self.invoice_no:
            self._ensure_number()
        super().save(*args, **kwargs)


class SaleInvoiceItem(models.Model):
    invoice  = models.ForeignKey(SaleInvoice, related_name="items", on_delete=models.CASCADE)
    product  = models.ForeignKey("inventory.Product", on_delete=models.PROTECT)
    batch    = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.SET_NULL)
    quantity = models.PositiveIntegerField()
    bonus    = models.PositiveIntegerField(default=0)
    # packing  = models.PositiveIntegerField(default=0)
    rate     = models.DecimalField(max_digits=10, decimal_places=2)
    discount1= models.DecimalField(max_digits=5, decimal_places=2, default=0)
    # discount2= models.DecimalField(max_digits=5, decimal_places=2, default=0)
    amount   = models.DecimalField(max_digits=12, decimal_places=2)
    bid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Track partial deliveries
    delivered_qty = models.PositiveIntegerField(default=0)
    @property
    def remaining_to_deliver(self) -> int:
        return max(int(self.quantity or 0) - int(self.delivered_qty or 0), 0)

    @property
    def total_ordered(self) -> int:
        return int((self.quantity or 0) + (self.bonus or 0))

    @property
    def remaining_to_deliver(self) -> int:
        return max(self.total_ordered - int(self.delivered_qty or 0), 0)



Q2 = Decimal("0.01")

SR_STATUS = (
    ("DRAFT", "Draft"),
    ("PRODUCTS_RETURNED", "Products Returned (Stock In)"),
    ("REFUNDED", "Refunded (Cash/Bank)"),
    ("CREDITED", "Credited to Receivable"),
    ("CANCELLED", "Cancelled"),
)

class SaleReturn(models.Model):
    return_no     = models.CharField(max_length=50, unique=True, blank=True)
    date          = models.DateField()
    invoice       = models.ForeignKey("sale.SaleInvoice", null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="returns")
    customer      = models.ForeignKey(Party, on_delete=models.PROTECT,
                                      limit_choices_to={"party_type":"customer"})
    warehouse     = models.ForeignKey(Warehouse, on_delete=models.PROTECT)

    total_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # from items (qty*rate)
    tax           = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # optional VAT on return
    status        = models.CharField(max_length=24, choices=SR_STATUS, default="DRAFT")

    # what’s actually been returned & paid
    returned_value   = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # sum(items.returned_qty*rate)
    refunded_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # accounting references
    credit_note_txn  = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="sr_credit_note_txn")
    refund_txn       = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="sr_refund_txn")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # DO NOT do side effects in save()
    def save(self, *args, **kwargs):
        creating = self.pk is None
        if creating and not self.return_no:
            self.return_no = self._next_sequence()
        return super().save(*args, **kwargs)

    @staticmethod
    def _next_sequence(prefix="SRN-"):
        last = (SaleReturn.objects
                .filter(return_no__startswith=prefix)
                .order_by("-id")
                .values_list("return_no", flat=True)
                .first())
        if not last: return f"{prefix}1"
        try: n = int(last.split("-")[-1])
        except Exception: n = 0
        return f"{prefix}{n+1}"

    def recompute_totals_from_items(self):
        total = Decimal("0")
        for li in self.items.all():
            amt = (Decimal(li.quantity or 0) * Decimal(li.rate or 0)).quantize(Q2)
            if li.amount != amt:
                li.amount = amt
                li.save(update_fields=["amount"])
            total += amt
        if self.total_amount != total:
            self.total_amount = total
            self.save(update_fields=["total_amount"])

    def recompute_returned_value(self):
        val = Decimal("0")
        for li in self.items.all():
            val += (Decimal(li.returned_qty or 0) * Decimal(li.rate or 0)).quantize(Q2)
        if self.returned_value != val:
            self.returned_value = val
            self.save(update_fields=["returned_value"])

class SaleReturnItem(models.Model):
    return_invoice = models.ForeignKey(SaleReturn, related_name="items", on_delete=models.CASCADE)
    product       = models.ForeignKey(Product, on_delete=models.PROTECT)
    batch_number  = models.CharField(max_length=50, blank=True)  # exact batch to return into stock
    expiry_date   = models.DateField(null=True, blank=True)
    quantity      = models.PositiveIntegerField()                # planned qty (from invoice loader)
    rate          = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount        = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # filled on “Return products” action
    returned_qty  = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.product} x {self.quantity} (SR {self.return_invoice.return_no})"


class RecoveryLog(models.Model):
    invoice = models.ForeignKey(SaleInvoice, on_delete=models.CASCADE, related_name='recovery_logs')
    employee = models.ForeignKey('hr.Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='recovery_logs')
    date = models.DateField()
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.invoice.invoice_no} - {self.date}"

